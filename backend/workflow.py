"""Shared workflow state, per-run events, and persisted step lifecycle."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from threading import Lock
from typing import Any, Callable, TypedDict

from sqlalchemy.orm import Session

from models.agent_step import AgentStep
from models.workflow_run import WorkflowRun


class WorkflowState(TypedDict, total=False):
    run_id: str
    bug_report: dict[str, Any]
    workspace_dir: str
    log_analysis: dict[str, Any]
    relevant_files: dict[str, Any]
    recent_changes: dict[str, Any]
    hypothesis: dict[str, Any]
    patch_result: dict[str, Any]
    patch_diff: str
    patch_description: str
    patch_approved: bool | None
    patch_applied: bool
    test_output: dict[str, Any]
    test_passed: bool
    iteration: int
    previous_failures: list[str]
    report: dict[str, Any]
    report_id: str


SessionFactory = Callable[[], Session]
StepOperation = Callable[[], Any]

_event_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
_event_loops: dict[str, asyncio.AbstractEventLoop] = {}
_event_queues_lock = Lock()
_default_checkpointer: Any | None = None
_checkpointer_lock = Lock()


def _memory_checkpointer() -> Any:
    global _default_checkpointer
    with _checkpointer_lock:
        if _default_checkpointer is None:
            from langgraph.checkpoint.memory import MemorySaver

            _default_checkpointer = MemorySaver()
        return _default_checkpointer


def get_event_queue(run_id: str) -> asyncio.Queue[dict[str, Any]]:
    """Return the stable event queue associated with a workflow run."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    with _event_queues_lock:
        queue = _event_queues.get(run_id)
        if queue is None:
            queue = asyncio.Queue()
            _event_queues[run_id] = queue
        if loop is not None:
            _event_loops[run_id] = loop
        return queue


def publish_event(run_id: str, event: dict[str, Any]) -> None:
    """Publish one structured workflow event to the run's queue."""
    queue = get_event_queue(run_id)
    with _event_queues_lock:
        loop = _event_loops.get(run_id)
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    if loop is not None and loop.is_running() and loop is not current_loop:
        loop.call_soon_threadsafe(queue.put_nowait, event)
    else:
        queue.put_nowait(event)


def remove_event_queue(run_id: str) -> None:
    """Release a run's event queue after its consumer has finished."""
    with _event_queues_lock:
        _event_queues.pop(run_id, None)
        _event_loops.pop(run_id, None)


async def stream_run_events(run_id: str):
    """Yield queued run events until a terminal workflow event is received."""
    queue = get_event_queue(run_id)
    while True:
        event = await queue.get()
        yield event
        if event.get("type") in {"run_completed", "run_failed"}:
            return


def record_agent_step(
    session_factory: SessionFactory,
    run_id: str,
    step_name: str,
    operation: StepOperation,
) -> Any:
    """Persist a step's lifecycle and publish start/completion events."""
    started_at = datetime.utcnow()
    with session_factory() as db:
        step = AgentStep(
            run_id=run_id,
            step_name=step_name,
            status="running",
            started_at=started_at,
        )
        db.add(step)
        db.commit()
        step_id = step.id

    publish_event(run_id, {"type": "step_started", "step_name": step_name})

    try:
        result = operation()
    except Exception as exc:
        evidence = {"error": str(exc)}
        with session_factory() as db:
            step = db.get(AgentStep, step_id)
            if step is not None:
                step.status = "failed"
                step.evidence = json.dumps(evidence, ensure_ascii=False, default=str)
                step.completed_at = datetime.utcnow()
                db.commit()
        publish_event(
            run_id,
            {"type": "step_failed", "step_name": step_name, "error": str(exc)},
        )
        raise

    evidence = result if isinstance(result, dict) else {"result": result}
    serialized_evidence = json.dumps(evidence, ensure_ascii=False, default=str)
    with session_factory() as db:
        step = db.get(AgentStep, step_id)
        if step is not None:
            step.status = "completed"
            step.evidence = serialized_evidence
            step.completed_at = datetime.utcnow()
            db.commit()

    publish_event(
        run_id,
        {
            "type": "step_completed",
            "step_name": step_name,
            "evidence": evidence,
        },
    )
    return result


