"""
Bug model.

Represents a bug report submitted to BugProof for debugging.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import String, Text, DateTime, Enum as SAEnum, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.connection import db


class BugStatus(str, PyEnum):
    """Lifecycle status of a bug record."""

    RECEIVED = "RECEIVED"
    INVESTIGATING = "INVESTIGATING"
    ROOT_CAUSE_FOUND = "ROOT_CAUSE_FOUND"
    FIX_GENERATED = "FIX_GENERATED"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    FIX_APPLIED = "FIX_APPLIED"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TargetRepository(str, PyEnum):
    """Supported target repositories for BugProof validation."""

    THEFUCK = "thefuck"
    TQDM = "tqdm"
    YOUTUBE_DL = "youtube_dl"


class Bug(db.Model):
    """
    A bug report submitted to BugProof.

    Stores the original report data and tracks the overall status
    of the debugging run.  The source repository itself is NOT stored
    in the database – only metadata is persisted here.
    """

    __tablename__ = "bugs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Which of the three supported repositories this bug belongs to
    target_repository: Mapped[str] = mapped_column(
        SAEnum(TargetRepository, name="target_repository_enum"),
        nullable=False,
    )

    # Optional: commit hash or version tag at which the bug is reproducible
    repository_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Original error message / traceback as reported
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Free-form extra context (steps to reproduce, environment info, etc.)
    extra_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    status: Mapped[str] = mapped_column(
        SAEnum(BugStatus, name="bug_status_enum"),
        nullable=False,
        default=BugStatus.RECEIVED,
    )

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
    investigations: Mapped[list["Investigation"]] = relationship(  # noqa: F821
        "Investigation", back_populates="bug", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        """Return a serialisable representation of this bug."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "target_repository": self.target_repository,
            "repository_ref": self.repository_ref,
            "error_message": self.error_message,
            "extra_context": self.extra_context,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<Bug id={self.id!r} title={self.title!r} status={self.status!r}>"
