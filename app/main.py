"""FastAPI app factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Settings, get_settings
from app.errors import install_error_handlers
from app.gateway.types import Provider
from app.services import build_services


def create_app(settings: Settings | None = None, provider: Provider | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.services = await build_services(settings, provider)
        try:
            yield
        finally:
            await app.state.services.db.close()

    app = FastAPI(title="portfolio-copilot", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)
    from app.api import ask, health

    app.include_router(health.router)
    app.include_router(ask.router)
    if not settings.is_prod:
        from app.api import debug

        app.include_router(debug.router)
    return app


app = create_app()
