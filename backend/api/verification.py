"""
Verification API.

Feature 1 – Automatic Bug Reproduction.
Feature 2 – Regression Test Generation.
Feature 3 – Change Impact Analysis.

Endpoints:
  GET  /api/verification/test-runs/<id>          – retrieve a single run
  GET  /api/verification/test-runs/fix/<fix_id>  – runs for a fix
  GET  /api/verification/test-runs/bug/<bug_id>  – runs for a bug
  POST /api/verification/run                     – trigger a test run
  POST /api/verification/regression/generate     – generate regression tests
  GET  /api/verification/regression/<fix_id>     – get generated tests for fix
  POST /api/verification/impact/analyse          – run change impact analysis
  GET  /api/verification/impact/<fix_id>         – get impact analysis for fix

The POST /run endpoint is synchronous: it executes the test suite inside the
request and returns when complete.  There is no background task queue in this
codebase; the 202 status code signals to callers that the operation may take
time, consistent with the rest of the API (see POST /api/bugs/<id>/start).

Repository resolution:
  - If ``bug_id`` is supplied, the target repository is read from
    ``Bug.target_repository``.
  - If only ``fix_id`` is supplied, we traverse fix → investigation → bug
    to obtain the same value.
  - If both ``bug_id`` and ``fix_id`` are supplied, the fix must belong to
    the given bug (via fix → investigation → bug).  Mismatched IDs are
    rejected with 422 and no TestRun is created.
  - The repository path is taken from ``REPOS_WORKSPACE`` in
    ``backend.config.settings`` and passed to ``get_repository()``.
  - No repository name or path from the request body is ever trusted.

Dependency installation:
  ``TheFuckRepository.install_dependencies()`` (pip install -e .[test]) is NOT
  called on every request.  It is a slow, process-level operation that belongs
  in environment bootstrap (CI/Docker setup), not in the request path.
"""

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from backend.config.settings import REPOS_WORKSPACE
from backend.database.connection import db
from backend.models import Bug, Fix, TestRun, TestRunKind, TestRunStatus
from backend.repositories import get_repository

verification_bp = Blueprint("verification", __name__, url_prefix="/api/verification")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _resolve_bug(bug_id: str | None, fix_id: str | None):
    """
    Return (bug, fix_or_none, error_response).

    Resolution rules:
    - Only bug_id supplied  → look up Bug; fix is None.
    - Only fix_id supplied  → walk fix → investigation → bug; fix is returned.
    - Both supplied         → look up both independently and verify the fix
                              actually belongs to the bug.  Return 422 if not.
    - Neither supplied      → return 400.

    Returns a 3-tuple where exactly one of (bug, error_response) is None.
    The fix element is always None when only bug_id is supplied.
    """
    if bug_id and fix_id:
        # Both provided: validate the relationship.
        bug = db.session.get(Bug, bug_id)
        if bug is None:
            return None, None, (jsonify({"error": f"Bug '{bug_id}' not found."}), 404)

        fix = db.session.get(Fix, fix_id)
        if fix is None:
            return None, None, (jsonify({"error": f"Fix '{fix_id}' not found."}), 404)

        # Walk fix → investigation → bug to verify ownership.
        # This avoids trusting the caller to supply a consistent pair.
        fix_bug_id = fix.investigation.bug_id
        if fix_bug_id != bug_id:
            return None, None, (
                jsonify({
                    "error": (
                        f"Fix '{fix_id}' does not belong to Bug '{bug_id}'. "
                        f"It belongs to Bug '{fix_bug_id}'."
                    )
                }),
                422,
            )
        return bug, fix, None

    if bug_id:
        bug = db.session.get(Bug, bug_id)
        if bug is None:
            return None, None, (jsonify({"error": f"Bug '{bug_id}' not found."}), 404)
        return bug, None, None

    if fix_id:
        fix = db.session.get(Fix, fix_id)
        if fix is None:
            return None, None, (jsonify({"error": f"Fix '{fix_id}' not found."}), 404)
        # fix → investigation → bug  (lazy-load is fine within a session)
        bug = fix.investigation.bug
        return bug, fix, None

    return None, None, (
        jsonify({"error": "Either 'bug_id' or 'fix_id' is required."}),
        400,
    )


