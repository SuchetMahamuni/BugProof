"""
Tests for the verification API – Feature 1: Automatic Bug Reproduction.

All repository execution (git, subprocess) is mocked.  No real clone or test
run is performed here.

Coverage:
  - _resolve_bug helper
  - _execute_and_persist helper (PASSED, FAILED, ERROR, exception path)
  - POST /api/verification/run  (validation, full happy-path, fix_id path,
                                  missing IDs, unknown IDs, bad kind, exception)
  - GET  /api/verification/test-runs/<id>
  - GET  /api/verification/test-runs/bug/<bug_id>
  - GET  /api/verification/test-runs/fix/<fix_id>
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    """Flask test app with in-memory SQLite."""
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
def db(app):
    from backend.database.connection import db as _db
    return _db


@pytest.fixture()
def sample_bug(app, db):
    """A persisted Bug record targeting thefuck."""
    from backend.models import Bug, BugStatus, TargetRepository

    with app.app_context():
        bug = Bug(
            title="AttributeError in no_such_command",
            description="NoneType has no attribute group",
            target_repository=TargetRepository.THEFUCK,
            error_message="AttributeError",
            status=BugStatus.RECEIVED,
        )
        db.session.add(bug)
        db.session.commit()
        yield bug


@pytest.fixture()
def sample_fix(app, db, sample_bug):
    """A Fix linked through Investigation → Bug (sample_bug)."""
    from backend.models import (
        Bug, BugStatus, TargetRepository,
        Investigation, InvestigationStatus,
        Fix, ApprovalStatus, ApplicationStatus,
    )

    with app.app_context():
        inv = Investigation(
            bug_id=sample_bug.id,
            status=InvestigationStatus.COMPLETED,
        )
        db.session.add(inv)
        db.session.commit()

        fix = Fix(
            investigation_id=inv.id,
            ai_generated=True,
            approval_status=ApprovalStatus.PENDING,
            application_status=ApplicationStatus.NOT_APPLIED,
        )
        db.session.add(fix)
        db.session.commit()
        yield fix


# ---------------------------------------------------------------------------
# Helper: a fake run_tests result dict
# ---------------------------------------------------------------------------

def _passing_result():
    return {
        "command": "pytest tests/ -v --tb=short",
        "exit_code": 0,
        "output": "3 passed in 1.23s",
        "tests_total": 3,
        "tests_passed": 3,
        "tests_failed": 0,
        "tests_errored": 0,
    }


def _failing_result():
    return {
        "command": "pytest tests/ -v --tb=short",
        "exit_code": 1,
        "output": "1 failed, 2 passed in 1.50s",
        "tests_total": 3,
        "tests_passed": 2,
        "tests_failed": 1,
        "tests_errored": 0,
    }


# ---------------------------------------------------------------------------
# Unit tests for _resolve_bug
# ---------------------------------------------------------------------------

class TestResolveBug:
    """Unit tests for the _resolve_bug helper within an app context."""

    def test_resolve_by_bug_id_found(self, app, sample_bug):
        with app.app_context():
            from backend.api.verification import _resolve_bug
            bug, fix, err = _resolve_bug(sample_bug.id, None)
            assert err is None
            assert bug.id == sample_bug.id
            assert fix is None  # no fix when only bug_id is supplied

    def test_resolve_by_bug_id_not_found(self, app):
        with app.app_context():
            from backend.api.verification import _resolve_bug
            bug, fix, err = _resolve_bug("no-such-id", None)
            assert bug is None
            assert err is not None
            response, code = err
            assert code == 404

    def test_resolve_by_fix_id_found(self, app, sample_fix, sample_bug):
        with app.app_context():
            from backend.api.verification import _resolve_bug
            bug, fix, err = _resolve_bug(None, sample_fix.id)
            assert err is None
            assert bug.id == sample_bug.id
            assert fix is not None
            assert fix.id == sample_fix.id

    def test_resolve_by_fix_id_not_found(self, app):
        with app.app_context():
            from backend.api.verification import _resolve_bug
            bug, fix, err = _resolve_bug(None, "no-such-fix")
            assert bug is None
            response, code = err
            assert code == 404

    def test_resolve_neither_supplied(self, app):
        with app.app_context():
            from backend.api.verification import _resolve_bug
            bug, fix, err = _resolve_bug(None, None)
            assert bug is None
            response, code = err
            assert code == 400

    def test_resolve_both_ids_matching(self, app, sample_bug, sample_fix):
        """Supplying both IDs returns the bug when fix belongs to that bug."""
        with app.app_context():
            from backend.api.verification import _resolve_bug
            bug, fix, err = _resolve_bug(sample_bug.id, sample_fix.id)
            assert err is None
            assert bug.id == sample_bug.id
            assert fix.id == sample_fix.id

    def test_resolve_both_ids_mismatched(self, app, db):
        """Supplying bug_id + a fix that belongs to a different bug returns 422."""
        from backend.models import (
            Bug, BugStatus, TargetRepository,
            Investigation, InvestigationStatus,
            Fix, ApprovalStatus, ApplicationStatus,
        )

        with app.app_context():
            # Create a second, independent bug + fix
            other_bug = Bug(
                title="Other bug",
                description="Different bug",
                target_repository=TargetRepository.THEFUCK,
                status=BugStatus.RECEIVED,
            )
            db.session.add(other_bug)
            db.session.commit()

            other_inv = Investigation(
                bug_id=other_bug.id,
                status=InvestigationStatus.COMPLETED,
            )
            db.session.add(other_inv)
            db.session.commit()

            other_fix = Fix(
                investigation_id=other_inv.id,
                ai_generated=True,
                approval_status=ApprovalStatus.PENDING,
                application_status=ApplicationStatus.NOT_APPLIED,
            )
            db.session.add(other_fix)
            db.session.commit()

            # Create the first bug separately (without sample_bug fixture)
            first_bug = Bug(
                title="First bug",
                description="First",
                target_repository=TargetRepository.TQDM,
                status=BugStatus.RECEIVED,
            )
            db.session.add(first_bug)
            db.session.commit()

            from backend.api.verification import _resolve_bug
            # Pass first_bug.id with other_fix (which belongs to other_bug)
            bug, fix, err = _resolve_bug(first_bug.id, other_fix.id)
            assert bug is None
            assert fix is None
            assert err is not None
            response, code = err
            assert code == 422
            data = response.get_json()
            assert "does not belong" in data["error"]


# ---------------------------------------------------------------------------
# Unit tests for _execute_and_persist
# ---------------------------------------------------------------------------

class TestExecuteAndPersist:
    """Unit tests for the execution helper; repo.run_tests() is always mocked."""

    def _make_test_run(self, app, db, bug):
        from backend.models import TestRun, TestRunKind, TestRunStatus
        with app.app_context():
            tr = TestRun(bug_id=bug.id, kind=TestRunKind.BUG_REPRODUCTION,
                         status=TestRunStatus.PENDING)
            db.session.add(tr)
            db.session.commit()
            return tr

    def test_passed_when_exit_code_zero(self, app, db, sample_bug):
        with app.app_context():
            from backend.models import TestRun, TestRunKind, TestRunStatus
            from backend.api.verification import _execute_and_persist

            tr = TestRun(bug_id=sample_bug.id, kind=TestRunKind.BUG_REPRODUCTION,
                         status=TestRunStatus.PENDING)
            db.session.add(tr)
            db.session.commit()

            mock_repo = MagicMock()
            mock_repo.run_tests.return_value = _passing_result()

            with patch("backend.api.verification.get_repository", return_value=mock_repo):
                _execute_and_persist(tr, sample_bug, ref=None)
                db.session.commit()

            assert tr.status == TestRunStatus.PASSED
            assert tr.exit_code == 0
            assert tr.tests_passed == 3
            assert tr.tests_failed == 0
            assert tr.command == "pytest tests/ -v --tb=short"
            assert tr.started_at is not None
            assert tr.completed_at is not None

    def test_failed_when_exit_code_nonzero(self, app, db, sample_bug):
        with app.app_context():
            from backend.models import TestRun, TestRunKind, TestRunStatus
            from backend.api.verification import _execute_and_persist

            tr = TestRun(bug_id=sample_bug.id, kind=TestRunKind.BUG_REPRODUCTION,
                         status=TestRunStatus.PENDING)
            db.session.add(tr)
            db.session.commit()

            mock_repo = MagicMock()
            mock_repo.run_tests.return_value = _failing_result()

            with patch("backend.api.verification.get_repository", return_value=mock_repo):
                _execute_and_persist(tr, sample_bug, ref=None)
                db.session.commit()

            assert tr.status == TestRunStatus.FAILED
            assert tr.exit_code == 1
            assert tr.tests_failed == 1

    def test_error_when_exit_code_none(self, app, db, sample_bug):
        with app.app_context():
            from backend.models import TestRun, TestRunKind, TestRunStatus
            from backend.api.verification import _execute_and_persist

            tr = TestRun(bug_id=sample_bug.id, kind=TestRunKind.BUG_REPRODUCTION,
                         status=TestRunStatus.PENDING)
            db.session.add(tr)
            db.session.commit()

            mock_repo = MagicMock()
            mock_repo.run_tests.return_value = {
                "command": "pytest tests/",
                "exit_code": None,
                "output": "Command not found: pytest",
                "tests_total": None,
                "tests_passed": None,
                "tests_failed": None,
                "tests_errored": None,
            }

            with patch("backend.api.verification.get_repository", return_value=mock_repo):
                _execute_and_persist(tr, sample_bug, ref=None)
                db.session.commit()

            assert tr.status == TestRunStatus.ERROR

    def test_error_status_on_exception(self, app, db, sample_bug):
        """An exception during execution must set ERROR and never leave RUNNING."""
        with app.app_context():
            from backend.models import TestRun, TestRunKind, TestRunStatus
            from backend.api.verification import _execute_and_persist

            tr = TestRun(bug_id=sample_bug.id, kind=TestRunKind.BUG_REPRODUCTION,
                         status=TestRunStatus.PENDING)
            db.session.add(tr)
            db.session.commit()

            mock_repo = MagicMock()
            mock_repo.checkout.side_effect = RuntimeError("git clone failed")

            with patch("backend.api.verification.get_repository", return_value=mock_repo):
                _execute_and_persist(tr, sample_bug, ref=None)
                db.session.commit()

            assert tr.status == TestRunStatus.ERROR
            assert "git clone failed" in tr.output
            assert tr.completed_at is not None

    def test_ref_is_passed_to_checkout(self, app, db, sample_bug):
        """The ref parameter from the request is forwarded to repo.checkout()."""
        with app.app_context():
            from backend.models import TestRun, TestRunKind, TestRunStatus
            from backend.api.verification import _execute_and_persist

            tr = TestRun(bug_id=sample_bug.id, kind=TestRunKind.BUG_REPRODUCTION,
                         status=TestRunStatus.PENDING)
            db.session.add(tr)
            db.session.commit()

            mock_repo = MagicMock()
            mock_repo.run_tests.return_value = _passing_result()

            with patch("backend.api.verification.get_repository", return_value=mock_repo):
                _execute_and_persist(tr, sample_bug, ref="v3.32")
                db.session.commit()

            mock_repo.checkout.assert_called_once_with("v3.32")

    def test_get_repository_called_with_workspace(self, app, db, sample_bug):
        """get_repository() must receive REPOS_WORKSPACE, not an arbitrary path."""
        with app.app_context():
            from backend.models import TestRun, TestRunKind, TestRunStatus
            from backend.api.verification import _execute_and_persist
            from backend.config.settings import REPOS_WORKSPACE

            tr = TestRun(bug_id=sample_bug.id, kind=TestRunKind.BUG_REPRODUCTION,
                         status=TestRunStatus.PENDING)
            db.session.add(tr)
            db.session.commit()

            mock_repo = MagicMock()
            mock_repo.run_tests.return_value = _passing_result()

            with patch("backend.api.verification.get_repository",
                       return_value=mock_repo) as mock_get_repo:
                _execute_and_persist(tr, sample_bug, ref=None)

            mock_get_repo.assert_called_once_with(
                sample_bug.target_repository, REPOS_WORKSPACE
            )


# ---------------------------------------------------------------------------
# API integration tests for POST /api/verification/run
# ---------------------------------------------------------------------------

class TestTriggerTestRunAPI:
    """End-to-end API tests using the Flask test client and mocked execution."""

    def _post_run(self, client, body):
        return client.post("/api/verification/run", json=body)

    def test_missing_both_ids_returns_400(self, client):
        resp = self._post_run(client, {"kind": "BUG_REPRODUCTION"})
        assert resp.status_code == 400
        assert "bug_id" in resp.get_json()["error"] or "fix_id" in resp.get_json()["error"]

    def test_invalid_kind_returns_400(self, client, sample_bug):
        resp = self._post_run(client, {
            "bug_id": sample_bug.id,
            "kind": "NOT_A_REAL_KIND",
        })
        assert resp.status_code == 400
        assert "Invalid kind" in resp.get_json()["error"]

    def test_unknown_bug_id_returns_404(self, client):
        mock_repo = MagicMock()
        mock_repo.run_tests.return_value = _passing_result()
        with patch("backend.api.verification.get_repository", return_value=mock_repo):
            resp = self._post_run(client, {"bug_id": "does-not-exist"})
        assert resp.status_code == 404

    def test_unknown_fix_id_returns_404(self, client):
        mock_repo = MagicMock()
        mock_repo.run_tests.return_value = _passing_result()
        with patch("backend.api.verification.get_repository", return_value=mock_repo):
            resp = self._post_run(client, {"fix_id": "does-not-exist"})
        assert resp.status_code == 404

    def test_happy_path_by_bug_id_returns_202_passed(self, client, sample_bug):
        mock_repo = MagicMock()
        mock_repo.run_tests.return_value = _passing_result()

        with patch("backend.api.verification.get_repository", return_value=mock_repo):
            resp = self._post_run(client, {
                "bug_id": sample_bug.id,
                "kind": "BUG_REPRODUCTION",
            })

        assert resp.status_code == 202
        data = resp.get_json()
        assert data["status"] == "PASSED"
        assert data["bug_id"] == sample_bug.id
        assert data["exit_code"] == 0
        assert data["tests_passed"] == 3
        assert data["command"] == "pytest tests/ -v --tb=short"
        assert data["started_at"] is not None
        assert data["completed_at"] is not None

    def test_happy_path_failing_tests_returns_202_failed(self, client, sample_bug):
        mock_repo = MagicMock()
        mock_repo.run_tests.return_value = _failing_result()

        with patch("backend.api.verification.get_repository", return_value=mock_repo):
            resp = self._post_run(client, {"bug_id": sample_bug.id})

        assert resp.status_code == 202
        data = resp.get_json()
        assert data["status"] == "FAILED"
        assert data["exit_code"] == 1
        assert data["tests_failed"] == 1

    def test_happy_path_by_fix_id(self, client, sample_fix, sample_bug):
        mock_repo = MagicMock()
        mock_repo.run_tests.return_value = _passing_result()

        with patch("backend.api.verification.get_repository", return_value=mock_repo):
            resp = self._post_run(client, {"fix_id": sample_fix.id})

        assert resp.status_code == 202
        data = resp.get_json()
        assert data["status"] == "PASSED"
        assert data["bug_id"] == sample_bug.id
        assert data["fix_id"] == sample_fix.id

    def test_execution_exception_returns_202_error_status(self, client, sample_bug):
        mock_repo = MagicMock()
        mock_repo.checkout.side_effect = OSError("git not found")

        with patch("backend.api.verification.get_repository", return_value=mock_repo):
            resp = self._post_run(client, {"bug_id": sample_bug.id})

        assert resp.status_code == 202
        data = resp.get_json()
        assert data["status"] == "ERROR"
        assert "git not found" in data["output"]

    def test_default_kind_is_bug_reproduction(self, client, sample_bug):
        """When kind is omitted the record should default to BUG_REPRODUCTION."""
        mock_repo = MagicMock()
        mock_repo.run_tests.return_value = _passing_result()

        with patch("backend.api.verification.get_repository", return_value=mock_repo):
            resp = self._post_run(client, {"bug_id": sample_bug.id})

        assert resp.status_code == 202
        assert resp.get_json()["kind"] == "BUG_REPRODUCTION"

    def test_ref_forwarded_to_checkout(self, client, sample_bug):
        mock_repo = MagicMock()
        mock_repo.run_tests.return_value = _passing_result()

        with patch("backend.api.verification.get_repository", return_value=mock_repo):
            self._post_run(client, {"bug_id": sample_bug.id, "ref": "abc123"})

        mock_repo.checkout.assert_called_once_with("abc123")

    def test_run_persisted_in_database(self, client, app, sample_bug):
        """The TestRun record must survive in the DB after the request."""
        mock_repo = MagicMock()
        mock_repo.run_tests.return_value = _passing_result()

        with patch("backend.api.verification.get_repository", return_value=mock_repo):
            resp = self._post_run(client, {"bug_id": sample_bug.id})

        run_id = resp.get_json()["id"]

        with app.app_context():
            from backend.models import TestRun
            from backend.database.connection import db as _db
            run = _db.session.get(TestRun, run_id)
            assert run is not None
            assert run.status == "PASSED"
            assert run.exit_code == 0


# ---------------------------------------------------------------------------
# API tests for GET endpoints
# ---------------------------------------------------------------------------

class TestGetEndpoints:
    def test_get_test_run_not_found(self, client):
        resp = client.get("/api/verification/test-runs/nonexistent")
        assert resp.status_code == 404

    def test_get_test_run_found(self, client, app, sample_bug):
        mock_repo = MagicMock()
        mock_repo.run_tests.return_value = _passing_result()

        with patch("backend.api.verification.get_repository", return_value=mock_repo):
            post_resp = client.post("/api/verification/run",
                                    json={"bug_id": sample_bug.id})

        run_id = post_resp.get_json()["id"]
        get_resp = client.get(f"/api/verification/test-runs/{run_id}")
        assert get_resp.status_code == 200
        assert get_resp.get_json()["id"] == run_id

    def test_list_runs_for_bug_empty(self, client):
        resp = client.get("/api/verification/test-runs/bug/no-such-bug")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_list_runs_for_bug_with_results(self, client, sample_bug):
        mock_repo = MagicMock()
        mock_repo.run_tests.return_value = _passing_result()

        with patch("backend.api.verification.get_repository", return_value=mock_repo):
            client.post("/api/verification/run", json={"bug_id": sample_bug.id})
            client.post("/api/verification/run", json={"bug_id": sample_bug.id})

        resp = client.get(f"/api/verification/test-runs/bug/{sample_bug.id}")
        assert resp.status_code == 200
        assert len(resp.get_json()) == 2

    def test_list_runs_for_fix(self, client, sample_fix, sample_bug):
        mock_repo = MagicMock()
        mock_repo.run_tests.return_value = _passing_result()

        with patch("backend.api.verification.get_repository", return_value=mock_repo):
            client.post("/api/verification/run", json={"fix_id": sample_fix.id})

        resp = client.get(f"/api/verification/test-runs/fix/{sample_fix.id}")
        assert resp.status_code == 200
        runs = resp.get_json()
        assert len(runs) == 1
        assert runs[0]["fix_id"] == sample_fix.id


# ---------------------------------------------------------------------------
# API tests for mismatched bug_id + fix_id
# ---------------------------------------------------------------------------

class TestMismatchedBugFixIds:
    """Verify that supplying a fix_id that belongs to a different bug is rejected."""

    def _create_separate_bug_and_fix(self, app, db):
        """Create an independent bug → investigation → fix, unrelated to fixtures."""
        from backend.models import (
            Bug, BugStatus, TargetRepository,
            Investigation, InvestigationStatus,
            Fix, ApprovalStatus, ApplicationStatus,
        )
        with app.app_context():
            bug = Bug(
                title="Independent bug",
                description="Unrelated bug",
                target_repository=TargetRepository.THEFUCK,
                status=BugStatus.RECEIVED,
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
                approval_status=ApprovalStatus.PENDING,
                application_status=ApplicationStatus.NOT_APPLIED,
            )
            db.session.add(fix)
            db.session.commit()
            return bug.id, fix.id

    def test_mismatched_ids_returns_422(self, client, app, db, sample_bug):
        """
        POST /run with a fix_id that belongs to a different bug must return 422
        and must NOT create a TestRun record.
        """
        other_bug_id, other_fix_id = self._create_separate_bug_and_fix(app, db)

        mock_repo = MagicMock()
        mock_repo.run_tests.return_value = _passing_result()

        with patch("backend.api.verification.get_repository", return_value=mock_repo):
            resp = client.post("/api/verification/run", json={
                "bug_id": sample_bug.id,   # sample_bug's id
                "fix_id": other_fix_id,    # fix from other_bug – mismatch!
            })

        assert resp.status_code == 422
        data = resp.get_json()
        assert "does not belong" in data["error"]
        # The repo must NOT have been called
        mock_repo.run_tests.assert_not_called()
        mock_repo.checkout.assert_not_called()

    def test_mismatched_ids_no_test_run_created(self, client, app, db, sample_bug):
        """No TestRun record is created when IDs are mismatched."""
        from backend.models import TestRun
        from backend.database.connection import db as _db

        other_bug_id, other_fix_id = self._create_separate_bug_and_fix(app, db)

        with patch("backend.api.verification.get_repository", return_value=MagicMock()):
            client.post("/api/verification/run", json={
                "bug_id": sample_bug.id,
                "fix_id": other_fix_id,
            })

        with app.app_context():
            runs = _db.session.execute(
                _db.select(TestRun).where(TestRun.bug_id == sample_bug.id)
            ).scalars().all()
            assert len(runs) == 0, "No TestRun should be created on mismatch"

    def test_matching_both_ids_succeeds(self, client, app, db, sample_bug, sample_fix):
        """
        POST /run with matching bug_id and fix_id for the same bug succeeds.
        """
        mock_repo = MagicMock()
        mock_repo.run_tests.return_value = _passing_result()

        with patch("backend.api.verification.get_repository", return_value=mock_repo):
            resp = client.post("/api/verification/run", json={
                "bug_id": sample_bug.id,
                "fix_id": sample_fix.id,
            })

        assert resp.status_code == 202
        data = resp.get_json()
        assert data["bug_id"] == sample_bug.id
        assert data["fix_id"] == sample_fix.id
        assert data["status"] == "PASSED"
