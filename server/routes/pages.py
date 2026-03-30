from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from server.db.database import get_db
from server.db.models import Campaign, GameLog, GameState
from server.db.models import Session as GameSession

templates = Jinja2Templates(directory="templates")
router = APIRouter()


@router.get("/")
def home(request: Request, db: DBSession = Depends(get_db)):
    campaigns = db.query(Campaign).all()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "campaigns": campaigns,
    })


@router.get("/play/{session_id}")
def game_session(request: Request, session_id: int, db: DBSession = Depends(get_db)):
    session = db.query(GameSession).filter(GameSession.id == session_id).first()
    if not session:
        return templates.TemplateResponse("index.html", {
            "request": request,
            "campaigns": [],
            "error": "Session not found",
        })

    game_state = db.query(GameState).filter(GameState.session_id == session_id).first()
    logs = (
        db.query(GameLog)
        .filter(GameLog.session_id == session_id)
        .order_by(GameLog.id.asc())
        .all()
    )
    characters = session.campaign.characters

    return templates.TemplateResponse("game_session.html", {
        "request": request,
        "session": session,
        "game_state": game_state,
        "logs": logs,
        "characters": characters,
    })