def _changed_paths(diff: str) -> list[str]:
    paths: list[str] = []
    for match in re.finditer(r"(?m)^diff --git a/(.*?) b/(.*?)$", diff):
        for path in match.groups():
            if path != "/dev/null" and path not in paths:
                paths.append(path)
    return paths


def _workspace_log_evidence(result: dict[str, Any], available_files: set[str]) -> dict[str, Any]:
    """Discard log-analysis locations that cannot exist in this repository."""
    module = result.get("module")
    if not isinstance(module, str) or module not in available_files:
        return {"exception": "Unknown", "module": "", "line": 0, "call_chain": []}
    call_chain = result.get("call_chain", [])
    if isinstance(call_chain, list):
        for entry in call_chain:
            if isinstance(entry, str) and ("/" in entry or "\\" in entry):
                referenced = entry.replace("\\", "/").split(":", 1)[0]
                if referenced not in available_files:
                    return {"exception": "Unknown", "module": "", "line": 0, "call_chain": []}
    return result


def _workspace_file_evidence(result: dict[str, Any], available_files: set[str]) -> dict[str, Any]:
    """Keep only file-identification candidates present in this clone."""
    candidates = result.get("files", [])
    if not isinstance(candidates, list):
        return {"files": []}
    files = [
        item for item in candidates
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and item["path"] in available_files
    ]
    return {"files": files}


def _set_run_status(
    session_factory: SessionFactory,
    run_id: str,
    status: str,
    iteration: int | None = None,
) -> None:
    with session_factory() as db:
        run = db.get(WorkflowRun, run_id)
        if run is not None:
            run.status = status
            if iteration is not None:
                run.iteration = iteration
            if status in {"completed", "failed", "rejected"}:
                run.completed_at = datetime.utcnow()
            db.commit()


def _begin_approval_step(session_factory: SessionFactory, run_id: str) -> str:
    with session_factory() as db:
        step = (
            db.query(AgentStep)
            .filter_by(run_id=run_id, step_name="awaiting_approval", status="running")
            .first()
        )
        if step is None:
            step = AgentStep(
                run_id=run_id,
                step_name="awaiting_approval",
                status="running",
                started_at=datetime.utcnow(),
            )
            db.add(step)
            db.commit()
            step_id = step.id
            publish_event(
                run_id,
                {"type": "step_started", "step_name": "awaiting_approval"},
            )
            return step_id
        return step.id


def _finish_approval_step(
    session_factory: SessionFactory,
    run_id: str,
    step_id: str,
    approved: bool,
) -> None:
    evidence = {"approved": approved}
    with session_factory() as db:
        step = db.get(AgentStep, step_id)
        if step is not None:
            step.status = "completed"
            step.evidence = json.dumps(evidence)
            step.completed_at = datetime.utcnow()
            db.commit()
    publish_event(
        run_id,
        {
            "type": "step_completed",
            "step_name": "awaiting_approval",
            "evidence": evidence,
        },
    )


