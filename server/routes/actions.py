from fastapi import APIRouter, Depends, Form, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from server.db.database import get_db
from server.db.models import GameLog, GameState
from server.db.models import Session as GameSession
from server.ai.orchestrator import process_player_action
from server.engine.character import (
    extract_character_choices,
    determine_creation_step,
    finalize_character,
)
from server.engine.action_processor import process_dm_response

templates = Jinja2Templates(directory="templates")
router = APIRouter()


@router.post("/action/{session_id}")
async def submit_action(
    request: Request,
    session_id: int,
    action: str = Form(...),
    db: DBSession = Depends(get_db),
):
    """Process a player action through the DM orchestrator."""
    session = db.query(GameSession).filter(GameSession.id == session_id).first()
    if not session:
        return {"error": "Session not found"}

    game_state = db.query(GameState).filter(GameState.session_id == session_id).first()
    characters = session.campaign.characters
    recent_logs = (
        db.query(GameLog)
        .filter(GameLog.session_id == session_id)
        .order_by(GameLog.id.desc())
        .limit(20)
        .all()
    )
    recent_logs.reverse()

    turn_number = len(recent_logs) + 1
    is_character_creation = game_state and game_state.game_mode == "character_creation"

    # Call the DM orchestrator
    narration = await process_player_action(
        action=action,
        campaign=session.campaign,
        game_state=game_state,
        characters=characters,
        recent_logs=recent_logs,
        mode="character_creation" if is_character_creation else "play",
    )

    # Handle character creation extraction
    if is_character_creation:
        # Build conversation text for extraction
        conversation_parts = []
        for log in recent_logs:
            if log.action_text:
                conversation_parts.append(f"Player: {log.action_text}")
            if log.narration_text:
                conversation_parts.append(f"DM: {log.narration_text}")
        conversation_parts.append(f"Player: {action}")
        conversation_parts.append(f"DM: {narration}")
        conversation_text = "\n".join(conversation_parts)

        # Extract choices
        choices = await extract_character_choices(conversation_text)

        if choices:
            # Find the player's character
            pc = next((c for c in characters if not c.is_npc and not c.is_enemy), None)
            if pc:
                # Update character with extracted choices
                if choices.get("race") and pc.race == "Human":
                    pc.race = choices["race"]
                if choices.get("class") and pc.char_class == "Fighter":
                    pc.char_class = choices["class"]
                if choices.get("name") and pc.character_name == "Unnamed Adventurer":
                    pc.character_name = choices["name"]

                # Determine current step
                current_choices = {
                    "race": pc.race if pc.race != "Human" else None,
                    "class": pc.char_class if pc.char_class != "Fighter" else None,
                    "name": pc.character_name if pc.character_name != "Unnamed Adventurer" else None,
                }
                new_step = determine_creation_step(current_choices)
                game_state.creation_step = new_step

                # Check if we're at confirm and player confirmed
                if new_step == "confirm" and any(
                    word in action.lower()
                    for word in ["yes", "ready", "let's go", "begin", "start", "confirm"]
                ):
                    finalize_character(pc, current_choices, game_state)

    # Process action tags (dice rolls, HP changes, etc.) for non-creation modes
    dice_rolls = []
    state_changes = {}
    if not is_character_creation:
        result = process_dm_response(narration, characters, game_state, db)
        narration = result["narration"]
        dice_rolls = result["dice_rolls"]
        state_changes = result["state_changes"]

    # Save to game log
    log_entry = GameLog(
        session_id=session_id,
        turn_number=turn_number,
        actor=characters[0].player_name if characters else "Player",
        action_text=action,
        narration_text=narration,
        dice_rolls=dice_rolls,
        state_changes=state_changes,
        game_mode=game_state.game_mode if game_state else "exploration",
    )
    db.add(log_entry)
    db.commit()

    # Return the new log entry as HTML partial (for HTMX)
    return templates.TemplateResponse("partials/narrative_entry.html", {
        "request": request,
        "log": log_entry,
    })
