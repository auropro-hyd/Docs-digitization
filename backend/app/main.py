"""FastAPI application entry point."""

from __future__ import annotations

import logging
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

# Keep SDK transport chatter out of dev terminal output; surface app-level progress via WebSocket/UI instead.
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.ai.documentintelligence").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    data_dir = Path(settings.storage.base_path)
    data_dir.mkdir(parents=True, exist_ok=True)
    validate_compliance_configs(get_registry())
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
