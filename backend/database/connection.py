"""
Database connection and SQLAlchemy instance.

Import ``db`` from this module everywhere models are defined so that
all models share the same SQLAlchemy metadata.
"""

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

# Single shared SQLAlchemy instance.
# Bound to the Flask app via db.init_app(app) in app.py.
db: SQLAlchemy = SQLAlchemy()
migrate: Migrate = Migrate()


def init_db(app) -> None:
    """Bind the SQLAlchemy and Migrate instances to a Flask application.

    Args:
        app: The Flask application instance.
    """
    db.init_app(app)
    migrate.init_app(app, db)


def create_all_tables(app) -> None:
    """Create all database tables that do not yet exist.

    Intended for use in development and testing.  In production prefer
    running Flask-Migrate migrations.

    Args:
        app: The Flask application instance.
    """
    with app.app_context():
        db.create_all()