def _execute_and_persist(test_run: TestRun, bug: Bug, ref: str | None) -> None:
    """
    Run the test suite for *bug*'s target repository and write results back
    into *test_run*.

    All exceptions are caught so the record is never left stuck in RUNNING.
    The caller is responsible for committing after this function returns.
    """
    test_run.status = TestRunStatus.RUNNING
    test_run.started_at = datetime.now(timezone.utc)
    db.session.commit()  # flush RUNNING status before potentially long execution

    try:
        repo = get_repository(bug.target_repository, REPOS_WORKSPACE)

        # Checkout the requested ref (or the repository's default branch).
        # This is a no-op if the repo is already at that ref, because GitRunner
        # simply runs `git checkout <ref>` which returns cleanly when already there.
        repo.checkout(ref)

        result = repo.run_tests()
    except Exception as exc:  # noqa: BLE001
        test_run.status = TestRunStatus.ERROR
        test_run.output = f"Execution error: {exc}"
        test_run.completed_at = datetime.now(timezone.utc)
        return

    # Persist the result fields using the exact column names from TestRun
    test_run.command = result.get("command")
    test_run.output = result.get("output")
    test_run.exit_code = result.get("exit_code")
    test_run.tests_total = result.get("tests_total")
    test_run.tests_passed = result.get("tests_passed")
    test_run.tests_failed = result.get("tests_failed")
    test_run.tests_errored = result.get("tests_errored")
    test_run.completed_at = datetime.now(timezone.utc)

    exit_code = result.get("exit_code")
    if exit_code == 0:
        test_run.status = TestRunStatus.PASSED
    elif exit_code is None:
        # Runner could not determine exit code (e.g. command not found)
        test_run.status = TestRunStatus.ERROR
    else:
        test_run.status = TestRunStatus.FAILED


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@verification_bp.route("/test-runs/<test_run_id>", methods=["GET"])
def get_test_run(test_run_id: str):
    """Retrieve a single test run by ID. Returns 200 or 404."""
    run = db.session.get(TestRun, test_run_id)
    if run is None:
        return jsonify({"error": f"TestRun '{test_run_id}' not found."}), 404
    return jsonify(run.to_dict()), 200


@verification_bp.route("/test-runs/fix/<fix_id>", methods=["GET"])
def list_test_runs_for_fix(fix_id: str):
    """List all test runs associated with a fix. Returns 200."""
    query = (
        db.select(TestRun)
        .where(TestRun.fix_id == fix_id)
        .order_by(TestRun.created_at.desc())
    )
    runs = db.session.execute(query).scalars().all()
    return jsonify([r.to_dict() for r in runs]), 200


