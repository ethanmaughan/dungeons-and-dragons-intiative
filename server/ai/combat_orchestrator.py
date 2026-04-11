"""Combat Orchestrator — resolves all enemy turns automatically.

After a player acts (or combat starts with enemies first), this walks
the initiative order, calls an enemy agent for each enemy turn, executes
the action mechanically, and stops when it reaches a PC's turn.

Manages EncounterState and EnvironmentState lifecycles for the tool pipeline.
"""

import traceback

from server.ai.enemy_agent import get_enemy_decision
from server.ai.tools import EncounterState, EnvironmentState
from server.engine.combat import (
    advance_turn,
    all_enemies_dead,
    all_pcs_down,
    end_combat,
    get_enemy_monster_data,
    is_enemy_turn,
)
from server.engine.death_saves import _set_dying
from server.engine.dice import attack_roll, roll, ability_modifier
from server.engine.action_processor import find_character


def _get_encounter_state(game_state, characters) -> EncounterState | None:
    """Restore or create EncounterState from game_state.active_effects."""
    effects = game_state.active_effects or {}
    if isinstance(effects, list):
        effects = {}
    snapshot = effects.get("encounter_state_snapshot")
    if snapshot:
        return EncounterState.from_snapshot(snapshot)
    # First enemy phase — create new
    enemy_types = list({
        c.char_class.lower() for c in characters
        if c.is_enemy and c.hp_current > 0
    })
    campaign_id = 0
    try:
        from server.db.models import Session as SessionModel
        session = next((s for s in [game_state.session] if s), None)
        if session:
            campaign_id = session.campaign_id
    except Exception:
        pass
    return EncounterState(campaign_id, enemy_types)


def _get_environment_state(game_state) -> EnvironmentState:
    """Restore or create EnvironmentState from game_state.active_effects."""
    effects = game_state.active_effects or {}
    if isinstance(effects, list):
        effects = {}
    snapshot = effects.get("environment_state")
    if snapshot:
        return EnvironmentState.from_snapshot(snapshot)
    env_state = EnvironmentState()
    # Register timers based on environment description
    try:
        from server.ai.tools.environment_shift import register_combat_timers
        register_combat_timers(env_state, game_state)
    except Exception:
        traceback.print_exc()
    return env_state


def _save_transient_state(game_state, encounter_state, env_state):
    """Persist transient state to game_state.active_effects."""
    effects = game_state.active_effects or {}
    if isinstance(effects, list):
        effects = {}
    effects["encounter_state_snapshot"] = encounter_state.to_snapshot()
    effects["environment_state"] = env_state.to_snapshot()
    game_state.active_effects = effects


