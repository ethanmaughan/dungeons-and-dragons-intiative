"""Combat Orchestrator — resolves all enemy turns automatically after a player acts.

Flow:
1. Player acts (processed by action_service)
2. Orchestrator checks initiative order
3. Advances turn. For each enemy turn:
   a. Calls EnemyAgent for tactical decision
   b. Executes the action mechanically (dice rolls, HP changes)
   c. Generates narration text
   d. Advances to next turn
4. Stops when it's a PC's turn (returns accumulated results)
"""

from server.ai.enemy_agent import get_enemy_decision
from server.engine.combat import (
    advance_turn,
    all_enemies_dead,
    end_combat,
    get_enemy_monster_data,
    is_enemy_turn,
)
from server.engine.dice import attack_roll, roll, ability_modifier
from server.engine.action_processor import find_character


async def resolve_enemy_phase(game_state, characters: list, db) -> list[dict]:
    """Resolve all enemy turns until it's a PC's turn.

    Handles two scenarios:
    1. Normal: player just acted → advance past them → resolve enemies → stop at next PC
    2. Combat start: if first in initiative is an enemy, resolve them before any PC acts

    Returns: list of {
        "narration": str,
        "dice_rolls": list,
        "state_changes": dict,
        "actor": str,
    }
    One entry per enemy turn resolved.
    """
    results = []

    # Safety limit to prevent infinite loops
    max_turns = len(game_state.initiative_order or []) * 2
    turns_resolved = 0

    # First: check if the CURRENT turn is an enemy (happens when combat just started
    # and an enemy won initiative). Resolve it before advancing.
    if is_enemy_turn(game_state):
        current_id = game_state.current_turn_character_id
        enemy = None
        for c in characters:
            if c.id == current_id:
                enemy = c
                break
        if enemy and enemy.hp_current > 0:
            decision = await get_enemy_decision(enemy, characters)
            result = _execute_enemy_action(enemy, decision, characters)
            results.append(result)
            turns_resolved += 1

    while turns_resolved < max_turns:
        # Advance to next turn
        next_entry = advance_turn(game_state)
        if not next_entry:
            break

        # If it's a PC's turn, stop — wait for player input
        if not next_entry.get("is_enemy", False):
            break

        # Check if combat should end
        if all_enemies_dead(characters):
            end_combat(game_state, characters, db)
            results.append({
                "narration": "\n--- COMBAT ENDS — All enemies defeated! ---\n",
                "dice_rolls": [],
                "state_changes": {"combat_ended": True},
                "actor": "system",
            })
            break

        # Find the enemy character
        enemy = None
        for c in characters:
            if c.id == next_entry["character_id"]:
                enemy = c
                break

        if not enemy or enemy.hp_current <= 0:
            turns_resolved += 1
            continue

        # Get the enemy's decision from the AI agent
        decision = await get_enemy_decision(enemy, characters)

        # Execute the decision
        result = _execute_enemy_action(enemy, decision, characters)
        results.append(result)

        # Check if any PC is down — combat might end
        if all_enemies_dead(characters):
            end_combat(game_state, characters, db)
            results.append({
                "narration": "\n--- COMBAT ENDS — All enemies defeated! ---\n",
                "dice_rolls": [],
                "state_changes": {"combat_ended": True},
                "actor": "system",
            })
            break

        turns_resolved += 1

    return results


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
        # Mark enemy as out of combat (set HP to 0 to remove from initiative)
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

    # Reject if target is another enemy (friendly fire), dead, or not found
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

    # Use monster's actual attack bonus and damage from action data
    atk_bonus = action_data.get("attack_bonus", get_enemy_monster_data(enemy).get("attack_bonus", 3))
    damage_notation = action_data.get("damage", get_enemy_monster_data(enemy).get("damage", "1d6+1"))
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
        # Critical: double the dice (not the modifier)
        # Parse damage notation to double dice count
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
        if target.hp_current <= 0:
            state_changes["player_down"] = True
            down_msg = f"\n\n** {target.character_name} falls unconscious! **"

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
        if target.hp_current <= 0:
            state_changes["player_down"] = True
            down_msg = f"\n\n** {target.character_name} falls unconscious! **"

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

    # Add special ability text if present
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
    """Double the dice count for critical hits. '1d6+2' → '2d6+2', '2d4+2' → '4d4+2'."""
    import re
    match = re.match(r"(\d*)d(\d+)([+-]\d+)?", notation.strip().lower())
    if not match:
        return notation
    count = int(match.group(1) or 1)
    sides = match.group(2)
    mod = match.group(3) or ""
    return f"{count * 2}d{sides}{mod}"
