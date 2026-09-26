"""Workflow run endpoints and server-sent events."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

if __package__ and __package__.startswith("backend."):
    from backend.database import SessionLocal, get_db
    from backend.models import AgentStep, BugReport, WorkflowRun
    from backend.workflow import get_event_queue, resume_workflow, run_workflow, stream_run_events
else:  # Support the documented `cd backend; uvicorn main:app` launch.
    from database import SessionLocal, get_db
    from models import AgentStep, BugReport, WorkflowRun
    from workflow import get_event_queue, resume_workflow, run_workflow, stream_run_events


router = APIRouter(prefix="/api/runs", tags=["runs"])


class BugReportCreate(BaseModel):
    title: str
    description: str
    stack_trace: str | None = None
    repo_url: str
    branch: str = "main"


class ApprovalRequest(BaseModel):
    approved: bool


def _isoformat(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _serialize_step(step: AgentStep) -> dict[str, Any]:
    evidence: Any = step.evidence
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except (TypeError, ValueError):
            pass
    return {
        "id": step.id,
        "step_name": step.step_name,
        "status": step.status,
        "evidence": evidence,
        "started_at": _isoformat(step.started_at),
        "completed_at": _isoformat(step.completed_at),
    }


@router.post("")
async def create_run(
    request: BugReportCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    bug_report = BugReport(**request.model_dump())
    db.add(bug_report)
    db.flush()
    run = WorkflowRun(bug_report_id=bug_report.id, status="pending")
    db.add(run)
    db.commit()
    db.refresh(run)

    # Bind the run's queue to the serving loop before the worker thread emits
    # events from the background workflow.
    get_event_queue(run.id)
    workflow_input = {
        "title": bug_report.title,
        "description": bug_report.description,
        "stack_trace": bug_report.stack_trace,
        "repo_url": bug_report.repo_url,
        "branch": bug_report.branch,
    }
    background_tasks.add_task(run_workflow, run.id, workflow_input, SessionLocal)
    return {"run_id": run.id}


@router.get("/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")

    steps = (
        db.query(AgentStep)
        .filter_by(run_id=run_id)
        .order_by(AgentStep.started_at, AgentStep.id)
        .all()
    )
    return {
        "run_id": run.id,
        "bug_report_id": run.bug_report_id,
        "status": run.status,
        "workspace_dir": run.workspace_dir,
        "iteration": run.iteration,
        "created_at": _isoformat(run.created_at),
        "completed_at": _isoformat(run.completed_at),
        "steps": [_serialize_step(step) for step in steps],
    }


def _encode_sse(event: dict[str, Any]) -> str:
    event_type = str(event.get("type", "message"))
    payload = json.dumps(event, ensure_ascii=False, default=str)
    return f"event: {event_type}\ndata: {payload}\n\n"


async def _terminal_event(status: str):
    event_type = "run_failed" if status == "failed" else "run_completed"
    yield _encode_sse({"type": event_type, "status": status})


@router.get("/{run_id}/events")
async def run_events(run_id: str) -> StreamingResponse:
    with SessionLocal() as db:
        run = db.get(WorkflowRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Workflow run not found")
        status = run.status

    queue = get_event_queue(run_id)
    if status in {"completed", "rejected", "failed"} and queue.empty():
        content = _terminal_event(status)
    else:
        async def content_stream():
            async for event in stream_run_events(run_id):
                yield _encode_sse(event)

        content = content_stream()

    return StreamingResponse(
        content,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{run_id}/approve")
def approve_run(
    run_id: str,
    request: ApprovalRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    run = db.get(WorkflowRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    if run.status != "awaiting_approval":
        raise HTTPException(
            status_code=409,
            detail=f"Run is not awaiting approval (status: {run.status})",
        )

    state = resume_workflow(run_id, request.approved, SessionLocal)
    db.refresh(run)
    return {
        "run_id": run.id,
        "status": run.status,
        "approved": request.approved,
        "report_id": state.get("report_id"),
    }
