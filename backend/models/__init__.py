# backend/models/__init__.py
"""
BugProof data models.

Import all models here to ensure they are registered with SQLAlchemy
before create_all() or migrate commands are invoked.
"""

from .bug import Bug, BugStatus, TargetRepository
from .investigation import Investigation, InvestigationStatus, Hypothesis
from .evidence import Evidence, EvidenceType
from .fix import Fix, ApprovalStatus, ApplicationStatus
from .verification import TestRun, TestRunStatus, TestRunKind, Report

__all__ = [
    "Bug",
    "BugStatus",
    "TargetRepository",
    "Investigation",
    "InvestigationStatus",
    "Hypothesis",
    "Evidence",
    "EvidenceType",
    "Fix",
    "ApprovalStatus",
    "ApplicationStatus",
    "TestRun",
    "TestRunStatus",
    "TestRunKind",
    "Report",
]
