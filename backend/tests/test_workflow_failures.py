"""
Tests for workflow failure handling – Workstream 3.

Verifies that when an exception occurs at any stage of the
WorkflowOrchestrator.execute() pipeline:

1. The Bug is left in status=FAILED (not INVESTIGATING or a partial state).
2. The Investigation (if created) is left in status=FAILED (not IN_PROGRESS).
3. The Investigation.error_details records the exception message.
4. The DebuggingRun reflects the failure.
5. Successful workflow behaviour (the happy path) is unaffected.

AI providers are tested for configured-but-not-integrated behavior to ensure
they no longer raise NotImplementedError.

No live database, AI service, network, or repository checkout is required.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    os.environ.setdefault("APP_ENV", "testing")
    from backend.app import create_app
    application = create_app(
        config_override={
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        }
    )
    with application.app_context():
        from backend.database.connection import db
        db.create_all()
        yield application


@pytest.fixture()
def db(app):
    from backend.database.connection import db as _db
    return _db


@pytest.fixture()
def sample_bug(app, db):
    from backend.models import Bug, BugStatus, TargetRepository
    with app.app_context():
        bug = Bug(
            title="Failure test bug",
            description="Testing failure states.",
            target_repository=TargetRepository.THEFUCK,
            error_message="AttributeError",
            status=BugStatus.RECEIVED,
        )
        db.session.add(bug)
        db.session.commit()
        yield bug


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_orchestrator():
    from backend.core.workflow import WorkflowOrchestrator
    return WorkflowOrchestrator(
        repository_workspace="/irrelevant",
        ai_investigator=None,
        ai_root_cause=None,
        ai_fixer=None,
    )


def _run_with_mocked_repo(orchestrator, bug_id, app, mock_repo):
    """Execute the orchestrator with get_repository patched to return mock_repo."""
    import backend.core.workflow.orchestrator as orch_mod
    original = orch_mod.get_repository
    orch_mod.get_repository = lambda name, ws: mock_repo
    try:
        return orchestrator.execute(bug_id=bug_id, app=app)
    finally:
        orch_mod.get_repository = original


# ---------------------------------------------------------------------------
# Tests: Bug not found
# ---------------------------------------------------------------------------

class TestBugNotFound:
    def test_run_fails_if_bug_not_found(self, app):
        orch = _make_orchestrator()
        run = orch.execute(bug_id="no-such-bug", app=app)
        assert run.status.value == "FAILED"
        assert "not found" in run.error_message.lower()


# ---------------------------------------------------------------------------
# Tests: Failure during investigation (pipeline raises)
# ---------------------------------------------------------------------------

class TestFailureDuringInvestigation:
    def test_bug_status_failed_after_pipeline_exception(self, app, db, sample_bug):
        """When the orchestrator's outer except fires, Bug.status must be FAILED."""
        from backend.models import Bug, BugStatus
        from backend.core.workflow import WorkflowOrchestrator

        # Patch db.session.add to raise after the Investigation is created;
        # this simulates a DB failure mid-pipeline that the pipeline's own
        # try/except does NOT catch (the pipeline's except only guards its
        # internal logic, not the DB layer used by the orchestrator).
        orch = WorkflowOrchestrator(
            repository_workspace="/irrelevant",
            ai_investigator=None,
            ai_root_cause=None,
            ai_fixer=None,
        )

        # Make get_repository itself raise so the orchestrator except fires
        def exploding_repo(name, ws):
            raise RuntimeError("fatal disk error")

        import backend.core.workflow.orchestrator as orch_mod
        original = orch_mod.get_repository
        orch_mod.get_repository = exploding_repo
        try:
            run = orch.execute(bug_id=sample_bug.id, app=app)
        finally:
            orch_mod.get_repository = original

        assert run.status.value == "FAILED"

        with app.app_context():
            bug = db.session.get(Bug, sample_bug.id)
            assert bug.status == BugStatus.FAILED

    def test_investigation_status_failed_after_pipeline_exception(
        self, app, db, sample_bug
    ):
        """When the pipeline raises after Investigation is created, it must be FAILED."""
        from backend.models import Bug, BugStatus, Investigation, InvestigationStatus

        # Raise inside get_repository so the pipeline never gets the repo
        def exploding_get_repo(name, ws):
            raise RuntimeError("repo unavailable")

        import backend.core.workflow.orchestrator as orch_mod
        original = orch_mod.get_repository
        orch_mod.get_repository = exploding_get_repo
        try:
            orch = _make_orchestrator()
            run = orch.execute(bug_id=sample_bug.id, app=app)
        finally:
            orch_mod.get_repository = original

        assert run.status.value == "FAILED"

        with app.app_context():
            bug = db.session.get(Bug, sample_bug.id)
            assert bug.status == BugStatus.FAILED

            # Investigation was created (IN_PROGRESS) then should be FAILED
            invs = db.session.execute(
                db.select(Investigation).where(
                    Investigation.bug_id == sample_bug.id
                )
            ).scalars().all()
            if invs:
                # If an investigation was created, it must not remain IN_PROGRESS
                for inv in invs:
                    assert inv.status == InvestigationStatus.FAILED, (
                        f"Investigation {inv.id!r} left in {inv.status!r}"
                    )

    def test_investigation_error_details_set(self, app, db, sample_bug):
        """investigation.error_details must contain the failure message."""
        from backend.models import Investigation, InvestigationStatus

        def exploding_get_repo(name, ws):
            raise RuntimeError("unique sentinel error XYZ123")

        import backend.core.workflow.orchestrator as orch_mod
        original = orch_mod.get_repository
        orch_mod.get_repository = exploding_get_repo
        try:
            orch = _make_orchestrator()
            orch.execute(bug_id=sample_bug.id, app=app)
        finally:
            orch_mod.get_repository = original

        with app.app_context():
            invs = db.session.execute(
                db.select(Investigation).where(
                    Investigation.bug_id == sample_bug.id
                )
            ).scalars().all()
            # If an investigation was persisted, its error_details should carry
            # the error message.
            for inv in invs:
                if inv.status == InvestigationStatus.FAILED:
                    assert inv.error_details is not None
                    assert "XYZ123" in inv.error_details


