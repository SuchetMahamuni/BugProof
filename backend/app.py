"""
BugProof Flask application entry point.

Creates and configures the Flask application.

Usage:
    # Development
    python app.py

    # Production (via gunicorn or similar)
    gunicorn "app:create_app()"

Environment:
    Set APP_ENV to 'development', 'testing', or 'production'.
    All secrets must be provided via environment variables.
    See .env.example for required variables.
"""

import os
from flask import Flask, jsonify
from dotenv import load_dotenv


def create_app(config_override: dict | None = None) -> Flask:
    """
    Application factory.

    Args:
        config_override: Optional dict of config values to overlay.
                         Used primarily in testing.

    Returns:
        A configured Flask application.
    """
    # Load .env file if present (never commit .env to git)
    load_dotenv()

    app = Flask(__name__)

    # ---- Configuration ----
    from backend.config.settings import get_config
    cfg = get_config()
    app.config.update(cfg.build())

    if config_override:
        app.config.update(config_override)

    # ---- Database ----
    from backend.database.connection import init_db
    init_db(app)

    # ---- Import all models so SQLAlchemy registers them ----
    import backend.models  # noqa: F401

    # ---- Register API blueprints ----
    from backend.api import (
        bugs_bp,
        investigations_bp,
        fixes_bp,
        verification_bp,
        reports_bp,
    )
    app.register_blueprint(bugs_bp)
    app.register_blueprint(investigations_bp)
    app.register_blueprint(fixes_bp)
    app.register_blueprint(verification_bp)
    app.register_blueprint(reports_bp)

    # ---- Health check ----
    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "service": "BugProof"}), 200

    # ---- Global error handlers ----
    @app.errorhandler(404)
    def not_found(err):
        return jsonify({"error": "Not found."}), 404

    @app.errorhandler(405)
    def method_not_allowed(err):
        return jsonify({"error": "Method not allowed."}), 405

    @app.errorhandler(500)
    def internal_error(err):
        return jsonify({"error": "Internal server error."}), 500

    return app


if __name__ == "__main__":
    port = int(os.environ.get("FLASK_PORT", 5000))
    debug = os.environ.get("APP_ENV", "development") == "development"
    application = create_app()
    application.run(host="0.0.0.0", port=port, debug=debug)
