"""Shared action processing logic used by both HTTP and WebSocket handlers.

Combat flow:
1. DM narrates encounter + emits [COMBAT:start:enemies] tag + STOPS
2. We extract the tag, keep only narration BEFORE it
3. Server creates enemies, rolls initiative, broadcasts as separate event
4. Turn system takes over: enemy agents act in order, then PC is prompted
"""

import re

from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from server.db.models import Character, GameLog, GameState
from server.db.models import Session as GameSession
from server.ai.orchestrator import process_player_action
from server.engine.character import finalize_character
from server.engine.action_processor import process_dm_response

templates = Jinja2Templates(directory="templates")

# Patterns for extracting/stripping combat tags from DM response
COMBAT_START_RE = re.compile(r"\[COMBAT:start:([^\]]+)\]")
ENEMY_TAG_RE = re.compile(r"\[ENEMY_(?:TURN|ATTACK):[^\]]+\]")
# The "--- COMBAT BEGINS ---...---" block that _handle_combat inserts
COMBAT_BLOCK_RE = re.compile(
    r"\n*---\s*COMBAT BEGINS\s*---.*?---\s*\n*",
    re.DOTALL,
)


def _build_char_states(characters):
    """Build character state dicts for broadcasting."""
    return [
        {
            "id": c.id,
            "character_name": c.character_name,
            "hp_current": c.hp_current,
            "hp_max": c.hp_max,
            "ac": c.ac,
            "conditions": c.conditions or [],
            "is_enemy": c.is_enemy,
            "is_npc": c.is_npc,
            "player_id": c.player_id,
        }
        for c in characters
        if not (c.is_enemy and c.hp_current <= 0)
    ]


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


def _extract_and_clean_combat(narration: str) -> tuple[str, list[str] | None]:
    """Extract [COMBAT:start:enemies] and return (clean_narration, enemy_list_or_None).

    Keeps ONLY text before the tag — everything after is truncated because
    the server handles initiative, turn order, and prompting from here.
    """
    match = COMBAT_START_RE.search(narration)
    if not match:
        return narration, None

    enemies = [e.strip() for e in match.group(1).split(",") if e.strip()]
    # Keep only narration before the tag
    clean = narration[:match.start()].rstrip()
    return clean, enemies


def _clean_combat_noise(narration: str) -> str:
    """Strip all combat-related noise from narration:
    - Enemy action tags (orchestrator handles these)
    - The '--- COMBAT BEGINS ---' block (now a separate broadcast)
    """
    narration = ENEMY_TAG_RE.sub("", narration)
    narration = COMBAT_BLOCK_RE.sub("", narration)
    return narration.strip()


async def process_action(
    session_id: int,
    player_id: int,
    action_text: str,
    db: DBSession,
) -> dict:
    """Process a player action. Returns structured result for broadcasting.

    Returns: {
        "log": GameLog,
        "characters": [...],
        "game_state": {...},
        "combat_start": {...} or None,
        "enemy_turns": [...],
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
    was_already_in_combat = game_state and game_state.game_mode == "combat"

    # --- Turn locking ---
    if (
        was_already_in_combat
        and not is_character_creation
        and acting_character
        and game_state.current_turn_character_id
    ):
        current_turn_id = game_state.current_turn_character_id
        if current_turn_id != acting_character.id:
            current_entry = next(
                (e for e in (game_state.initiative_order or [])
                 if e["character_id"] == current_turn_id),
                None,
            )
            if current_entry and current_entry.get("is_enemy", False):
                return {"error": "It's not your turn — enemies are acting"}

    # --- Character creation ---
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
                finalize_character(pc, {"race": pc.race, "class": pc.char_class, "name": pc.character_name}, game_state)

        step_order = ["greeting", "race", "class", "abilities", "name", "confirm", "done"]
        try:
            idx = step_order.index(current_step)
            game_state.creation_step = step_order[min(idx + 1, len(step_order) - 1)]
        except ValueError:
            game_state.creation_step = "race"

    # --- Call DM ---
    action_for_ai = action_text
    if acting_character and not is_character_creation:
        action_for_ai = f"[{acting_character.character_name}]: {action_text}"

    narration = await process_player_action(
        action=action_for_ai,
        campaign=session.campaign,
        game_state=game_state,
        characters=characters,
        recent_logs=recent_logs,
        mode="character_creation" if is_character_creation else "play",
    )

    # ==========================================================
    # PHASE 1 — Extract combat trigger, clean narration
    # ==========================================================
    combat_enemies = None
    if not is_character_creation:
        narration, combat_enemies = _extract_and_clean_combat(narration)
        narration = _clean_combat_noise(narration)

    # ==========================================================
    # PHASE 2 — Process remaining tags (rolls, HP, spells, etc.)
    # ==========================================================
    dice_rolls = []
    state_changes = {}
    if not is_character_creation:
        result = process_dm_response(narration, characters, game_state, db)
        narration = result["narration"]
        # Strip any combat block that _handle_combat might have inserted
        narration = _clean_combat_noise(narration)
        dice_rolls = result["dice_rolls"]
        state_changes = result["state_changes"]

    # Save the DM's narrative to game log
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

    # ==========================================================
    # PHASE 3 — Combat start (separate event)
    # ==========================================================
    combat_start_event = None
    combat_just_started = bool(combat_enemies) or state_changes.get("combat_started")

    if combat_just_started and not was_already_in_combat:
        from server.engine.combat import start_combat

        if game_state.game_mode != "combat":
            # We need to call start_combat (our extraction prevented _handle_combat)
            characters = session.campaign.characters
            combat_result = start_combat(
                combat_enemies or [], characters, game_state, session.campaign_id, db
            )
            db.commit()
            characters = session.campaign.characters
        else:
            # _handle_combat already ran start_combat — just read the state
            characters = session.campaign.characters

        if game_state.initiative_order:
            init_lines = [
                f"{e['character_name']}: {e['initiative']}"
                for e in game_state.initiative_order
            ]
            combat_start_event = {
                "initiative_order": game_state.initiative_order,
                "initiative_summary": "\n".join(init_lines),
                "round": game_state.round_number or 1,
            }

    # ==========================================================
    # PHASE 4 — Resolve enemy turns via the turn system
    # ==========================================================
    enemy_turn_results = []
    if game_state and game_state.game_mode == "combat" and not is_character_creation:
        from server.ai.combat_orchestrator import resolve_enemy_phase

        should_resolve = False

        if combat_start_event:
            # Combat JUST started. Resolve enemies only if they go first.
            first = (game_state.initiative_order or [{}])[0]
            should_resolve = first.get("is_enemy", False)
        elif was_already_in_combat and not state_changes.get("combat_ended"):
            # Normal turn: player just acted, resolve enemies until next PC
            should_resolve = True

        if should_resolve:
            characters = session.campaign.characters
            enemy_results = await resolve_enemy_phase(game_state, characters, db)
            for er in enemy_results:
                enemy_log = GameLog(
                    session_id=session_id,
                    turn_number=turn_number,
                    actor=er["actor"],
                    narration_text=er["narration"],
                    dice_rolls=er["dice_rolls"],
                    state_changes=er["state_changes"],
                    game_mode="combat",
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
        "combat_start": combat_start_event,
        "enemy_turns": enemy_turn_results,
    }
