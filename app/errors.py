"""HTTP error mapping. After streaming starts, errors become an `error` event followed by `done`."""

from __future__ import annotations

import logging
import math

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.gateway.types import RateLimited

log = logging.getLogger(__name__)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RateLimited)
    async def _rate_limited(_: Request, exc: RateLimited) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"detail": "rate limited"},
            headers={"Retry-After": str(math.ceil(exc.retry_after_s))},
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error type=%s", type(exc).__name__)
        return JSONResponse(status_code=500, content={"detail": "internal error"})
