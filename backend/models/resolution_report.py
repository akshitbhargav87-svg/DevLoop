from datetime import datetime
from uuid import uuid4

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ResolutionReport(Base):
    __tablename__ = "resolution_reports"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id"),
        nullable=False,
    )
    root_cause: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    evidence: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    changes_made: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    test_results: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    iteration_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    patch_approved: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    generated_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow,
        nullable=False,
    )