"""CORS configuration for browser dashboard clients."""

from __future__ import annotations

import os

from starlette.middleware.cors import CORSMiddleware
from starlette.applications import Starlette


def cors_allow_origins() -> list[str]:
    # Prefer 127.0.0.1 for local dashboard + API (SameSite=Lax cookie sessions).
    # localhost remains listed so mis-typed tabs still pass CORS, but the cookie
    # host must still match the API host the browser used for Set-Cookie.
    raw = os.getenv(
        "GATEWAY_CORS_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173",
    )
    return [part.strip() for part in raw.split(",") if part.strip()]


def apply_cors(app: Starlette) -> Starlette:
    """
    Allow credentialed requests from the dashboard origin(s).

    Required for HttpOnly cookie sessions across the Vite dev server and gateway.
    Origins must be explicit (not ``*``) when ``allow_credentials`` is True.
    """
    origins = cors_allow_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )
    return app