@verification_bp.route("/test-runs/bug/<bug_id>", methods=["GET"])
def list_test_runs_for_bug(bug_id: str):
    """List all test runs associated with a bug. Returns 200."""
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
    Trigger a verification test run against the target repository.

    Request body (JSON):
      - bug_id  (str, optional) – link this run to a bug and resolve the repo
      - fix_id  (str, optional) – resolve the repo via fix → investigation → bug
      - kind    (str, optional) – TestRunKind value; defaults to BUG_REPRODUCTION
      - ref     (str, optional) – git ref (branch/tag/commit) to check out

    One of ``bug_id`` or ``fix_id`` is required.  When both are supplied the
    fix must belong to the bug; mismatched IDs are rejected with 422 and no
    TestRun is created or executed.

    Returns 202 with the completed TestRun record.
    """
    data = request.get_json(silent=True) or {}

    # --- Validate kind ---
    kind = data.get("kind", TestRunKind.BUG_REPRODUCTION)
    valid_kinds = [k.value for k in TestRunKind]
    if kind not in valid_kinds:
        return jsonify(
            {"error": f"Invalid kind '{kind}'. Valid: {valid_kinds}"}
        ), 400

    # --- Resolve the bug (and therefore the target repository) ---
    # _resolve_bug returns (bug, fix_or_none, error_or_none).
    bug, _fix, err = _resolve_bug(data.get("bug_id"), data.get("fix_id"))
    if err is not None:
        return err

    # --- Create the TestRun record in PENDING state ---
    test_run = TestRun(
        fix_id=data.get("fix_id"),
        bug_id=bug.id,
        kind=kind,
        status=TestRunStatus.PENDING,
    )
    db.session.add(test_run)
    db.session.commit()

    # --- Execute synchronously and persist the results ---
    _execute_and_persist(test_run, bug, ref=data.get("ref"))
    db.session.commit()

    return jsonify(test_run.to_dict()), 202


# ---------------------------------------------------------------------------
# Regression test generation
# ---------------------------------------------------------------------------

@verification_bp.route("/regression/generate", methods=["POST"])
def generate_regression_tests():
    """
    Generate regression tests for a fix or bug.

    Request body (JSON):
      - fix_id  (str, required) – the fix to generate regression tests for
      - bug_id  (str, optional) – required if fix_id not supplied

    One of ``fix_id`` or ``bug_id`` must be supplied.

    Returns 202 with a GenerationResult dict (tests may be stubs if AI is
    not available).  Returns 400/404/422 on invalid input.
    Regression tests are NOT automatically written to the repository.
    """
    data = request.get_json(silent=True) or {}

    bug, fix, err = _resolve_bug(data.get("bug_id"), data.get("fix_id"))
    if err is not None:
        return err

    from backend.core.regression import RegressionTestGenerator
    from backend.models import Investigation

    # Gather context for generation
    investigation = None
    if fix is not None:
        investigation = db.session.get(Investigation, fix.investigation_id)

    relevant_files: list = []
    fix_explanation: str | None = None
    fix_patch: str | None = None

    if investigation:
        relevant_files = investigation.relevant_files or []

    if fix:
        fix_explanation = fix.explanation
        fix_patch = fix.patch

    # Evidence from investigation (dicts)
    evidence: list = []
    if investigation:
        from backend.models import Evidence
        ev_rows = db.session.execute(
            db.select(Evidence).where(Evidence.investigation_id == investigation.id)
        ).scalars().all()
        evidence = [e.to_dict() for e in ev_rows]

    generator = RegressionTestGenerator(ai_fixer=None)  # AI fixer requires approval; keep separate
    gen_result = generator.generate(
        investigation_id=investigation.id if investigation else (fix.id if fix else bug.id),
        bug_description=bug.description,
        target_repository=bug.target_repository,
        error_message=bug.error_message,
        relevant_files=relevant_files,
        fix_explanation=fix_explanation,
        fix_patch=fix_patch,
        evidence=evidence,
    )

    # Persist generation metadata into Fix.risk_info if a fix is linked
    # (we extend risk_info with regression test metadata rather than adding schema)
    if fix and gen_result.total_generated > 0:
        risk_info = dict(fix.risk_info or {})
        risk_info["regression_tests_generated"] = gen_result.total_generated
        risk_info["regression_tests_validated"] = gen_result.total_validated
        risk_info["regression_generation_ai"] = gen_result.ai_generated
        risk_info["regression_generation_at"] = (
            gen_result.generated_at.isoformat() if gen_result.generated_at else None
        )
        fix.risk_info = risk_info
        db.session.commit()

    response_data = gen_result.to_dict()
    response_data["fix_id"] = fix.id if fix else None
    response_data["bug_id"] = bug.id

    return jsonify(response_data), 202


@verification_bp.route("/regression/<fix_id>", methods=["GET"])
def get_regression_info(fix_id: str):
    """
    Retrieve regression test generation metadata stored on a fix.

    Returns the regression test metadata embedded in Fix.risk_info, or 404.
    """
    fix = db.session.get(Fix, fix_id)
    if fix is None:
        return jsonify({"error": f"Fix '{fix_id}' not found."}), 404

    risk_info = fix.risk_info or {}
    regression_data = {
        "fix_id": fix_id,
        "regression_tests_generated": risk_info.get("regression_tests_generated"),
        "regression_tests_validated": risk_info.get("regression_tests_validated"),
        "regression_generation_ai": risk_info.get("regression_generation_ai"),
        "regression_generation_at": risk_info.get("regression_generation_at"),
    }
    return jsonify(regression_data), 200


# ---------------------------------------------------------------------------
# Change impact analysis
# ---------------------------------------------------------------------------

@verification_bp.route("/impact/analyse", methods=["POST"])
def analyse_impact():
    """
    Run change impact analysis for a fix.

    Request body (JSON):
      - fix_id  (str, required) – the fix to analyse

    Returns 202 with an ImpactResult dict.
    The repository must be cloned locally for full analysis; if unavailable,
    patch-level analysis is returned with a clear note.

    Impact findings are stored in Fix.risk_info for report integration.
    """
    data = request.get_json(silent=True) or {}
    fix_id = data.get("fix_id")
    if not fix_id:
        return jsonify({"error": "'fix_id' is required."}), 400

    fix = db.session.get(Fix, fix_id)
    if fix is None:
        return jsonify({"error": f"Fix '{fix_id}' not found."}), 404

    from backend.models import Investigation, Bug
    from backend.core.impact import ChangeImpactAnalyzer

    investigation = db.session.get(Investigation, fix.investigation_id)
    if investigation is None:
        return jsonify({"error": "Linked investigation not found."}), 404

    bug = db.session.get(Bug, investigation.bug_id)
    if bug is None:
        return jsonify({"error": "Linked bug not found."}), 404

    # Determine repository path (may not exist if not cloned)
    try:
        repo = get_repository(bug.target_repository, REPOS_WORKSPACE)
        repo_path = repo.repository_path if repo.is_cloned() else None
    except Exception:  # noqa: BLE001
        repo_path = None

    analyzer = ChangeImpactAnalyzer(repository_path=repo_path)
    impact_result = analyzer.analyse(
        patch=fix.patch,
        files_changed=fix.files_changed or [],
        investigation_id=investigation.id,
        relevant_files=investigation.relevant_files or [],
        fix_explanation=fix.explanation,
    )

    # Persist impact metadata into Fix.risk_info
    risk_info = dict(fix.risk_info or {})
    risk_info["impact_risk_level"] = impact_result.risk_level
    risk_info["impact_changed_files"] = impact_result.changed_files
    risk_info["impact_affected_areas_count"] = len(impact_result.affected_areas)
    risk_info["impact_related_tests"] = impact_result.related_test_files
    risk_info["impact_analysed_at"] = (
        impact_result.analysed_at.isoformat() if impact_result.analysed_at else None
    )
    if impact_result.error:
        risk_info["impact_analysis_error"] = impact_result.error
    fix.risk_info = risk_info
    db.session.commit()

    response_data = impact_result.to_dict()
    response_data["fix_id"] = fix_id

    return jsonify(response_data), 202


@verification_bp.route("/impact/<fix_id>", methods=["GET"])
def get_impact_info(fix_id: str):
    """
    Retrieve impact analysis metadata stored on a fix.

    Returns the impact metadata embedded in Fix.risk_info, or 404.
    """
    fix = db.session.get(Fix, fix_id)
    if fix is None:
        return jsonify({"error": f"Fix '{fix_id}' not found."}), 404

    risk_info = fix.risk_info or {}
    impact_data = {
        "fix_id": fix_id,
        "impact_risk_level": risk_info.get("impact_risk_level"),
        "impact_changed_files": risk_info.get("impact_changed_files"),
        "impact_affected_areas_count": risk_info.get("impact_affected_areas_count"),
        "impact_related_tests": risk_info.get("impact_related_tests"),
        "impact_analysed_at": risk_info.get("impact_analysed_at"),
        "impact_analysis_error": risk_info.get("impact_analysis_error"),
    }
    return jsonify(impact_data), 200
