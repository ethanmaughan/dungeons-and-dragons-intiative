from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from server.db.database import create_tables
from server.routes.pages import router as pages_router
from server.routes.campaigns import router as campaigns_router
from server.routes.actions import router as actions_router


def create_app() -> FastAPI:
    app = FastAPI(title="D&D Initiative", version="0.1.0")

    # Mount static files
    app.mount("/static", StaticFiles(directory="static"), name="static")

    # Include routers
    app.include_router(pages_router)
    app.include_router(campaigns_router, prefix="/api")
    app.include_router(actions_router, prefix="/api")

    @app.on_event("startup")
    def on_startup():
        create_tables()

    return app