async def resolve_enemy_phase(game_state, characters: list, db) -> list[dict]:
    """Resolve all consecutive enemy turns until it's a PC's turn.

    Returns list of result dicts, one per enemy turn resolved.
    Always leaves current_turn_character_id pointing at the next PC.
    """
    results = []
    max_turns = len(game_state.initiative_order or []) * 2
    turns_resolved = 0
    prev_round = game_state.round_number

    # Restore transient state
    encounter_state = _get_encounter_state(game_state, characters)
    env_state = _get_environment_state(game_state)

    # If the CURRENT turn is already an enemy (combat just started, enemy won init),
    # resolve it, then advance.
    if is_enemy_turn(game_state):
        result = await _resolve_current_enemy(game_state, characters, encounter_state, db)
        if result:
            results.append(result)
            # Track damage dealt by this enemy
            _track_enemy_damage(encounter_state, result)
        turns_resolved += 1

        # All PCs down? End combat — the party has fallen.
        if all_pcs_down(characters):
            _save_transient_state(game_state, encounter_state, env_state)
            end_combat(game_state, characters, db, encounter_state=encounter_state, outcome="defeat")
            results.append({
                "narration": "\n--- The party has fallen! ---\n",
                "dice_rolls": [],
                "state_changes": {"combat_ended": True, "tpk": True},
                "actor": "system",
            })
            return results

    # Walk forward through initiative, resolving enemies, stopping at next PC
    while turns_resolved < max_turns:
        next_entry = advance_turn(game_state)
        if not next_entry:
            break

        # Check if round incremented — tick environment
        if game_state.round_number > prev_round:
            encounter_state.round_number = game_state.round_number
            try:
                from server.ai.tools.environment_shift import process_round_end
                env_result = process_round_end(env_state, game_state.round_number, game_state)
                for narr in env_result.get("narrative_additions", []):
                    results.append({
                        "narration": f"\n{narr}",
                        "dice_rolls": [],
                        "state_changes": {},
                        "actor": "environment",
                    })
            except Exception:
                traceback.print_exc()
            prev_round = game_state.round_number

        # PC's turn — stop, let the player act
        if not next_entry.get("is_enemy", False):
            break

        # All enemies dead?
        if all_enemies_dead(characters):
            _save_transient_state(game_state, encounter_state, env_state)
            end_combat(game_state, characters, db, encounter_state=encounter_state, outcome="victory")
            results.append({
                "narration": "\n--- COMBAT ENDS — All enemies defeated! ---\n",
                "dice_rolls": [],
                "state_changes": {"combat_ended": True},
                "actor": "system",
            })
            break

        # Resolve this enemy's turn
        result = await _resolve_current_enemy(game_state, characters, encounter_state, db)
        if result:
            results.append(result)
            _track_enemy_damage(encounter_state, result)

        # Check again after the enemy acted
        if all_enemies_dead(characters):
            _save_transient_state(game_state, encounter_state, env_state)
            end_combat(game_state, characters, db, encounter_state=encounter_state, outcome="victory")
            results.append({
                "narration": "\n--- COMBAT ENDS — All enemies defeated! ---\n",
                "dice_rolls": [],
                "state_changes": {"combat_ended": True},
                "actor": "system",
            })
            break

        # All PCs down? End combat — the party has fallen.
        if all_pcs_down(characters):
            _save_transient_state(game_state, encounter_state, env_state)
            end_combat(game_state, characters, db, encounter_state=encounter_state, outcome="defeat")
            results.append({
                "narration": "\n--- The party has fallen! ---\n",
                "dice_rolls": [],
                "state_changes": {"combat_ended": True, "tpk": True},
                "actor": "system",
            })
            break

        turns_resolved += 1

    # Save transient state for next player action cycle
    _save_transient_state(game_state, encounter_state, env_state)

    return results


def _track_enemy_damage(encounter_state: EncounterState, result: dict):
    """Track damage dealt by enemies into the encounter state."""
    for roll_data in result.get("dice_rolls", []):
        if roll_data.get("type") == "damage":
            encounter_state.record_damage(False, roll_data.get("total", 0))


async def resolve_dying_pc_turns(game_state, characters: list, db) -> list[dict]:
    """Auto-resolve turns for dying/stable/dead PCs at the current turn position.

    After enemy phase resolves and current_turn points to a PC, if that PC is
    at 0 HP, auto-roll their death save (or skip if stable/dead) and advance.
    Loops through consecutive down PCs, resolving any enemies in between.
    """
    from server.engine.death_saves import is_dying, is_dead, is_stable, roll_death_save

    results = []
    max_iterations = len(game_state.initiative_order or [])

    for _ in range(max_iterations):
        current_id = game_state.current_turn_character_id
        pc = next((c for c in characters if c.id == current_id), None)

        if not pc or pc.is_enemy:
            break  # Not a PC — stop

        if pc.hp_current > 0:
            break  # PC is conscious — they can act normally

        if is_dead(pc):
            results.append({
                "narration": f"\n{pc.character_name}'s body lies motionless...",
                "dice_rolls": [],
                "state_changes": {"death_save_skip": pc.character_name},
                "actor": pc.character_name,
            })
            next_entry = advance_turn(game_state)
            if next_entry and next_entry.get("is_enemy", False):
                enemy_results = await resolve_enemy_phase(game_state, characters, db)
                results.extend(enemy_results)
            continue

        if is_stable(pc):
            results.append({
                "narration": f"\n{pc.character_name} is unconscious but stable.",
                "dice_rolls": [],
                "state_changes": {"death_save_skip": pc.character_name},
                "actor": pc.character_name,
            })
            next_entry = advance_turn(game_state)
            if next_entry and next_entry.get("is_enemy", False):
                enemy_results = await resolve_enemy_phase(game_state, characters, db)
                results.extend(enemy_results)
            continue

        # PC is dying — roll death save
        save_result = roll_death_save(pc)
        results.append({
            "narration": save_result["narration"],
            "dice_rolls": [{
                "type": "death_save",
                "roll": save_result["roll"],
                "outcome": save_result["outcome"],
            }],
            "state_changes": {"death_save": {
                "character": pc.character_name,
                "outcome": save_result["outcome"],
                "successes": save_result["successes"],
                "failures": save_result["failures"],
            }},
            "actor": pc.character_name,
        })

        if save_result["outcome"] == "nat20":
            # PC woke up with 1 HP — they get to act this turn
            break

        # Any other outcome: turn consumed, advance
        next_entry = advance_turn(game_state)
        if next_entry and next_entry.get("is_enemy", False):
            enemy_results = await resolve_enemy_phase(game_state, characters, db)
            results.extend(enemy_results)

        # If combat ended during enemy resolution, stop
        if game_state.game_mode != "combat":
            break

    return results


