import asyncio
import json
from threading import Barrier, Thread
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import Mock

from backend.database import Base
from backend.models import AgentStep, BugReport, ResolutionReport, WorkflowRun
from backend.workflow import (
    get_event_queue,
    publish_event,
    resume_workflow,
    run_workflow,
    record_agent_step,
    remove_event_queue,
)


@pytest.fixture
def workflow_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'workflow.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    report_id = str(uuid4())
    run_id = str(uuid4())
    with factory() as db:
        db.add(
            BugReport(
                id=report_id,
                title="Test bug",
                description="A test failure",
                repo_url="file:///unused",
                branch="main",
            )
        )
        db.add(WorkflowRun(id=run_id, bug_report_id=report_id, status="running"))
        db.commit()

    yield factory, run_id

    remove_event_queue(run_id)
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_record_agent_step_persists_evidence_and_events(workflow_db):
    factory, run_id = workflow_db
    result = {"exception": "AssertionError", "line": 12}

    returned = record_agent_step(
        factory,
        run_id,
        "log_analysis",
        lambda: result,
    )

    assert returned == result
    with factory() as db:
        step = db.query(AgentStep).filter_by(run_id=run_id).one()
        assert step.status == "completed"
        assert json.loads(step.evidence) == result
        assert step.completed_at is not None

    queue = get_event_queue(run_id)
    assert queue.get_nowait() == {"type": "step_started", "step_name": "log_analysis"}
    assert queue.get_nowait() == {
        "type": "step_completed",
        "step_name": "log_analysis",
        "evidence": result,
    }


def test_record_agent_step_marks_failures(workflow_db):
    factory, run_id = workflow_db

    def fail():
        raise ValueError("agent failed")

    with pytest.raises(ValueError, match="agent failed"):
        record_agent_step(factory, run_id, "hypothesis", fail)

    with factory() as db:
        step = db.query(AgentStep).filter_by(run_id=run_id).one()
        assert step.status == "failed"
        assert json.loads(step.evidence) == {"error": "agent failed"}

    queue = get_event_queue(run_id)
    assert queue.get_nowait() == {"type": "step_started", "step_name": "hypothesis"}
    assert queue.get_nowait() == {
        "type": "step_failed",
        "step_name": "hypothesis",
        "error": "agent failed",
    }


def test_event_queues_are_isolated_by_run():
    first = get_event_queue("run-a")
    second = get_event_queue("run-b")
    publish_event("run-a", {"type": "step_started", "step_name": "clone_repo"})

    assert first is get_event_queue("run-a")
    assert first.get_nowait()["step_name"] == "clone_repo"
    assert second.empty()
    remove_event_queue("run-a")
    remove_event_queue("run-b")


def test_event_queue_accepts_publication_from_worker_thread():
    async def exercise():
        queue = get_event_queue("run-thread")
        waiter = asyncio.create_task(queue.get())
        await asyncio.sleep(0)
        thread = Thread(
            target=publish_event,
            args=("run-thread", {"type": "step_started", "step_name": "clone_repo"}),
        )
        thread.start()
        thread.join()
        event = await asyncio.wait_for(waiter, timeout=1)
        remove_event_queue("run-thread")
        return event

    assert asyncio.run(exercise()) == {
        "type": "step_started",
        "step_name": "clone_repo",
    }


