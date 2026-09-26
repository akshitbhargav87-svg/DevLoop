import asyncio
import json
import shutil
from threading import Barrier, Thread
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import Mock

from backend.database import Base
from backend.models import AgentStep, BugReport, ResolutionReport, WorkflowRun
from backend.workflow import (
    _split_patchable_files,
    _workspace_log_evidence,
    _workspace_file_evidence,
    get_event_queue,
    publish_event,
    resume_workflow,
    run_workflow,
    record_agent_step,
    remove_event_queue,
)


def test_patch_targets_exclude_repository_tests_but_keep_them_identified():
    implementation, tests = _split_patchable_files(
        ["pricing.py", "test_pricing.py", "pkg/tests/test_discount.py"]
    )
    assert implementation == ["pricing.py"]
    assert tests == ["test_pricing.py", "pkg/tests/test_discount.py"]


def test_stale_log_analysis_path_is_removed_before_hypothesis():
    evidence = {
        "exception": "CouponDiscountCalculationError",
        "module": "app/coupon/discount.py",
        "line": 123,
        "call_chain": ["app/coupon/discount.py:123"],
    }
    assert _workspace_log_evidence(evidence, {"pricing.py", "test_pricing.py"}) == {
        "exception": "Unknown",
        "module": "",
        "line": 0,
        "call_chain": [],
    }


def test_stale_file_identification_path_is_removed_before_hypothesis():
    evidence = {
        "files": [
            {"path": "app/coupon/discount.py", "relevance_reason": "Stale", "rank": 1},
            {"path": "pricing.py", "relevance_reason": "In repository", "rank": 2},
        ]
    }

    assert _workspace_file_evidence(evidence, {"pricing.py", "test_pricing.py"}) == {
        "files": [
            {"path": "pricing.py", "relevance_reason": "In repository", "rank": 2}
        ]
    }


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
        evidence = json.loads(step.evidence)
        assert evidence["error"] == "agent failed"
        assert evidence["exception_type"] == "ValueError"
        assert "ValueError: agent failed" in evidence["traceback"]
        assert "in fail" in evidence["traceback"]

    queue = get_event_queue(run_id)
    assert queue.get_nowait() == {"type": "step_started", "step_name": "hypothesis"}
    assert queue.get_nowait() == {
        "type": "step_failed",
        "step_name": "hypothesis",
        "error": "agent failed",
        "exception_type": "ValueError",
        "traceback": evidence["traceback"],
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
                {"path": "store/pricing.py", "relevance_reason": "Price formula", "rank": 1},
                {"path": "tests/test_pricing.py", "relevance_reason": "Expected behavior", "rank": 2},
            ]
        }

    monkeypatch.setattr("backend.tools.repo_tools.clone_repo", lambda url, branch: workspace)
    monkeypatch.setattr(
        "backend.tools.repo_tools.list_files",
        lambda path: "store/pricing.py\ntests/test_pricing.py",
    )
    monkeypatch.setattr("backend.agents.log_analysis.analyze_log", analyze_log)
    monkeypatch.setattr("backend.agents.file_identification.identify_files", identify_files)
    def read_file(path, rel_path):
        if rel_path == "tests/test_pricing.py":
            return "assert calculate_discount(100, 0.25) == 75"
        return "def calculate_discount(): return order_total * coupon_rate"

    monkeypatch.setattr("backend.tools.repo_tools.read_file", read_file)
    hypothesis_calls = []

    def generate_hypothesis(*args):
        hypothesis_calls.append(args)
        return {
            "root_cause": "The pricing formula returns the coupon amount.",
            "affected_files": ["store/pricing.py"],
            "fix_strategy": "Return the discounted total.",
            "confidence": 0.9,
        }

    monkeypatch.setattr("backend.agents.hypothesis.generate_hypothesis", generate_hypothesis)
    patch_calls = []

    def generate_patch(*args):
        patch_calls.append(args)
        return {
            "patch": "diff --git a/store/pricing.py b/store/pricing.py",
            "files_changed": ["store/pricing.py"],
            "explanation": "Fix the formula.",
        }

    monkeypatch.setattr("backend.agents.patch.generate_patch", generate_patch)
    monkeypatch.setattr("backend.tools.test_tools.apply_patch", lambda path, diff: True)
    validation_calls = []

    def validate_patch(path, diff, command=None, **kwargs):
        validation_calls.append(diff)
        if len(validation_calls) < 3:
            return {
                "passed": False,
                "test_results": {
                    "passed": 1,
                    "failed": 1,
                    "errors": [],
                    "output_excerpt": "expected 75, got 25",
                },
            }
        return {
            "passed": True,
            "test_results": {"passed": 2, "failed": 0, "errors": []},
        }

    monkeypatch.setattr("backend.tools.test_tools.validate_patch", validate_patch)
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

    checkpoint_path = tmp_path / "workflow-checkpoints.sqlite"
    with SqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
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
            checkpointer,
        )

    assert result["workspace_dir"] == workspace
    assert result["log_analysis"]["exception"] == "AssertionError"
    assert result["relevant_files"]["files"][0]["path"] == "store/pricing.py"
    assert result["recent_changes"]["commits"][0]["files_changed"] == ["store/pricing.py"]
    assert result["hypothesis"]["root_cause"].startswith("The pricing formula")
    assert result["patch_result"]["files_changed"] == ["store/pricing.py"]
    assert patch_calls[0][1] == {
        "store/pricing.py": "def calculate_discount(): return order_total * coupon_rate"
    }
    assert patch_calls[0][3]["test_evidence"] == {
        "tests/test_pricing.py": "assert calculate_discount(100, 0.25) == 75"
    }
    assert len(patch_calls) == len(validation_calls) == 3
    assert hypothesis_calls[0][6] == {
        "tests/test_pricing.py": "assert calculate_discount(100, 0.25) == 75"
    }
    assert result.get("__interrupt__")

    with SqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        resumed = resume_workflow(run_id, approved, factory, checkpointer)
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
    checkpointer = MemorySaver()

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
    monkeypatch.setattr(
        "backend.tools.test_tools.validate_patch",
        lambda path, diff, command=None, **kwargs: {
            "passed": True,
            "test_results": {"passed": 2, "failed": 0, "errors": []},
        },
    )
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
        checkpointer,
    )
    assert started.get("__interrupt__")

    retry_wait = resume_workflow(run_id, True, factory, checkpointer)
    assert retry_wait["iteration"] == 2
    assert retry_wait.get("__interrupt__")

    finished = resume_workflow(run_id, True, factory, checkpointer)
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


