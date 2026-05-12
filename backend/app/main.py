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

    # Surface obvious config conflicts the signature-crop pipeline
    # needs at startup, where the operator will actually see them.
    # Akhilesh's runs were producing zero signature crops because
    # ``AT_DATALAB__FETCH_BLOCK_BBOXES=false`` and
    # ``AT_DATALAB__DISABLE_IMAGE_EXTRACTION=true`` were set in
    # ``.env`` from a prior speed-tuning phase, and the new
    # signature-crop code can't function with those off. We log
    # WARNINGs (not raise) so the app stays bootable; operator
    # decides whether to flip the env vars.
    dl = settings.datalab
    if getattr(dl, "signature_enrichment", True) and not dl.fetch_block_bboxes:
        logger.warning(
            "datalab.config.signature_pipeline_disabled — "
            "signature_enrichment=on but fetch_block_bboxes=off → "
            "no JSON-tree bboxes will be fetched, signature image "
            "crops won't be generated. Set "
            "AT_DATALAB__FETCH_BLOCK_BBOXES=true to enable crops."
        )
    if getattr(dl, "signature_enrichment", True) and dl.disable_image_extraction:
        logger.warning(
            "datalab.config.image_extraction_disabled — "
            "signature_enrichment=on but disable_image_extraction=on → "
            "Datalab won't return binary images, markdown <img> tags "
            "will be broken on signature cells it does classify. "
            "Set AT_DATALAB__DISABLE_IMAGE_EXTRACTION=false to fix."
        )

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