@pytest.mark.parametrize("approved", [True, False])
def test_workflow_pauses_for_approval_after_parallel_analysis(
    workflow_db,
    monkeypatch,
    tmp_path,
    approved,
):
    factory, run_id = workflow_db
    workspace = str(tmp_path / "clone")
    barrier = Barrier(2, timeout=5)

    def analyze_log(stack_trace, description):
        barrier.wait()
        return {"exception": "AssertionError", "module": "store/pricing.py", "line": 3}

    def identify_files(stack_trace, file_listing):
        barrier.wait()
        return {
            "files": [
                {"path": "store/pricing.py", "relevance_reason": "Price formula", "rank": 1}
            ]
        }

    monkeypatch.setattr("backend.tools.repo_tools.clone_repo", lambda url, branch: workspace)
    monkeypatch.setattr("backend.tools.repo_tools.list_files", lambda path: "store/pricing.py")
    monkeypatch.setattr("backend.agents.log_analysis.analyze_log", analyze_log)
    monkeypatch.setattr("backend.agents.file_identification.identify_files", identify_files)
    monkeypatch.setattr(
        "backend.tools.repo_tools.read_file",
        lambda path, rel_path: "def calculate_discount(): return order_total * coupon_rate",
    )
    monkeypatch.setattr(
        "backend.agents.hypothesis.generate_hypothesis",
        lambda *args: {
            "root_cause": "The pricing formula returns the coupon amount.",
            "affected_files": ["store/pricing.py"],
            "fix_strategy": "Return the discounted total.",
            "confidence": 0.9,
        },
    )
    monkeypatch.setattr(
        "backend.agents.patch.generate_patch",
        lambda *args: {
            "patch": "diff --git a/store/pricing.py b/store/pricing.py",
            "files_changed": ["store/pricing.py"],
            "explanation": "Fix the formula.",
        },
    )
    monkeypatch.setattr("backend.tools.test_tools.apply_patch", lambda path, diff: True)
    monkeypatch.setattr(
        "backend.tools.test_tools.run_pytest",
        lambda path: {"passed": 2, "failed": 0, "errors": [], "output_excerpt": "2 passed"},
    )
    monkeypatch.setattr(
        "backend.tools.git_tools.recent_commits",
        lambda path, n: [{"sha": "abc", "message": "fix price", "author": "dev", "date": "today"}],
    )
    monkeypatch.setattr(
        "backend.tools.git_tools.show_commit_diff",
        lambda path, sha: "diff --git a/store/pricing.py b/store/pricing.py\n",
    )

    result = run_workflow(
        run_id,
        {
            "title": "Wrong checkout total",
            "description": "Coupon charge is wrong.",
            "stack_trace": "Expected 75, got 25",
            "repo_url": "file:///demo-target",
            "branch": "main",
        },
        factory,
    )

    assert result["workspace_dir"] == workspace
    assert result["log_analysis"]["exception"] == "AssertionError"
    assert result["relevant_files"]["files"][0]["path"] == "store/pricing.py"
    assert result["recent_changes"]["commits"][0]["files_changed"] == ["store/pricing.py"]
    assert result["hypothesis"]["root_cause"].startswith("The pricing formula")
    assert result["patch_result"]["files_changed"] == ["store/pricing.py"]
    assert result.get("__interrupt__")

    resumed = resume_workflow(run_id, approved, factory)
    assert resumed["patch_approved"] is approved
    assert resumed["report"]["patch_approved"] == int(approved)
    assert resumed["report_id"] == resumed["report"]["id"]

    with factory() as db:
        run = db.get(WorkflowRun, run_id)
        steps = db.query(AgentStep).filter_by(run_id=run_id).all()
        report = db.query(ResolutionReport).filter_by(run_id=run_id).one()
        assert run.workspace_dir == workspace
        expected_steps = {
            "clone_repo",
            "log_analysis",
            "file_identification",
            "change_inspection",
            "hypothesis",
            "patch",
            "awaiting_approval",
            "report",
        }
        if approved:
            expected_steps.update({
                "apply_patch",
                "test_runner",
            })

        assert {step.step_name for step in steps} == expected_steps
        assert all(step.status == "completed" for step in steps)
        assert run.status == ("completed" if approved else "rejected")
        assert run.iteration == 1
        assert report.id == resumed["report_id"]


