"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import compliance, corrections, documents, review, rules
from app.compliance.rules.profiles import validate_compliance_configs
from app.compliance.rules.registry import get_registry
from app.api.websocket import router as ws_router
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

    origins = ["*"] if settings.debug else settings.cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
    app.include_router(review.router, prefix="/api/review", tags=["review"])
    app.include_router(compliance.router, prefix="/api/compliance", tags=["compliance"])
    app.include_router(rules.router, prefix="/api/rules", tags=["rules"])
    app.include_router(corrections.router, prefix="/api/corrections", tags=["corrections"])
    app.include_router(ws_router, tags=["websocket"])

    return app


app = create_app()
