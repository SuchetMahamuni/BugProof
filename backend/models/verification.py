"""
Verification models – TestRun and Report.

TestRun: metadata for a single execution of a test suite.
Report:  a structured summary of the overall debugging session evidence.

These models are the integration point for Team Member 2's verification work
and Team Member 3's reporting/UI work.  The data contracts are defined here;
the implementation logic belongs to the respective team members.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import String, Text, Integer, Boolean, DateTime, ForeignKey, JSON, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.connection import db


class TestRunStatus(str, PyEnum):
    """Execution status of a test run."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"


class TestRunKind(str, PyEnum):
    """Classification of what a test run is validating."""

    # Running the repository's existing test suite to confirm the bug
    BUG_REPRODUCTION = "BUG_REPRODUCTION"
    # Running the repository's existing test suite after a fix is applied
    REGRESSION = "REGRESSION"
    # Running a newly generated regression test
    GENERATED_REGRESSION = "GENERATED_REGRESSION"
    # Ad-hoc or exploratory run
    EXPLORATORY = "EXPLORATORY"


class TestRun(db.Model):
    """
    Metadata for a single test-suite execution.

    Records the command used, its output, exit code, and result status.
    This is machine-generated, verified data – not AI speculation.

    Note: the actual test-generation and verification logic is owned by
    Team Member 2.  This model defines the shared data contract.
    """

    __tablename__ = "test_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # A test run may be linked to a Fix (optional – exploratory runs may not be)
    fix_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("fixes.id", ondelete="SET NULL"), nullable=True
    )

    # Also linkable directly to a Bug for pre-fix reproduction runs
    bug_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("bugs.id", ondelete="SET NULL"), nullable=True
    )

    kind: Mapped[str] = mapped_column(
        SAEnum(TestRunKind, name="test_run_kind_enum"),
        nullable=False,
        default=TestRunKind.REGRESSION,
    )

    status: Mapped[str] = mapped_column(
        SAEnum(TestRunStatus, name="test_run_status_enum"),
        nullable=False,
        default=TestRunStatus.PENDING,
    )

    # The exact command that was executed
    command: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Captured stdout + stderr
    output: Mapped[str | None] = mapped_column(Text, nullable=True)

    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Summary counts (populated after the run completes)
    tests_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tests_passed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tests_failed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tests_errored: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Additional structured result data (e.g., pytest JSON report)
    result_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

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
    fix: Mapped["Fix | None"] = relationship("Fix", back_populates="test_runs")  # noqa: F821

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "fix_id": self.fix_id,
            "bug_id": self.bug_id,
            "kind": self.kind,
            "status": self.status,
            "command": self.command,
            "output": self.output,
            "exit_code": self.exit_code,
            "tests_total": self.tests_total,
            "tests_passed": self.tests_passed,
            "tests_failed": self.tests_failed,
            "tests_errored": self.tests_errored,
            "result_data": self.result_data,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<TestRun id={self.id!r} kind={self.kind!r} status={self.status!r}>"


class Report(db.Model):
    """
    A structured debugging-session report for a Bug.

    Aggregates the investigation findings, fix details, and test run results
    into a single evidence-backed summary.

    The content generation logic is owned by Team Member 3.  This model
    defines the shared data contract.
    """

    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    bug_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("bugs.id", ondelete="CASCADE"), nullable=False
    )

    # High-level fix-confidence score [0.0, 1.0] synthesised from evidence + test results
    fix_confidence: Mapped[float | None] = mapped_column(
        # Note: this is a calculated value – see AI boundary note below
        type_=__import__("sqlalchemy").Float,
        nullable=True,
    )

    # Whether the fix_confidence value was calculated from machine-verified evidence
    confidence_machine_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # Structured report content (JSON blob for flexible rendering by the frontend)
    content: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Summary paragraph (may be AI-assisted; marked accordingly in content)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "bug_id": self.bug_id,
            "fix_confidence": self.fix_confidence,
            "confidence_machine_verified": self.confidence_machine_verified,
            "content": self.content,
            "summary": self.summary,
            "generated_at": (
                self.generated_at.isoformat() if self.generated_at else None
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<Report id={self.id!r} bug_id={self.bug_id!r}>"
