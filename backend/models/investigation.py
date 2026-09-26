"""
Investigation model.

An Investigation is the structured result of BugProof's smart investigation
pipeline for a specific Bug.  It contains both AI-generated hypotheses AND
machine-verified evidence, clearly distinguished.

IMPORTANT DISTINCTION
---------------------
- ``hypotheses``   → AI-generated suspicions (NOT verified)
- ``evidence_items`` → machine-verified facts (verified execution/observation)
- ``suspected_cause`` / ``root_cause_explanation`` → AI analysis,
  marked with ``root_cause_ai_generated = True``.
- ``confidence`` reflects AI confidence in its own analysis and does NOT
  imply machine verification.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import String, Text, Float, Boolean, DateTime, ForeignKey, JSON, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.connection import db


class InvestigationStatus(str, PyEnum):
    """Processing status of an investigation."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Investigation(db.Model):
    """
    A structured debugging investigation linked to one Bug.

    Contains the investigation summary, AI-generated root-cause analysis,
    and references to Evidence and Hypothesis records.
    """

    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    bug_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bugs.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[str] = mapped_column(
        SAEnum(InvestigationStatus, name="investigation_status_enum"),
        nullable=False,
        default=InvestigationStatus.PENDING,
    )

    # ---- Summary (may be AI-assisted) ----
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- Relevant files and functions identified during investigation ----
    # Stored as JSON lists of strings / dicts for flexibility.
    relevant_files: Mapped[list | None] = mapped_column(JSON, nullable=True)
    relevant_functions: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # ---- AI-generated root-cause analysis ----
    # These fields reflect what the AI suspects, not what was verified.
    suspected_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Confidence value in [0.0, 1.0] as reported by the AI component.
    # A high confidence does NOT imply machine verification.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Always True – the root cause fields above are AI-generated, not verified.
    root_cause_ai_generated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    # Whether the root cause has also been supported by machine-executed evidence
    root_cause_machine_supported: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Execution context captured during investigation (e.g., env info, git log snippet)
    execution_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    bug: Mapped["Bug"] = relationship("Bug", back_populates="investigations")  # noqa: F821
    evidence_items: Mapped[list["Evidence"]] = relationship(  # noqa: F821
        "Evidence", back_populates="investigation", cascade="all, delete-orphan"
    )
    hypotheses: Mapped[list["Hypothesis"]] = relationship(  # noqa: F821
        "Hypothesis", back_populates="investigation", cascade="all, delete-orphan"
    )
    fixes: Mapped[list["Fix"]] = relationship(  # noqa: F821
        "Fix", back_populates="investigation", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "bug_id": self.bug_id,
            "status": self.status,
            "summary": self.summary,
            "relevant_files": self.relevant_files,
            "relevant_functions": self.relevant_functions,
            "suspected_cause": self.suspected_cause,
            "root_cause_explanation": self.root_cause_explanation,
            "confidence": self.confidence,
            "root_cause_ai_generated": self.root_cause_ai_generated,
            "root_cause_machine_supported": self.root_cause_machine_supported,
            "execution_context": self.execution_context,
            "error_details": self.error_details,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<Investigation id={self.id!r} bug_id={self.bug_id!r} "
            f"status={self.status!r}>"
        )


class Hypothesis(db.Model):
    """
    An AI-generated hypothesis about the root cause of a bug.

    A hypothesis is explicitly NOT verified.  It represents what the AI
    suspects based on the available information.  Evidence items may later
    support or refute a hypothesis, but the hypothesis itself remains
    AI-generated until machine execution confirms it.
    """

    __tablename__ = "hypotheses"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    investigation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("investigations.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Short label for this hypothesis
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    # Detailed AI-generated explanation
    explanation: Mapped[str] = mapped_column(Text, nullable=False)

    # AI-reported confidence [0.0, 1.0]
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # This hypothesis was produced by an AI component.
    # Must never be set to False unless it was confirmed by machine execution.
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Whether at least one Evidence item has corroborated this hypothesis
    machine_corroborated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    investigation: Mapped["Investigation"] = relationship(
        "Investigation", back_populates="hypotheses"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "investigation_id": self.investigation_id,
            "title": self.title,
            "explanation": self.explanation,
            "confidence": self.confidence,
            "ai_generated": self.ai_generated,
            "machine_corroborated": self.machine_corroborated,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<Hypothesis id={self.id!r} title={self.title!r} "
            f"ai_generated={self.ai_generated!r}>"
        )