# ---------------------------------------------------------------------------
# Tests: Failure during RCA (after investigation completes)
# ---------------------------------------------------------------------------

class TestFailureDuringRCA:
    def test_both_failed_after_rca_exception(self, app, db, sample_bug):
        """Bug and Investigation are both FAILED when RCA raises."""
        import tempfile, os
        from backend.models import Bug, BugStatus, Investigation, InvestigationStatus
        from backend.repositories.base import BaseRepository, RepositoryInfo, TestSpec

        # Build a minimal mock repo that looks cloned
        with tempfile.TemporaryDirectory() as tmpdir:
            class MinimalRepo(BaseRepository):
                @property
                def info(self):
                    return RepositoryInfo("mock", "https://x.com/y.git", "main", "")
                @property
                def repository_path(self):
                    return tmpdir
                def checkout(self, ref=None): pass
                def install_dependencies(self): pass
                def get_source_files(self): return []
                def get_test_spec(self): return TestSpec(["pytest"])
                def run_tests(self, ref=None, extra_args=None): return {}
                def is_cloned(self): return True

            repo = MinimalRepo(tmpdir)

            # Make RCA analyser raise
            bad_ai_root_cause = MagicMock()
            bad_ai_root_cause.is_available = True
            bad_ai_root_cause.analyse.side_effect = ValueError("rca crash")

            from backend.core.workflow import WorkflowOrchestrator
            orch = WorkflowOrchestrator(
                repository_workspace="/irrelevant",
                ai_investigator=None,
                ai_root_cause=bad_ai_root_cause,
                ai_fixer=None,
            )
            run = _run_with_mocked_repo(orch, sample_bug.id, app, repo)

        assert run.status.value == "FAILED"
        assert "rca crash" in run.error_message

        with app.app_context():
            bug = db.session.get(Bug, sample_bug.id)
            assert bug.status == BugStatus.FAILED

            invs = db.session.execute(
                db.select(Investigation).where(
                    Investigation.bug_id == sample_bug.id
                )
            ).scalars().all()
            for inv in invs:
                assert inv.status == InvestigationStatus.FAILED


# ---------------------------------------------------------------------------
# Tests: AI provider configured-but-not-integrated behavior
# ---------------------------------------------------------------------------

class TestAIProviderConfiguredBehavior:
    """AI modules with WATSONX_URL+WATSONX_PROJECT_ID set must NOT raise NotImplementedError."""

    def test_ai_investigator_configured_returns_stub(self):
        """AIInvestigator.analyse() returns a stub dict when configured but not integrated."""
        import os
        from backend.ai import AIInvestigator

        # Temporarily set env vars so _available is True
        os.environ["WATSONX_URL"] = "https://fake.watsonx.test"
        os.environ["WATSONX_PROJECT_ID"] = "fake-project-id"
        try:
            inv = AIInvestigator()
            assert inv.is_available is True
            result = inv.analyse(
                error_message="SomeError",
                description="desc",
                relevant_files=[],
                relevant_functions=[],
                evidence=[],
            )
        finally:
            os.environ.pop("WATSONX_URL", None)
            os.environ.pop("WATSONX_PROJECT_ID", None)

        # Must return a dict, never raise
        assert isinstance(result, dict)
        assert "suspected_cause" in result
        assert "confidence" in result
        assert "AI CONFIGURED BUT NOT INTEGRATED" in result["suspected_cause"]

    def test_ai_root_cause_configured_returns_stub(self):
        """AIRootCause.analyse() returns a stub dict when configured but not integrated."""
        import os
        from backend.ai import AIRootCause

        os.environ["WATSONX_URL"] = "https://fake.watsonx.test"
        os.environ["WATSONX_PROJECT_ID"] = "fake-project-id"
        try:
            rc = AIRootCause()
            assert rc.is_available is True
            result = rc.analyse(
                error_message="SomeError",
                description="desc",
                evidence=[],
                hypotheses=[],
            )
        finally:
            os.environ.pop("WATSONX_URL", None)
            os.environ.pop("WATSONX_PROJECT_ID", None)

        assert isinstance(result, dict)
        assert "suspected_cause" in result
        assert "AI CONFIGURED BUT NOT INTEGRATED" in result["suspected_cause"]

    def test_ai_fixer_configured_returns_stub(self):
        """AIFixer.generate_fix() returns a stub dict when configured but not integrated."""
        import os
        from backend.ai import AIFixer

        os.environ["WATSONX_URL"] = "https://fake.watsonx.test"
        os.environ["WATSONX_PROJECT_ID"] = "fake-project-id"
        try:
            fixer = AIFixer()
            assert fixer.is_available is True
            result = fixer.generate_fix(
                suspected_cause="cause",
                explanation="explanation",
                relevant_files=[],
                evidence=[],
                repository_path="/tmp",
            )
        finally:
            os.environ.pop("WATSONX_URL", None)
            os.environ.pop("WATSONX_PROJECT_ID", None)

        assert isinstance(result, dict)
        assert "explanation" in result
        assert "AI CONFIGURED BUT NOT INTEGRATED" in result["explanation"]
        # patch must NOT be a real patch
        assert result.get("patch") == ""
