import json
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from git import Repo

from backend.database import Base, get_db
from backend.main import app
from backend.models import AgentStep, BugReport, ResolutionReport, WorkflowRun
from backend.workflow import publish_event, remove_event_queue
from backend.routers import runs as runs_router
from backend import config as backend_config


@pytest.fixture
def api_db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'api.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(app.router, "on_startup", [])

    def override_get_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(runs_router, "SessionLocal", factory)
    yield factory
    app.dependency_overrides.clear()
    with factory() as db:
        run_ids = [run.id for run in db.query(WorkflowRun).all()]
    for run_id in run_ids:
        remove_event_queue(run_id)
    Base.metadata.drop_all(engine)
    engine.dispose()


def _make_run(factory, status="running"):
    bug_id = str(uuid4())
    run_id = str(uuid4())
    with factory() as db:
        db.add(
            BugReport(
                id=bug_id,
                title="Broken checkout",
                description="Discount total is wrong.",
                repo_url="https://example.invalid/demo.git",
                branch="main",
            )
        )
        db.add(WorkflowRun(id=run_id, bug_report_id=bug_id, status=status))
        db.commit()
    return run_id


def test_create_run_persists_report_and_starts_workflow(api_db, monkeypatch):
    calls = []
    monkeypatch.setattr(
        runs_router,
        "run_workflow",
        lambda run_id, bug_report, factory: calls.append((run_id, bug_report, factory)),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={
                "title": "Broken checkout",
                "description": "Coupon amount is returned as the total.",
                "stack_trace": "AssertionError: expected 75, got 25",
                "repo_url": "https://example.invalid/demo.git",
                "branch": "main",
            },
        )

    assert response.status_code == 200
    run_id = response.json()["run_id"]
    assert len(calls) == 1
    assert calls[0][0] == run_id
    assert calls[0][1]["title"] == "Broken checkout"
    assert calls[0][1]["apply_to_source"] is False
    with api_db() as db:
        run = db.get(WorkflowRun, run_id)
        assert run is not None
        assert run.status == "pending"
        assert db.get(BugReport, run.bug_report_id).repo_url.endswith("demo.git")


def test_create_local_run_can_opt_into_source_application(api_db, monkeypatch, tmp_path):
    repository = tmp_path / "local-repository"
    Repo.init(repository, initial_branch="main")
    calls = []
    monkeypatch.setattr(
        runs_router,
        "run_workflow",
        lambda run_id, bug_report, factory: calls.append(bug_report),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={
                "title": "Broken checkout",
                "description": "Coupon amount is returned as the total.",
                "repo_url": str(repository),
                "repository_type": "local",
                "branch": "main",
                "apply_to_source": True,
            },
        )

    assert response.status_code == 200
    assert calls[0]["apply_to_source"] is True
    assert calls[0]["repository_type"] == "local"
    assert calls[0]["repo_url"] == str(repository.resolve())


def test_create_run_rejects_source_application_for_git_urls(api_db):
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={
                "title": "Broken checkout",
                "description": "Coupon amount is returned as the total.",
                "repo_url": "https://example.invalid/demo.git",
                "repository_type": "git",
                "apply_to_source": True,
            },
        )

    assert response.status_code == 422
    assert "requires a local repository" in response.json()["detail"]


def test_public_deployment_rejects_local_repository_paths(api_db, monkeypatch):
    monkeypatch.setattr(backend_config.settings, "allow_local_repositories", False)
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={
                "title": "Broken checkout",
                "description": "Coupon amount is wrong.",
                "repo_url": "C:/server/private-repo",
                "repository_type": "local",
            },
        )

    assert response.status_code == 403
    assert "disabled on this public deployment" in response.json()["detail"]


def test_deployment_access_code_protects_api_and_sets_http_only_cookie(api_db, monkeypatch):
    monkeypatch.setattr(backend_config.settings, "access_token", "demo-secret")
    run_id = _make_run(api_db)

    with TestClient(app) as client:
        blocked = client.get(f"/api/runs/{run_id}")
        wrong_login = client.post("/api/auth/login", json={"token": "wrong"})
        login = client.post("/api/auth/login", json={"token": "demo-secret"})
        allowed = client.get(f"/api/runs/{run_id}")

    assert blocked.status_code == 401
    assert wrong_login.status_code == 401
    assert login.status_code == 200
    assert "httponly" in login.headers["set-cookie"].lower()
    assert allowed.status_code == 200


