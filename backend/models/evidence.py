"""
Evidence model.

Evidence represents MACHINE-VERIFIED facts gathered during investigation.

IMPORTANT DISTINCTION
---------------------
Evidence is NOT the same as an AI hypothesis.

- Evidence: the result of actually executing something or reading a file.
  Examples: a command was run and produced this output; a specific line of
  source code was found at this path; a test suite was run and failed.

- Hypothesis: an AI-generated suspicion that has NOT been machine-verified.
  Hypotheses live on the Investigation model, not here.

Never represent an AI-generated claim as Evidence unless it was actually
confirmed by machine execution.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import String, Text, Boolean, DateTime, Integer, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database.connection import db


class EvidenceType(str, PyEnum):
    """Classification of the evidence item."""

    # A specific location in source code
    SOURCE_CODE = "SOURCE_CODE"
    # The result of executing a command (stdout/stderr + exit code)
    COMMAND_OUTPUT = "COMMAND_OUTPUT"
    # The result of running the test suite
    TEST_RESULT = "TEST_RESULT"
    # A git diff / patch produced by the system
    DIFF = "DIFF"
    # Log file content
    LOG_ENTRY = "LOG_ENTRY"
    # Any other verified artefact
    OTHER = "OTHER"


class Evidence(db.Model):
    """
    A single piece of machine-verified evidence linked to an investigation.

    Each evidence item records what was actually observed or executed,
    not what an AI suspects.
    """

    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    investigation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )

    evidence_type: Mapped[str] = mapped_column(
        SAEnum(EvidenceType, name="evidence_type_enum"), nullable=False
    )

    # Human-readable description of what this evidence shows
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # ---------- Source-code evidence ----------
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    line_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    code_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---------- Command-execution evidence ----------
    command_executed: Mapped[str | None] = mapped_column(Text, nullable=True)
    command_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ---------- Test-result evidence ----------
    test_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    test_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    test_output: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Whether this evidence was actually verified by machine execution.
    # Must be False for any AI-generated claim that was not run.
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationship back to investigation
    investigation: Mapped["Investigation"] = relationship(  # noqa: F821
        "Investigation", back_populates="evidence_items"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "investigation_id": self.investigation_id,
            "evidence_type": self.evidence_type,
            "description": self.description,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "code_snippet": self.code_snippet,
            "command_executed": self.command_executed,
            "command_output": self.command_output,
            "exit_code": self.exit_code,
            "test_name": self.test_name,
            "test_passed": self.test_passed,
            "test_output": self.test_output,
            "is_verified": self.is_verified,
            "collected_at": self.collected_at.isoformat() if self.collected_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<Evidence id={self.id!r} type={self.evidence_type!r} "
            f"verified={self.is_verified!r}>"
        )
