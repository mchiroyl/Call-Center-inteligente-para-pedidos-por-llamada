"""FastAPI application factory."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lifespan import lifespan
from routers import admin, auth, operations, static, twilio, web_voice
from routers.static import NoCacheStaticFiles
from settings import STATIC_DIR, allowed_origins


def create_app() -> FastAPI:
    if not STATIC_DIR.exists():
        raise RuntimeError(
            f"Web build not found at {STATIC_DIR}. "
            "Run 'make build-web' or 'make dev-py' from the project root."
        )

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Operations-Key",
            "X-Admin-Key",
        ],
    )
    app.include_router(admin.router)
    app.include_router(auth.router)
    app.include_router(operations.router)
    app.include_router(twilio.router)
    app.include_router(web_voice.router)
    app.include_router(static.router)
    app.mount("/", NoCacheStaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
