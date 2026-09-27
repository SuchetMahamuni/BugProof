"""
Tests for the Report generation API (Step 5).

Coverage:
  - POST /api/reports/bug/<bug_id>/generate  (new report = 201)
  - POST /api/reports/bug/<bug_id>/generate  (regenerate = 200 upsert)
  - GET  /api/reports/<report_id>
  - GET  /api/reports/bug/<bug_id>
  - Report not found → 404
  - Bug not found → 404
  - _compute_confidence: machine-verified from PASSED regression run
  - _compute_confidence: low from FAILED regression run
  - _compute_confidence: AI fallback when no test runs
  - _build_report_content: sections present, AI labels correct
  - _build_summary: text includes status and cause
  - Limitations appear for bugs with no investigation
  - Limitations appear for AI-generated fixes
  - Evidence is labelled correctly (is_verified flag)
"""

import os
import sys
from datetime import datetime, timezone

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
            title="Report test bug",
            description="Test bug for report generation",
            target_repository=TargetRepository.THEFUCK,
            error_message="AttributeError: group",
            status=BugStatus.WAITING_FOR_APPROVAL,
        )
        db.session.add(bug)
        db.session.commit()
        yield bug


@pytest.fixture()
def full_pipeline_bug(app, db):
    """A bug with investigation, fix, evidence, and test run."""
    from backend.models import (
        Bug, BugStatus, TargetRepository,
        Investigation, InvestigationStatus,
        Evidence, EvidenceType,
        Fix, ApprovalStatus, ApplicationStatus,
        TestRun, TestRunKind, TestRunStatus,
    )
    with app.app_context():
        bug = Bug(
            title="Full pipeline bug",
            description="Complete pipeline test",
            target_repository=TargetRepository.THEFUCK,
            error_message="AttributeError",
            status=BugStatus.FIX_APPLIED,
        )
        db.session.add(bug)
        db.session.commit()

        inv = Investigation(
            bug_id=bug.id,
            status=InvestigationStatus.COMPLETED,
            summary="Machine investigation summary",
            suspected_cause="[AI NOT CONFIGURED] No AI analysis available.",
            root_cause_explanation="[AI NOT CONFIGURED] Set env vars.",
            confidence=0.0,
            relevant_files=["thefuck/rules/no_such.py"],
            root_cause_ai_generated=True,
            root_cause_machine_supported=False,
        )
        db.session.add(inv)
        db.session.commit()

        ev = Evidence(
            investigation_id=inv.id,
            evidence_type=EvidenceType.SOURCE_CODE,
            description="Found at line 42",
            file_path="thefuck/rules/no_such.py",
            line_number=42,
            is_verified=True,
        )
        db.session.add(ev)
        db.session.commit()

        fix = Fix(
            investigation_id=inv.id,
            ai_generated=True,
            explanation="Add None check",
            patch="--- a/thefuck/rules/no_such.py\n+++ b/thefuck/rules/no_such.py\n",
            files_changed=["thefuck/rules/no_such.py"],
            approval_status=ApprovalStatus.APPROVED,
            application_status=ApplicationStatus.APPLIED,
            confidence=0.75,
        )
        db.session.add(fix)
        db.session.commit()

        tr = TestRun(
            bug_id=bug.id,
            fix_id=fix.id,
            kind=TestRunKind.REGRESSION,
            status=TestRunStatus.PASSED,
            exit_code=0,
            tests_total=5,
            tests_passed=5,
            tests_failed=0,
            tests_errored=0,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )
        db.session.add(tr)
        db.session.commit()

        yield bug


# ---------------------------------------------------------------------------
# Tests: basic CRUD and 404s
# ---------------------------------------------------------------------------