def build_workflow(
    session_factory: SessionFactory,
    checkpointer: Any | None = None,
) -> Any:
    """Build the graph through patch review and its human approval gate."""
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Send, interrupt

    def clone_repo_node(state: WorkflowState) -> dict[str, Any]:
        from tools.repo_tools import clone_repo

        bug_report = state["bug_report"]
        workspace = record_agent_step(
            session_factory,
            state["run_id"],
            "clone_repo",
            lambda: {
                "workspace_dir": clone_repo(
                    bug_report["repo_url"],
                    bug_report.get("branch") or "main",
                )
            },
        )["workspace_dir"]
        with session_factory() as db:
            run = db.get(WorkflowRun, state["run_id"])
            if run is not None:
                run.workspace_dir = workspace
                run.status = "running"
                db.commit()
        return {"workspace_dir": workspace}

    def log_analysis_node(state: WorkflowState) -> dict[str, Any]:
        from agents.log_analysis import analyze_log
        from tools.repo_tools import list_files

        bug_report = state["bug_report"]
        def analyze() -> dict[str, Any]:
            result = analyze_log(
                bug_report.get("stack_trace") or "",
                bug_report.get("description", ""),
            )
            # Log analysis is model supplied. A module absent from this clone is
            # stale or invented evidence and must not enter downstream prompts.
            available = set(list_files(state["workspace_dir"]).splitlines())
            return _workspace_log_evidence(result, available)

        result = record_agent_step(
            session_factory,
            state["run_id"],
            "log_analysis",
            analyze,
        )
        return {"log_analysis": result}

    def file_identification_node(state: WorkflowState) -> dict[str, Any]:
        from agents.file_identification import identify_files
        from tools.repo_tools import list_files

        bug_report = state["bug_report"]

        def identify() -> dict[str, Any]:
            file_listing = list_files(state["workspace_dir"])
            available = set(file_listing.splitlines())
            result = identify_files(bug_report.get("stack_trace") or "", file_listing)
            return _workspace_file_evidence(result, available)

        result = record_agent_step(
            session_factory,
            state["run_id"],
            "file_identification",
            identify,
        )
        return {"relevant_files": result}

    def change_inspection_node(state: WorkflowState) -> dict[str, Any]:
        from tools.git_tools import recent_commits, show_commit_diff

        relevant_items = state.get("relevant_files", {}).get("files", [])
        relevant_paths = {
            item["path"]
            for item in relevant_items
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }

        def inspect_changes() -> dict[str, Any]:
            commits: list[dict[str, Any]] = []
            for commit in recent_commits(state["workspace_dir"], 5):
                diff = show_commit_diff(state["workspace_dir"], commit["sha"])
                files_changed = _changed_paths(diff)
                if relevant_paths and not relevant_paths.intersection(files_changed):
                    continue
                commits.append({**commit, "files_changed": files_changed, "diff": diff})
            return {"commits": commits}

        result = record_agent_step(
            session_factory,
            state["run_id"],
            "change_inspection",
            inspect_changes,
        )
        return {"recent_changes": result}

    def hypothesis_node(state: WorkflowState) -> dict[str, Any]:
        from agents.hypothesis import generate_hypothesis
        from tools.repo_tools import read_file

        paths = [
            item["path"]
            for item in state.get("relevant_files", {}).get("files", [])[:5]
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        ]
        file_contents = {
            path: read_file(state["workspace_dir"], path)[:12000]
            for path in paths
        }

        result = record_agent_step(
            session_factory,
            state["run_id"],
            "hypothesis",
            lambda: generate_hypothesis(
                state.get("log_analysis", {}),
                state.get("relevant_files", {}),
                state.get("recent_changes", {}),
                state.get("previous_failures", []),
                state.get("bug_report", {}),
                file_contents,
            ),
        )
        return {"hypothesis": result}

    def patch_node(state: WorkflowState) -> dict[str, Any]:
        from agents.patch import generate_patch
        from tools.repo_tools import list_files, read_file
        from tools.test_tools import validate_patch

        hypothesis = state.get("hypothesis", {})

        def create_patch() -> dict[str, Any]:
            file_contents = {
                path: read_file(state["workspace_dir"], path)
                for path in hypothesis.get("affected_files", [])
                if isinstance(path, str)
            }
            previous_failures = list(state.get("previous_failures", []))
            test_command = state.get("bug_report", {}).get("test_command")

            for attempt in range(2):
                result = generate_patch(
                    hypothesis,
                    file_contents,
                    previous_failures,
                    state.get("bug_report", {}),
                )
                declared = result.get("files_changed", [])
                available = set(list_files(state["workspace_dir"]).splitlines())
                invalid_declarations = (
                    not isinstance(declared, list)
                    or not declared
                    or any(not isinstance(path, str) or path not in available for path in declared)
                )
                if invalid_declarations:
                    validation = {
                        "passed": False,
                        "error": "Candidate patch declares no changed files or targets a file absent from the isolated workspace.",
                    }
                else:
                    validation = validate_patch(
                        state["workspace_dir"],
                        result.get("patch", ""),
                        test_command,
                        expected_files=declared,
                    )
                if validation.get("passed"):
                    result["preapproval_validation"] = validation
                    return result

                test_results = validation.get("test_results", {})
                failure_details = (
                    test_results.get("output_excerpt", "")
                    if isinstance(test_results, dict)
                    else ""
                )
                failure_details = failure_details or validation.get(
                    "error",
                    "The candidate patch did not pass its test run.",
                )
                previous_failures.append(
                    f"Pre-approval patch validation attempt {attempt + 1} failed:\n"
                    f"{failure_details}"
                )

            raise RuntimeError(
                "Generated patch did not pass pre-approval tests after two attempts. "
                f"{previous_failures[-1]}"
            )

        result = record_agent_step(
            session_factory,
            state["run_id"],
            "patch",
            create_patch,
        )
        publish_event(
            state["run_id"],
            {
                "type": "patch_ready",
                "diff": result.get("patch", ""),
                "description": result.get("explanation", ""),
            },
        )
        return {
            "patch_result": result,
            "patch_diff": result.get("patch", ""),
            "patch_description": result.get("explanation", ""),
        }

    def approval_node(state: WorkflowState) -> dict[str, Any]:
        _set_run_status(session_factory, state["run_id"], "awaiting_approval")
        step_id = _begin_approval_step(session_factory, state["run_id"])
        approved = bool(
            interrupt(
                {
                    "type": "approval_required",
                    "run_id": state["run_id"],
                    "diff": state.get("patch_diff", ""),
                    "description": state.get("patch_description", ""),
                }
            )
        )
        status = "approved" if approved else "rejected"
        _set_run_status(session_factory, state["run_id"], status)
        _finish_approval_step(session_factory, state["run_id"], step_id, approved)
        return {"patch_approved": approved}

    def apply_patch_node(state: WorkflowState) -> dict[str, Any]:
        from tools.test_tools import apply_patch

        applied = record_agent_step(
            session_factory,
            state["run_id"],
            "apply_patch",
            lambda: {
                "success": apply_patch(
                    state["workspace_dir"],
                    state.get("patch_diff", ""),
                )
            },
        )["success"]
        return {"patch_applied": bool(applied)}

    def test_runner_node(state: WorkflowState) -> dict[str, Any]:
        from tools.test_tools import run_pytest

        def run_tests() -> dict[str, Any]:
            test_command = state.get("bug_report", {}).get("test_command")
            if test_command:
                result = run_pytest(state["workspace_dir"], test_command)
            else:
                result = run_pytest(state["workspace_dir"])
            if not state.get("patch_applied", False):
                result = {
                    **result,
                    "errors": [
                        *result.get("errors", []),
                        "Patch application failed; tests ran against the unchanged workspace.",
                    ],
                }
            return result

        result = record_agent_step(
            session_factory,
            state["run_id"],
            "test_runner",
            run_tests,
        )
        passed = (
            state.get("patch_applied", False)
            and result.get("passed", 0) > 0
            and result.get("failed", 0) == 0
            and result.get("errors", []) == []
        )
        return {"test_output": result, "test_passed": passed}

    def prepare_retry_node(state: WorkflowState) -> dict[str, Any]:
        output = state.get("test_output", {})
        failure = output.get("output_excerpt") or json.dumps(
            {"failed": output.get("failed", 0), "errors": output.get("errors", [])},
            ensure_ascii=False,
            default=str,
        )
        previous_failures = [*state.get("previous_failures", []), failure]
        iteration = state.get("iteration", 1) + 1
        with session_factory() as db:
            run = db.get(WorkflowRun, state["run_id"])
            if run is not None:
                run.iteration = iteration
                db.commit()
        return {"previous_failures": previous_failures, "iteration": iteration}

    def report_node(state: WorkflowState) -> dict[str, Any]:
        from agents.report import generate_report
        from models.resolution_report import ResolutionReport

        test_result = state.get(
            "test_output",
            {"passed": 0, "failed": 0, "errors": [], "output_excerpt": ""},
        )
        patch_result = {
            **state.get("patch_result", {}),
            "approved": bool(state.get("patch_approved", False)),
            "applied": bool(state.get("patch_applied", False)),
        }

        def persist_report() -> dict[str, Any]:
            report_data = generate_report(
                state["run_id"],
                state.get("hypothesis", {}),
                patch_result,
                test_result,
                state.get("iteration", 1),
            )
            with session_factory() as db:
                report = ResolutionReport(**report_data)
                db.add(report)
                db.commit()
                report_data["id"] = report.id
            return report_data

        result = record_agent_step(
            session_factory,
            state["run_id"],
            "report",
            persist_report,
        )
        final_status = (
            "rejected"
            if not state.get("patch_approved", False)
            else "completed"
        )
        _set_run_status(session_factory, state["run_id"], final_status)
        publish_event(
            state["run_id"],
            {"type": "run_completed", "report_id": result["id"]},
        )
        return {"report": result, "report_id": result["id"]}

    def after_approval(state: WorkflowState) -> str:
        return "apply_patch" if state.get("patch_approved") else "report"

    def after_tests(state: WorkflowState) -> str:
        if state.get("test_passed") or state.get("iteration", 1) >= 3:
            return "report"
        return "prepare_retry"

    def dispatch_analysis(state: WorkflowState) -> list[Any]:
        return [
            Send("log_analysis", state),
            Send("file_identification", state),
        ]

    graph = StateGraph(WorkflowState)
    graph.add_node("clone_repo", clone_repo_node)
    graph.add_node("log_analysis", log_analysis_node)
    graph.add_node("file_identification", file_identification_node)
    graph.add_node("change_inspection", change_inspection_node)
    graph.add_node("hypothesis", hypothesis_node)
    graph.add_node("patch", patch_node)
    graph.add_node("awaiting_approval", approval_node)
    graph.add_node("apply_patch", apply_patch_node)
    graph.add_node("test_runner", test_runner_node)
    graph.add_node("prepare_retry", prepare_retry_node)
    graph.add_node("report", report_node)
    graph.add_edge(START, "clone_repo")
    graph.add_conditional_edges(
        "clone_repo",
        dispatch_analysis,
        ["log_analysis", "file_identification"],
    )
    graph.add_edge(["log_analysis", "file_identification"], "change_inspection")
    graph.add_edge("change_inspection", "hypothesis")
    graph.add_edge("hypothesis", "patch")
    graph.add_edge("patch", "awaiting_approval")
    graph.add_conditional_edges(
        "awaiting_approval",
        after_approval,
        {"apply_patch": "apply_patch", "report": "report"},
    )
    graph.add_edge("apply_patch", "test_runner")
    graph.add_conditional_edges(
        "test_runner",
        after_tests,
        {"report": "report", "prepare_retry": "prepare_retry"},
    )
    graph.add_edge("prepare_retry", "hypothesis")
    graph.add_edge("report", END)
    return graph.compile(checkpointer=checkpointer or _memory_checkpointer())


