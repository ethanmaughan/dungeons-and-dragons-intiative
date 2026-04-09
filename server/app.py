from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware

from server.config import SECRET_KEY, ADMIN_USERNAME
from server.db.database import create_tables
from server.security import SecurityHeadersMiddleware, limiter
from server.routes.pages import router as pages_router
from server.routes.auth import router as auth_router
from server.routes.campaigns import router as campaigns_router
from server.routes.actions import router as actions_router
from server.routes.characters import router as characters_router
from server.routes.ws import router as ws_router
from server.routes.stories import router as stories_router
from server.routes.subscription import router as subscription_router
from server.routes.admin import router as admin_router


def create_app() -> FastAPI:
    app = FastAPI(title="Foray", version="1.0.0")

    @app.get("/healthz")
    def health_check():
        return JSONResponse({"status": "ok"})

    # Middleware (outermost first)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

    # Rate limiting
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # WebSocket route must be registered BEFORE static files mount
    app.include_router(ws_router)

    # Mount static files
    app.mount("/static", StaticFiles(directory="static"), name="static")

    # Include routers
    app.include_router(auth_router)
    app.include_router(pages_router)
    app.include_router(subscription_router)
    app.include_router(admin_router)
    app.include_router(campaigns_router, prefix="/api")
    app.include_router(actions_router, prefix="/api")
    app.include_router(characters_router, prefix="/api")
    app.include_router(stories_router)

    @app.on_event("startup")
    def on_startup():
        create_tables()
        # Auto-import story JSON files
        from server.db.database import SessionLocal
        from server.services.story_service import import_all_stories
        db = SessionLocal()
        try:
            import_all_stories(db)
            # Seed admin user if configured
            if ADMIN_USERNAME:
                from server.db.models import Player
                admin = db.query(Player).filter(Player.username == ADMIN_USERNAME).first()
                if admin and not admin.is_admin:
                    admin.is_admin = True
                    db.commit()
        finally:
            db.close()

    return app
