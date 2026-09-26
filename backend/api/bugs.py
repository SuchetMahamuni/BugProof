"""
Bugs API.

Endpoints for creating, retrieving, and managing bug reports.
"""

import threading

from flask import Blueprint, jsonify, request, current_app
from backend.database.connection import db
from backend.models import Bug, BugStatus, TargetRepository

bugs_bp = Blueprint("bugs", __name__, url_prefix="/api/bugs")


def _run_workflow_in_thread(app, bug_id: str) -> None:
    """
    Execute the WorkflowOrchestrator in a background thread.

    This function is the thread target.  It sets up an app context and
    runs the full investigation → RCA → fix-proposal pipeline.
    """
    from backend.core.workflow import WorkflowOrchestrator
    from backend.config.settings import REPOS_WORKSPACE

    orchestrator = WorkflowOrchestrator(
        repository_workspace=REPOS_WORKSPACE,
        # AI components default to None; stubs are used when not configured.
        ai_investigator=None,
        ai_root_cause=None,
        ai_fixer=None,
    )
    orchestrator.execute(bug_id=bug_id, app=app)


@bugs_bp.route("", methods=["POST"])
def create_bug():
    """
    Submit a new bug report.

    Request body (JSON):
      - title (str, required)
      - description (str, required)
      - target_repository (str, required): 'thefuck' | 'tqdm' | 'youtube_dl'
      - repository_ref (str, optional): git ref where bug is reproducible
      - error_message (str, optional): raw error / traceback
      - extra_context (dict, optional)

    Returns 201 with the created bug, or 400 on validation error.
    """
    data = request.get_json(silent=True) or {}

    missing = [f for f in ("title", "description", "target_repository") if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {missing}"}), 400

    repo_name = data["target_repository"]
    valid_repos = [r.value for r in TargetRepository]
    if repo_name not in valid_repos:
        return jsonify(
            {"error": f"Invalid target_repository '{repo_name}'. Valid: {valid_repos}"}
        ), 400

    bug = Bug(
        title=data["title"],
        description=data["description"],
        target_repository=repo_name,
        repository_ref=data.get("repository_ref"),
        error_message=data.get("error_message"),
        extra_context=data.get("extra_context"),
        status=BugStatus.RECEIVED,
    )
    db.session.add(bug)
    db.session.commit()

    return jsonify(bug.to_dict()), 201


@bugs_bp.route("", methods=["GET"])
def list_bugs():
    """
    List all bugs with optional status filter.

    Query params:
      - status (str, optional): filter by BugStatus value
      - repository (str, optional): filter by target_repository

    Returns 200 with a list of bug dicts.
    """
    query = db.select(Bug)

    status_filter = request.args.get("status")
    if status_filter:
        query = query.where(Bug.status == status_filter)

    repo_filter = request.args.get("repository")
    if repo_filter:
        query = query.where(Bug.target_repository == repo_filter)

    bugs = db.session.execute(query.order_by(Bug.created_at.desc())).scalars().all()
    return jsonify([b.to_dict() for b in bugs]), 200


@bugs_bp.route("/<bug_id>", methods=["GET"])
def get_bug(bug_id: str):
    """
    Retrieve a single bug by ID.

    Returns 200 with the bug dict, or 404 if not found.
    """
    bug = db.session.get(Bug, bug_id)
    if bug is None:
        return jsonify({"error": f"Bug '{bug_id}' not found."}), 404
    return jsonify(bug.to_dict()), 200


@bugs_bp.route("/<bug_id>/start", methods=["POST"])
def start_debugging(bug_id: str):
    """
    Trigger the debugging workflow for a bug.

    Loads the stored Bug record (by bug_id) and starts the investigation
    pipeline in a background thread.  Returns 202 immediately; the caller
    should poll GET /api/bugs/<bug_id> or GET /api/investigations/bug/<bug_id>
    to track progress.

    The full pipeline executes as:
      Investigation → Evidence collection → RCA → Fix proposal (PENDING)

    The bug's status is set to INVESTIGATING before the response is returned.

    Returns 202 Accepted with the run metadata, or 404 / 409 on error.
    """
    bug = db.session.get(Bug, bug_id)
    if bug is None:
        return jsonify({"error": f"Bug '{bug_id}' not found."}), 404

    if bug.status not in (BugStatus.RECEIVED, BugStatus.FAILED):
        return jsonify(
            {"error": f"Bug is already in status '{bug.status}'. Cannot restart."}
        ), 409

    # Set status to INVESTIGATING immediately so the caller knows work started.
    bug.status = BugStatus.INVESTIGATING
    db.session.commit()

    # Run the workflow in a daemon background thread.
    # The thread uses its own Flask app context (passed as argument).
    app = current_app._get_current_object()  # unwrap proxy for thread safety
    thread = threading.Thread(
        target=_run_workflow_in_thread,
        args=(app, bug_id),
        daemon=True,
    )
    thread.start()

    return jsonify({
        "message": "Debugging run started. Poll the bug status or investigations endpoint for progress.",
        "bug_id": bug_id,
        "status": bug.status,
        "poll_urls": {
            "bug_status": f"/api/bugs/{bug_id}",
            "investigations": f"/api/investigations/bug/{bug_id}",
        },
    }), 202
