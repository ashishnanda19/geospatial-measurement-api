"""Application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import files, ui
from app.config import Settings
from app.database import make_engine, make_session_factory
from app.errors import AppError
from app.models import Base

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title="Geospatial File Measurement API",
        description="Upload a zipped Shapefile or a KML file and get per-feature area and length, "
        "measured in a projected CRS.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = make_session_factory(engine)

    app.include_router(files.router)
    app.include_router(ui.router)
    app.mount("/static", StaticFiles(directory=ui.STATIC_DIR), name="static")

    @app.get("/health", tags=["meta"], summary="Liveness check")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
