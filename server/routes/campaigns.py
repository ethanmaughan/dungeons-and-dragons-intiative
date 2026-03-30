from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DBSession

from server.db.database import get_db
from server.db.models import Campaign, Character, GameState
from server.db.models import Session as GameSession

router = APIRouter()


@router.post("/campaigns")
def create_campaign(
    name: str = "New Adventure",
    setting: str = "A classic fantasy world of swords and sorcery.",
    player_name: str = "Adventurer",
    character_name: str = "Hero",
    db: DBSession = Depends(get_db),
):
    """Create a new campaign with a default character and start a session."""
    # Create campaign
    campaign = Campaign(name=name, setting=setting)
    db.add(campaign)
    db.flush()

    # Create a default player character
    character = Character(
        campaign_id=campaign.id,
        player_name=player_name,
        character_name=character_name,
        race="Human",
        char_class="Fighter",
        level=1,
        hp_current=12,
        hp_max=12,
        ac=16,
        str_score=16,
        dex_score=14,
        con_score=14,
        int_score=10,
        wis_score=12,
        cha_score=8,
        proficiency_bonus=2,
    )
    db.add(character)
    db.flush()

    # Create a session
    session = GameSession(campaign_id=campaign.id, session_number=1)
    db.add(session)
    db.flush()

    # Create game state
    game_state = GameState(
        session_id=session.id,
        game_mode="exploration",
        environment_description="You find yourself in a dimly lit tavern. The smell of ale and roasted meat fills the air.",
    )
    db.add(game_state)
    db.commit()

    return RedirectResponse(url=f"/play/{session.id}", status_code=303)
