from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from server.config import SECRET_KEY
from server.db.database import create_tables
from server.routes.pages import router as pages_router
from server.routes.auth import router as auth_router
from server.routes.campaigns import router as campaigns_router
from server.routes.actions import router as actions_router
from server.routes.characters import router as characters_router
from server.routes.ws import router as ws_router
from server.routes.stories import router as stories_router


def create_app() -> FastAPI:
    app = FastAPI(title="Foray", version="0.3.0")

    # Session middleware for auth cookies
    app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

    # WebSocket route must be registered BEFORE static files mount
    # so the /ws/ path isn't caught by static file handler
    app.include_router(ws_router)

    # Mount static files
    app.mount("/static", StaticFiles(directory="static"), name="static")

    # Include routers
    app.include_router(auth_router)
    app.include_router(pages_router)
    app.include_router(campaigns_router, prefix="/api")
    app.include_router(actions_router, prefix="/api")
    app.include_router(characters_router, prefix="/api")
    app.include_router(stories_router)

    @app.on_event("startup")
    def on_startup():
        create_tables()
        # Auto-import story JSON files so they're always available
        from server.db.database import SessionLocal
        from server.services.story_service import import_all_stories
        db = SessionLocal()
        try:
            import_all_stories(db)
        finally:
            db.close()

    return app
