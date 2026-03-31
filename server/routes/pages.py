import shutil
import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from server.auth import get_current_player
from server.db.database import get_db
from server.db.models import Campaign, Character, GameLog, GameState, Player
from server.db.models import Session as GameSession

templates = Jinja2Templates(directory="templates")
router = APIRouter()


@router.get("/")
def home(request: Request, db: DBSession = Depends(get_db)):
    player = get_current_player(request, db)
    if player:
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "player": None,
    })


@router.get("/dashboard")
def dashboard(request: Request, db: DBSession = Depends(get_db)):
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    characters = (
        db.query(Character)
        .filter(Character.player_id == player.id)
        .all()
    )

    # Find active sessions for this player's characters
    active_sessions = []
    for c in characters:
        sessions = (
            db.query(GameSession)
            .filter(
                GameSession.campaign_id == c.campaign_id,
                GameSession.status == "active",
            )
            .all()
        )
        for s in sessions:
            if s not in active_sessions:
                active_sessions.append(s)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "player": player,
        "characters": characters,
        "active_sessions": active_sessions,
    })


@router.get("/play/{session_id}")
def game_session(request: Request, session_id: int, db: DBSession = Depends(get_db)):
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    session = db.query(GameSession).filter(GameSession.id == session_id).first()
    if not session:
        return RedirectResponse(url="/dashboard", status_code=303)

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
        "player": player,
        "session": session,
        "game_state": game_state,
        "logs": logs,
        "characters": characters,
    })


@router.get("/character/{character_id}")
def character_profile(request: Request, character_id: int, db: DBSession = Depends(get_db)):
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    character = db.query(Character).filter(Character.id == character_id).first()
    if not character or character.player_id != player.id:
        return RedirectResponse(url="/dashboard", status_code=303)

    sessions = (
        db.query(GameSession)
        .filter(GameSession.campaign_id == character.campaign_id)
        .order_by(GameSession.session_number.desc())
        .all()
    )

    return templates.TemplateResponse("character_profile.html", {
        "request": request,
        "player": player,
        "character": character,
        "sessions": sessions,
    })


@router.post("/api/character/{character_id}/avatar")
async def upload_avatar(
    request: Request,
    character_id: int,
    avatar_file: UploadFile | None = File(None),
    avatar_url: str = Form(""),
    db: DBSession = Depends(get_db),
):
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    character = db.query(Character).filter(Character.id == character_id).first()
    if not character or character.player_id != player.id:
        return RedirectResponse(url="/dashboard", status_code=303)

    if avatar_file and avatar_file.filename:
        # Save uploaded file
        ext = avatar_file.filename.rsplit(".", 1)[-1] if "." in avatar_file.filename else "png"
        filename = f"{uuid.uuid4().hex}.{ext}"
        filepath = f"static/uploads/avatars/{filename}"
        with open(filepath, "wb") as f:
            shutil.copyfileobj(avatar_file.file, f)
        character.avatar_url = f"/static/uploads/avatars/{filename}"
    elif avatar_url.strip():
        character.avatar_url = avatar_url.strip()

    db.commit()
    return RedirectResponse(url=f"/character/{character_id}", status_code=303)