class TestReportCRUD:
    def test_get_report_not_found(self, client):
        resp = client.get("/api/reports/no-such-id")
        assert resp.status_code == 404

    def test_generate_bug_not_found(self, client):
        resp = client.post("/api/reports/bug/no-such-bug/generate")
        assert resp.status_code == 404

    def test_list_reports_empty(self, client, sample_bug):
        resp = client.get(f"/api/reports/bug/{sample_bug.id}")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_generate_creates_new_report_201(self, client, sample_bug):
        resp = client.post(f"/api/reports/bug/{sample_bug.id}/generate")
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["bug_id"] == sample_bug.id
        assert data["id"] is not None
        assert data["content"] is not None

    def test_generate_twice_updates_existing_report_200(self, client, sample_bug):
        client.post(f"/api/reports/bug/{sample_bug.id}/generate")
        resp2 = client.post(f"/api/reports/bug/{sample_bug.id}/generate")
        assert resp2.status_code == 200  # upsert

    def test_generate_then_get_by_id(self, client, sample_bug):
        gen_resp = client.post(f"/api/reports/bug/{sample_bug.id}/generate")
        report_id = gen_resp.get_json()["id"]

        get_resp = client.get(f"/api/reports/{report_id}")
        assert get_resp.status_code == 200
        assert get_resp.get_json()["id"] == report_id

    def test_list_reports_after_generate(self, client, sample_bug):
        client.post(f"/api/reports/bug/{sample_bug.id}/generate")
        resp = client.get(f"/api/reports/bug/{sample_bug.id}")
        assert resp.status_code == 200
        assert len(resp.get_json()) >= 1


# ---------------------------------------------------------------------------
# Tests: report content structure
# ---------------------------------------------------------------------------

class TestReportContent:
    def test_content_has_required_sections(self, client, full_pipeline_bug):
        resp = client.post(f"/api/reports/bug/{full_pipeline_bug.id}/generate")
        data = resp.get_json()
        content = data["content"]
        assert "bug" in content
        assert "investigation" in content
        assert "evidence" in content
        assert "fix" in content
        assert "test_runs" in content
        assert "limitations" in content

    def test_content_bug_section_machine_verified(self, client, full_pipeline_bug):
        resp = client.post(f"/api/reports/bug/{full_pipeline_bug.id}/generate")
        content = resp.get_json()["content"]
        assert content["bug"]["_machine_verified"] is True

    def test_content_investigation_ai_labelled(self, client, full_pipeline_bug):
        resp = client.post(f"/api/reports/bug/{full_pipeline_bug.id}/generate")
        content = resp.get_json()["content"]
        assert content["investigation"]["_ai_generated"] is True

    def test_content_fix_ai_labelled(self, client, full_pipeline_bug):
        resp = client.post(f"/api/reports/bug/{full_pipeline_bug.id}/generate")
        content = resp.get_json()["content"]
        assert content["fix"]["_ai_generated"] is True

    def test_content_test_runs_machine_verified(self, client, full_pipeline_bug):
        resp = client.post(f"/api/reports/bug/{full_pipeline_bug.id}/generate")
        content = resp.get_json()["content"]
        assert content["test_runs"]["total"] >= 1
        if content["test_runs"]["runs"]:
            assert content["test_runs"]["runs"][0]["_machine_verified"] is True

    def test_content_evidence_counts(self, client, full_pipeline_bug):
        resp = client.post(f"/api/reports/bug/{full_pipeline_bug.id}/generate")
        content = resp.get_json()["content"]
        assert content["evidence"]["total"] >= 1
        assert content["evidence"]["machine_verified_count"] >= 1

    def test_content_has_ai_label_note(self, client, full_pipeline_bug):
        resp = client.post(f"/api/reports/bug/{full_pipeline_bug.id}/generate")
        content = resp.get_json()["content"]
        assert "ai_label_note" in content
        assert "AI" in content["ai_label_note"]

    def test_no_investigation_adds_limitation(self, client, sample_bug):
        resp = client.post(f"/api/reports/bug/{sample_bug.id}/generate")
        content = resp.get_json()["content"]
        lims = content["limitations"]
        assert any("No investigation" in lim for lim in lims)

    def test_ai_fix_adds_limitation(self, client, full_pipeline_bug):
        resp = client.post(f"/api/reports/bug/{full_pipeline_bug.id}/generate")
        content = resp.get_json()["content"]
        lims = content["limitations"]
        # AI-generated root cause should appear as limitation
        assert any("AI" in lim for lim in lims), f"Expected AI limitation; got {lims}"


# ---------------------------------------------------------------------------
# Tests: confidence computation
# ---------------------------------------------------------------------------

