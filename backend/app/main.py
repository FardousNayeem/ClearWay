"""Application factory.

Everything the process needs is assembled here and nowhere else: logging,
middleware, error handlers, routers and the scheduler. Importing ``app`` has no
side effects beyond that, which keeps tests able to build their own instance.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import bind_request_id, configure_logging, new_request_id, reset_request_id

logger = logging.getLogger(__name__)

DESCRIPTION = """
Air quality nowcast, forecast and exposure guidance, built entirely on open data.

CAMS forecasts PM2.5 globally on a coarse grid. Clearway learns the local bias at
each monitoring station and corrects it, then scores itself every day against
persistence, climatology and raw CAMS on predictions made before the outcome was
known.

Weather and air quality data by Open-Meteo (CC BY 4.0). Ground truth from OpenAQ
and the World Air Quality Index project.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "starting %s (env=%s, ground truth: %s)",
            settings.project_name,
            settings.env,
            ", ".join(settings.enabled_measurement_providers) or "none, CAMS-only mode",
        )
        yield
        logger.info("shutting down")

    app = FastAPI(
        title=settings.project_name,
        description=DESCRIPTION,
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or new_request_id()
        token = bind_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            reset_request_id(token)
        response.headers["X-Request-ID"] = request_id
        return response

    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