def run_workflow(
    run_id: str,
    bug_report: dict[str, Any],
    session_factory: SessionFactory,
    checkpointer: Any | None = None,
) -> WorkflowState:
    """Start a workflow and pause after the patch is ready for approval."""
    _set_run_status(session_factory, run_id, "running", iteration=1)
    graph = build_workflow(session_factory, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": run_id}}
    try:
        return graph.invoke(
            {
                "run_id": run_id,
                "bug_report": bug_report,
                "workspace_dir": "",
                "previous_failures": [],
                "iteration": 1,
                "test_output": {
                    "passed": 0,
                    "failed": 0,
                    "errors": [],
                    "output_excerpt": "",
                },
                "patch_applied": False,
            },
            config=config,
        )
    except Exception as exc:
        _set_run_status(session_factory, run_id, "failed")
        publish_event(run_id, {"type": "run_failed", "error": str(exc)})
        raise


def resume_workflow(
    run_id: str,
    approved: bool,
    session_factory: SessionFactory,
    checkpointer: Any | None = None,
) -> WorkflowState:
    """Resume a paused run with the developer's approval decision."""
    from langgraph.types import Command

    graph = build_workflow(session_factory, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": run_id}}
    try:
        checkpoint = graph.get_state(config)
        print("=== RESUME DEBUG ===")
        print("run_id:", run_id)
        print("approved:", approved)
        print("next:", checkpoint.next)
        print("tasks:", checkpoint.tasks)
        print("====================")
        result = graph.invoke(Command(resume=approved), config=config)
        print("=== RESUME RESULT ===")
        print(result)
        print("====================")
        return result
    except Exception as exc:
        _set_run_status(session_factory, run_id, "failed")
        publish_event(run_id, {"type": "run_failed", "error": str(exc)})
        raise
