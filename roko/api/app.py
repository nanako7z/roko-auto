"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .routes_commands import router as commands_router
from .routes_record import router as record_router
from .routes_screen import router as screen_router
from .routes_system import router as system_router
from .routes_tasks import router as tasks_router

STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(
        title="roko-auto",
        description="Multi-task input automation scheduling platform",
        version="2.0.0",
    )

    app.include_router(tasks_router)
    app.include_router(commands_router)
    app.include_router(record_router)
    app.include_router(screen_router)
    app.include_router(system_router)

    # Serve the web UI
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    # Serve other static assets (CSS/JS if split out later)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app
