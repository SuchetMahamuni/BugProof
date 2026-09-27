"""
Tests for the regression test generator (Step 2).

Coverage:
  - RegressionTestGenerator.generate() success and failure paths
  - Template-based generation (AI unavailable)
  - AI-configured-but-not-integrated path (returns stub, not error)
  - TestCodeValidator: syntax check, assert check, forbidden patterns
  - GeneratedTest and GenerationResult to_dict()
  - write_test_to_file: validates, refuses overwrite, writes correctly
  - API: POST /api/verification/regression/generate
  - API: GET  /api/verification/regression/<fix_id>
  - Insufficient context raises graceful error result (not exception)
  - Pipeline internal errors surface correctly in workflow
"""

import os
import sys
import tempfile
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
    application = create_app(config_override={
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    })
    with application.app_context():
        from backend.database.connection import db
        db.create_all()
        yield application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def db(app):
    from backend.database.connection import db as _db
    return _db


@pytest.fixture()
def sample_bug(app, db):
    from backend.models import Bug, BugStatus, TargetRepository
    with app.app_context():
        bug = Bug(
            title="NoneType.group error",
            description="AttributeError: NoneType has no attribute group",
            target_repository=TargetRepository.THEFUCK,
            error_message="AttributeError: 'NoneType' object has no attribute 'group'",
            status=BugStatus.RECEIVED,
        )
        db.session.add(bug)
        db.session.commit()
        yield bug


@pytest.fixture()
def sample_fix(app, db, sample_bug):
    from backend.models import (
        Investigation, InvestigationStatus,
        Fix, ApprovalStatus, ApplicationStatus,
    )
    with app.app_context():
        inv = Investigation(
            bug_id=sample_bug.id,
            status=InvestigationStatus.COMPLETED,
            relevant_files=["thefuck/rules/no_such_command.py"],
        )
        db.session.add(inv)
        db.session.commit()

        fix = Fix(
            investigation_id=inv.id,
            ai_generated=True,
            explanation="Add None check before calling .group()",
            patch="--- a/thefuck/rules/no_such.py\n+++ b/thefuck/rules/no_such.py\n",
            files_changed=["thefuck/rules/no_such_command.py"],
            approval_status=ApprovalStatus.PENDING,
            application_status=ApplicationStatus.NOT_APPLIED,
        )
        db.session.add(fix)
        db.session.commit()
        yield fix


# ---------------------------------------------------------------------------
# TestCodeValidator
# ---------------------------------------------------------------------------

class TestCodeValidator:
    def _validator(self):
        from backend.core.regression.generator import TestCodeValidator
        return TestCodeValidator()

    def test_valid_test_passes(self):
        code = "def test_foo():\n    assert 1 == 1\n"
        valid, err = self._validator().validate(code)
        assert valid is True
        assert err is None

    def test_empty_code_fails(self):
        valid, err = self._validator().validate("")
        assert valid is False
        assert err is not None

    def test_no_test_function_fails(self):
        code = "def helper():\n    assert 1 == 1\n"
        valid, err = self._validator().validate(code)
        assert valid is False
        assert "test_*" in err

    def test_no_assert_fails(self):
        code = "def test_foo():\n    x = 1\n"
        valid, err = self._validator().validate(code)
        assert valid is False
        assert "assert" in err

    def test_syntax_error_fails(self):
        code = "def test_foo(\n    assert True\n"
        valid, err = self._validator().validate(code)
        assert valid is False
        assert "Syntax error" in err or "syntax" in err.lower()

    def test_eval_forbidden(self):
        code = "def test_foo():\n    eval('1+1')\n    assert True\n"
        valid, err = self._validator().validate(code)
        assert valid is False
        assert "forbidden" in err.lower()

    def test_exec_forbidden(self):
        code = "def test_foo():\n    exec('x=1')\n    assert True\n"
        valid, err = self._validator().validate(code)
        assert valid is False

    def test_subprocess_shell_true_forbidden(self):
        code = (
            "import subprocess\n"
            "def test_foo():\n"
            "    subprocess.run(['ls'], shell=True)\n"
            "    assert True\n"
        )
        valid, err = self._validator().validate(code)
        assert valid is False


# ---------------------------------------------------------------------------
# RegressionTestGenerator – unit tests
# ---------------------------------------------------------------------------

