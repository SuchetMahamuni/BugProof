"""
Reports API.

Endpoints for retrieving and generating debugging session reports.

A report aggregates:
  - Bug and investigation context
  - Root-cause findings and supporting evidence
  - Proposed/applied fix and approval status
  - Verification and regression test outcomes
  - Change impact findings
  - Confidence scores and AI/machine-verified labels
  - Errors, missing information, and limitations

The report generation is performed by POST /api/reports/bug/<bug_id>/generate,
which reads all existing DB records for the bug and synthesises a structured
Report record.  It never invents evidence or labels AI content as verified.
"""

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from backend.database.connection import db
from backend.models import Bug, Report

reports_bp = Blueprint("reports", __name__, url_prefix="/api/reports")


@reports_bp.route("/<report_id>", methods=["GET"])
def get_report(report_id: str):
    """
    Retrieve a single report by ID.

    Returns 200 with the report dict, or 404.
    """
    report = db.session.get(Report, report_id)
    if report is None:
        return jsonify({"error": f"Report '{report_id}' not found."}), 404
    return jsonify(report.to_dict()), 200


@reports_bp.route("/bug/<bug_id>", methods=["GET"])
def list_reports_for_bug(bug_id: str):
    """
    List all reports for a given bug.

    Returns 200 with a list of report dicts.
    """
    query = (
        db.select(Report)
        .where(Report.bug_id == bug_id)
        .order_by(Report.created_at.desc())
    )
    reports = db.session.execute(query).scalars().all()
    return jsonify([r.to_dict() for r in reports]), 200


@reports_bp.route("/bug/<bug_id>/generate", methods=["POST"])
def generate_report(bug_id: str):
    """
    Generate (or regenerate) a structured debugging report for a bug.

    Reads all existing DB records for the bug and synthesises a Report.
    If a Report already exists for this bug, it is updated in place.

    The report content is machine-synthesised from persisted records.
    AI-generated fields are explicitly labelled in the content JSON.
    No AI call is made; the report reflects only what is already stored.

    Returns 201 (new) or 200 (updated) with the report dict.
    Returns 404 if the bug does not exist.
    """
    bug = db.session.get(Bug, bug_id)
    if bug is None:
        return jsonify({"error": f"Bug '{bug_id}' not found."}), 404

    from backend.models import Investigation, Fix, TestRun, Evidence

    # --- Gather all relevant records ---
    investigations = db.session.execute(
        db.select(Investigation).where(Investigation.bug_id == bug_id)
        .order_by(Investigation.created_at.desc())
    ).scalars().all()

    latest_inv = investigations[0] if investigations else None

    # Fixes via latest investigation
    fixes = []
    evidence_items = []
    if latest_inv:
        fixes = db.session.execute(
            db.select(Fix).where(Fix.investigation_id == latest_inv.id)
            .order_by(Fix.created_at.desc())
        ).scalars().all()

        evidence_items = db.session.execute(
            db.select(Evidence).where(Evidence.investigation_id == latest_inv.id)
        ).scalars().all()

    latest_fix = fixes[0] if fixes else None

    # Test runs for this bug
    test_runs = db.session.execute(
        db.select(TestRun).where(TestRun.bug_id == bug_id)
        .order_by(TestRun.created_at.desc())
    ).scalars().all()

    # --- Compute fix_confidence ---
    # Confidence is derived from machine-verified test runs where possible.
    fix_confidence, confidence_machine_verified = _compute_confidence(
        latest_inv, latest_fix, test_runs
    )

    # --- Build structured content ---
    content = _build_report_content(
        bug=bug,
        investigation=latest_inv,
        fixes=fixes,
        evidence_items=evidence_items,
        test_runs=test_runs,
    )

    # --- Build summary paragraph ---
    summary = _build_summary(bug, latest_inv, latest_fix, test_runs)

    # --- Upsert the Report record ---
    existing_reports = db.session.execute(
        db.select(Report).where(Report.bug_id == bug_id)
        .order_by(Report.created_at.desc())
    ).scalars().all()

    now = datetime.now(timezone.utc)

    if existing_reports:
        report = existing_reports[0]
        report.fix_confidence = fix_confidence
        report.confidence_machine_verified = confidence_machine_verified
        report.content = content
        report.summary = summary
        report.generated_at = now
        db.session.commit()
        status_code = 200
    else:
        report = Report(
            bug_id=bug_id,
            fix_confidence=fix_confidence,
            confidence_machine_verified=confidence_machine_verified,
            content=content,
            summary=summary,
            generated_at=now,
        )
        db.session.add(report)
        db.session.commit()
        status_code = 201

    return jsonify(report.to_dict()), status_code


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _compute_confidence(
    investigation, fix, test_runs: list
) -> tuple:
    """
    Return (fix_confidence, confidence_machine_verified).

    Machine-verified confidence requires at least one PASSED TestRun of kind
    REGRESSION or GENERATED_REGRESSION.  AI confidence comes from the fix
    or investigation record.  Returns (0.0, False) when no evidence exists.
    """
    from backend.models import TestRunStatus, TestRunKind

    # Check for machine-verified test runs (PASSED regression tests)
    regression_passed = [
        tr for tr in test_runs
        if tr.status == TestRunStatus.PASSED
        and tr.kind in (TestRunKind.REGRESSION, TestRunKind.GENERATED_REGRESSION)
    ]
    if regression_passed:
        # Full confidence if regression tests pass
        return 1.0, True

    regression_failed = [
        tr for tr in test_runs
        if tr.status == TestRunStatus.FAILED
        and tr.kind in (TestRunKind.REGRESSION, TestRunKind.GENERATED_REGRESSION)
    ]
    if regression_failed:
        # Failed regression tests – low confidence
        return 0.1, True

    # Fall back to AI-reported confidence from fix or investigation
    if fix and fix.confidence is not None:
        return fix.confidence, False
    if investigation and investigation.confidence is not None:
        return investigation.confidence, False

    return 0.0, False


