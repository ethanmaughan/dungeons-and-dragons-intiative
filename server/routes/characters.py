import shutil
import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DBSession

from server.auth import get_current_player
from server.db.database import get_db
from server.db.models import Campaign, Character, GameState
from server.db.models import Session as GameSession
from server.engine.character import finalize_character

router = APIRouter()


@router.post("/characters")
async def create_character(
    request: Request,
    character_name: str = Form(...),
    race: str = Form("Human"),
    char_class: str = Form("Fighter"),
    avatar_file: UploadFile | None = File(None),
    avatar_url: str = Form(""),
    db: DBSession = Depends(get_db),
):
    """Create a standalone character (not tied to a campaign)."""
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    character = Character(
        player_id=player.id,
        player_name=player.display_name,
        character_name=character_name,
        race=race,
        char_class=char_class,
        campaign_id=None,
    )

    # Handle avatar
    if avatar_file and avatar_file.filename:
        ext = avatar_file.filename.rsplit(".", 1)[-1] if "." in avatar_file.filename else "png"
        filename = f"{uuid.uuid4().hex}.{ext}"
        filepath = f"static/uploads/avatars/{filename}"
        with open(filepath, "wb") as f:
            shutil.copyfileobj(avatar_file.file, f)
        character.avatar_url = f"/static/uploads/avatars/{filename}"
    elif avatar_url.strip():
        character.avatar_url = avatar_url.strip()

    # Finalize stats, equipment, spells
    finalize_character(character)

    db.add(character)
    db.commit()

    return RedirectResponse(url=f"/character/{character.id}", status_code=303)


@router.post("/characters/{character_id}/assign")
def assign_character(
    request: Request,
    character_id: int,
    campaign_id: int = Form(...),
    db: DBSession = Depends(get_db),
):
    """Assign a character to a campaign."""
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    character = db.query(Character).filter(Character.id == character_id).first()
    if not character or character.player_id != player.id:
        return RedirectResponse(url="/dashboard", status_code=303)

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        return RedirectResponse(url="/dashboard", status_code=303)

    character.campaign_id = campaign.id

    # Create a session if none exists for this campaign
    existing_session = db.query(GameSession).filter(
        GameSession.campaign_id == campaign.id,
        GameSession.status == "active",
    ).first()

    if not existing_session:
        session = GameSession(campaign_id=campaign.id, session_number=1)
        db.add(session)
        db.flush()

        game_state = GameState(
            session_id=session.id,
            game_mode="exploration",
            environment_description="Your adventure begins...",
        )
        db.add(game_state)

    db.commit()
    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/characters/{character_id}/unassign")
def unassign_character(
    request: Request,
    character_id: int,
    db: DBSession = Depends(get_db),
):
    """Remove a character from a campaign."""
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    character = db.query(Character).filter(Character.id == character_id).first()
    if not character or character.player_id != player.id:
        return RedirectResponse(url="/dashboard", status_code=303)

    character.campaign_id = None
    db.commit()
    return RedirectResponse(url="/dashboard", status_code=303)
