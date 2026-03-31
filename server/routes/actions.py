from fastapi import APIRouter, Depends, Form, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from server.db.database import get_db
from server.db.models import GameLog, GameState
from server.db.models import Session as GameSession
from server.ai.orchestrator import process_player_action
from server.engine.character import finalize_character
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

    # Handle character creation step logic
    if is_character_creation:
        current_step = game_state.creation_step or "greeting"
        pc = next((c for c in characters if not c.is_npc and not c.is_enemy), None)
        player_answer = action.strip()

        if pc:
            # Store the player's answer for the current step
            if current_step == "race":
                pc.race = player_answer.title()
            elif current_step == "class":
                pc.char_class = player_answer.title()
            elif current_step == "abilities":
                pass  # Abilities are handled by finalize_character
            elif current_step == "name":
                pc.character_name = player_answer.title()
            elif current_step == "confirm":
                # Player confirmed — finalize and switch to exploration
                current_choices = {
                    "race": pc.race,
                    "class": pc.char_class,
                    "name": pc.character_name,
                }
                finalize_character(pc, current_choices, game_state)

        # Advance to the next step (this is what the DM will present)
        step_order = ["greeting", "race", "class", "abilities", "name", "confirm", "done"]
        try:
            idx = step_order.index(current_step)
            game_state.creation_step = step_order[min(idx + 1, len(step_order) - 1)]
        except ValueError:
            game_state.creation_step = "race"

    # Call the DM orchestrator
    narration = await process_player_action(
        action=action,
        campaign=session.campaign,
        game_state=game_state,
        characters=characters,
        recent_logs=recent_logs,
        mode="character_creation" if is_character_creation and game_state.game_mode == "character_creation" else "play",
    )

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
