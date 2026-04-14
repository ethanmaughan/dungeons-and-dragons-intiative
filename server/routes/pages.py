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
        "primary": "#a8c4e0", "secondary": "#5a7a98", "accent": "#d8e4f0",
        "glow": "rgba(168,196,224,0.5)", "glow_strong": "rgba(168,196,224,0.8)",
        "gradient": "linear-gradient(135deg, #18140e 0%, #1e2a38 50%, #18140e 100%)",
        "border_style": "2px solid #a8c4e0", "icon": "\u2694", "flavor": "soldier",
    },
    "wizard": {
        "primary": "#b88ce8", "secondary": "#6040a0", "accent": "#dcc0ff",
        "glow": "rgba(184,140,232,0.5)", "glow_strong": "rgba(184,140,232,0.8)",
        "gradient": "linear-gradient(135deg, #18140e 0%, #281848 50%, #18140e 100%)",
        "border_style": "2px solid #b88ce8", "icon": "\u2726", "flavor": "arcane",
    },
    "cleric": {
        "primary": "#e8d48a", "secondary": "#a08850", "accent": "#fff0c0",
        "glow": "rgba(232,212,138,0.5)", "glow_strong": "rgba(232,212,138,0.85)",
        "gradient": "linear-gradient(135deg, #18140e 0%, #382c14 50%, #18140e 100%)",
        "border_style": "2px solid #e8d48a", "icon": "\u2600", "flavor": "divine",
    },
    "rogue": {
        "primary": "#d06060", "secondary": "#5a4a48", "accent": "#e89090",
        "glow": "rgba(208,96,96,0.5)", "glow_strong": "rgba(232,144,144,0.75)",
        "gradient": "linear-gradient(135deg, #18140e 0%, #2a1818 50%, #18140e 100%)",
        "border_style": "2px solid #d06060", "icon": "\u25c6", "flavor": "shadow",
    },
    "ranger": {
        "primary": "#78b878", "secondary": "#3a6a3a", "accent": "#a8e0a8",
        "glow": "rgba(120,184,120,0.5)", "glow_strong": "rgba(120,184,120,0.75)",
        "gradient": "linear-gradient(135deg, #18140e 0%, #1a2e18 50%, #18140e 100%)",
        "border_style": "2px solid #78b878", "icon": "\U0001f3f9", "flavor": "wilderness",
    },
    "barbarian": {
        "primary": "#e85830", "secondary": "#883818", "accent": "#f09070",
        "glow": "rgba(232,88,48,0.55)", "glow_strong": "rgba(232,88,48,0.85)",
        "gradient": "linear-gradient(135deg, #18140e 0%, #301808 50%, #18140e 100%)",
        "border_style": "2px solid #e85830", "icon": "\u26a1", "flavor": "fury",
    },
    "bard": {
        "primary": "#b868d8", "secondary": "#6028a0", "accent": "#d8a0f0",
        "glow": "rgba(184,104,216,0.5)", "glow_strong": "rgba(216,160,240,0.75)",
        "gradient": "linear-gradient(135deg, #18140e 0%, #241840 50%, #18140e 100%)",
        "border_style": "2px dashed #b868d8", "icon": "\u266a", "flavor": "performance",
    },
    "paladin": {
        "primary": "#90c8e8", "secondary": "#4878a0", "accent": "#d8f0ff",
        "glow": "rgba(144,200,232,0.55)", "glow_strong": "rgba(216,240,255,0.8)",
        "gradient": "linear-gradient(135deg, #18140e 0%, #182838 50%, #18140e 100%)",
        "border_style": "2px solid #90c8e8", "icon": "\u2726", "flavor": "holy",
    },
    "druid": {
        "primary": "#98c050", "secondary": "#507820", "accent": "#c8e890",
        "glow": "rgba(152,192,80,0.5)", "glow_strong": "rgba(200,232,144,0.75)",
        "gradient": "linear-gradient(135deg, #18140e 0%, #1e3010 50%, #18140e 100%)",
        "border_style": "2px solid #98c050", "icon": "\U0001f33f", "flavor": "nature",
    },
    "monk": {
        "primary": "#e0d080", "secondary": "#706850", "accent": "#f0e8b8",
        "glow": "rgba(224,208,128,0.45)", "glow_strong": "rgba(240,232,184,0.7)",
        "gradient": "linear-gradient(135deg, #18140e 0%, #2c2818 50%, #18140e 100%)",
        "border_style": "1px solid #e0d080", "icon": "\u25ef", "flavor": "discipline",
    },
    "sorcerer": {
        "primary": "#60d0ff", "secondary": "#2078b0", "accent": "#a0e8ff",
        "glow": "rgba(96,208,255,0.55)", "glow_strong": "rgba(160,232,255,0.85)",
        "gradient": "linear-gradient(135deg, #18140e 0%, #102840 60%, #18140e 100%)",
        "border_style": "2px solid #60d0ff", "icon": "\u2605", "flavor": "wild magic",
    },
    "warlock": {
        "primary": "#50d090", "secondary": "#206840", "accent": "#80f0b8",
        "glow": "rgba(80,208,144,0.5)", "glow_strong": "rgba(128,240,184,0.75)",
        "gradient": "linear-gradient(135deg, #18140e 0%, #102018 50%, #18140e 100%)",
        "border_style": "2px solid #50d090", "icon": "\u2b21", "flavor": "eldritch",
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
