"""
BugProof – Member 1 milestone tests.

Tests for the complete Smart Bug Investigation → Evidence-Based RCA →
Safe Fix Proposal flow.

These tests:
1. Use an in-memory SQLite database (no PostgreSQL required).
2. Use a mock repository adapter (no git checkout required).
3. Use a mock AI component (no watsonx connection required).
4. Verify the complete flow from Bug creation through to Fix Proposal.
5. Verify that a fix is NOT automatically applied.
6. Cover evidence collection, RCA evidence-id references, and error cases.

No live database, AI service, network, or repository checkout is required.
"""

import os
import sys
import tempfile
import threading
import time
import uuid
from typing import Optional
from unittest.mock import MagicMock, patch

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


@pytest.fixture()
def sample_source_tree():
    """
    Create a temporary directory with minimal Python source files
    that simulate a real repository for investigation tests.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake source structure
        src_dir = os.path.join(tmpdir, "myproject")
        os.makedirs(src_dir, exist_ok=True)

        # Source file that contains the error keyword
        with open(os.path.join(src_dir, "parser.py"), "w") as f:
            f.write(
                "class Parser:\n"
                "    def parse(self, text):\n"
                "        result = self.tokenize(text)\n"
                "        if result is None:\n"
                "            raise ValueError('tokenize returned None')\n"
                "        return result\n"
                "\n"
                "    def tokenize(self, text):\n"
                "        # Bug: returns None when text is empty\n"
                "        if not text:\n"
                "            return None  # should return []\n"
                "        return text.split()\n"
            )

        # Source file that does NOT contain the keyword
        with open(os.path.join(src_dir, "utils.py"), "w") as f:
            f.write("def helper():\n    pass\n")

        # Test file containing the failing test
        tests_dir = os.path.join(tmpdir, "tests")
        os.makedirs(tests_dir, exist_ok=True)
        with open(os.path.join(tests_dir, "test_parser.py"), "w") as f:
            f.write(
                "from myproject.parser import Parser\n"
                "\n"
                "def test_tokenize_empty():\n"
                "    p = Parser()\n"
                "    result = p.parse('')\n"
                "    assert result == []\n"
            )

        yield tmpdir


@pytest.fixture()
def mock_repository(sample_source_tree):
    """
    A mock repository adapter backed by sample_source_tree.
    Implements just enough of BaseRepository to drive the pipeline.
    """
    from backend.repositories.base import BaseRepository, RepositoryInfo, TestSpec

    class MockRepository(BaseRepository):
        _INFO = RepositoryInfo(
            name="mock_repo",
            url="https://github.com/mock/repo.git",
            default_branch="main",
            description="Mock repository for testing.",
        )

        def __init__(self, workspace_path):
            super().__init__(workspace_path)
            # Override repository_path to point directly to the tmp tree
            self._custom_path = sample_source_tree

        @property
        def repository_path(self):
            return self._custom_path

        @property
        def info(self):
            return self._INFO

        def checkout(self, ref=None):
            pass  # no-op for tests

        def install_dependencies(self):
            pass

        def get_source_files(self):
            result = []
            for dirpath, _dirs, filenames in os.walk(self._custom_path):
                for fname in filenames:
                    if fname.endswith(".py"):
                        abs_path = os.path.join(dirpath, fname)
                        result.append(
                            os.path.relpath(abs_path, self._custom_path)
                        )
            return result

        def get_test_spec(self):
            return TestSpec(
                command=["pytest", "tests/"],
                working_dir="",
                timeout_seconds=30,
            )

        def is_cloned(self):
            return True  # always "cloned" for tests

        def run_tests(self, ref=None, extra_args=None):
            return {
                "command": "pytest tests/",
                "exit_code": 1,
                "output": "FAILED tests/test_parser.py::test_tokenize_empty",
                "tests_total": 1,
                "tests_passed": 0,
                "tests_failed": 1,
                "tests_errored": 0,
            }

    return MockRepository(sample_source_tree)


# ---------------------------------------------------------------------------
# Test: Bug creation via API
# ---------------------------------------------------------------------------

class TestBugCreation:
    def test_create_bug_minimal(self, client):
        """A bug can be created with just the required fields."""
        response = client.post("/api/bugs", json={
            "title": "Parser returns None for empty input",
            "description": "When tokenize receives an empty string it returns None instead of [].",
            "target_repository": "thefuck",
        })
        assert response.status_code == 201
        data = response.get_json()
        assert "id" in data
        assert data["status"] == "RECEIVED"
        assert data["title"] == "Parser returns None for empty input"

    def test_create_bug_with_error_message(self, client):
        """A bug can be created with an error message."""
        response = client.post("/api/bugs", json={
            "title": "ValueError on empty tokenize",
            "description": "tokenize raises ValueError",
            "target_repository": "tqdm",
            "error_message": "ValueError: tokenize returned None",
        })
        assert response.status_code == 201
        data = response.get_json()
        assert data["error_message"] == "ValueError: tokenize returned None"
        assert data["status"] == "RECEIVED"

    def test_create_bug_with_failing_test(self, client):
        """A bug can be created with failing_test info in extra_context."""
        response = client.post("/api/bugs", json={
            "title": "Failing test: test_tokenize_empty",
            "description": "test_tokenize_empty fails when input is empty string.",
            "target_repository": "thefuck",
            "extra_context": {
                "failing_test": {
                    "file": "tests/test_parser.py",
                    "function": "test_tokenize_empty",
                    "module": "tests.test_parser",
                }
            },
        })
        assert response.status_code == 201
        data = response.get_json()
        assert data["extra_context"]["failing_test"]["function"] == "test_tokenize_empty"

    def test_create_bug_missing_required_fields(self, client):
        """Missing required fields returns 400."""
        response = client.post("/api/bugs", json={"title": "Only title"})
        assert response.status_code == 400
        assert "error" in response.get_json()

    def test_create_bug_invalid_repository(self, client):
        """Invalid target_repository returns 400."""
        response = client.post("/api/bugs", json={
            "title": "Test",
            "description": "desc",
            "target_repository": "not_a_real_repo",
        })
        assert response.status_code == 400

    def test_create_bug_retrieval(self, client):
        """Created bug can be retrieved by ID."""
        create_resp = client.post("/api/bugs", json={
            "title": "Retrieve test",
            "description": "Testing retrieval.",
            "target_repository": "youtube_dl",
            "error_message": "AttributeError: NoneType",
        })
        assert create_resp.status_code == 201
        bug_id = create_resp.get_json()["id"]

        get_resp = client.get(f"/api/bugs/{bug_id}")
        assert get_resp.status_code == 200
        assert get_resp.get_json()["id"] == bug_id
        assert get_resp.get_json()["error_message"] == "AttributeError: NoneType"

    def test_get_bug_not_found(self, client):
        """Non-existent bug returns 404."""
        response = client.get("/api/bugs/does-not-exist")
        assert response.status_code == 404

    def test_list_bugs_empty(self, client):
        """Empty bug list returns 200 with empty array."""
        response = client.get("/api/bugs")
        assert response.status_code == 200
        assert response.get_json() == []


# ---------------------------------------------------------------------------
# Test: start endpoint
# ---------------------------------------------------------------------------

class TestStartInvestigation:
    def test_start_returns_202(self, client):
        """POST /api/bugs/<id>/start returns 202."""
        create_resp = client.post("/api/bugs", json={
            "title": "Start test bug",
            "description": "A bug to start.",
            "target_repository": "thefuck",
        })
        bug_id = create_resp.get_json()["id"]

        # Patch _run_workflow_in_thread so we don't actually run the pipeline
        with patch("backend.api.bugs._run_workflow_in_thread"):
            start_resp = client.post(f"/api/bugs/{bug_id}/start")

        assert start_resp.status_code == 202
        data = start_resp.get_json()
        assert data["bug_id"] == bug_id
        assert "poll_urls" in data

    def test_start_bug_not_found(self, client):
        """Starting a non-existent bug returns 404."""
        with patch("backend.api.bugs._run_workflow_in_thread"):
            response = client.post("/api/bugs/nonexistent/start")
        assert response.status_code == 404

    def test_start_already_investigating(self, client):
        """Starting a bug that is already INVESTIGATING returns 409."""
        from backend.database.connection import db
        from backend.models import Bug, BugStatus, TargetRepository

        create_resp = client.post("/api/bugs", json={
            "title": "Already investigating",
            "description": "Already running.",
            "target_repository": "tqdm",
        })
        bug_id = create_resp.get_json()["id"]

        with client.application.app_context():
            bug = db.session.get(Bug, bug_id)
            bug.status = BugStatus.INVESTIGATING
            db.session.commit()

        with patch("backend.api.bugs._run_workflow_in_thread"):
            response = client.post(f"/api/bugs/{bug_id}/start")
        assert response.status_code == 409

    def test_start_loads_bug_from_db(self, client):
        """
        The investigation uses the bug description already stored in DB,
        not a new submission.
        """
        # Create bug with specific description and error_message
        create_resp = client.post("/api/bugs", json={
            "title": "Description-only bug",
            "description": "The parse function fails when input is empty.",
            "target_repository": "thefuck",
        })
        bug_id = create_resp.get_json()["id"]

        calls = []

        def capture_call(app, bid):
            calls.append(bid)

        with patch("backend.api.bugs._run_workflow_in_thread", side_effect=capture_call):
            start_resp = client.post(f"/api/bugs/{bug_id}/start")

        assert start_resp.status_code == 202
        # The captured bug_id must be the one we created
        assert bug_id in calls


# ---------------------------------------------------------------------------
# Test: InvestigationPipeline (unit)
# ---------------------------------------------------------------------------

class TestInvestigationPipeline:
    def test_pipeline_runs_without_error_message(self, mock_repository):
        """Pipeline runs using description alone (no error_message)."""
        from backend.core.investigation.pipeline import InvestigationPipeline

        pipeline = InvestigationPipeline(mock_repository)
        result = pipeline.run(
            investigation_id=str(uuid.uuid4()),
            bug_id=str(uuid.uuid4()),
            description="The tokenize function returns None for empty input.",
        )
        assert result.error is None
        assert result.completed_at is not None

    def test_pipeline_finds_relevant_files(self, mock_repository):
        """Pipeline identifies source files containing the bug keywords."""
        from backend.core.investigation.pipeline import InvestigationPipeline

        pipeline = InvestigationPipeline(mock_repository)
        result = pipeline.run(
            investigation_id=str(uuid.uuid4()),
            bug_id=str(uuid.uuid4()),
            description="Parser tokenize returns None on empty string.",
            error_message="ValueError: tokenize returned None",
        )
        # parser.py contains 'tokenize' and 'None'
        assert any("parser.py" in f for f in result.relevant_files)

    def test_pipeline_collects_verified_evidence(self, mock_repository):
        """Pipeline collects SOURCE_CODE evidence with is_verified=True."""
        from backend.core.investigation.pipeline import InvestigationPipeline

        pipeline = InvestigationPipeline(mock_repository)
        result = pipeline.run(
            investigation_id=str(uuid.uuid4()),
            bug_id=str(uuid.uuid4()),
            description="Parser tokenize returns None.",
            error_message="ValueError: tokenize returned None",
        )
        verified = [e for e in result.evidence if e.is_verified]
        assert len(verified) > 0

        source_ev = [e for e in verified if e.evidence_type == "SOURCE_CODE"]
        assert len(source_ev) > 0
        for ev in source_ev:
            assert ev.file_path is not None
            assert ev.line_number is not None
            assert ev.code_snippet is not None

    def test_pipeline_evidence_has_search_query(self, mock_repository):
        """Evidence items produced by the pipeline carry a search_query."""
        from backend.core.investigation.pipeline import InvestigationPipeline

        pipeline = InvestigationPipeline(mock_repository)
        result = pipeline.run(
            investigation_id=str(uuid.uuid4()),
            bug_id=str(uuid.uuid4()),
            description="tokenize returns None",
        )
        source_ev = [
            e for e in result.evidence
            if e.is_verified and e.evidence_type == "SOURCE_CODE"
        ]
        for ev in source_ev:
            assert ev.search_query is not None

    def test_pipeline_collects_command_evidence(self, mock_repository):
        """Pipeline collects COMMAND_OUTPUT evidence (git log, status, grep)."""
        from backend.core.investigation.pipeline import InvestigationPipeline

        pipeline = InvestigationPipeline(mock_repository)
        result = pipeline.run(
            investigation_id=str(uuid.uuid4()),
            bug_id=str(uuid.uuid4()),
            description="tokenize returns None",
        )
        cmd_ev = [e for e in result.evidence if e.evidence_type == "COMMAND_OUTPUT"]
        assert len(cmd_ev) > 0
        for ev in cmd_ev:
            assert ev.command_executed is not None
            assert ev.command_output is not None
            assert ev.is_verified is True

    def test_pipeline_with_failing_test_focuses_search(self, mock_repository, sample_source_tree):
        """Pipeline uses failing_test to focus evidence collection."""
        from backend.core.investigation.pipeline import InvestigationPipeline

        pipeline = InvestigationPipeline(mock_repository)
        result = pipeline.run(
            investigation_id=str(uuid.uuid4()),
            bug_id=str(uuid.uuid4()),
            description="Parser tokenize returns None.",
            failing_test={
                "file": "tests/test_parser.py",
                "function": "test_tokenize_empty",
                "module": "tests.test_parser",
            },
        )
        # test_parser.py should appear in relevant files
        assert any("test_parser.py" in f for f in result.relevant_files)

    def test_pipeline_handles_uncloned_repository(self):
        """
        Pipeline handles a repository that is not cloned gracefully.
        Evidence is marked unavailable; no paths are fabricated.
        """
        from backend.core.investigation.pipeline import InvestigationPipeline
        from backend.repositories.base import BaseRepository, RepositoryInfo, TestSpec

        class UnclonedRepo(BaseRepository):
            @property
            def info(self):
                return RepositoryInfo("uncloned", "https://x.com/y.git", "main", "")

            def checkout(self, ref=None): pass
            def install_dependencies(self): pass
            def get_source_files(self): return []
            def get_test_spec(self): return TestSpec(["pytest"])
            def run_tests(self, ref=None, extra_args=None): return {}
            def is_cloned(self): return False

        repo = UnclonedRepo("/nonexistent/path")
        pipeline = InvestigationPipeline(repo)
        result = pipeline.run(
            investigation_id=str(uuid.uuid4()),
            bug_id=str(uuid.uuid4()),
            description="Something broke",
            error_message="AttributeError",
        )
        # Should complete without error
        assert result.error is None
        # No source files found
        assert result.relevant_files == []
        # Command evidence records 'unavailable'
        unavail = [
            e for e in result.evidence
            if e.command_output and "UNAVAILABLE" in e.command_output
        ]
        assert len(unavail) > 0

    def test_pipeline_evidence_never_fabricates_file_paths(self, mock_repository):
        """
        All SOURCE_CODE evidence file paths must exist on disk.
        """
        from backend.core.investigation.pipeline import InvestigationPipeline

        pipeline = InvestigationPipeline(mock_repository)
        result = pipeline.run(
            investigation_id=str(uuid.uuid4()),
            bug_id=str(uuid.uuid4()),
            description="tokenize returns None",
        )
        for ev in result.evidence:
            if ev.evidence_type == "SOURCE_CODE" and ev.file_path:
                abs_path = os.path.join(mock_repository.repository_path, ev.file_path)
                assert os.path.isfile(abs_path), (
                    f"Evidence references non-existent file: {ev.file_path}"
                )

    def test_pipeline_with_ai_investigator(self, mock_repository):
        """Pipeline integrates with an AI investigator stub."""
        from backend.core.investigation.pipeline import InvestigationPipeline

        ai = MagicMock()
        ai.analyse.return_value = {
            "summary": "AI summary of the bug.",
            "suspected_cause": "tokenize returns None on empty input",
            "root_cause_explanation": "The function does not handle empty strings.",
            "confidence": 0.85,
            "hypotheses": [
                {"title": "Empty string not handled", "explanation": "...", "confidence": 0.85}
            ],
        }

        pipeline = InvestigationPipeline(mock_repository, ai_investigator=ai)
        result = pipeline.run(
            investigation_id=str(uuid.uuid4()),
            bug_id=str(uuid.uuid4()),
            description="tokenize returns None",
        )
        assert result.suspected_cause == "tokenize returns None on empty input"
        assert result.confidence == 0.85
        assert len(result.hypotheses) == 1
        ai.analyse.assert_called_once()


# ---------------------------------------------------------------------------
# Test: RootCauseAnalyser (unit)
# ---------------------------------------------------------------------------

class TestRootCauseAnalyser:
    def test_rca_no_ai_no_evidence(self):
        """RCA without AI and no evidence gives insufficient_evidence status."""
        from backend.core.root_cause.analyser import RootCauseAnalyser, RCA_STATUS_INSUFFICIENT

        analyser = RootCauseAnalyser(ai_root_cause=None)
        rca = analyser.analyse(
            investigation_id="test-id",
            error_message="",
            description="Something is wrong.",
            evidence=[],
            hypotheses=[],
        )
        assert rca.machine_corroborated is False
        assert rca.status == RCA_STATUS_INSUFFICIENT
        assert rca.supporting_evidence == []
        assert rca.supporting_evidence_ids == []

    def test_rca_with_verified_evidence_no_ai(self):
        """RCA with verified evidence (no AI) gives suspected status."""
        from backend.core.root_cause.analyser import RootCauseAnalyser, RCA_STATUS_SUSPECTED

        analyser = RootCauseAnalyser(ai_root_cause=None)
        evidence = [
            {
                "id": "ev-001",
                "evidence_type": "SOURCE_CODE",
                "is_verified": True,
                "file_path": "myproject/parser.py",
                "line_number": 10,
                "description": "tokenize returns None",
            },
            {
                "id": "ev-002",
                "evidence_type": "COMMAND_OUTPUT",
                "is_verified": True,
                "description": "git log output",
            },
        ]
        rca = analyser.analyse(
            investigation_id="test-id",
            error_message="ValueError: tokenize returned None",
            description="Parser bug.",
            evidence=evidence,
            hypotheses=[],
        )
        assert rca.machine_corroborated is True
        assert rca.status == RCA_STATUS_SUSPECTED  # no AI, confidence = 0.0
        assert "ev-001" in rca.supporting_evidence_ids
        assert "ev-002" in rca.supporting_evidence_ids

    def test_rca_references_evidence_ids(self):
        """RCA supporting_evidence_ids must be IDs from evidence with is_verified=True."""
        from backend.core.root_cause.analyser import RootCauseAnalyser

        analyser = RootCauseAnalyser()
        evidence = [
            {"id": "e1", "is_verified": True, "evidence_type": "SOURCE_CODE",
             "file_path": "a.py", "line_number": 1, "description": "match"},
            {"id": "e2", "is_verified": False, "evidence_type": "OTHER",
             "description": "AI speculation"},
            {"id": "e3", "is_verified": True, "evidence_type": "COMMAND_OUTPUT",
             "description": "grep result"},
        ]
        rca = analyser.analyse(
            investigation_id="test",
            error_message="",
            description="test",
            evidence=evidence,
            hypotheses=[],
        )
        # Only verified evidence IDs should appear
        assert "e1" in rca.supporting_evidence_ids
        assert "e3" in rca.supporting_evidence_ids
        assert "e2" not in rca.supporting_evidence_ids

    def test_rca_affected_files_from_relevant_files(self):
        """RCA affected_files is populated from relevant_files argument."""
        from backend.core.root_cause.analyser import RootCauseAnalyser

        analyser = RootCauseAnalyser()
        rca = analyser.analyse(
            investigation_id="test",
            error_message="",
            description="test",
            evidence=[],
            hypotheses=[],
            relevant_files=["src/parser.py", "src/utils.py"],
            relevant_functions=["src/parser.py::tokenize"],
        )
        assert "src/parser.py" in rca.affected_files
        assert "src/utils.py" in rca.affected_files
        assert "src/parser.py::tokenize" in rca.affected_functions

    def test_rca_with_ai(self):
        """RCA uses AI component output and marks result as AI-generated."""
        from backend.core.root_cause.analyser import RootCauseAnalyser, RCA_STATUS_CONFIRMED

        ai = MagicMock()
        ai.analyse.return_value = {
            "suspected_cause": "tokenize does not handle empty string",
            "explanation": "When text is empty, returns None instead of [].",
            "mechanism": "returns None",
            "summary": "Bug in tokenize",
            "confidence": 0.9,
        }

        analyser = RootCauseAnalyser(ai_root_cause=ai)
        evidence = [
            {"id": "ev-1", "is_verified": True, "evidence_type": "SOURCE_CODE",
             "file_path": "parser.py", "line_number": 5, "description": "match"},
        ]
        rca = analyser.analyse(
            investigation_id="test",
            error_message="ValueError",
            description="Parser bug",
            evidence=evidence,
            hypotheses=[],
        )
        assert rca.ai_generated is True
        assert rca.confidence == 0.9
        assert rca.status == RCA_STATUS_CONFIRMED
        assert rca.suspected_cause == "tokenize does not handle empty string"
        assert rca.mechanism == "returns None"
        ai.analyse.assert_called_once()

    def test_rca_status_insufficient_without_evidence(self):
        """RCA status is insufficient_evidence when no verified evidence exists."""
        from backend.core.root_cause.analyser import RootCauseAnalyser, RCA_STATUS_INSUFFICIENT

        ai = MagicMock()
        ai.analyse.return_value = {
            "suspected_cause": "AI guess",
            "explanation": "...",
            "confidence": 0.95,
        }
        analyser = RootCauseAnalyser(ai_root_cause=ai)
        # Pass only unverified evidence
        evidence = [
            {"id": "e1", "is_verified": False, "evidence_type": "OTHER", "description": "AI claim"}
        ]
        rca = analyser.analyse(
            investigation_id="t",
            error_message="",
            description="",
            evidence=evidence,
            hypotheses=[],
        )
        # Even with high AI confidence, no machine evidence → insufficient
        assert rca.machine_corroborated is False
        assert rca.status == RCA_STATUS_INSUFFICIENT


# ---------------------------------------------------------------------------
# Test: FixGenerator (unit)
# ---------------------------------------------------------------------------

class TestFixGenerator:
    def test_fix_proposal_pending_approval_status(self):
        """Fix proposal must always start as PENDING."""
        from backend.core.fix_generation.generator import FixGenerator

        gen = FixGenerator()
        proposal = gen.generate(
            investigation_id="inv-1",
            suspected_cause="bug",
            explanation="explanation",
            relevant_files=[],
            evidence=[],
            repository_path="/tmp",
        )
        assert proposal.approval_status == "PENDING"
        assert proposal.ai_generated is True
        assert proposal.approval_required is True

    def test_fix_proposal_not_auto_applied(self):
        """
        A fix proposal must NOT be automatically applied.
        The proposal object does not contain any 'applied' state.
        """
        from backend.core.fix_generation.generator import FixGenerator

        gen = FixGenerator()
        proposal = gen.generate(
            investigation_id="inv-1",
            suspected_cause="bug",
            explanation="explanation",
            relevant_files=[],
            evidence=[],
            repository_path="/tmp",
        )
        d = proposal.to_dict()
        assert d["approval_status"] == "PENDING"
        # No 'applied' key in the proposal dict
        assert "application_status" not in d

    def test_fix_proposal_references_evidence_ids(self):
        """Fix proposal supporting_evidence_ids references verified evidence."""
        from backend.core.fix_generation.generator import FixGenerator

        gen = FixGenerator()
        evidence = [
            {"id": "e1", "is_verified": True, "evidence_type": "SOURCE_CODE",
             "file_path": "a.py", "line_number": 5,
             "line_number_end": 10, "description": "keyword match"},
            {"id": "e2", "is_verified": False, "evidence_type": "OTHER",
             "description": "AI guess"},
            {"id": "e3", "is_verified": True, "evidence_type": "COMMAND_OUTPUT",
             "description": "grep output"},
        ]
        proposal = gen.generate(
            investigation_id="inv-1",
            suspected_cause="bug",
            explanation="explanation",
            relevant_files=["a.py"],
            evidence=evidence,
            repository_path="/tmp",
        )
        # Only verified evidence IDs
        assert "e1" in proposal.supporting_evidence_ids
        assert "e3" in proposal.supporting_evidence_ids
        assert "e2" not in proposal.supporting_evidence_ids

    def test_fix_proposal_affected_lines_from_evidence(self):
        """affected_lines is built from SOURCE_CODE evidence."""
        from backend.core.fix_generation.generator import FixGenerator

        gen = FixGenerator()
        evidence = [
            {"id": "e1", "is_verified": True, "evidence_type": "SOURCE_CODE",
             "file_path": "parser.py", "line_number": 9, "line_number_end": 12,
             "description": "tokenize returns None"},
        ]
        proposal = gen.generate(
            investigation_id="inv-1",
            suspected_cause="bug",
            explanation="explanation",
            relevant_files=["parser.py"],
            evidence=evidence,
            repository_path="/tmp",
        )
        assert len(proposal.affected_lines) == 1
        al = proposal.affected_lines[0]
        assert al["file"] == "parser.py"
        assert al["line_start"] == 9
        assert al["line_end"] == 12

    def test_fix_proposal_with_ai(self):
        """FixGenerator uses AI output when AI fixer is provided."""
        from backend.core.fix_generation.generator import FixGenerator

        ai = MagicMock()
        ai.generate_fix.return_value = {
            "explanation": "Add empty-string guard",
            "patch": "--- a/parser.py\n+++ b/parser.py\n@@ -1,1 +1,1 @@\n-return None\n+return []\n",
            "files_changed": ["parser.py"],
            "risk_info": {"level": "low", "notes": "Minimal change"},
            "confidence": 0.88,
        }

        gen = FixGenerator(ai_fixer=ai)
        evidence = [
            {"id": "e1", "is_verified": True, "evidence_type": "SOURCE_CODE",
             "file_path": "parser.py", "line_number": 10,
             "description": "tokenize returns None"},
        ]
        proposal = gen.generate(
            investigation_id="inv-1",
            suspected_cause="tokenize returns None",
            explanation="...",
            relevant_files=["parser.py"],
            evidence=evidence,
            repository_path="/tmp",
        )
        assert proposal.patch != ""
        assert "parser.py" in proposal.files_changed
        assert proposal.confidence == 0.88
        assert proposal.approval_status == "PENDING"
        ai.generate_fix.assert_called_once()

    def test_fix_no_ai_populates_files_changed_from_evidence(self):
        """Without AI, files_changed is populated from verified SOURCE_CODE evidence."""
        from backend.core.fix_generation.generator import FixGenerator

        gen = FixGenerator()
        evidence = [
            {"id": "e1", "is_verified": True, "evidence_type": "SOURCE_CODE",
             "file_path": "parser.py", "line_number": 5, "description": "match"},
            {"id": "e2", "is_verified": True, "evidence_type": "SOURCE_CODE",
             "file_path": "utils.py", "line_number": 3, "description": "match"},
        ]
        proposal = gen.generate(
            investigation_id="inv-1",
            suspected_cause="bug",
            explanation="...",
            relevant_files=["parser.py", "utils.py"],
            evidence=evidence,
            repository_path="/tmp",
        )
        assert "parser.py" in proposal.files_changed
        assert "utils.py" in proposal.files_changed


# ---------------------------------------------------------------------------
# Test: Fix approval gate (API)
# ---------------------------------------------------------------------------

class TestFixApprovalGate:
    def _create_bug_and_fix(self, app):
        """Helper: create a Bug → Investigation → Fix in PENDING state."""
        from backend.database.connection import db
        from backend.models import (
            Bug, BugStatus, TargetRepository,
            Investigation, InvestigationStatus,
            Fix, ApprovalStatus, ApplicationStatus,
        )

        with app.app_context():
            bug = Bug(
                title="Fix gate test",
                description="Testing fix gate.",
                target_repository=TargetRepository.THEFUCK,
                status=BugStatus.WAITING_FOR_APPROVAL,
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
                explanation="Add empty-string guard.",
                patch="--- a/x.py\n+++ b/x.py\n",
                files_changed=["x.py"],
                supporting_evidence_ids=["ev-001"],
                confidence=0.75,
                approval_required=True,
                approval_status=ApprovalStatus.PENDING,
                application_status=ApplicationStatus.NOT_APPLIED,
            )
            db.session.add(fix)
            db.session.commit()
            return bug.id, inv.id, fix.id

    def test_fix_starts_pending(self, app, client):
        """A fix created by the pipeline must have approval_status=PENDING."""
        bug_id, inv_id, fix_id = self._create_bug_and_fix(app)
        response = client.get(f"/api/fixes/{fix_id}")
        assert response.status_code == 200
        data = response.get_json()
        assert data["approval_status"] == "PENDING"
        assert data["application_status"] == "NOT_APPLIED"
        assert data["ai_generated"] is True
        assert data["supporting_evidence_ids"] == ["ev-001"]
        assert data["confidence"] == 0.75

    def test_fix_not_auto_applied(self, app, client):
        """A PENDING fix must NOT be auto-applied (application_status stays NOT_APPLIED)."""
        bug_id, inv_id, fix_id = self._create_bug_and_fix(app)
        response = client.get(f"/api/fixes/{fix_id}")
        assert response.get_json()["application_status"] == "NOT_APPLIED"

    def test_fix_cannot_be_applied_without_approval(self, app, client):
        """Attempting to apply a PENDING fix must return 409."""
        bug_id, inv_id, fix_id = self._create_bug_and_fix(app)
        response = client.post(f"/api/fixes/{fix_id}/apply")
        assert response.status_code == 409
        assert "APPROVED" in response.get_json()["error"]

    def test_fix_approve_transitions_to_approved(self, app, client):
        """Approving a fix changes approval_status to APPROVED."""
        bug_id, inv_id, fix_id = self._create_bug_and_fix(app)
        response = client.post(f"/api/fixes/{fix_id}/approve", json={"approved_by": "dev1"})
        assert response.status_code == 200
        data = response.get_json()
        assert data["approval_status"] == "APPROVED"
        assert data["approved_by"] == "dev1"

    def test_fix_reject_transitions_to_rejected(self, app, client):
        """Rejecting a fix changes approval_status to REJECTED."""
        bug_id, inv_id, fix_id = self._create_bug_and_fix(app)
        response = client.post(
            f"/api/fixes/{fix_id}/reject",
            json={"rejection_reason": "Patch is too risky."}
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["approval_status"] == "REJECTED"
        assert data["rejection_reason"] == "Patch is too risky."

    def test_fix_double_approve_returns_409(self, app, client):
        """Approving an already-approved fix returns 409."""
        bug_id, inv_id, fix_id = self._create_bug_and_fix(app)
        client.post(f"/api/fixes/{fix_id}/approve")
        response = client.post(f"/api/fixes/{fix_id}/approve")
        assert response.status_code == 409

    def test_fix_not_found(self, client):
        """GET/POST for a non-existent fix returns 404."""
        assert client.get("/api/fixes/nonexistent").status_code == 404


# ---------------------------------------------------------------------------
# Test: Evidence persistence (DB + API)
# ---------------------------------------------------------------------------

class TestEvidencePersistence:
    def test_evidence_stored_with_all_fields(self, app):
        """Evidence records are stored with the new fields."""
        from backend.database.connection import db
        from backend.models import (
            Bug, BugStatus, TargetRepository,
            Investigation, InvestigationStatus,
            Evidence, EvidenceType,
        )

        with app.app_context():
            bug = Bug(
                title="Evidence field test",
                description="Testing evidence fields.",
                target_repository=TargetRepository.TQDM,
                status=BugStatus.INVESTIGATING,
            )
            db.session.add(bug)
            db.session.commit()

            inv = Investigation(bug_id=bug.id, status=InvestigationStatus.IN_PROGRESS)
            db.session.add(inv)
            db.session.commit()

            ev = Evidence(
                investigation_id=inv.id,
                evidence_type=EvidenceType.SOURCE_CODE,
                description="Keyword match in parser.py at line 10",
                file_path="parser.py",
                line_number=10,
                line_number_end=15,
                code_snippet="    return None  # bug here",
                search_query="tokenize, None",
                relevance_explanation="Source line contains 'tokenize' and 'None'.",
                is_verified=True,
            )
            db.session.add(ev)
            db.session.commit()

            retrieved = db.session.get(Evidence, ev.id)
            assert retrieved.line_number_end == 15
            assert retrieved.search_query == "tokenize, None"
            assert retrieved.relevance_explanation is not None
            assert retrieved.is_verified is True

    def test_evidence_retrieved_via_api(self, app, client):
        """Evidence items are accessible via GET /api/investigations/<id>/evidence."""
        from backend.database.connection import db
        from backend.models import (
            Bug, BugStatus, TargetRepository,
            Investigation, InvestigationStatus,
            Evidence, EvidenceType,
        )

        with app.app_context():
            bug = Bug(
                title="API evidence test",
                description="Test.",
                target_repository=TargetRepository.THEFUCK,
                status=BugStatus.INVESTIGATING,
            )
            db.session.add(bug)
            db.session.commit()

            inv = Investigation(bug_id=bug.id, status=InvestigationStatus.COMPLETED)
            db.session.add(inv)
            db.session.commit()

            for i in range(3):
                ev = Evidence(
                    investigation_id=inv.id,
                    evidence_type=EvidenceType.SOURCE_CODE,
                    description=f"Evidence item {i}",
                    is_verified=(i % 2 == 0),
                    file_path=f"file_{i}.py",
                    line_number=i + 1,
                    search_query=f"keyword_{i}",
                )
                db.session.add(ev)
            db.session.commit()
            inv_id = inv.id

        response = client.get(f"/api/investigations/{inv_id}/evidence")
        assert response.status_code == 200
        items = response.get_json()
        assert len(items) == 3
        # Verify the new fields are returned
        for item in items:
            assert "search_query" in item
            assert "line_number_end" in item
            assert "relevance_explanation" in item
            assert "is_verified" in item


# ---------------------------------------------------------------------------
# Test: Full flow via WorkflowOrchestrator (integration)
# ---------------------------------------------------------------------------

class TestWorkflowIntegration:
    def test_full_flow_creates_investigation_evidence_fix(
        self, app, mock_repository
    ):
        """
        WorkflowOrchestrator.execute() creates Investigation, Evidence, and Fix
        records with the correct states.
        """
        import backend.core.workflow.orchestrator as orch_mod
        from backend.database.connection import db
        from backend.models import (
            Bug, BugStatus, TargetRepository,
            Investigation, InvestigationStatus,
            Evidence, Fix, ApprovalStatus, ApplicationStatus,
        )
        from backend.core.workflow import WorkflowOrchestrator

        with app.app_context():
            bug = Bug(
                title="Full flow test",
                description="The tokenize function returns None for empty input.",
                target_repository=TargetRepository.THEFUCK,
                error_message="ValueError: tokenize returned None",
                status=BugStatus.RECEIVED,
            )
            db.session.add(bug)
            db.session.commit()
            bug_id = bug.id

        orchestrator = WorkflowOrchestrator(
            repository_workspace="/irrelevant",
            ai_investigator=None,
            ai_root_cause=None,
            ai_fixer=None,
        )

        # Swap get_repository at module level to return our mock
        original = orch_mod.get_repository
        orch_mod.get_repository = lambda name, ws: mock_repository
        try:
            run = orchestrator.execute(bug_id=bug_id, app=app)
        finally:
            orch_mod.get_repository = original

        # Run must reach WAITING_FOR_APPROVAL (not FAILED)
        assert run.status.value == "WAITING_FOR_APPROVAL", (
            f"Run status was {run.status}. Error: {run.error_message}"
        )
        assert run.investigation_id is not None
        assert run.fix_id is not None

        with app.app_context():
            # Bug status updated
            bug = db.session.get(Bug, bug_id)
            assert bug.status == BugStatus.WAITING_FOR_APPROVAL

            # Investigation created and completed
            inv = db.session.get(Investigation, run.investigation_id)
            assert inv is not None
            assert inv.status == InvestigationStatus.COMPLETED
            assert inv.relevant_files is not None

            # Evidence collected
            evidence_list = (
                db.session.execute(
                    db.select(Evidence).where(Evidence.investigation_id == inv.id)
                ).scalars().all()
            )
            assert len(evidence_list) > 0
            verified = [e for e in evidence_list if e.is_verified]
            assert len(verified) > 0

            # Fix created in PENDING state
            fix = db.session.get(Fix, run.fix_id)
            assert fix is not None
            assert fix.approval_status == ApprovalStatus.PENDING
            assert fix.application_status == ApplicationStatus.NOT_APPLIED
            assert fix.ai_generated is True
            assert fix.approval_required is True

    def test_full_flow_fix_stays_pending_after_completion(self, app, mock_repository):
        """
        After the workflow completes, the fix remains PENDING (not auto-applied).
        """
        from backend.database.connection import db
        from backend.models import (
            Bug, BugStatus, TargetRepository,
            Fix, ApprovalStatus, ApplicationStatus,
        )
        from backend.core.workflow import WorkflowOrchestrator

        with app.app_context():
            bug = Bug(
                title="Pending fix check",
                description="tokenize returns None",
                target_repository=TargetRepository.TQDM,
                status=BugStatus.RECEIVED,
            )
            db.session.add(bug)
            db.session.commit()
            bug_id = bug.id

        import backend.core.workflow.orchestrator as orch_mod
        orchestrator = WorkflowOrchestrator("/irrelevant")

        original = orch_mod.get_repository
        orch_mod.get_repository = lambda name, ws: mock_repository
        try:
            run = orchestrator.execute(bug_id=bug_id, app=app)
        finally:
            orch_mod.get_repository = original

        assert run.fix_id is not None

        with app.app_context():
            fix = db.session.get(Fix, run.fix_id)
            # Fix is PENDING, not applied
            assert fix.approval_status == ApprovalStatus.PENDING
            assert fix.application_status == ApplicationStatus.NOT_APPLIED
            # AI generated
            assert fix.ai_generated is True

    def test_full_flow_evidence_ids_referenced_in_fix(self, app, mock_repository):
        """
        The fix's supporting_evidence_ids must reference actual Evidence record IDs.
        """
        from backend.database.connection import db
        from backend.models import (
            Bug, BugStatus, TargetRepository, Fix, Evidence,
        )
        from backend.core.workflow import WorkflowOrchestrator

        with app.app_context():
            bug = Bug(
                title="Evidence ID linkage test",
                description="tokenize returns None",
                target_repository=TargetRepository.THEFUCK,
                error_message="ValueError: tokenize returned None",
                status=BugStatus.RECEIVED,
            )
            db.session.add(bug)
            db.session.commit()
            bug_id = bug.id

        import backend.core.workflow.orchestrator as orch_mod
        orchestrator = WorkflowOrchestrator("/irrelevant")

        original = orch_mod.get_repository
        orch_mod.get_repository = lambda name, ws: mock_repository
        try:
            run = orchestrator.execute(bug_id=bug_id, app=app)
        finally:
            orch_mod.get_repository = original

        with app.app_context():
            fix = db.session.get(Fix, run.fix_id)
            if fix.supporting_evidence_ids:
                for ev_id in fix.supporting_evidence_ids:
                    ev = db.session.get(Evidence, ev_id)
                    assert ev is not None, (
                        f"Fix references evidence ID {ev_id!r} that does not exist in DB"
                    )

    def test_full_flow_with_failing_test_context(self, app, mock_repository):
        """
        Workflow uses failing_test from extra_context to focus investigation.
        """
        from backend.database.connection import db
        from backend.models import Bug, BugStatus, TargetRepository, Investigation
        from backend.core.workflow import WorkflowOrchestrator

        with app.app_context():
            bug = Bug(
                title="Failing test focus",
                description="Parser tokenize fails on empty string.",
                target_repository=TargetRepository.THEFUCK,
                extra_context={
                    "failing_test": {
                        "file": "tests/test_parser.py",
                        "function": "test_tokenize_empty",
                    }
                },
                status=BugStatus.RECEIVED,
            )
            db.session.add(bug)
            db.session.commit()
            bug_id = bug.id

        import backend.core.workflow.orchestrator as orch_mod
        orchestrator = WorkflowOrchestrator("/irrelevant")

        original = orch_mod.get_repository
        orch_mod.get_repository = lambda name, ws: mock_repository
        try:
            run = orchestrator.execute(bug_id=bug_id, app=app)
        finally:
            orch_mod.get_repository = original

        with app.app_context():
            inv = db.session.get(Investigation, run.investigation_id)
            # test_parser.py should appear in relevant files
            assert inv.relevant_files is not None
            assert any("test_parser.py" in f for f in inv.relevant_files)

    def test_workflow_fails_gracefully_on_bad_bug_id(self, app):
        """WorkflowOrchestrator handles missing bug ID gracefully."""
        from backend.core.workflow import WorkflowOrchestrator, RunStatus

        orchestrator = WorkflowOrchestrator("/irrelevant")
        run = orchestrator.execute(bug_id="nonexistent-id", app=app)
        assert run.status == RunStatus.FAILED
        assert run.error_message is not None


# ---------------------------------------------------------------------------
# Test: Full API round-trip with synchronous execution
# ---------------------------------------------------------------------------

class TestAPIRoundTrip:
    def test_create_then_start_then_poll(self, client, app, mock_repository):
        """
        Full round-trip: create bug → start → run workflow directly → poll API.

        We call the workflow directly (not via a thread) so the full pipeline
        completes before we poll the API endpoints.  The start endpoint is tested
        separately; here we focus on verifying the complete data flow.
        """
        import backend.core.workflow.orchestrator as orch_mod
        from backend.core.workflow import WorkflowOrchestrator
        from backend.models import Bug, BugStatus

        # 1. Create bug
        create_resp = client.post("/api/bugs", json={
            "title": "Round-trip test",
            "description": "tokenize returns None for empty input.",
            "target_repository": "thefuck",
            "error_message": "ValueError: tokenize returned None",
        })
        assert create_resp.status_code == 201
        bug_id = create_resp.get_json()["id"]

        # 2. Start via API (suppress the thread so it doesn't run in background)
        with patch("backend.api.bugs._run_workflow_in_thread"):
            start_resp = client.post(f"/api/bugs/{bug_id}/start")
        assert start_resp.status_code == 202

        # 3. Run the workflow directly in the test's own app context
        #    (mirrors what the background thread would do)
        original = orch_mod.get_repository
        orch_mod.get_repository = lambda name, ws: mock_repository
        try:
            orchestrator = WorkflowOrchestrator("/irrelevant")
            run = orchestrator.execute(bug_id=bug_id, app=app)
        finally:
            orch_mod.get_repository = original

        # Run should reach WAITING_FOR_APPROVAL or FAILED
        assert run.status.value in (
            "WAITING_FOR_APPROVAL", "ROOT_CAUSE_FOUND", "FIX_GENERATED", "FAILED"
        ), f"Unexpected status: {run.status}. Error: {run.error_message}"

        # 4. Poll bug status via API
        bug_resp = client.get(f"/api/bugs/{bug_id}")
        assert bug_resp.status_code == 200
        assert bug_resp.get_json()["status"] in (
            "INVESTIGATING", "ROOT_CAUSE_FOUND", "FIX_GENERATED",
            "WAITING_FOR_APPROVAL", "FAILED",
        )

        # 5. Check investigations endpoint
        inv_resp = client.get(f"/api/investigations/bug/{bug_id}")
        assert inv_resp.status_code == 200
        investigations = inv_resp.get_json()
        assert len(investigations) >= 1
        inv_id = investigations[0]["id"]

        # 6. Check evidence endpoint
        ev_resp = client.get(f"/api/investigations/{inv_id}/evidence")
        assert ev_resp.status_code == 200
        evidence_items = ev_resp.get_json()
        assert len(evidence_items) >= 1
        for item in evidence_items:
            assert "is_verified" in item
            assert "search_query" in item  # new field

        # 7. Check hypotheses endpoint
        hyp_resp = client.get(f"/api/investigations/{inv_id}/hypotheses")
        assert hyp_resp.status_code == 200

        # 8. Check fix
        fix_resp = client.get(f"/api/fixes/investigation/{inv_id}")
        assert fix_resp.status_code == 200
        fixes = fix_resp.get_json()
        if fixes:
            fix = fixes[0]
            assert fix["approval_status"] == "PENDING"
            assert fix["application_status"] == "NOT_APPLIED"
            assert fix["ai_generated"] is True
            # New fields are present
            assert "supporting_evidence_ids" in fix
            assert "affected_lines" in fix
            assert "confidence" in fix


# ---------------------------------------------------------------------------
# Test: RCA status values
# ---------------------------------------------------------------------------

def test_rca_status_constants():
    """RCA status constants have expected values."""
    from backend.core.root_cause.analyser import (
        RCA_STATUS_CONFIRMED,
        RCA_STATUS_SUSPECTED,
        RCA_STATUS_INSUFFICIENT,
    )
    assert RCA_STATUS_CONFIRMED == "confirmed_by_evidence"
    assert RCA_STATUS_SUSPECTED == "suspected"
    assert RCA_STATUS_INSUFFICIENT == "insufficient_evidence"


def test_fix_proposal_to_dict_includes_new_fields():
    """FixProposal.to_dict() includes all new fields."""
    from backend.core.fix_generation.generator import FixProposal

    proposal = FixProposal(
        investigation_id="test",
        explanation="fix it",
        patch="--- a\n+++ b\n",
        files_changed=["a.py"],
        affected_lines=[{"file": "a.py", "line_start": 1, "line_end": 5}],
        supporting_evidence_ids=["ev-1", "ev-2"],
        confidence=0.75,
    )
    d = proposal.to_dict()
    assert "affected_lines" in d
    assert "supporting_evidence_ids" in d
    assert "confidence" in d
    assert d["confidence"] == 0.75
    assert "ev-1" in d["supporting_evidence_ids"]


def test_rca_to_dict_includes_new_fields():
    """RootCauseAnalysis.to_dict() includes all new fields."""
    from backend.core.root_cause.analyser import RootCauseAnalysis

    rca = RootCauseAnalysis(
        investigation_id="test",
        summary="summary",
        suspected_cause="cause",
        mechanism="how",
        supporting_evidence_ids=["ev-1"],
        affected_files=["a.py"],
        affected_functions=["a.py::foo"],
        status="suspected",
    )
    d = rca.to_dict()
    assert d["summary"] == "summary"
    assert d["mechanism"] == "how"
    assert "supporting_evidence_ids" in d
    assert "affected_files" in d
    assert "affected_functions" in d
    assert d["status"] == "suspected"
