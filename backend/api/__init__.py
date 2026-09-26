# backend/api/__init__.py
from .bugs import bugs_bp
from .investigations import investigations_bp
from .fixes import fixes_bp
from .verification import verification_bp
from .reports import reports_bp

__all__ = [
    "bugs_bp",
    "investigations_bp",
    "fixes_bp",
    "verification_bp",
    "reports_bp",
]
