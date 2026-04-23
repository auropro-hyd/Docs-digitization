"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    bmr_hitl,
    bmr_packages,
    bmr_runs,
    compliance,
    corrections,
    documents,
    review,
    rules,
)
from app.api.websocket import router as ws_router
from app.compliance.rules.profiles import validate_compliance_configs
from app.compliance.rules.registry import get_registry
from app.config.settings import get_settings
from app.observability import configure as configure_observability
from app.observability import get_logger
from app.observability.middleware import install as install_observability

# Observability has to configure BEFORE any application logger is consulted,
# so the stdlib→structlog bridge is in place by the first import-time log
# line. Azure / httpx noise suppression is handled inside configure().
configure_observability()

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    data_dir = Path(settings.storage.base_path)
    data_dir.mkdir(parents=True, exist_ok=True)
    validate_compliance_configs(get_registry())
    logger.info("app.lifespan.ready")
    yield
    from app.core.task_manager import task_manager
    await task_manager.shutdown(timeout=10)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )

    # CORS spec forbids wildcard origins when credentials are allowed —
    # browsers reject the combination outright. In debug mode accept
    # any localhost / 127.0.0.1 port via a regex so frontends on
    # non-default ports (3100, 5173, etc.) work without reconfiguring
    # the backend; in production keep the explicit allowlist from
    # settings.cors_origins.
    cors_kwargs: dict[str, Any] = {
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
        "allow_origins": list(settings.cors_origins),
    }
    if settings.debug:
        cors_kwargs["allow_origin_regex"] = (
            r"^http://(localhost|127\.0\.0\.1)(:\d+)?$"
        )
    app.add_middleware(CORSMiddleware, **cors_kwargs)

    # Observability middleware + /metrics + /health* — must be installed
    # after CORS so the response headers it emits go through CORS too.
    install_observability(app)

    app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
    app.include_router(review.router, prefix="/api/review", tags=["review"])
    app.include_router(compliance.router, prefix="/api/compliance", tags=["compliance"])
    app.include_router(rules.router, prefix="/api/rules", tags=["rules"])
    app.include_router(corrections.router, prefix="/api/corrections", tags=["corrections"])
    app.include_router(bmr_packages.router, prefix="/api/bmr", tags=["bmr"])
    app.include_router(bmr_runs.router, prefix="/api/bmr", tags=["bmr"])
    app.include_router(bmr_hitl.router, prefix="/api/bmr", tags=["bmr"])
    app.include_router(ws_router, tags=["websocket"])

    return app


app = create_app()