def test_get_run_returns_ordered_steps_and_decoded_evidence(api_db):
    run_id = _make_run(api_db)
    started = datetime.utcnow()
    with api_db() as db:
        db.add_all(
            [
                AgentStep(
                    run_id=run_id,
                    step_name="clone_repo",
                    status="completed",
                    evidence=json.dumps({"workspace_dir": "C:/temp/demo"}),
                    started_at=started,
                    completed_at=started + timedelta(seconds=1),
                ),
                AgentStep(
                    run_id=run_id,
                    step_name="log_analysis",
                    status="running",
                    evidence=None,
                    started_at=started + timedelta(seconds=2),
                ),
            ]
        )
        db.commit()

    with TestClient(app) as client:
        response = client.get(f"/api/runs/{run_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run_id
    assert [step["step_name"] for step in body["steps"]] == [
        "clone_repo",
        "log_analysis",
    ]
    assert body["steps"][0]["evidence"] == {"workspace_dir": "C:/temp/demo"}
    assert body["steps"][1]["evidence"] is None


def test_run_events_stream_structured_events_and_terminal_replay(api_db):
    run_id = _make_run(api_db)
    publish_event(run_id, {"type": "step_started", "step_name": "clone_repo"})
    publish_event(run_id, {"type": "run_completed", "report_id": "report-1"})

    with TestClient(app) as client:
        response = client.get(f"/api/runs/{run_id}/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: step_started" in response.text
    assert '"step_name": "clone_repo"' in response.text
    assert "event: run_completed" in response.text

    with api_db() as db:
        db.get(WorkflowRun, run_id).status = "completed"
        db.commit()
    with TestClient(app) as client:
        replay = client.get(f"/api/runs/{run_id}/events")
    assert "event: run_completed" in replay.text


@pytest.mark.parametrize(
    ("approved", "final_status"),
    [(True, "completed"), (False, "rejected")],
)
def test_approve_endpoint_resumes_paused_workflow(
    api_db,
    monkeypatch,
    approved,
    final_status,
):
    run_id = _make_run(api_db, status="awaiting_approval")
    calls = []

    def resume(run, decision, factory):
        calls.append((run, decision))
        with factory() as db:
            db.get(WorkflowRun, run).status = final_status
            db.commit()
        return {"report_id": "report-final"}

    monkeypatch.setattr(runs_router, "resume_workflow", resume)
    with TestClient(app) as client:
        response = client.post(
            f"/api/runs/{run_id}/approve",
            json={"approved": approved},
        )

    assert response.status_code == 200
    assert calls == [(run_id, approved)]
    assert response.json() == {
        "run_id": run_id,
        "status": final_status,
        "approved": approved,
        "report_id": "report-final",
    }


def test_get_report_returns_resolution_report(api_db):
    run_id = _make_run(api_db, status="completed")
    with api_db() as db:
        report = ResolutionReport(
            run_id=run_id,
            root_cause="Wrong coupon formula",
            evidence='{"line": 3}',
            changes_made="Updated pricing.py",
            test_results='{"passed": 2, "failed": 0}',
            iteration_count=2,
            patch_approved=1,
        )
        db.add(report)
        db.commit()
        report_id = report.id

    with TestClient(app) as client:
        response = client.get(f"/api/reports/{run_id}")

    assert response.status_code == 200
    assert response.json()["id"] == report_id
    assert response.json()["root_cause"] == "Wrong coupon formula"
    assert response.json()["patch_approved"] == 1


def test_missing_run_and_report_return_404(api_db):
    missing_id = str(uuid4())
    with TestClient(app) as client:
        run_response = client.get(f"/api/runs/{missing_id}")
        report_response = client.get(f"/api/reports/{missing_id}")

    assert run_response.status_code == 404
    assert report_response.status_code == 404
