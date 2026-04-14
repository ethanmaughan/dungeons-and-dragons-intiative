import shutil
import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from server.auth import get_current_player
from server.db.database import get_db
from server.db.models import Campaign, Character, GameLog, GameState, JoinRequest, Player
from server.db.models import Session as GameSession
from server.security import player_can_play

templates = Jinja2Templates(directory="templates")
router = APIRouter()


# ---- Class Themes (CSS-only, no image assets) ----

CLASS_THEMES = {
    "fighter": {
        "primary": "#8ca9c4", "secondary": "#4a6278", "accent": "#c0c8d0",
        "glow": "rgba(140,169,196,0.45)", "glow_strong": "rgba(140,169,196,0.75)",
        "gradient": "linear-gradient(135deg, #1a2530 0%, #2a3a4a 50%, #1a2530 100%)",
        "border_style": "2px solid #8ca9c4", "icon": "\u2694", "flavor": "soldier",
    },
    "wizard": {
        "primary": "#9b6fc4", "secondary": "#4a2a6a", "accent": "#c8a8f0",
        "glow": "rgba(155,111,196,0.45)", "glow_strong": "rgba(155,111,196,0.75)",
        "gradient": "linear-gradient(135deg, #1a0a2e 0%, #2e1a4a 50%, #1a0a2e 100%)",
        "border_style": "2px solid #9b6fc4", "icon": "\u2726", "flavor": "arcane",
    },
    "cleric": {
        "primary": "#d4bc7a", "secondary": "#8a7040", "accent": "#f0e8c0",
        "glow": "rgba(212,188,122,0.45)", "glow_strong": "rgba(212,188,122,0.8)",
        "gradient": "linear-gradient(135deg, #1e1a0a 0%, #3a3010 50%, #1e1a0a 100%)",
        "border_style": "2px solid #d4bc7a", "icon": "\u2600", "flavor": "divine",
    },
    "rogue": {
        "primary": "#8c3a3a", "secondary": "#4a4a54", "accent": "#c85a5a",
        "glow": "rgba(140,58,58,0.5)", "glow_strong": "rgba(200,90,90,0.7)",
        "gradient": "linear-gradient(135deg, #0e0e14 0%, #1a1a22 50%, #0e0e14 100%)",
        "border_style": "2px solid #8c3a3a", "icon": "\u25c6", "flavor": "shadow",
    },
    "ranger": {
        "primary": "#5a8c5a", "secondary": "#2a4a2a", "accent": "#8ab88a",
        "glow": "rgba(90,140,90,0.45)", "glow_strong": "rgba(90,140,90,0.7)",
        "gradient": "linear-gradient(135deg, #0a1a0a 0%, #1a2e1a 50%, #0a1a0a 100%)",
        "border_style": "2px solid #5a8c5a", "icon": "\U0001f3f9", "flavor": "wilderness",
    },
    "barbarian": {
        "primary": "#c43a1a", "secondary": "#6a2a0a", "accent": "#e0886a",
        "glow": "rgba(196,58,26,0.5)", "glow_strong": "rgba(196,58,26,0.8)",
        "gradient": "linear-gradient(135deg, #1a0a00 0%, #2e1400 50%, #1a0a00 100%)",
        "border_style": "2px solid #c43a1a", "icon": "\u26a1", "flavor": "fury",
    },
    "bard": {
        "primary": "#8c44aa", "secondary": "#4a1a6a", "accent": "#c878e8",
        "glow": "rgba(140,68,170,0.45)", "glow_strong": "rgba(200,120,232,0.7)",
        "gradient": "linear-gradient(135deg, #140a1e 0%, #281444 50%, #140a1e 100%)",
        "border_style": "2px dashed #8c44aa", "icon": "\u266a", "flavor": "performance",
    },
    "paladin": {
        "primary": "#7ab0d4", "secondary": "#3a6080", "accent": "#d0eaf8",
        "glow": "rgba(122,176,212,0.5)", "glow_strong": "rgba(208,234,248,0.75)",
        "gradient": "linear-gradient(135deg, #0a141e 0%, #162232 50%, #0a141e 100%)",
        "border_style": "2px solid #7ab0d4", "icon": "\u2726", "flavor": "holy",
    },
    "druid": {
        "primary": "#7a9c3a", "secondary": "#3a5a1a", "accent": "#b4cc7a",
        "glow": "rgba(122,156,58,0.45)", "glow_strong": "rgba(180,204,122,0.7)",
        "gradient": "linear-gradient(135deg, #0a1400 0%, #182800 50%, #0a1400 100%)",
        "border_style": "2px solid #7a9c3a", "icon": "\U0001f33f", "flavor": "nature",
    },
    "monk": {
        "primary": "#c8b86a", "secondary": "#5a5040", "accent": "#e8d8a0",
        "glow": "rgba(200,184,106,0.4)", "glow_strong": "rgba(232,216,160,0.65)",
        "gradient": "linear-gradient(135deg, #141008 0%, #24200e 50%, #141008 100%)",
        "border_style": "1px solid #c8b86a", "icon": "\u25ef", "flavor": "discipline",
    },
    "sorcerer": {
        "primary": "#4ab8e8", "secondary": "#1a5880", "accent": "#88daf8",
        "glow": "rgba(74,184,232,0.5)", "glow_strong": "rgba(136,218,248,0.8)",
        "gradient": "linear-gradient(135deg, #001828 0%, #002840 60%, #001828 100%)",
        "border_style": "2px solid #4ab8e8", "icon": "\u2605", "flavor": "wild magic",
    },
    "warlock": {
        "primary": "#3aaa6a", "secondary": "#1a4a30", "accent": "#6ad8a0",
        "glow": "rgba(58,170,106,0.45)", "glow_strong": "rgba(106,216,160,0.7)",
        "gradient": "linear-gradient(135deg, #000e08 0%, #001a10 50%, #000e08 100%)",
        "border_style": "2px solid #3aaa6a", "icon": "\u2b21", "flavor": "eldritch",
    },
}
FALLBACK_THEME = CLASS_THEMES["fighter"]

