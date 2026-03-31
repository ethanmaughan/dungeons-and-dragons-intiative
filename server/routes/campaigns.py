from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DBSession

from server.auth import get_current_player
from server.db.database import get_db
from server.db.models import Campaign, Character, GameState, Player
from server.db.models import Session as GameSession

router = APIRouter()


@router.post("/campaigns")
def create_campaign(
    request: Request,
    name: str = Form("New Adventure"),
    setting: str = Form("A classic fantasy world of swords and sorcery."),
    official_campaign: str = Form(""),
    creation_mode: str = Form("dm_guided"),
    db: DBSession = Depends(get_db),
):
    """Create a new campaign and start a session."""
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    # If an official campaign is selected, add it to the setting context
    if official_campaign:
        setting = (
            f"OFFICIAL CAMPAIGN: {official_campaign}\n\n"
            f"You are running an official D&D 5e adventure \"{official_campaign}\". "
            f"Follow the campaign's storyline, key NPCs, locations, encounters, and plot beats faithfully. "
            f"Start at the beginning of the adventure and guide the players through it.\n\n"
            f"Setting: {setting}"
        )

    # Create campaign
    campaign = Campaign(name=name, setting=setting)
    db.add(campaign)
    db.flush()

    # Create a placeholder character (to be filled in during creation)
    if creation_mode == "dm_guided":
        character = Character(
            campaign_id=campaign.id,
            player_id=player.id,
            player_name=player.display_name,
            character_name="Unnamed Adventurer",
            creation_complete=False,
        )
    else:
        # Quick form — redirect to the form page
        character = Character(
            campaign_id=campaign.id,
            player_id=player.id,
            player_name=player.display_name,
            character_name="Unnamed Adventurer",
            creation_complete=False,
        )
        db.add(character)
        db.flush()

        session = GameSession(campaign_id=campaign.id, session_number=1)
        db.add(session)
        db.flush()

        game_state = GameState(
            session_id=session.id,
            game_mode="exploration",
        )
        db.add(game_state)
        db.commit()
        return RedirectResponse(url=f"/character/{character.id}/create?session_id={session.id}", status_code=303)

    db.add(character)
    db.flush()

    # Create a session
    session = GameSession(campaign_id=campaign.id, session_number=1)
    db.add(session)
    db.flush()

    # Create game state in character_creation mode
    game_state = GameState(
        session_id=session.id,
        game_mode="character_creation",
        creation_step="race",
        environment_description="A wise sage sits across from you at a weathered oak table in a quiet corner of the tavern.",
    )
    db.add(game_state)
    db.commit()

    return RedirectResponse(url=f"/play/{session.id}", status_code=303)
