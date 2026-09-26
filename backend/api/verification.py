"""
Verification API.

Integration point for Team Member 2's verification work.

This module defines the API contract for:
  - Retrieving test run metadata
  - Triggering test runs (once Member 2 implements the logic)

The actual test execution and regression-test generation logic
is owned by Team Member 2 and will be connected here.
"""

from flask import Blueprint, jsonify, request
from backend.database.connection import db
from backend.models import TestRun, TestRunKind, TestRunStatus

verification_bp = Blueprint("verification", __name__, url_prefix="/api/verification")


@verification_bp.route("/test-runs/<test_run_id>", methods=["GET"])
def get_test_run(test_run_id: str):
    """
    Retrieve a single test run by ID.

    Returns 200 with the test run dict, or 404.
    """
    run = db.session.get(TestRun, test_run_id)
    if run is None:
        return jsonify({"error": f"TestRun '{test_run_id}' not found."}), 404
    return jsonify(run.to_dict()), 200


@verification_bp.route("/test-runs/fix/<fix_id>", methods=["GET"])
def list_test_runs_for_fix(fix_id: str):
    """
    List all test runs associated with a fix.

    Returns 200 with a list of test run dicts.
    """
    query = (
        db.select(TestRun)
        .where(TestRun.fix_id == fix_id)
        .order_by(TestRun.created_at.desc())
    )
    runs = db.session.execute(query).scalars().all()
    return jsonify([r.to_dict() for r in runs]), 200


@verification_bp.route("/test-runs/bug/<bug_id>", methods=["GET"])
def list_test_runs_for_bug(bug_id: str):
    """
    List all test runs associated with a bug (e.g., reproduction runs).

    Returns 200 with a list of test run dicts.
    """
    query = (
        db.select(TestRun)
        .where(TestRun.bug_id == bug_id)
        .order_by(TestRun.created_at.desc())
    )
    runs = db.session.execute(query).scalars().all()
    return jsonify([r.to_dict() for r in runs]), 200


@verification_bp.route("/run", methods=["POST"])
def trigger_test_run():
    """
    Trigger a verification test run.

    Integration point for Team Member 2.
    This endpoint accepts a test run request and creates a TestRun record.
    The actual test execution logic must be connected by Team Member 2.

    Request body (JSON):
      - fix_id (str, optional)
      - bug_id (str, optional)
      - kind (str): TestRunKind value

    Returns 202 with the created TestRun record.
    """
    data = request.get_json(silent=True) or {}

    kind = data.get("kind", TestRunKind.REGRESSION)
    valid_kinds = [k.value for k in TestRunKind]
    if kind not in valid_kinds:
        return jsonify(
            {"error": f"Invalid kind '{kind}'. Valid: {valid_kinds}"}
        ), 400

    test_run = TestRun(
        fix_id=data.get("fix_id"),
        bug_id=data.get("bug_id"),
        kind=kind,
        status=TestRunStatus.PENDING,
    )
    db.session.add(test_run)
    db.session.commit()

    # Integration point: Team Member 2 connects the execution logic here.
    return jsonify({
        "message": "Test run created. Execution logic to be connected by Team Member 2.",
        "test_run": test_run.to_dict(),
    }), 202
