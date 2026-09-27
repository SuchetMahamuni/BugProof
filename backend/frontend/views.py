"""
Frontend views blueprint.

Serves the developer-facing BugProof UI at the application root.
"""

import os
from flask import Blueprint, render_template

frontend_bp = Blueprint(
    "frontend",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
    static_folder=os.path.join(os.path.dirname(__file__), "static"),
    static_url_path="/static",
)


@frontend_bp.route("/")
def index():
    """Render the main bug submission UI."""
    return render_template("index.html")


@frontend_bp.route("/report/<bug_id>")
def report(bug_id: str):
    """Render the Evidence Report UI for a given bug (Team Member 3)."""
    return render_template("report.html", bug_id=bug_id)