class TestRegressionTestGenerator:
    def _make_gen(self, ai_fixer=None):
        from backend.core.regression import RegressionTestGenerator
        return RegressionTestGenerator(ai_fixer=ai_fixer)

    def test_generate_no_ai_returns_template_stub(self):
        gen = self._make_gen(ai_fixer=None)
        result = gen.generate(
            investigation_id="inv-123",
            bug_description="Test bug description",
            target_repository="thefuck",
            error_message="AttributeError",
        )
        assert result.error is None
        assert len(result.tests) == 1
        test = result.tests[0]
        assert test.ai_generated is False
        assert test.is_validated is True
        assert "test_regression_" in test.test_name
        assert "TODO" in test.test_code
        assert result.total_generated == 1
        assert result.total_validated == 1

    def test_generate_empty_description_returns_error(self):
        gen = self._make_gen(ai_fixer=None)
        result = gen.generate(
            investigation_id="inv-456",
            bug_description="",
            target_repository="thefuck",
            error_message=None,
        )
        assert result.error is not None
        assert "Insufficient context" in result.error
        assert len(result.tests) == 0

    def test_generate_with_description_only(self):
        gen = self._make_gen(ai_fixer=None)
        result = gen.generate(
            investigation_id="inv-789",
            bug_description="Some bug",
            target_repository="tqdm",
        )
        assert result.error is None
        assert len(result.tests) == 1
        assert result.tests[0].target_repository == "tqdm"

    def test_generate_template_suggested_file_thefuck(self):
        gen = self._make_gen(ai_fixer=None)
        result = gen.generate(
            investigation_id="inv-abc",
            bug_description="bug",
            target_repository="thefuck",
            error_message="error",
        )
        assert result.tests[0].suggested_file == "tests/test_regression.py"

    def test_generate_template_suggested_file_youtube_dl(self):
        gen = self._make_gen(ai_fixer=None)
        result = gen.generate(
            investigation_id="inv-def",
            bug_description="bug",
            target_repository="youtube_dl",
            error_message="error",
        )
        assert result.tests[0].suggested_file == "test/test_regression.py"

    def test_generate_with_ai_unavailable_stub(self):
        """AI configured but not integrated → stub returned, ai_generated=True."""
        mock_ai = MagicMock()
        mock_ai.is_available = True
        mock_ai.generate_fix.return_value = {
            "explanation": "[AI CONFIGURED BUT NOT INTEGRATED] stub",
            "patch": "",
            "files_changed": [],
            "risk_info": {},
        }
        gen = self._make_gen(ai_fixer=mock_ai)
        result = gen.generate(
            investigation_id="inv-stub",
            bug_description="Test bug",
            target_repository="thefuck",
            error_message="SomeError",
        )
        assert result.error is None
        assert len(result.tests) == 1
        # ai_generated=True because AI was requested
        assert result.tests[0].ai_generated is True
        # The test code is a stub but still valid Python
        assert result.tests[0].is_validated is True

    def test_generate_ai_exception_falls_back_to_template(self):
        """If the AI call throws, we get a fallback stub rather than an error."""
        mock_ai = MagicMock()
        mock_ai.is_available = True
        mock_ai.generate_fix.side_effect = RuntimeError("AI connection error")
        gen = self._make_gen(ai_fixer=mock_ai)
        result = gen.generate(
            investigation_id="inv-exc",
            bug_description="Test bug",
            target_repository="thefuck",
            error_message="SomeError",
        )
        assert result.error is None
        assert len(result.tests) == 1
        assert result.tests[0].ai_generated is True

    def test_to_dict_shape(self):
        gen = self._make_gen(ai_fixer=None)
        result = gen.generate(
            investigation_id="inv-dict",
            bug_description="dict test",
            target_repository="thefuck",
        )
        d = result.to_dict()
        assert "tests" in d
        assert "ai_generated" in d
        assert "total_generated" in d
        assert "total_validated" in d
        assert "generated_at" in d
        assert isinstance(d["tests"], list)


# ---------------------------------------------------------------------------
# write_test_to_file
# ---------------------------------------------------------------------------

class TestWriteTestToFile:
    def _make_gen(self):
        from backend.core.regression import RegressionTestGenerator
        return RegressionTestGenerator()

    def test_write_valid_test(self):
        gen = self._make_gen()
        result = gen.generate(
            investigation_id="inv-write",
            bug_description="write test",
            target_repository="thefuck",
            error_message="error",
        )
        test = result.tests[0]
        assert test.is_validated is True

        with tempfile.TemporaryDirectory() as tmpdir:
            write_result = gen.write_test_to_file(test, tmpdir)
            assert write_result["success"] is True
            assert write_result["error"] is None
            assert os.path.isfile(write_result["path"])

    def test_write_refuses_unvalidated_test(self):
        from backend.core.regression.generator import GeneratedTest, GenerationStatus
        test = GeneratedTest(
            test_name="test_bad",
            test_code="not valid python {{{{",
            suggested_file="tests/test_x.py",
            target_repository="thefuck",
            ai_generated=False,
            is_validated=False,
            validation_error="Syntax error",
            status=GenerationStatus.VALIDATION_FAILED,
        )
        gen = self._make_gen()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = gen.write_test_to_file(test, tmpdir)
            assert result["success"] is False
            assert "not been validated" in result["error"]

    def test_write_refuses_existing_file_without_overwrite(self):
        gen = self._make_gen()
        result = gen.generate(
            investigation_id="inv-over",
            bug_description="overwrite test",
            target_repository="thefuck",
        )
        test = result.tests[0]

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write once
            first = gen.write_test_to_file(test, tmpdir)
            assert first["success"] is True

            # Write again without overwrite=True
            second = gen.write_test_to_file(test, tmpdir)
            assert second["success"] is False
            assert "already exists" in second["error"]

    def test_write_overwrites_when_flag_set(self):
        gen = self._make_gen()
        result = gen.generate(
            investigation_id="inv-over2",
            bug_description="overwrite test",
            target_repository="thefuck",
        )
        test = result.tests[0]

        with tempfile.TemporaryDirectory() as tmpdir:
            gen.write_test_to_file(test, tmpdir)
            second = gen.write_test_to_file(test, tmpdir, overwrite=True)
            assert second["success"] is True