PERSONALITY_TAGS = [
    "Brooding", "Cheerful", "Stoic", "Dramatic", "Sarcastic",
    "Honorable", "Ruthless", "Chaotic", "Merciful", "Cunning",
    "Reckless", "Cautious", "Verbose", "Silent", "Charming",
    "Greedy", "Selfless", "Cowardly", "Brave", "Vengeful",
    "Scholarly", "Street-smart", "Naive", "World-weary", "Devout",
    "Bloodthirsty", "Protective", "Strategic", "Berserker", "Pacifist",
]


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

    # Pending join requests for campaigns this player owns
    owned_campaign_ids = [c.id for c in owned_campaigns]
    pending_requests = []
    if owned_campaign_ids:
        pending_requests = (
            db.query(JoinRequest)
            .filter(
                JoinRequest.campaign_id.in_(owned_campaign_ids),
                JoinRequest.status == "pending",
            )
            .all()
        )

    # Available stories for campaign creation
    from server.db.models import StoryTemplate
    available_stories = db.query(StoryTemplate).all()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "player": player,
        "characters": characters,
        "campaigns": all_campaigns,
        "active_sessions": active_sessions,
        "unassigned_chars": unassigned_chars,
        "pending_requests": pending_requests,
        "stories": available_stories,
        "can_play": player_can_play(player),
    })


@router.get("/browse")
def browse_campaigns(request: Request, db: DBSession = Depends(get_db)):
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    # Only show open campaigns on browse — private campaigns are invite-code only
    campaigns = (
        db.query(Campaign)
        .filter(Campaign.visibility == "open", Campaign.owner_id != player.id)
        .all()
    )
    private_campaigns = []  # Private campaigns don't appear on browse

    # Get player's unassigned characters for the join dropdown
    unassigned_chars = (
        db.query(Character)
        .filter(
            Character.player_id == player.id,
            Character.campaign_id == None,
            Character.is_enemy == False,
            Character.creation_complete == True,
        )
        .all()
    )

    # Get player's pending requests to show status
    pending_request_campaign_ids = set(
        r.campaign_id for r in db.query(JoinRequest).filter(
            JoinRequest.player_id == player.id,
            JoinRequest.status == "pending",
        ).all()
    )

    # Get campaigns the player is already in
    player_campaign_ids = set(
        c.campaign_id for c in db.query(Character).filter(
            Character.player_id == player.id,
            Character.campaign_id != None,
        ).all()
    )

    # Annotate campaigns with player count
    all_browse = []
    for c in campaigns + private_campaigns:
        if c.id in player_campaign_ids:
            continue  # Skip campaigns player is already in
        pc_count = (
            db.query(Character)
            .filter(
                Character.campaign_id == c.id,
                Character.is_enemy == False,
                Character.is_npc == False,
            )
            .count()
        )
        all_browse.append({
            "campaign": c,
            "player_count": pc_count,
            "has_pending_request": c.id in pending_request_campaign_ids,
        })

    return templates.TemplateResponse("browse.html", {
        "request": request,
        "player": player,
        "browse_campaigns": all_browse,
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
    if not player_can_play(player):
        return RedirectResponse(url="/subscription?reason=subscription_required", status_code=303)

    session = db.query(GameSession).filter(GameSession.id == session_id).first()
    if not session:
        return RedirectResponse(url="/dashboard", status_code=303)

    # Find this player's character in the campaign
    my_character = (
        db.query(Character)
        .filter(
            Character.player_id == player.id,
            Character.campaign_id == session.campaign_id,
            Character.is_enemy == False,
            Character.is_npc == False,
        )
        .first()
    )

    # Allow campaign owner even without a character, or players with characters
    is_owner = session.campaign.owner_id == player.id
    if not my_character and not is_owner:
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
        "my_character": my_character,
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

    theme = CLASS_THEMES.get(character.char_class.lower(), FALLBACK_THEME)

    return templates.TemplateResponse("character_profile.html", {
        "request": request,
        "player": player,
        "character": character,
        "sessions": sessions,
        "available_campaigns": available_campaigns,
        "theme": theme,
        "is_owner": character.player_id == player.id,
        "all_tags": PERSONALITY_TAGS,
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
