from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..errors import BudgetExceeded
from .limits import RateLimiter
from .routes import router


def create_app(c, manage_lifecycle: bool = True) -> FastAPI:
    """manage_lifecycle=False when the caller (tests, an embedding app) already started the container."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if manage_lifecycle:
            await c.start()
        try:
            yield
        finally:
            if manage_lifecycle:
                await c.close()

    app = FastAPI(title="Memaso", version="0.1.0", lifespan=lifespan)
    app.state.c = c
    app.state.limiter = RateLimiter(int(c.cfg.limits["requests_per_minute"]))
    app.include_router(router)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, e: ValueError):
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(BudgetExceeded)
    async def _budget(_: Request, e: BudgetExceeded):
        return JSONResponse({"detail": str(e)}, status_code=429)

    return app
