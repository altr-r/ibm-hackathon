"""
black_flag/web — Local web application for the Black Flag portability engine.

This package is a thin interface layer. It reuses the existing core engine
(analyzer, adaptation engine, Docker build matrix, packager) and never
re-implements portability logic. FastAPI/uvicorn are imported lazily so that
the core CLI keeps working without the web dependencies installed.

Layout:
    app.py       — FastAPI application factory + static SPA mount
    routes.py    — /api/* endpoints
    schemas.py   — Pydantic request/response models
    services.py  — orchestration + in-process job registry + safe uploads
    static/      — index.html, styles.css, app.js (vanilla JS single-page app)
"""

__all__ = ["create_app"]


def create_app(*args, **kwargs):
    """Lazy import wrapper so `import black_flag.web` never requires FastAPI."""
    from .app import create_app as _create_app

    return _create_app(*args, **kwargs)