def test_workflow_retries_failed_tests_then_persists_report(
    workflow_db,
    monkeypatch,
    tmp_path,
):
    factory, run_id = workflow_db
    workspace = str(tmp_path / "retry-clone")
    barrier = Barrier(2, timeout=5)
    hypothesis_failures = []

    def analyze_log(stack_trace, description):
        barrier.wait()
        return {"exception": "AssertionError", "module": "store/pricing.py", "line": 3}

    def identify_files(stack_trace, file_listing):
        barrier.wait()
        return {
            "files": [
                {"path": "store/pricing.py", "relevance_reason": "Price formula", "rank": 1}
            ]
        }

    def generate_hypothesis(*args):
        hypothesis_failures.append(args[3])
        return {
            "root_cause": "The pricing formula returns the coupon amount.",
            "affected_files": ["store/pricing.py"],
            "fix_strategy": "Return the discounted total.",
            "confidence": 0.9,
        }

    monkeypatch.setattr("backend.tools.repo_tools.clone_repo", lambda url, branch: workspace)
    monkeypatch.setattr("backend.tools.repo_tools.list_files", lambda path: "store/pricing.py")
    monkeypatch.setattr("backend.tools.repo_tools.read_file", lambda path, rel: "buggy formula")
    monkeypatch.setattr("backend.agents.log_analysis.analyze_log", analyze_log)
    monkeypatch.setattr("backend.agents.file_identification.identify_files", identify_files)
    monkeypatch.setattr("backend.agents.hypothesis.generate_hypothesis", generate_hypothesis)
    monkeypatch.setattr(
        "backend.agents.patch.generate_patch",
        lambda *args: {
            "patch": "diff --git a/store/pricing.py b/store/pricing.py",
            "files_changed": ["store/pricing.py"],
            "explanation": "Fix the formula.",
        },
    )
    monkeypatch.setattr(
        "backend.tools.git_tools.recent_commits",
        lambda path, n: [{"sha": "abc", "message": "fix price", "author": "dev", "date": "today"}],
    )
    monkeypatch.setattr(
        "backend.tools.git_tools.show_commit_diff",
        lambda path, sha: "diff --git a/store/pricing.py b/store/pricing.py\n",
    )
    apply_patch_mock = Mock(return_value=True)
    pytest_mock = Mock(
        side_effect=[
            {"passed": 1, "failed": 1, "errors": [], "output_excerpt": "expected 75, got 25"},
            {"passed": 2, "failed": 0, "errors": [], "output_excerpt": "2 passed"},
        ]
    )
    monkeypatch.setattr("backend.tools.test_tools.apply_patch", apply_patch_mock)
    monkeypatch.setattr("backend.tools.test_tools.run_pytest", pytest_mock)

    started = run_workflow(
        run_id,
        {
            "title": "Wrong checkout total",
            "description": "Coupon charge is wrong.",
            "stack_trace": "Expected 75, got 25",
            "repo_url": "file:///demo-target",
            "branch": "main",
        },
        factory,
    )
    assert started.get("__interrupt__")

    retry_wait = resume_workflow(run_id, True, factory)
    assert retry_wait["iteration"] == 2
    assert retry_wait.get("__interrupt__")

    finished = resume_workflow(run_id, True, factory)
    assert finished["report"]["test_results"]
    assert finished["report"]["iteration_count"] == 2
    assert len(hypothesis_failures) == 2
    assert hypothesis_failures[0] == []
    assert "expected 75, got 25" in hypothesis_failures[1][0]
    assert apply_patch_mock.call_count == 2
    assert pytest_mock.call_count == 2

    with factory() as db:
        run = db.get(WorkflowRun, run_id)
        steps = db.query(AgentStep).filter_by(run_id=run_id).all()
        assert run.status == "completed"
        assert run.iteration == 2
        assert sum(step.step_name == "test_runner" for step in steps) == 2
        assert sum(step.step_name == "hypothesis" for step in steps) == 2
        assert sum(step.step_name == "awaiting_approval" for step in steps) == 2