# ---------------------------------------------------------------------------
# API: POST /api/verification/regression/generate
# ---------------------------------------------------------------------------

class TestRegressionGenerationAPI:
    def test_generate_by_fix_id_returns_202(self, client, sample_fix, sample_bug):
        resp = client.post(
            "/api/verification/regression/generate",
            json={"fix_id": sample_fix.id},
        )
        assert resp.status_code == 202
        data = resp.get_json()
        assert "tests" in data
        assert "total_generated" in data
        assert data["fix_id"] == sample_fix.id
        assert data["bug_id"] == sample_bug.id

    def test_generate_by_bug_id_only_returns_202(self, client, sample_bug):
        resp = client.post(
            "/api/verification/regression/generate",
            json={"bug_id": sample_bug.id},
        )
        assert resp.status_code == 202
        data = resp.get_json()
        assert "tests" in data
        assert data["bug_id"] == sample_bug.id

    def test_generate_no_ids_returns_400(self, client):
        resp = client.post("/api/verification/regression/generate", json={})
        assert resp.status_code == 400

    def test_generate_unknown_fix_returns_404(self, client):
        resp = client.post(
            "/api/verification/regression/generate",
            json={"fix_id": "no-such-fix"},
        )
        assert resp.status_code == 404

    def test_generate_stores_metadata_in_fix(self, client, app, db, sample_fix):
        """Generated test count should be stored in fix.risk_info."""
        client.post(
            "/api/verification/regression/generate",
            json={"fix_id": sample_fix.id},
        )
        with app.app_context():
            from backend.models import Fix
            from backend.database.connection import db as _db
            fix = _db.session.get(Fix, sample_fix.id)
            assert fix.risk_info is not None
            assert "regression_tests_generated" in fix.risk_info

    def test_generate_returns_validated_test(self, client, sample_fix):
        resp = client.post(
            "/api/verification/regression/generate",
            json={"fix_id": sample_fix.id},
        )
        data = resp.get_json()
        assert len(data["tests"]) >= 1
        test = data["tests"][0]
        # Template test should pass validation
        assert test["is_validated"] is True

    def test_get_regression_info_by_fix_id(self, client, sample_fix):
        # First generate
        client.post(
            "/api/verification/regression/generate",
            json={"fix_id": sample_fix.id},
        )
        resp = client.get(f"/api/verification/regression/{sample_fix.id}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["fix_id"] == sample_fix.id

    def test_get_regression_info_not_found(self, client):
        resp = client.get("/api/verification/regression/no-such-fix")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Workflow: pipeline error surfaces as FAILED
# ---------------------------------------------------------------------------

class TestPipelineErrorSurfaces:
    """Verify that inv_result.error in the orchestrator causes FAILED state."""

    def test_pipeline_internal_error_causes_failed_bug(self, app, db, sample_bug):
        """
        When the investigation pipeline returns inv_result.error != None,
        the orchestrator must set Bug.status=FAILED.
        """
        from backend.models import Bug, BugStatus
        from backend.core.workflow import WorkflowOrchestrator
        from backend.repositories.base import BaseRepository, RepositoryInfo, TestSpec

        with tempfile.TemporaryDirectory() as tmpdir:
            class ErrorPipelineRepo(BaseRepository):
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

            repo = ErrorPipelineRepo(tmpdir)

            # Patch the pipeline to return an error in the result
            from backend.core.investigation.pipeline import InvestigationResult
            mock_pipeline_cls = MagicMock()
            mock_pipeline_instance = MagicMock()
            error_result = InvestigationResult(
                investigation_id="inv-test",
                bug_id=sample_bug.id,
                error="Simulated pipeline error for test",
            )
            mock_pipeline_instance.run.return_value = error_result
            mock_pipeline_cls.return_value = mock_pipeline_instance

            orch = WorkflowOrchestrator(
                repository_workspace="/irrelevant",
            )

            import backend.core.workflow.orchestrator as orch_mod
            original = orch_mod.get_repository
            orch_mod.get_repository = lambda name, ws: repo
            # The orchestrator does `from backend.core.investigation import
            # InvestigationPipeline` inside execute(). Patch at the source module.
            try:
                with patch(
                    "backend.core.investigation.InvestigationPipeline",
                    mock_pipeline_cls,
                ):
                    run = orch.execute(bug_id=sample_bug.id, app=app)
            finally:
                orch_mod.get_repository = original

        assert run.status.value == "FAILED"
        assert "pipeline error" in run.error_message.lower()

        with app.app_context():
            bug = db.session.get(Bug, sample_bug.id)
            assert bug.status == BugStatus.FAILED
