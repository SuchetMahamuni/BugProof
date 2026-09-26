"""
Fixes API.

Endpoints for retrieving fix proposals and managing the approval workflow.

The approval gate is enforced here:
  - Fixes start as PENDING.
  - Only an approved fix can proceed to application.
  - Application is a separate, explicit action.
"""

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from backend.database.connection import db
from backend.models import Fix, ApprovalStatus, ApplicationStatus, Bug, BugStatus

fixes_bp = Blueprint("fixes", __name__, url_prefix="/api/fixes")


@fixes_bp.route("/<fix_id>", methods=["GET"])
def get_fix(fix_id: str):
    """
    Retrieve a single fix proposal by ID.

    Returns 200 with the fix dict, or 404.
    """
    fix = db.session.get(Fix, fix_id)
    if fix is None:
        return jsonify({"error": f"Fix '{fix_id}' not found."}), 404
    return jsonify(fix.to_dict()), 200


@fixes_bp.route("/investigation/<investigation_id>", methods=["GET"])
def list_fixes_for_investigation(investigation_id: str):
    """
    List all fix proposals for a given investigation.

    Returns 200 with a list of fix dicts.
    """
    query = (
        db.select(Fix)
        .where(Fix.investigation_id == investigation_id)
        .order_by(Fix.created_at.desc())
    )
    fixes = db.session.execute(query).scalars().all()
    return jsonify([f.to_dict() for f in fixes]), 200


@fixes_bp.route("/<fix_id>/approve", methods=["POST"])
def approve_fix(fix_id: str):
    """
    Approve a fix proposal.

    Request body (JSON):
      - approved_by (str, optional): identifier of the approver

    Transitions the fix from PENDING to APPROVED.
    Returns 200 with the updated fix, or 409 if not in PENDING state.
    """
    fix = db.session.get(Fix, fix_id)
    if fix is None:
        return jsonify({"error": f"Fix '{fix_id}' not found."}), 404

    if fix.approval_status != ApprovalStatus.PENDING:
        return jsonify(
            {"error": f"Fix is in '{fix.approval_status}' state, not PENDING."}
        ), 409

    data = request.get_json(silent=True) or {}
    fix.approval_status = ApprovalStatus.APPROVED
    fix.approved_by = data.get("approved_by")
    fix.approved_at = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify(fix.to_dict()), 200


@fixes_bp.route("/<fix_id>/reject", methods=["POST"])
def reject_fix(fix_id: str):
    """
    Reject a fix proposal.

    Request body (JSON):
      - rejection_reason (str, optional)

    Transitions the fix from PENDING to REJECTED.
    Returns 200 with the updated fix, or 409 if not in PENDING state.
    """
    fix = db.session.get(Fix, fix_id)
    if fix is None:
        return jsonify({"error": f"Fix '{fix_id}' not found."}), 404

    if fix.approval_status != ApprovalStatus.PENDING:
        return jsonify(
            {"error": f"Fix is in '{fix.approval_status}' state, not PENDING."}
        ), 409

    data = request.get_json(silent=True) or {}
    fix.approval_status = ApprovalStatus.REJECTED
    fix.rejection_reason = data.get("rejection_reason")
    db.session.commit()

    return jsonify(fix.to_dict()), 200


@fixes_bp.route("/<fix_id>/apply", methods=["POST"])
def apply_fix(fix_id: str):
    """
    Apply an approved fix to the repository.

    The fix must be in APPROVED state.  Application uses git apply on the
    stored patch against the checked-out repository.

    Returns 200 on success, 409 if not approved, 422 if patch fails.

    Note: Full application logic (patch, sandbox, etc.) is established here.
    Team Member 2's verification workflow connects after application.
    """
    fix = db.session.get(Fix, fix_id)
    if fix is None:
        return jsonify({"error": f"Fix '{fix_id}' not found."}), 404

    if fix.approval_status != ApprovalStatus.APPROVED:
        return jsonify(
            {"error": "Fix must be APPROVED before it can be applied."}
        ), 409

    if fix.application_status == ApplicationStatus.APPLIED:
        return jsonify({"error": "Fix has already been applied."}), 409

    if not fix.patch:
        return jsonify({"error": "Fix has no patch to apply."}), 422

    from backend.core.fix_generation import FixGenerator
    from backend.models import Investigation, Bug
    from backend.repositories import get_repository
    from backend.config.settings import REPOS_WORKSPACE

    investigation = db.session.get(Investigation, fix.investigation_id)
    if investigation is None:
        return jsonify({"error": "Linked investigation not found."}), 404

    bug = db.session.get(Bug, investigation.bug_id)
    if bug is None:
        return jsonify({"error": "Linked bug not found."}), 404

    repo = get_repository(bug.target_repository, REPOS_WORKSPACE)
    generator = FixGenerator()
    validation = generator.validate_patch(fix.patch, repo.repository_path)

    if not validation["valid"]:
        fix.application_status = ApplicationStatus.FAILED
        fix.application_error = validation.get("error")
        db.session.commit()
        return jsonify(
            {"error": "Patch validation failed.", "details": validation.get("error")}
        ), 422

    from backend.execution.git import GitRunner

    fix.application_status = ApplicationStatus.APPLYING
    db.session.commit()

    git = GitRunner(repo.repository_path)
    result = git.apply_patch(fix.patch, check=False)

    if result.succeeded:
        fix.application_status = ApplicationStatus.APPLIED
        fix.applied_at = datetime.now(timezone.utc)
        bug.status = BugStatus.FIX_APPLIED
    else:
        fix.application_status = ApplicationStatus.FAILED
        fix.application_error = result.stderr

    db.session.commit()

    if not result.succeeded:
        return jsonify(
            {"error": "Patch application failed.", "details": result.stderr}
        ), 422

    return jsonify(fix.to_dict()), 200
