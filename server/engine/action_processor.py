"""Parse DM action tags, execute mechanics, and replace with results."""

import re
import traceback

from server.engine.dice import (
    ability_check,
    ability_modifier,
    attack_roll,
    roll,
    saving_throw,
)

# Matches tags like [ROLL:ability_check:STR:15] or [HP:Hero:-7]
TAG_PATTERN = re.compile(r"\[([A-Z]+):([^\]]+)\]")

# Map ability abbreviations to character attribute names
ABILITY_MAP = {
    "STR": "str_score",
    "DEX": "dex_score",
    "CON": "con_score",
    "INT": "int_score",
    "WIS": "wis_score",
    "CHA": "cha_score",
}


def find_character(characters, name: str):
    """Find a character by name (case-insensitive partial match)."""
    name_lower = name.lower().strip()
    for c in characters:
        if c.character_name.lower() == name_lower:
            return c
        if name_lower in c.character_name.lower():
            return c
    return None


def process_dm_response(raw_text: str, characters: list, game_state, db) -> dict:
    """Parse DM action tags, execute mechanics, replace with results.

    Returns: {
        "narration": str (processed text),
        "dice_rolls": list of roll results,
        "state_changes": dict of changes made,
    }
    """
    dice_rolls = []
    state_changes = {}
    processed = raw_text

    for match in TAG_PATTERN.finditer(raw_text):
        tag_type = match.group(1)
        params = match.group(2).split(":")
        replacement = ""

        try:
            if tag_type == "ROLL":
                replacement = _handle_roll(params, characters, dice_rolls)
            elif tag_type == "HP":
                replacement = _handle_hp(params, characters, state_changes)
            elif tag_type == "CONDITION":
                replacement = _handle_condition(params, characters, state_changes)
            elif tag_type == "INVENTORY":
                replacement = _handle_inventory(params, characters, state_changes)
            elif tag_type == "COMBAT":
                replacement = _handle_combat(params, characters, game_state, state_changes, db)
            else:
                replacement = f"[{tag_type}:{match.group(2)}]"
        except Exception:
            traceback.print_exc()
            replacement = f"[error processing {tag_type}]"

        processed = processed.replace(match.group(0), replacement, 1)

    return {
        "narration": processed,
        "dice_rolls": dice_rolls,
        "state_changes": state_changes,
    }


def _handle_roll(params: list, characters: list, dice_rolls: list) -> str:
    """Handle [ROLL:type:params] tags."""
    if len(params) < 1:
        return "[invalid roll]"

    roll_type = params[0].lower()

    if roll_type == "ability_check" and len(params) >= 3:
        # [ROLL:ability_check:ABILITY:DC]
        ability = params[1].upper()
        dc = int(params[2])
        attr = ABILITY_MAP.get(ability)

        # Find the first player character
        pc = next((c for c in characters if not c.is_npc and not c.is_enemy), None)
        if not pc or not attr:
            return "[roll failed]"

        score = getattr(pc, attr, 10)
        result = ability_check(score, pc.proficiency_bonus)
        dice_rolls.append({
            "type": "ability_check",
            "ability": ability,
            "dc": dc,
            "roll": result["rolls"][0],
            "modifier": result["modifier"],
            "total": result["total"],
            "success": result["total"] >= dc,
        })

        success = "Success" if result["total"] >= dc else "Failure"
        return f"({ability} check: rolled {result['rolls'][0]} + {result['modifier']} = {result['total']} vs DC {dc} — {success}!)"

    elif roll_type == "saving_throw" and len(params) >= 3:
        # [ROLL:saving_throw:ABILITY:DC]
        ability = params[1].upper()
        dc = int(params[2])
        attr = ABILITY_MAP.get(ability)

        pc = next((c for c in characters if not c.is_npc and not c.is_enemy), None)
        if not pc or not attr:
            return "[roll failed]"

        score = getattr(pc, attr, 10)
        result = saving_throw(score, pc.proficiency_bonus)
        dice_rolls.append({
            "type": "saving_throw",
            "ability": ability,
            "dc": dc,
            "roll": result["rolls"][0],
            "modifier": result["modifier"],
            "total": result["total"],
            "success": result["total"] >= dc,
        })

        success = "Success" if result["total"] >= dc else "Failure"
        return f"({ability} save: rolled {result['rolls'][0]} + {result['modifier']} = {result['total']} vs DC {dc} — {success}!)"

    elif roll_type == "attack" and len(params) >= 2:
        # [ROLL:attack:TARGET]
        target_name = params[1]
        pc = next((c for c in characters if not c.is_npc and not c.is_enemy), None)
        target = find_character(characters, target_name)

        if not pc:
            return "[roll failed]"

        # Calculate attack bonus (STR or DEX based + proficiency)
        str_mod = ability_modifier(pc.str_score)
        dex_mod = ability_modifier(pc.dex_score)
        attack_mod = max(str_mod, dex_mod) + pc.proficiency_bonus

        result = attack_roll(attack_mod)
        target_ac = target.ac if target else 15

        hit = result["critical"] or (not result["fumble"] and result["total"] >= target_ac)
        dice_rolls.append({
            "type": "attack",
            "target": target_name,
            "roll": result["rolls"][0],
            "modifier": result["modifier"],
            "total": result["total"],
            "target_ac": target_ac,
            "hit": hit,
            "critical": result["critical"],
            "fumble": result["fumble"],
        })

        if result["critical"]:
            return f"(Attack: rolled {result['rolls'][0]} — CRITICAL HIT!)"
        elif result["fumble"]:
            return f"(Attack: rolled {result['rolls'][0]} — Critical miss!)"
        else:
            hit_text = "Hit" if hit else "Miss"
            return f"(Attack: rolled {result['rolls'][0]} + {result['modifier']} = {result['total']} vs AC {target_ac} — {hit_text}!)"

    elif roll_type == "damage" and len(params) >= 2:
        # [ROLL:damage:NOTATION]
        notation = params[1]
        result = roll(notation)
        dice_rolls.append({
            "type": "damage",
            "notation": notation,
            "rolls": result["rolls"],
            "modifier": result["modifier"],
            "total": result["total"],
        })
        return f"({result['total']} damage)"

    else:
        # Generic roll
        try:
            result = roll(params[0])
            dice_rolls.append({
                "type": "generic",
                "notation": params[0],
                "rolls": result["rolls"],
                "total": result["total"],
            })
            return f"(rolled {result['total']})"
        except ValueError:
            return f"[invalid roll: {params[0]}]"


