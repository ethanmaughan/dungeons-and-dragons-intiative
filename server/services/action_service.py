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
            "death_saves": c.death_saves or {"successes": 0, "failures": 0},
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


async def _resolve_post_turn(
    session_id, turn_number, game_state, session,
    was_already_in_combat, combat_start_event, player_took_combat_action,
    state_changes, db,
) -> list:
    """Run Phase 4 (enemy turns) and Phase 5 (dying PC turns).

    Returns list of enemy/dying turn result dicts for broadcasting.
    """
    from server.ai.combat_orchestrator import resolve_enemy_phase, resolve_dying_pc_turns

    enemy_turn_results = []
    characters = session.campaign.characters

    if game_state and game_state.game_mode == "combat":
        should_resolve = False

        if combat_start_event:
            first = (game_state.initiative_order or [{}])[0]
            should_resolve = first.get("is_enemy", False)
        elif was_already_in_combat and not state_changes.get("combat_ended") and player_took_combat_action:
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

    # Phase 5 — dying PC turns
    if game_state and game_state.game_mode == "combat" and not state_changes.get("combat_ended"):
        characters = session.campaign.characters
        dying_results = await resolve_dying_pc_turns(game_state, characters, db)
        for dr in dying_results:
            dying_log = GameLog(
                session_id=session_id,
                turn_number=turn_number,
                actor=dr["actor"],
                narration_text=dr["narration"],
                dice_rolls=dr["dice_rolls"],
                state_changes=dr["state_changes"],
                game_mode="combat",
            )
            db.add(dying_log)
            enemy_turn_results.append({
                "log": dying_log,
                "narration": dr["narration"],
                "actor": dr["actor"],
            })
        if dying_results:
            db.commit()

    return enemy_turn_results


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
                # Enemy's turn — try to resolve stuck enemy turns instead of rejecting
                from server.ai.combat_orchestrator import resolve_enemy_phase
                characters = session.campaign.characters
                enemy_results = await resolve_enemy_phase(game_state, characters, db)
                enemy_turn_results = []
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
                # Return the resolved enemy turns + updated state so the client unblocks
                return {
                    "log": None,
                    "characters": _build_char_states(characters),
                    "game_state": _build_gs_info(game_state),
                    "combat_start": None,
                    "enemy_turns": enemy_turn_results,
                }
            # else: it's another PC's turn. Let the message through to the DM
            # (for questions/conversation) but player_took_combat_action will be
            # False so the turn won't advance.

    # --- Slash commands: /endcombat, /rewind ---
    if (
        was_already_in_combat
        and action_text.lower().strip() in ("/endcombat", "/rewind")
    ):
        from server.engine.combat import end_combat
        is_rewind = action_text.lower().strip() == "/rewind"
        end_combat(game_state, characters, db)
        if is_rewind:
            for c in characters:
                if not c.is_npc and not c.is_enemy:
                    c.hp_current = c.hp_max
                    c.conditions = []
                    c.death_saves = {"successes": 0, "failures": 0}
        narration = (
            "--- Combat rewound — returning to before the encounter ---"
            if is_rewind else "--- Combat ended ---"
        )
        state_changes = {"combat_ended": True}
        if is_rewind:
            state_changes["rewind"] = True
        log_entry = GameLog(
            session_id=session_id,
            character_id=acting_character.id if acting_character else None,
            turn_number=turn_number,
            actor=acting_character.character_name if acting_character else "Player",
            action_text=action_text,
            narration_text=narration,
            dice_rolls=[],
            state_changes=state_changes,
            game_mode="exploration",
        )
        db.add(log_entry)
        db.commit()
        return {
            "log": log_entry,
            "characters": _build_char_states(session.campaign.characters),
            "game_state": _build_gs_info(game_state),
            "combat_start": None,
            "enemy_turns": [],
        }

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

    # ==========================================================
    # COMBAT TRACKER — bypass DM AI for combat actions
    # ==========================================================
    if was_already_in_combat and acting_character and not is_character_creation:
        from server.ai.combat_tracker import parse_combat_intent, execute_combat_turn

        intent = await parse_combat_intent(
            action_text, acting_character, characters, game_state,
        )

        if intent.get("intent") != "question" and not intent.get("route_to_dm"):
            # Execute mechanically — NO DM AI call
            ct_result = execute_combat_turn(
                intent, acting_character, characters, game_state, db,
            )
            narration = ct_result["narration"]
            dice_rolls = ct_result["dice_rolls"]
            state_changes = ct_result["state_changes"]

            is_my_turn = (
                game_state.current_turn_character_id == acting_character.id
            )
            player_took_combat_action = ct_result["turn_consumed"] and is_my_turn

            actor_name = acting_character.character_name
            log_entry = GameLog(
                session_id=session_id,
                character_id=acting_character.id,
                turn_number=turn_number,
                actor=actor_name,
                action_text=action_text,
                narration_text=narration,
                dice_rolls=dice_rolls,
                state_changes=state_changes,
                game_mode="combat",
            )
            db.add(log_entry)
            db.commit()

            # Run Phase 4 + 5 (enemy turns + dying PC turns)
            enemy_turn_results = await _resolve_post_turn(
                session_id, turn_number, game_state, session,
                was_already_in_combat, None, player_took_combat_action,
                state_changes, db,
            )

            return {
                "log": log_entry,
                "characters": _build_char_states(session.campaign.characters),
                "game_state": _build_gs_info(game_state),
                "combat_start": None,
                "enemy_turns": enemy_turn_results,
            }

        # else: question — fall through to DM AI path below

    # --- Load story context ---
    _chapter_context = None
    if not is_character_creation:
        from server.ai.story_engine import build_chapter_context
        _chapter_context = build_chapter_context(session.campaign_id, db)

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
        chapter_context=_chapter_context,
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

    # Detect if the player took a mechanical combat action (attack, spell, dodge, etc.)
    # Only counts if it's actually this player's turn — prevents out-of-turn actions
    # from advancing the initiative.
    is_my_turn = (
        acting_character
        and game_state
        and game_state.current_turn_character_id == acting_character.id
    )
    player_took_combat_action = is_my_turn and bool(
        any(r.get("type") == "player_attack" for r in dice_rolls)
        or state_changes.get("spells_cast")
        or state_changes.get("combat_action")
    )

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
    # MILESTONE CHECK — track story objective completion
    # ==========================================================
    if _chapter_context and not is_character_creation and game_state.game_mode != "combat":
        from server.ai.story_engine import (
            check_keyword_matches,
            confirm_objective,
            mark_objective_complete,
        )
        from server.services.story_service import get_current_chapter
        chapter_data = get_current_chapter(session.campaign_id, db)
        if chapter_data:
            keyword_hits = check_keyword_matches(narration, action_text, chapter_data)
            recent_narrations = [
                log.narration_text for log in recent_logs[-3:] if log.narration_text
            ] + [narration]

            for obj in keyword_hits:
                result = await confirm_objective(obj, recent_narrations, action_text)
                if result.get("completed"):
                    mark_objective_complete(
                        chapter_data["campaign_story_id"],
                        chapter_data["chapter_number"],
                        obj["key"],
                        result.get("summary", ""),
                        turn_number,
                        db,
                    )

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
    # PHASE 4+5 — Resolve enemy turns + dying PC turns
    # ==========================================================
    enemy_turn_results = []
    if not is_character_creation:
        enemy_turn_results = await _resolve_post_turn(
            session_id, turn_number, game_state, session,
            was_already_in_combat, combat_start_event, player_took_combat_action,
            state_changes, db,
        )

    return {
        "log": log_entry,
        "characters": _build_char_states(characters),
        "game_state": _build_gs_info(game_state),
        "combat_start": combat_start_event,
        "enemy_turns": enemy_turn_results,
    }