def test_approved_local_patch_updates_source_only_after_approval(
    workflow_db,
    monkeypatch,
    tmp_path,
):
    from git import Repo

    factory, run_id = workflow_db
    source = tmp_path / "source-repository"
    (source / "tests").mkdir(parents=True)
    (source / "pricing.py").write_text(
        "def calculate_discount(total, coupon_rate=None):\n"
        "    if coupon_rate is None:\n"
        "        return total\n"
        "    return total * coupon_rate\n",
        encoding="utf-8",
    )
    (source / "tests" / "test_pricing.py").write_text(
        "from pricing import calculate_discount\n\n"
        "def test_coupon_discount():\n"
        "    assert calculate_discount(100, 0.25) == 75\n\n"
        "def test_no_coupon():\n"
        "    assert calculate_discount(100) == 100\n",
        encoding="utf-8",
    )
    repo = Repo.init(source, initial_branch="main")
    with repo.config_writer() as config:
        config.set_value("user", "name", "DevLoop Test")
        config.set_value("user", "email", "devloop@example.invalid")
    repo.index.add(["pricing.py", "tests/test_pricing.py"])
    repo.index.commit("Add coupon pricing and tests")

    monkeypatch.setattr(
        "backend.agents.log_analysis.analyze_log",
        lambda *args: {"exception": "AssertionError", "module": "", "line": 0},
    )
    monkeypatch.setattr(
        "backend.agents.file_identification.identify_files",
        lambda *args: {
            "files": [
                {"path": "pricing.py", "relevance_reason": "Pricing formula", "rank": 1},
                {"path": "tests/test_pricing.py", "relevance_reason": "Expected result", "rank": 2},
            ]
        },
    )
    monkeypatch.setattr(
        "backend.agents.hypothesis.generate_hypothesis",
        lambda *args: {
            "root_cause": "The coupon rate is used as the final price.",
            "affected_files": ["pricing.py"],
            "fix_strategy": "Subtract the coupon rate from one before multiplying.",
            "confidence": 0.99,
        },
    )
    patch = (
        "diff --git a/pricing.py b/pricing.py\n"
        "--- a/pricing.py\n"
        "+++ b/pricing.py\n"
        "@@ -1,4 +1,4 @@\n"
        " def calculate_discount(total, coupon_rate=None):\n"
        "     if coupon_rate is None:\n"
        "         return total\n"
        "-    return total * coupon_rate\n"
        "+    return total * (1 - coupon_rate)\n"
    )
    monkeypatch.setattr(
        "backend.agents.patch.generate_patch",
        lambda *args: {
            "patch": patch,
            "files_changed": ["pricing.py"],
            "explanation": "Return the remaining balance.",
        },
    )

    checkpoint_path = tmp_path / "local-apply-checkpoints.sqlite"
    with SqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        paused = run_workflow(
            run_id,
            {
                "title": "Coupon total is wrong",
                "description": "A 25% coupon should leave 75% of the total.",
                "repo_url": str(source),
                "repository_type": "local",
                "apply_to_source": True,
                "branch": "main",
                "test_command": "pytest -q",
            },
            factory,
            checkpointer,
        )
    pricing_file = source / "pricing.py"
    assert "return total * coupon_rate" in pricing_file.read_text(encoding="utf-8")
    assert paused.get("__interrupt__")

    with SqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        completed = resume_workflow(run_id, True, factory, checkpointer)

    assert completed["patch_applied"] is True
    assert completed["test_output"]["passed"] == 2
    assert completed["test_output"]["failed"] == 0
    assert "return total * (1 - coupon_rate)" in pricing_file.read_text(encoding="utf-8")
    with factory() as db:
        assert db.get(WorkflowRun, run_id).status == "completed"