def _handle_hp(params: list, characters: list, state_changes: dict) -> str:
    """Handle [HP:TARGET:CHANGE] tags."""
    if len(params) < 2:
        return ""

    target_name = params[0]
    change = int(params[1])

    target = find_character(characters, target_name)
    if not target:
        return ""

    old_hp = target.hp_current
    target.hp_current = max(0, min(target.hp_max, target.hp_current + change))

    state_changes.setdefault("hp_changes", []).append({
        "target": target.character_name,
        "old": old_hp,
        "new": target.hp_current,
        "change": change,
    })

    if change < 0:
        return f"({target.character_name}: {abs(change)} damage, HP {old_hp} → {target.hp_current})"
    else:
        return f"({target.character_name}: healed {change}, HP {old_hp} → {target.hp_current})"


def _handle_condition(params: list, characters: list, state_changes: dict) -> str:
    """Handle [CONDITION:TARGET:CONDITION] tags."""
    if len(params) < 2:
        return ""

    target_name = params[0]
    condition = params[1]

    target = find_character(characters, target_name)
    if not target:
        return ""

    if target.conditions is None:
        target.conditions = []
    if condition not in target.conditions:
        target.conditions = target.conditions + [condition]

    state_changes.setdefault("conditions", []).append({
        "target": target.character_name,
        "condition": condition,
    })

    return f"({target.character_name} is now {condition})"


def _handle_inventory(params: list, characters: list, state_changes: dict) -> str:
    """Handle [INVENTORY:add:ITEM] or [INVENTORY:remove:ITEM] tags."""
    if len(params) < 2:
        return ""

    action = params[0].lower()
    item = params[1]

    pc = next((c for c in characters if not c.is_npc and not c.is_enemy), None)
    if not pc:
        return ""

    if pc.inventory is None:
        pc.inventory = []

    if action == "add":
        pc.inventory = pc.inventory + [item]
        state_changes.setdefault("inventory", []).append({"action": "add", "item": item})
        return f"(Added {item} to inventory)"
    elif action == "remove":
        inv = list(pc.inventory)
        if item in inv:
            inv.remove(item)
            pc.inventory = inv
            state_changes.setdefault("inventory", []).append({"action": "remove", "item": item})
            return f"(Removed {item} from inventory)"

    return ""


def _handle_combat(params: list, characters: list, game_state, state_changes: dict, db) -> str:
    """Handle [COMBAT:start:enemies] and [COMBAT:end] tags."""
    from server.engine.combat import start_combat, end_combat

    if not params:
        return ""

    action = params[0].lower()

    if action == "start" and len(params) >= 2:
        enemy_names = params[1].split(",")
        enemy_names = [e.strip() for e in enemy_names if e.strip()]

        # Get campaign_id from the first character
        campaign_id = characters[0].campaign_id if characters else 0

        result = start_combat(enemy_names, characters, game_state, campaign_id, db)

        state_changes["combat_started"] = True
        state_changes["initiative_order"] = result["initiative_order"]

        return f"\n\n--- COMBAT BEGINS ---\nInitiative Order:\n{result['initiative_summary']}\n---\n"

    elif action == "end":
        end_combat(game_state, characters, db)
        state_changes["combat_ended"] = True
        return "\n--- COMBAT ENDS ---\n"

    return ""
