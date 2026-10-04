"""FastAPI app factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import events, health, routes
from app.config import Settings, get_settings
from app.container import Container
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, get_logger

log = get_logger("main")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()  # missing provider key / bad config fails here, at startup
    configure_logging()
    container = Container(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container.startup()
        interrupted = await container.orchestrator.recover_interrupted()
        if interrupted:
            log.warning("marked %d interrupted workflow(s) as FAILED", len(interrupted))
        log.info("ChainAudit.ai started (provider=%s, model=%s)", container.provider.name, container.provider.model)
        yield
        for t in list(container.orchestrator._tasks):
            t.cancel()
        await container.orchestrator.wait_idle()
        container.db.dispose()

    app = FastAPI(
        title="ChainAudit.ai API",
        version="1.0.0",
        description="Evidence reconciliation with deterministic rules, an independent Auditor and human approval. Demo Buyer Framework on Synthetic Demo Data.",
        lifespan=lifespan,
    )
    app.state.container = container
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_list, allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Highlight"])
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(events.router)
    app.include_router(routes.router)
    return app


def _default_app() -> FastAPI:
    return create_app()


app = _default_app() if __name__ != "__main__" and __import__("os").environ.get("CHAINAUDIT_NO_AUTOAPP") != "1" else None  # type: ignore[assignment]
