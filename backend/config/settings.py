"""
BugProof backend configuration.

All sensitive values are read from environment variables.
Never hardcode credentials, API keys, or passwords here.
"""

import os
from typing import Optional


def _require_env(name: str) -> str:
    """Return the value of a required environment variable, raising if absent."""
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{name}' is not set. "
            "Copy .env.example to .env and fill in the values."
        )
    return value


class BaseConfig:
    """Shared configuration base."""

    # Flask
    DEBUG: bool = False
    TESTING: bool = False
    SECRET_KEY: str = os.environ.get("FLASK_SECRET_KEY", "change-me-in-env")

    # SQLAlchemy
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False

    @classmethod
    def get_database_uri(cls) -> str:
        """Build the PostgreSQL URI from environment variables."""
        db_host = os.environ.get("DB_HOST", "localhost")
        db_port = os.environ.get("DB_PORT", "5432")
        db_name = os.environ.get("DB_NAME", "bugproof")
        db_user = os.environ.get("DB_USER", "bugproof_user")
        db_pass = os.environ.get("DB_PASSWORD", "")
        return f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"

    @classmethod
    def build(cls) -> dict:
        """Return a Flask-compatible config dict."""
        return {
            "DEBUG": cls.DEBUG,
            "TESTING": cls.TESTING,
            "SECRET_KEY": cls.SECRET_KEY,
            "SQLALCHEMY_DATABASE_URI": cls.get_database_uri(),
            "SQLALCHEMY_TRACK_MODIFICATIONS": cls.SQLALCHEMY_TRACK_MODIFICATIONS,
        }


class DevelopmentConfig(BaseConfig):
    """Development configuration."""

    DEBUG: bool = True


class TestingConfig(BaseConfig):
    """Testing configuration – uses a separate test database."""

    TESTING: bool = True
    DEBUG: bool = True

    @classmethod
    def get_database_uri(cls) -> str:
        return os.environ.get(
            "TEST_DATABASE_URL",
            "postgresql://bugproof_user:@localhost:5432/bugproof_test",
        )


class ProductionConfig(BaseConfig):
    """Production configuration."""

    DEBUG: bool = False

    @classmethod
    def get_database_uri(cls) -> str:
        # In production a full DATABASE_URL env var is expected.
        return os.environ.get("DATABASE_URL") or super().get_database_uri()


# Map APP_ENV values to config classes
_CONFIG_MAP: dict[str, type[BaseConfig]] = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config() -> type[BaseConfig]:
    """Return the active config class based on the APP_ENV environment variable."""
    env = os.environ.get("APP_ENV", "development").lower()
    return _CONFIG_MAP.get(env, DevelopmentConfig)


# Workspace directory where target repositories are checked out.
REPOS_WORKSPACE: str = os.environ.get(
    "REPOS_WORKSPACE",
    os.path.join(os.path.dirname(__file__), "..", "..", "workspace", "repos"),
)

# IBM watsonx / AI service settings (populated via environment variables only)
WATSONX_URL: Optional[str] = os.environ.get("WATSONX_URL")
WATSONX_PROJECT_ID: Optional[str] = os.environ.get("WATSONX_PROJECT_ID")
WATSONX_MODEL_ID: str = os.environ.get("WATSONX_MODEL_ID", "ibm/granite-13b-instruct-v2")