async def _resolve_current_enemy(
    game_state, characters: list,
    encounter_state: EncounterState | None = None,
    db=None,
) -> dict | None:
    """Resolve the current turn's enemy action. Returns result dict or None."""
    current_id = game_state.current_turn_character_id
    enemy = next((c for c in characters if c.id == current_id), None)

    if not enemy or enemy.hp_current <= 0 or not enemy.is_enemy:
        return None

    positions = dict(game_state.combat_positions) if game_state.combat_positions else None

    # Get decision from the tool pipeline (includes battlefield analysis)
    move_narration = ""
    try:
        decision = await get_enemy_decision(
            enemy, characters,
            positions=positions,
            encounter_state=encounter_state,
            db=db,
        )

        # Use movement from battlefield_tactics if available
        movement_path = decision.pop("movement_path", None)
        if movement_path is None and positions:
            # Fallback to legacy movement
            from server.engine.combat import compute_enemy_movement
            movement_path = compute_enemy_movement(enemy.id, positions)

        # Execute movement on grid
        if movement_path and positions:
            from server.engine.combat import execute_move
            for direction in movement_path:
                execute_move(enemy.id, direction, positions)
            game_state.combat_positions = positions
            if movement_path:
                move_narration = f"{enemy.character_name} moves {len(movement_path) * 5} feet closer. "

        # Add flavor text from personality tool if present
        flavor = decision.pop("flavor_text", "")

        result = _execute_enemy_action(enemy, decision, characters)
        if flavor:
            result["narration"] = f"\n*{flavor}*" + result["narration"]
        if move_narration:
            result["narration"] = move_narration + result["narration"]
        return result

    except Exception:
        traceback.print_exc()
        # Fallback: legacy movement + rule decision
        try:
            if positions:
                from server.engine.combat import compute_enemy_movement, execute_move
                moves = compute_enemy_movement(enemy.id, positions)
                for direction in moves:
                    execute_move(enemy.id, direction, positions)
                game_state.combat_positions = positions
                if moves:
                    move_narration = f"{enemy.character_name} moves {len(moves) * 5} feet closer. "

            from server.ai.enemy_agent import _rule_decision
            decision = _rule_decision(enemy, characters)
            result = _execute_enemy_action(enemy, decision, characters)
            if move_narration:
                result["narration"] = move_narration + result["narration"]
            return result
        except Exception:
            traceback.print_exc()
            return {
                "narration": f"\n{move_narration}{enemy.character_name} hesitates, unsure what to do.",
                "dice_rolls": [],
                "state_changes": {},
                "actor": enemy.character_name,
            }