class TestComputeConfidence:
    def _compute(self, investigation=None, fix=None, test_runs=None):
        from backend.api.reports import _compute_confidence
        return _compute_confidence(investigation, fix, test_runs or [])

    def test_passed_regression_run_gives_full_machine_confidence(self, app):
        with app.app_context():
            from backend.models import TestRun, TestRunKind, TestRunStatus
            from backend.database.connection import db
            from backend.models import Bug, TargetRepository, BugStatus
            bug = Bug(
                title="conf test", description="x",
                target_repository=TargetRepository.THEFUCK,
                status=BugStatus.RECEIVED,
            )
            db.session.add(bug)
            db.session.commit()
            tr = TestRun(
                bug_id=bug.id,
                kind=TestRunKind.REGRESSION,
                status=TestRunStatus.PASSED,
            )
            db.session.add(tr)
            db.session.commit()

            conf, machine = self._compute(test_runs=[tr])
            assert conf == 1.0
            assert machine is True

    def test_failed_regression_run_gives_low_machine_confidence(self, app):
        with app.app_context():
            from backend.models import TestRun, TestRunKind, TestRunStatus
            from backend.database.connection import db
            from backend.models import Bug, TargetRepository, BugStatus
            bug = Bug(
                title="conf test 2", description="x",
                target_repository=TargetRepository.THEFUCK,
                status=BugStatus.RECEIVED,
            )
            db.session.add(bug)
            db.session.commit()
            tr = TestRun(
                bug_id=bug.id,
                kind=TestRunKind.REGRESSION,
                status=TestRunStatus.FAILED,
            )
            db.session.add(tr)
            db.session.commit()

            conf, machine = self._compute(test_runs=[tr])
            assert conf < 0.5
            assert machine is True

    def test_no_test_runs_uses_fix_confidence(self, app):
        with app.app_context():
            from backend.models import (
                Bug, TargetRepository, BugStatus,
                Investigation, InvestigationStatus,
                Fix, ApprovalStatus, ApplicationStatus,
            )
            from backend.database.connection import db
            bug = Bug(
                title="conf test 3", description="x",
                target_repository=TargetRepository.THEFUCK,
                status=BugStatus.RECEIVED,
            )
            db.session.add(bug)
            db.session.commit()
            inv = Investigation(
                bug_id=bug.id, status=InvestigationStatus.COMPLETED
            )
            db.session.add(inv)
            db.session.commit()
            fix = Fix(
                investigation_id=inv.id,
                ai_generated=True,
                approval_status=ApprovalStatus.PENDING,
                application_status=ApplicationStatus.NOT_APPLIED,
                confidence=0.6,
            )
            db.session.add(fix)
            db.session.commit()

            conf, machine = self._compute(fix=fix, test_runs=[])
            assert conf == 0.6
            assert machine is False

    def test_no_data_returns_zero_not_verified(self):
        conf, machine = self._compute()
        assert conf == 0.0
        assert machine is False


# ---------------------------------------------------------------------------
# Tests: summary text
# ---------------------------------------------------------------------------

class TestBuildSummary:
    def _summary(self, bug, investigation=None, fix=None, test_runs=None):
        from backend.api.reports import _build_summary
        return _build_summary(bug, investigation, fix, test_runs or [])

    def test_summary_includes_bug_title(self, app, sample_bug):
        with app.app_context():
            summary = self._summary(sample_bug)
            assert "Report test bug" in summary

    def test_summary_includes_status(self, app, sample_bug):
        with app.app_context():
            summary = self._summary(sample_bug)
            assert "WAITING_FOR_APPROVAL" in summary

    def test_summary_includes_suspected_cause(self, app, full_pipeline_bug):
        with app.app_context():
            from backend.models import Investigation
            from backend.database.connection import db
            invs = db.session.execute(
                db.select(Investigation).where(Investigation.bug_id == full_pipeline_bug.id)
            ).scalars().all()
            inv = invs[0] if invs else None
            summary = self._summary(full_pipeline_bug, investigation=inv)
            assert "AI-generated" in summary  # root_cause_ai_generated=True

    def test_summary_includes_no_test_runs_note(self, app, sample_bug):
        with app.app_context():
            summary = self._summary(sample_bug)
            assert "No verification" in summary
