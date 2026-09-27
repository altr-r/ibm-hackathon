"""
web/app.py — FastAPI application factory for the Black Flag web dashboard.

The API router is registered FIRST so that /api/* always takes precedence over
the static single-page-app mount at "/". The SPA (index.html + styles.css +
app.js) is served from black_flag/web/static/.

Run locally with either:

    black-flag web                 # CLI command (recommended)
    uvicorn black_flag.web.app:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .routes import router

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(
        title="Black Flag",
        description="AI-Assisted Linux Portability Engine",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # API first so it wins over the static catch-all mount.
    app.include_router(router)

    # Never leak stack traces to the browser; keep a human-readable message.
    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request, exc):  # pragma: no cover
        import traceback

        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error. See server logs for details."},
        )

    # Static SPA (index.html served at "/").
    if _STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")

    return app


# Module-level app for `uvicorn black_flag.web.app:app`.
app = create_app()
