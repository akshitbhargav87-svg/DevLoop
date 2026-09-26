"""Resolution report retrieval endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

if __package__ and __package__.startswith("backend."):
    from database import get_db
    from models import ResolutionReport
else:  # Support the documented `cd backend; uvicorn main:app` launch.
    from database import get_db
    from models import ResolutionReport


router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/{run_id}")
def get_report(run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    report = (
        db.query(ResolutionReport)
        .filter_by(run_id=run_id)
        .order_by(ResolutionReport.generated_at.desc())
        .first()
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Resolution report not found")

    return {
        "id": report.id,
        "run_id": report.run_id,
        "root_cause": report.root_cause,
        "evidence": report.evidence,
        "changes_made": report.changes_made,
        "test_results": report.test_results,
        "iteration_count": report.iteration_count,
        "patch_approved": report.patch_approved,
        "generated_at": report.generated_at.isoformat(),
    }
