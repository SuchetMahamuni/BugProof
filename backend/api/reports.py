"""
Reports API.

Integration point for Team Member 3's reporting/UI work.

This module defines the API contract for retrieving debugging session reports.
The report generation and frontend rendering logic is owned by Team Member 3.
"""

from flask import Blueprint, jsonify
from backend.database.connection import db
from backend.models import Report

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
    The frontend (Team Member 3) uses this to render the evidence report UI.
    """
    query = (
        db.select(Report)
        .where(Report.bug_id == bug_id)
        .order_by(Report.created_at.desc())
    )
    reports = db.session.execute(query).scalars().all()
    return jsonify([r.to_dict() for r in reports]), 200
