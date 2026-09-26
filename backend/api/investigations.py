"""
Investigations API.

Endpoints for retrieving investigation results, evidence, and hypotheses.
"""

from flask import Blueprint, jsonify
from backend.database.connection import db
from backend.models import Investigation, Evidence, Hypothesis

investigations_bp = Blueprint("investigations", __name__, url_prefix="/api/investigations")


@investigations_bp.route("/<investigation_id>", methods=["GET"])
def get_investigation(investigation_id: str):
    """
    Retrieve a single investigation by ID.

    Returns 200 with the investigation dict, or 404.
    """
    inv = db.session.get(Investigation, investigation_id)
    if inv is None:
        return jsonify({"error": f"Investigation '{investigation_id}' not found."}), 404
    return jsonify(inv.to_dict()), 200


@investigations_bp.route("/bug/<bug_id>", methods=["GET"])
def list_investigations_for_bug(bug_id: str):
    """
    List all investigations for a given bug.

    Returns 200 with a list of investigation dicts.
    """
    query = (
        db.select(Investigation)
        .where(Investigation.bug_id == bug_id)
        .order_by(Investigation.created_at.desc())
    )
    investigations = db.session.execute(query).scalars().all()
    return jsonify([i.to_dict() for i in investigations]), 200


@investigations_bp.route("/<investigation_id>/evidence", methods=["GET"])
def list_evidence(investigation_id: str):
    """
    List all evidence items for an investigation.

    Returns 200 with a list of evidence dicts.
    Each item includes the ``is_verified`` flag so clients can distinguish
    machine-confirmed evidence from AI-generated claims.
    """
    inv = db.session.get(Investigation, investigation_id)
    if inv is None:
        return jsonify({"error": f"Investigation '{investigation_id}' not found."}), 404

    query = (
        db.select(Evidence)
        .where(Evidence.investigation_id == investigation_id)
        .order_by(Evidence.collected_at)
    )
    evidence_items = db.session.execute(query).scalars().all()
    return jsonify([e.to_dict() for e in evidence_items]), 200


@investigations_bp.route("/<investigation_id>/hypotheses", methods=["GET"])
def list_hypotheses(investigation_id: str):
    """
    List AI-generated hypotheses for an investigation.

    Returns 200 with a list of hypothesis dicts.
    All returned items have ``ai_generated = true``.
    """
    inv = db.session.get(Investigation, investigation_id)
    if inv is None:
        return jsonify({"error": f"Investigation '{investigation_id}' not found."}), 404

    query = (
        db.select(Hypothesis)
        .where(Hypothesis.investigation_id == investigation_id)
        .order_by(Hypothesis.confidence.desc())
    )
    hypotheses = db.session.execute(query).scalars().all()
    return jsonify([h.to_dict() for h in hypotheses]), 200
