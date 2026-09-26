"""
BugProof backend – basic foundation tests.

These tests verify that:
1. The Flask application can be created.
2. All Python modules import successfully.
3. The database configuration can be initialised without hardcoded credentials.
4. The repository abstraction can be instantiated for all three targets.
5. The core engine interfaces can be instantiated.

No live database, AI service, or network connection is required.
"""

import os
import sys
import pytest

# Ensure the project root is on the path so `backend` is importable.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    """Create a test Flask application using an in-memory SQLite database."""
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
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Test: Flask application creation
# ---------------------------------------------------------------------------

def test_app_creates_successfully(app):
    """The Flask application factory must return a valid app."""
    assert app is not None
    assert app.testing is True


def test_health_endpoint(client):
    """The /health endpoint must return 200 with service name."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert data["service"] == "BugProof"


# ---------------------------------------------------------------------------
# Test: Module imports
# ---------------------------------------------------------------------------

def test_config_imports():
    from backend.config.settings import get_config, DevelopmentConfig
    cfg = get_config()
    assert issubclass(cfg, DevelopmentConfig) or cfg is not None


def test_models_import():
    from backend.models import (
        Bug, BugStatus, TargetRepository,
        Investigation, InvestigationStatus, Hypothesis,
        Evidence, EvidenceType,
        Fix, ApprovalStatus, ApplicationStatus,
        TestRun, TestRunStatus, TestRunKind,
        Report,
    )
    # Verify enums have expected members
    assert BugStatus.RECEIVED == "RECEIVED"
    assert BugStatus.WAITING_FOR_APPROVAL == "WAITING_FOR_APPROVAL"
    assert BugStatus.COMPLETED == "COMPLETED"
    assert TargetRepository.THEFUCK == "thefuck"
    assert TargetRepository.TQDM == "tqdm"
    assert TargetRepository.YOUTUBE_DL == "youtube_dl"


def test_repository_module_imports():
    from backend.repositories import (
        BaseRepository, TheFuckRepository, TqdmRepository,
        YoutubeDlRepository, get_repository,
    )
    assert BaseRepository is not None


def test_execution_module_imports():
    from backend.execution import CommandRunner, GitRunner, TestRunner, LocalSandbox
    assert CommandRunner is not None


def test_core_module_imports():
    from backend.core.investigation import InvestigationPipeline, InvestigationResult
    from backend.core.root_cause import RootCauseAnalyser, RootCauseAnalysis
    from backend.core.fix_generation import FixGenerator, FixProposal
    from backend.core.workflow import WorkflowOrchestrator, DebuggingRun, RunStatus
    assert RunStatus.RECEIVED == "RECEIVED"
    assert RunStatus.WAITING_FOR_APPROVAL == "WAITING_FOR_APPROVAL"
    assert RunStatus.COMPLETED == "COMPLETED"


def test_ai_module_imports():
    from backend.ai import AIInvestigator, AIRootCause, AIFixer
    assert AIInvestigator is not None


# ---------------------------------------------------------------------------
# Test: Repository abstraction instantiation
# ---------------------------------------------------------------------------

WORKSPACE = "/tmp/bugproof_test_workspace"


def test_thefuck_repository_instantiation():
    from backend.repositories import get_repository
    from backend.repositories.base import RepositoryInfo

    repo = get_repository("thefuck", WORKSPACE)
    assert repo is not None
    assert isinstance(repo.info, RepositoryInfo)
    assert repo.info.name == "thefuck"
    assert repo.info.url == "https://github.com/nvbn/thefuck.git"
    assert not repo.is_cloned()  # workspace doesn't exist in test env


def test_tqdm_repository_instantiation():
    from backend.repositories import get_repository

    repo = get_repository("tqdm", WORKSPACE)
    assert repo.info.name == "tqdm"
    assert "tqdm" in repo.info.url


def test_youtube_dl_repository_instantiation():
    from backend.repositories import get_repository

    repo = get_repository("youtube_dl", WORKSPACE)
    assert repo.info.name == "youtube_dl"
    assert "youtube-dl" in repo.info.url or "ytdl" in repo.info.url


def test_invalid_repository_raises():
    from backend.repositories import get_repository

    with pytest.raises(ValueError, match="Unsupported repository"):
        get_repository("nonexistent", WORKSPACE)


def test_all_repositories_share_interface():
    """All three adapters must satisfy the BaseRepository abstract interface."""
    from backend.repositories import (
        TheFuckRepository, TqdmRepository, YoutubeDlRepository, BaseRepository,
    )

    for cls in (TheFuckRepository, TqdmRepository, YoutubeDlRepository):
        repo = cls(WORKSPACE)
        assert isinstance(repo, BaseRepository)
        spec = repo.get_test_spec()
        assert isinstance(spec.command, list)
        assert len(spec.command) > 0
        assert isinstance(spec.timeout_seconds, int)


# ---------------------------------------------------------------------------
# Test: Database models can be created in-memory
# ---------------------------------------------------------------------------

def test_bug_model_crud(app):
    """Bug records can be created and retrieved using the ORM."""
    from backend.database.connection import db
    from backend.models import Bug, BugStatus, TargetRepository

    with app.app_context():
        bug = Bug(
            title="Test bug",
            description="A test bug for foundation validation.",
            target_repository=TargetRepository.THEFUCK,
            error_message="AttributeError: 'NoneType' object has no attribute 'group'",
            status=BugStatus.RECEIVED,
        )
        db.session.add(bug)
        db.session.commit()

        retrieved = db.session.get(Bug, bug.id)
        assert retrieved is not None
        assert retrieved.title == "Test bug"
        assert retrieved.status == BugStatus.RECEIVED
        assert retrieved.target_repository == TargetRepository.THEFUCK


def test_investigation_model_crud(app):
    """Investigation records can be linked to Bug records."""
    from backend.database.connection import db
    from backend.models import Bug, BugStatus, TargetRepository, Investigation, InvestigationStatus

    with app.app_context():
        bug = Bug(
            title="Bug for investigation test",
            description="Testing investigation model.",
            target_repository=TargetRepository.TQDM,
            status=BugStatus.INVESTIGATING,
        )
        db.session.add(bug)
        db.session.commit()

        inv = Investigation(
            bug_id=bug.id,
            status=InvestigationStatus.PENDING,
            root_cause_ai_generated=True,
            root_cause_machine_supported=False,
        )
        db.session.add(inv)
        db.session.commit()

        retrieved = db.session.get(Investigation, inv.id)
        assert retrieved is not None
        assert retrieved.bug_id == bug.id
        # AI/machine distinction preserved
        assert retrieved.root_cause_ai_generated is True
        assert retrieved.root_cause_machine_supported is False


def test_evidence_model_crud(app):
    """Evidence records correctly carry the is_verified flag."""
    from backend.database.connection import db
    from backend.models import (
        Bug, TargetRepository, BugStatus,
        Investigation, InvestigationStatus,
        Evidence, EvidenceType,
    )

    with app.app_context():
        bug = Bug(
            title="Evidence test bug",
            description="Testing evidence model.",
            target_repository=TargetRepository.YOUTUBE_DL,
            status=BugStatus.INVESTIGATING,
        )
        db.session.add(bug)
        db.session.commit()

        inv = Investigation(
            bug_id=bug.id,
            status=InvestigationStatus.IN_PROGRESS,
        )
        db.session.add(inv)
        db.session.commit()

        # Machine-verified evidence
        ev_verified = Evidence(
            investigation_id=inv.id,
            evidence_type=EvidenceType.SOURCE_CODE,
            description="Found relevant line in source.",
            file_path="youtube_dl/extractor/youtube.py",
            line_number=42,
            is_verified=True,
        )
        # AI-generated (unverified) claim
        ev_unverified = Evidence(
            investigation_id=inv.id,
            evidence_type=EvidenceType.OTHER,
            description="AI suspects this function is involved.",
            is_verified=False,
        )
        db.session.add_all([ev_verified, ev_unverified])
        db.session.commit()

        assert ev_verified.is_verified is True
        assert ev_unverified.is_verified is False


# ---------------------------------------------------------------------------
# Test: Fix approval gate
# ---------------------------------------------------------------------------

def test_fix_approval_gate(app):
    """A fix must start as PENDING and require explicit approval."""
    from backend.database.connection import db
    from backend.models import (
        Bug, TargetRepository, BugStatus,
        Investigation, InvestigationStatus,
        Fix, ApprovalStatus, ApplicationStatus,
    )

    with app.app_context():
        bug = Bug(
            title="Fix gate test",
            description="Testing fix approval gate.",
            target_repository=TargetRepository.THEFUCK,
            status=BugStatus.FIX_GENERATED,
        )
        db.session.add(bug)
        db.session.commit()

        inv = Investigation(
            bug_id=bug.id,
            status=InvestigationStatus.COMPLETED,
        )
        db.session.add(inv)
        db.session.commit()

        fix = Fix(
            investigation_id=inv.id,
            ai_generated=True,
            explanation="Replace None check with early return.",
            patch="--- a/thefuck/rules/foo.py\n+++ b/thefuck/rules/foo.py\n",
            approval_required=True,
            approval_status=ApprovalStatus.PENDING,
            application_status=ApplicationStatus.NOT_APPLIED,
        )
        db.session.add(fix)
        db.session.commit()

        assert fix.approval_status == ApprovalStatus.PENDING
        assert fix.application_status == ApplicationStatus.NOT_APPLIED
        assert fix.ai_generated is True
        assert fix.approval_required is True


# ---------------------------------------------------------------------------
# Test: API endpoints return correct status codes
# ---------------------------------------------------------------------------

def test_bugs_list_empty(client):
    response = client.get("/api/bugs")
    assert response.status_code == 200
    assert response.get_json() == []


def test_bugs_create_missing_fields(client):
    response = client.post("/api/bugs", json={"title": "Only title"})
    assert response.status_code == 400


def test_bugs_create_invalid_repo(client):
    response = client.post("/api/bugs", json={
        "title": "Test",
        "description": "desc",
        "target_repository": "invalid_repo",
    })
    assert response.status_code == 400


def test_bugs_create_and_retrieve(client):
    response = client.post("/api/bugs", json={
        "title": "Real bug",
        "description": "A real bug.",
        "target_repository": "thefuck",
        "error_message": "AttributeError",
    })
    assert response.status_code == 201
    data = response.get_json()
    bug_id = data["id"]

    get_response = client.get(f"/api/bugs/{bug_id}")
    assert get_response.status_code == 200
    assert get_response.get_json()["id"] == bug_id


def test_bug_not_found(client):
    response = client.get("/api/bugs/nonexistent-id")
    assert response.status_code == 404


def test_investigation_not_found(client):
    response = client.get("/api/investigations/nonexistent-id")
    assert response.status_code == 404


def test_fix_not_found(client):
    response = client.get("/api/fixes/nonexistent-id")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Test: AI stubs return correct structure when not configured
# ---------------------------------------------------------------------------

def test_ai_investigator_stub():
    os.environ.pop("WATSONX_URL", None)
    os.environ.pop("WATSONX_PROJECT_ID", None)

    from backend.ai import AIInvestigator
    investigator = AIInvestigator()
    assert not investigator.is_available

    result = investigator.analyse(
        error_message="AttributeError",
        description="test bug",
        relevant_files=[],
        relevant_functions=[],
        evidence=[],
    )
    assert "AI NOT CONFIGURED" in result["suspected_cause"]
    assert result["confidence"] == 0.0


def test_workflow_run_status_values():
    from backend.core.workflow import RunStatus
    expected = {
        "RECEIVED", "INVESTIGATING", "ROOT_CAUSE_FOUND", "FIX_GENERATED",
        "WAITING_FOR_APPROVAL", "FIX_APPLIED", "VERIFYING",
        "COMPLETED", "FAILED", "CANCELLED",
    }
    actual = {s.value for s in RunStatus}
    assert expected == actual


def test_debugging_run_transition():
    from backend.core.workflow import DebuggingRun, RunStatus
    run = DebuggingRun(bug_id="test-bug-id")
    assert run.status == RunStatus.RECEIVED
    run.transition(RunStatus.INVESTIGATING, step="investigation")
    assert run.status == RunStatus.INVESTIGATING
    assert run.current_step == "investigation"
    run.fail("Something went wrong")
    assert run.status == RunStatus.FAILED
    assert run.error_message == "Something went wrong"
