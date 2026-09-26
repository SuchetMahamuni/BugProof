"""
Fix model.

Represents an AI-generated fix proposal and its approval / application state.

WORKFLOW
--------
1. AI generates a proposed patch (ai_generated = True, approval_status = PENDING).
2. Developer reviews the patch.
3. Developer approves or rejects (approval_status = APPROVED / REJECTED).
4. Only after approval may the patch be applied (application_status = APPLIED).

Automatic modification without an approval gate is NOT supported.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import String, Text, Float, Boolean, DateTime, ForeignKey, JSON, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.connection import db


class ApprovalStatus(str, PyEnum):
    """Developer approval state for a fix proposal."""

    PENDING = "PENDING"       # Awaiting developer review
    APPROVED = "APPROVED"     # Developer approved the fix
    REJECTED = "REJECTED"     # Developer rejected the fix


class ApplicationStatus(str, PyEnum):
    """Whether the approved patch has been applied to the repository."""

    NOT_APPLIED = "NOT_APPLIED"
    APPLYING = "APPLYING"
    APPLIED = "APPLIED"
    FAILED = "FAILED"


class Fix(db.Model):
    """
    A proposed fix for a bug investigation.

    Contains the AI-generated patch/diff and tracks the approval and
    application lifecycle.  A fix must be explicitly approved before
    it can be applied.
    """

    __tablename__ = "fixes"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    investigation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )

    # ---- AI-generated content ----
    # This fix was produced by an AI component.  Its correctness is NOT
    # guaranteed until a test run verifies it.
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Human-readable explanation of what the fix does
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The unified diff / patch to be applied
    patch: Mapped[str | None] = mapped_column(Text, nullable=True)

    # List of file paths changed by this fix
    files_changed: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Specific lines or code regions affected by the fix
    # List of {file, line_start, line_end, description} dicts
    affected_lines: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Evidence IDs that directly support this fix proposal.
    # These are IDs of Evidence records (is_verified=True).
    supporting_evidence_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # AI-reported confidence in this fix [0.0, 1.0]
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Risk assessment produced by the AI (e.g., {"level": "low", "notes": "..."})
    risk_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ---- Approval gate ----
    approval_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    approval_status: Mapped[str] = mapped_column(
        SAEnum(ApprovalStatus, name="approval_status_enum"),
        nullable=False,
        default=ApprovalStatus.PENDING,
    )
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- Application state ----
    application_status: Mapped[str] = mapped_column(
        SAEnum(ApplicationStatus, name="application_status_enum"),
        nullable=False,
        default=ApplicationStatus.NOT_APPLIED,
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    application_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    investigation: Mapped["Investigation"] = relationship(  # noqa: F821
        "Investigation", back_populates="fixes"
    )
    test_runs: Mapped[list["TestRun"]] = relationship(  # noqa: F821
        "TestRun", back_populates="fix", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "investigation_id": self.investigation_id,
            "ai_generated": self.ai_generated,
            "explanation": self.explanation,
            "patch": self.patch,
            "files_changed": self.files_changed,
            "affected_lines": self.affected_lines,
            "supporting_evidence_ids": self.supporting_evidence_ids,
            "confidence": self.confidence,
            "risk_info": self.risk_info,
            "approval_required": self.approval_required,
            "approval_status": self.approval_status,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "rejection_reason": self.rejection_reason,
            "application_status": self.application_status,
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
            "application_error": self.application_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<Fix id={self.id!r} approval={self.approval_status!r} "
            f"application={self.application_status!r}>"
        )