def _build_report_content(
    bug, investigation, fixes: list, evidence_items: list, test_runs: list
) -> dict:
    """
    Build the structured content dict for a Report.

    All AI-generated fields are labelled with ``_ai_generated: true``.
    All machine-verified fields are labelled with ``_machine_verified: true``.
    """
    from backend.models import TestRunStatus, TestRunKind, ApprovalStatus, ApplicationStatus

    # Bug section
    bug_section = {
        "id": bug.id,
        "title": bug.title,
        "description": bug.description,
        "target_repository": bug.target_repository,
        "error_message": bug.error_message,
        "status": bug.status,
        "_machine_verified": True,
    }

    # Investigation section
    inv_section = None
    if investigation:
        inv_section = {
            "id": investigation.id,
            "status": investigation.status,
            "summary": investigation.summary,
            "suspected_cause": investigation.suspected_cause,
            "root_cause_explanation": investigation.root_cause_explanation,
            "confidence": investigation.confidence,
            "relevant_files": investigation.relevant_files or [],
            "relevant_functions": investigation.relevant_functions or [],
            "root_cause_ai_generated": investigation.root_cause_ai_generated,
            "root_cause_machine_supported": investigation.root_cause_machine_supported,
            "error_details": investigation.error_details,
            "_ai_generated": investigation.root_cause_ai_generated,
            "_machine_verified": investigation.root_cause_machine_supported,
        }

    # Evidence section
    verified_evidence = [e for e in evidence_items if e.is_verified]
    ai_evidence = [e for e in evidence_items if not e.is_verified]

    evidence_section = {
        "total": len(evidence_items),
        "machine_verified_count": len(verified_evidence),
        "ai_generated_count": len(ai_evidence),
        "items": [
            {
                "id": e.id,
                "type": e.evidence_type,
                "description": e.description,
                "file_path": e.file_path,
                "line_number": e.line_number,
                "is_verified": e.is_verified,
            }
            for e in evidence_items[:20]  # Cap for report readability
        ],
    }

    # Fix section
    fix_section = None
    if fixes:
        f = fixes[0]
        risk_info = f.risk_info or {}
        fix_section = {
            "id": f.id,
            "explanation": f.explanation,
            "patch_available": bool(f.patch),
            "files_changed": f.files_changed or [],
            "approval_status": f.approval_status,
            "approved_by": f.approved_by,
            "application_status": f.application_status,
            "confidence": f.confidence,
            "risk_info": risk_info,
            "ai_generated": f.ai_generated,
            # Regression test metadata (stored in risk_info)
            "regression_tests_generated": risk_info.get("regression_tests_generated"),
            "regression_tests_validated": risk_info.get("regression_tests_validated"),
            "regression_generation_ai": risk_info.get("regression_generation_ai"),
            # Impact analysis metadata
            "impact_risk_level": risk_info.get("impact_risk_level"),
            "impact_changed_files": risk_info.get("impact_changed_files"),
            "impact_affected_areas_count": risk_info.get("impact_affected_areas_count"),
            "impact_related_tests": risk_info.get("impact_related_tests"),
            "_ai_generated": f.ai_generated,
        }

    # Test runs section
    def _tr_summary(tr) -> dict:
        return {
            "id": tr.id,
            "kind": tr.kind,
            "status": tr.status,
            "exit_code": tr.exit_code,
            "tests_total": tr.tests_total,
            "tests_passed": tr.tests_passed,
            "tests_failed": tr.tests_failed,
            "started_at": tr.started_at.isoformat() if tr.started_at else None,
            "completed_at": tr.completed_at.isoformat() if tr.completed_at else None,
            "_machine_verified": True,
        }

    test_run_section = {
        "total": len(test_runs),
        "passed": sum(1 for tr in test_runs if tr.status == TestRunStatus.PASSED),
        "failed": sum(1 for tr in test_runs if tr.status == TestRunStatus.FAILED),
        "error": sum(1 for tr in test_runs if tr.status == TestRunStatus.ERROR),
        "runs": [_tr_summary(tr) for tr in test_runs[:10]],
    }

    # Limitations / notes section
    limitations = []
    if not investigation:
        limitations.append("No investigation has been run for this bug.")
    if investigation and investigation.root_cause_ai_generated and not investigation.root_cause_machine_supported:
        limitations.append(
            "Root cause analysis is AI-generated and has not been confirmed by "
            "machine-executed tests."
        )
    if not fixes:
        limitations.append("No fix has been generated for this bug.")
    if fixes and fixes[0].ai_generated:
        limitations.append(
            "The proposed fix is AI-generated and requires developer review and "
            "approval before application."
        )
    if not test_runs:
        limitations.append(
            "No verification test runs have been performed. "
            "Run a regression test to confirm the fix."
        )

    return {
        "bug": bug_section,
        "investigation": inv_section,
        "evidence": evidence_section,
        "fix": fix_section,
        "test_runs": test_run_section,
        "limitations": limitations,
        "generated_by": "BugProof report generator (machine-synthesised from stored records)",
        "ai_label_note": (
            "Fields labelled _ai_generated=true contain AI-generated content "
            "that has NOT been machine-verified unless also labelled "
            "_machine_verified=true."
        ),
    }


