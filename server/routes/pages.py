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
        .filter(Character.player_id == player.id, Character.is_enemy == False)
        .all()
    )

    # Get campaigns the player owns or has characters in
    owned_campaigns = db.query(Campaign).filter(Campaign.owner_id == player.id).all()
    assigned_campaign_ids = {c.campaign_id for c in characters if c.campaign_id}
    assigned_campaigns = (
        db.query(Campaign).filter(Campaign.id.in_(assigned_campaign_ids)).all()
        if assigned_campaign_ids else []
    )
    all_campaigns = list({c.id: c for c in owned_campaigns + assigned_campaigns}.values())

    # Find active sessions
    active_sessions = []
    for campaign in all_campaigns:
        sessions = (
            db.query(GameSession)
            .filter(GameSession.campaign_id == campaign.id, GameSession.status == "active")
            .all()
        )
        active_sessions.extend(sessions)

    # Unassigned characters (for the assign dropdown)
    unassigned_chars = [c for c in characters if c.campaign_id is None and c.creation_complete]

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "player": player,
        "characters": characters,
        "campaigns": all_campaigns,
        "active_sessions": active_sessions,
        "unassigned_chars": unassigned_chars,
    })


@router.get("/character/create")
def character_create_page(request: Request, db: DBSession = Depends(get_db)):
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("character_create.html", {
        "request": request,
        "player": player,
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

    sessions = []
    if character.campaign_id:
        sessions = (
            db.query(GameSession)
            .filter(GameSession.campaign_id == character.campaign_id)
            .order_by(GameSession.session_number.desc())
            .all()
        )

    # Get campaigns for assign dropdown (if unassigned)
    available_campaigns = []
    if not character.campaign_id:
        available_campaigns = db.query(Campaign).filter(Campaign.owner_id == player.id).all()

    return templates.TemplateResponse("character_profile.html", {
        "request": request,
        "player": player,
        "character": character,
        "sessions": sessions,
        "available_campaigns": available_campaigns,
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
