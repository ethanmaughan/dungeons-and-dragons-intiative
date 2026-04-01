"""Shared action processing logic used by both HTTP and WebSocket handlers."""

from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from server.db.models import Character, GameLog, GameState
from server.db.models import Session as GameSession
from server.ai.orchestrator import process_player_action
from server.engine.character import finalize_character
from server.engine.action_processor import process_dm_response

templates = Jinja2Templates(directory="templates")


def _build_char_states(characters):
    """Build character state dicts for broadcasting."""
    states = []
    for c in characters:
        if c.is_enemy and c.hp_current <= 0:
            continue
        states.append({
            "id": c.id,
            "character_name": c.character_name,
            "hp_current": c.hp_current,
            "hp_max": c.hp_max,
            "ac": c.ac,
            "conditions": c.conditions or [],
            "is_enemy": c.is_enemy,
            "is_npc": c.is_npc,
            "player_id": c.player_id,
        })
    return states


def _build_gs_info(game_state):
    """Build game state dict for broadcasting."""
    if not game_state:
        return {}
    return {
        "game_mode": game_state.game_mode,
        "round_number": game_state.round_number,
        "current_turn_character_id": game_state.current_turn_character_id,
        "initiative_order": game_state.initiative_order or [],
    }


async def process_action(
    session_id: int,
    player_id: int,
    action_text: str,
    db: DBSession,
) -> dict:
    """Process a player action and return result dict.

    Returns: {
        "log": GameLog entry (player's action),
        "characters": list of character state dicts for broadcasting,
        "game_state": dict of current game state,
        "enemy_turns": list of dicts for enemy turn results (combat only),
    }
    """
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

    # Find the acting character (the PC belonging to this player in this campaign)
    acting_character = (
        db.query(Character)
        .filter(
            Character.player_id == player_id,
            Character.campaign_id == session.campaign_id,
            Character.is_enemy == False,
            Character.is_npc == False,
        )
        .first()
    )

    is_character_creation = game_state and game_state.game_mode == "character_creation"

    # --- Turn locking: in combat, only the player whose character's turn it is can act ---
    if (
        game_state
        and game_state.game_mode == "combat"
        and not is_character_creation
        and acting_character
        and game_state.current_turn_character_id
    ):
        current_turn_id = game_state.current_turn_character_id
        # Check if it's this player's character's turn
        if current_turn_id != acting_character.id:
            # Check if current turn belongs to ANY PC (flexible: any player can act on a PC turn)
            # but reject if it's an enemy's turn
            current_entry = None
            for entry in (game_state.initiative_order or []):
                if entry["character_id"] == current_turn_id:
                    current_entry = entry
                    break
            if current_entry and current_entry.get("is_enemy", False):
                return {"error": "It's not your turn — enemies are acting"}

    # Handle character creation step logic
    if is_character_creation:
        current_step = game_state.creation_step or "greeting"
        pc = acting_character or next((c for c in characters if not c.is_npc and not c.is_enemy), None)
        player_answer = action_text.strip()

        if pc:
            if current_step == "race":
                pc.race = player_answer.title()
            elif current_step == "class":
                pc.char_class = player_answer.title()
            elif current_step == "abilities":
                pass
            elif current_step == "name":
                pc.character_name = player_answer.title()
            elif current_step == "confirm":
                current_choices = {
                    "race": pc.race,
                    "class": pc.char_class,
                    "name": pc.character_name,
                }
                finalize_character(pc, current_choices, game_state)

        step_order = ["greeting", "race", "class", "abilities", "name", "confirm", "done"]
        try:
            idx = step_order.index(current_step)
            game_state.creation_step = step_order[min(idx + 1, len(step_order) - 1)]
        except ValueError:
            game_state.creation_step = "race"

    # Prefix action with character name for multiplayer context
    action_for_ai = action_text
    if acting_character and not is_character_creation:
        action_for_ai = f"[{acting_character.character_name}]: {action_text}"

    # Call the DM orchestrator
    narration = await process_player_action(
        action=action_for_ai,
        campaign=session.campaign,
        game_state=game_state,
        characters=characters,
        recent_logs=recent_logs,
        mode="character_creation" if is_character_creation and game_state.game_mode == "character_creation" else "play",
    )

    # Process action tags for non-creation modes
    dice_rolls = []
    state_changes = {}
    if not is_character_creation:
        # Strip enemy action tags from DM response if in combat — the orchestrator
        # handles enemy turns now. This prevents enemies from acting twice (once from
        # DM tags, once from the orchestrator).
        if game_state and game_state.game_mode == "combat":
            import re
            narration = re.sub(r"\[ENEMY_TURN:[^\]]+\]", "", narration)
            narration = re.sub(r"\[ENEMY_ATTACK:[^\]]+\]", "", narration)

        result = process_dm_response(narration, characters, game_state, db)
        narration = result["narration"]
        dice_rolls = result["dice_rolls"]
        state_changes = result["state_changes"]

    # Save player action to game log
    actor_name = acting_character.character_name if acting_character else "Player"
    log_entry = GameLog(
        session_id=session_id,
        character_id=acting_character.id if acting_character else None,
        turn_number=turn_number,
        actor=actor_name,
        action_text=action_text,
        narration_text=narration,
        dice_rolls=dice_rolls,
        state_changes=state_changes,
        game_mode=game_state.game_mode if game_state else "exploration",
    )
    db.add(log_entry)
    db.commit()

    # --- Auto-resolve enemy turns if in combat ---
    enemy_turn_results = []
    if (
        game_state
        and game_state.game_mode == "combat"
        and not is_character_creation
        and not state_changes.get("combat_ended")
    ):
        from server.ai.combat_orchestrator import resolve_enemy_phase

        # Refresh characters list (combat may have created new enemies)
        characters = session.campaign.characters
        enemy_results = await resolve_enemy_phase(game_state, characters, db)

        for er in enemy_results:
            # Save each enemy turn to game log
            enemy_log = GameLog(
                session_id=session_id,
                turn_number=turn_number,
                actor=er["actor"],
                action_text=None,
                narration_text=er["narration"],
                dice_rolls=er["dice_rolls"],
                state_changes=er["state_changes"],
                game_mode=game_state.game_mode if game_state else "combat",
            )
            db.add(enemy_log)
            enemy_turn_results.append({
                "log": enemy_log,
                "narration": er["narration"],
                "actor": er["actor"],
            })

        if enemy_turn_results:
            db.commit()

    return {
        "log": log_entry,
        "characters": _build_char_states(characters),
        "game_state": _build_gs_info(game_state),
        "enemy_turns": enemy_turn_results,
    }