def _build_summary(bug, investigation, fix, test_runs: list) -> str:
    """Build a concise text summary paragraph for the report."""
    from backend.models import TestRunStatus, ApprovalStatus, ApplicationStatus

    parts = [f"Bug '{bug.title}' targeting {bug.target_repository}. Status: {bug.status}."]

    if investigation:
        if investigation.suspected_cause:
            # Note if AI-generated
            label = " (AI-generated)" if investigation.root_cause_ai_generated else ""
            parts.append(
                f"Suspected cause{label}: {investigation.suspected_cause[:200]}"
            )
        if investigation.confidence is not None:
            parts.append(f"AI confidence: {investigation.confidence:.0%}.")

    if fix:
        parts.append(
            f"Fix: approval_status={fix.approval_status}, "
            f"application_status={fix.application_status}."
        )

    passed_runs = [tr for tr in test_runs if tr.status == TestRunStatus.PASSED]
    failed_runs = [tr for tr in test_runs if tr.status == TestRunStatus.FAILED]
    if passed_runs:
        parts.append(f"{len(passed_runs)} test run(s) passed.")
    if failed_runs:
        parts.append(f"{len(failed_runs)} test run(s) failed.")
    if not test_runs:
        parts.append("No verification test runs performed.")

    return " ".join(parts)