def _execute_enemy_action(enemy, decision: dict, characters: list) -> dict:
    """Execute an enemy's combat action mechanically. Returns narration + state changes."""
    dice_rolls = []
    state_changes = {}
    action_data = decision.get("action_data", {})
    target_name = decision.get("target")
    action_name = decision.get("action", "attack")

    # Handle flee
    if action_name == "flee" or not target_name:
        narration = f"\n{enemy.character_name} turns and flees from the battle!"
        enemy.hp_current = 0
        state_changes["fled"] = enemy.character_name
        return {
            "narration": narration,
            "dice_rolls": dice_rolls,
            "state_changes": state_changes,
            "actor": enemy.character_name,
        }

    # Find target — must be an alive PC, never an ally
    target = find_character(characters, target_name)
    alive_pcs = [c for c in characters if not c.is_npc and not c.is_enemy and c.hp_current > 0]

    if not target or target.hp_current <= 0 or target.is_enemy:
        if not alive_pcs:
            return {
                "narration": f"\n{enemy.character_name} looks around but sees no standing foes.",
                "dice_rolls": [],
                "state_changes": {},
                "actor": enemy.character_name,
            }
        import secrets
        target = alive_pcs[secrets.randbelow(len(alive_pcs))]

    # Use monster's actual attack bonus and damage
    monster_data = get_enemy_monster_data(enemy)
    atk_bonus = action_data.get("attack_bonus", monster_data.get("attack_bonus", 3))
    damage_notation = action_data.get("damage", monster_data.get("damage", "1d6+1"))
    action_label = action_data.get("name", "attack")

    # Roll attack
    atk = attack_roll(atk_bonus)
    hit = atk["critical"] or (not atk["fumble"] and atk["total"] >= target.ac)

    dice_rolls.append({
        "type": "enemy_attack",
        "attacker": enemy.character_name,
        "target": target.character_name,
        "action": action_label,
        "roll": atk["rolls"][0],
        "modifier": atk["modifier"],
        "total": atk["total"],
        "target_ac": target.ac,
        "hit": hit,
        "critical": atk["critical"],
    })

    if atk["critical"]:
        crit_damage = _double_dice(damage_notation)
        dmg = roll(crit_damage)
        total_dmg = dmg["total"]

        old_hp = target.hp_current
        target.hp_current = max(0, target.hp_current - total_dmg)

        dice_rolls.append({"type": "damage", "total": total_dmg, "critical": True})
        state_changes.setdefault("hp_changes", []).append({
            "target": target.character_name, "old": old_hp, "new": target.hp_current, "change": -total_dmg,
        })

        down_msg = ""
        if target.hp_current <= 0 and old_hp > 0 and not target.is_enemy:
            _set_dying(target)
            state_changes["player_down"] = True
            down_msg = f"\n\n** {target.character_name} falls unconscious and is dying! **"

        narration = (
            f"\n{enemy.character_name} uses {action_label} on {target.character_name} — "
            f"**CRITICAL HIT!** (rolled {atk['rolls'][0]} + {atk['modifier']} = {atk['total']} vs AC {target.ac}) "
            f"dealing {total_dmg} damage! ({target.character_name}: HP {old_hp} -> {target.hp_current})"
            f"{down_msg}"
        )

    elif hit:
        dmg = roll(damage_notation)
        total_dmg = dmg["total"]

        old_hp = target.hp_current
        target.hp_current = max(0, target.hp_current - total_dmg)

        dice_rolls.append({"type": "damage", "total": total_dmg})
        state_changes.setdefault("hp_changes", []).append({
            "target": target.character_name, "old": old_hp, "new": target.hp_current, "change": -total_dmg,
        })

        down_msg = ""
        if target.hp_current <= 0 and old_hp > 0 and not target.is_enemy:
            _set_dying(target)
            state_changes["player_down"] = True
            down_msg = f"\n\n** {target.character_name} falls unconscious and is dying! **"

        narration = (
            f"\n{enemy.character_name} uses {action_label} on {target.character_name} — "
            f"Hit! (rolled {atk['rolls'][0]} + {atk['modifier']} = {atk['total']} vs AC {target.ac}) "
            f"dealing {total_dmg} damage. ({target.character_name}: HP {old_hp} -> {target.hp_current})"
            f"{down_msg}"
        )

    else:
        narration = (
            f"\n{enemy.character_name} uses {action_label} on {target.character_name} — "
            f"Miss! (rolled {atk['rolls'][0]} + {atk['modifier']} = {atk['total']} vs AC {target.ac})"
        )

    special = action_data.get("special")
    if special and hit:
        narration += f"\n({special})"

    return {
        "narration": narration,
        "dice_rolls": dice_rolls,
        "state_changes": state_changes,
        "actor": enemy.character_name,
    }


def _double_dice(notation: str) -> str:
    """Double dice count for critical hits. '1d6+2' -> '2d6+2'."""
    import re
    match = re.match(r"(\d*)d(\d+)([+-]\d+)?", notation.strip().lower())
    if not match:
        return notation
    count = int(match.group(1) or 1)
    sides = match.group(2)
    mod = match.group(3) or ""
    return f"{count * 2}d{sides}{mod}"
