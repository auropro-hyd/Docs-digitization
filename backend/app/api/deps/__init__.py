"""FastAPI dependency helpers."""

from app.api.deps.bmr_auth import require_actor

__all__ = ["require_actor"]
