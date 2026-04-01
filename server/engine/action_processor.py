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

# Matches tags like [ROLL:ability_check:STR:15] or [ENEMY_ATTACK:Goblin:Hero]
TAG_PATTERN = re.compile(r"\[([A-Z_]+):([^\]]+)\]")

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
            elif tag_type == "SPELL":
                replacement = _handle_spell(params, characters, state_changes)
            elif tag_type == "REST":
                replacement = _handle_rest(params, characters, state_changes)
            elif tag_type == "XP":
                replacement = _handle_xp(params, characters, state_changes)
            elif tag_type == "ENEMY_ATTACK":
                replacement = _handle_enemy_attack(params, characters, dice_rolls, state_changes)
            elif tag_type == "PLAYER_ATTACK":
                replacement = _handle_player_attack(params, characters, dice_rolls, state_changes)
            elif tag_type == "ENEMY_TURN":
                replacement = _handle_enemy_turn(params, characters, dice_rolls, state_changes)
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


def _handle_spell(params: list, characters: list, state_changes: dict) -> str:
    """Handle [SPELL:caster:spell_name:slot_level] tags."""
    if len(params) < 3:
        return "[invalid spell]"

    caster_name = params[0]
    spell_name = params[1]
    slot_level = params[2]

    caster = find_character(characters, caster_name)
    if not caster:
        return f"[{caster_name} not found]"

    # Cantrips (level 0) don't consume slots
    if slot_level == "0" or slot_level == "cantrip":
        state_changes.setdefault("spells_cast", []).append({
            "caster": caster.character_name, "spell": spell_name, "level": 0,
        })
        return f"({caster.character_name} casts {spell_name})"

    # Check spell slots
    current_slots = caster.spell_slots_current or {}
    available = current_slots.get(slot_level, 0)

    if available <= 0:
        return f"({caster.character_name} has no level {slot_level} spell slots remaining!)"

    # Consume the slot
    current_slots[slot_level] = available - 1
    caster.spell_slots_current = dict(current_slots)

    state_changes.setdefault("spells_cast", []).append({
        "caster": caster.character_name, "spell": spell_name,
        "level": int(slot_level), "slots_remaining": available - 1,
    })

    return f"({caster.character_name} casts {spell_name} — Level {slot_level} slot used, {available - 1} remaining)"


def _handle_rest(params: list, characters: list, state_changes: dict) -> str:
    """Handle [REST:long] and [REST:short] tags."""
    rest_type = params[0].lower() if params else "long"

    pc = next((c for c in characters if not c.is_npc and not c.is_enemy), None)
    if not pc:
        return ""

    if rest_type == "long":
        pc.hp_current = pc.hp_max
        pc.spell_slots_current = dict(pc.spell_slots or {})
        pc.conditions = []
        state_changes["rest"] = {
            "type": "long",
            "hp_restored": pc.hp_max,
            "slots_restored": True,
        }
        return f"({pc.character_name} completes a long rest. HP and spell slots fully restored.)"
    else:
        state_changes["rest"] = {"type": "short"}
        return f"({pc.character_name} takes a short rest.)"


def _handle_xp(params: list, characters: list, state_changes: dict) -> str:
    """Handle [XP:amount] tags. Awards XP and checks for level up."""
    from server.engine.leveling import check_level_up

    if not params:
        return ""

    try:
        amount = int(params[0])
    except ValueError:
        return "[invalid XP amount]"

    pc = next((c for c in characters if not c.is_npc and not c.is_enemy), None)
    if not pc:
        return ""

    pc.xp += amount
    result_text = f"(+{amount} XP — Total: {pc.xp})"

    state_changes.setdefault("xp_gained", []).append({
        "character": pc.character_name,
        "amount": amount,
        "total": pc.xp,
    })

    # Check for level up
    level_info = check_level_up(pc)
    if level_info:
        result_text += (
            f"\n\n*** LEVEL UP! {pc.character_name} is now Level {level_info['new_level']}! ***"
            f"\n(+{level_info['hp_increase']} HP — Max HP now {pc.hp_max})"
        )
        if level_info.get("new_spell_slots"):
            slots_str = ", ".join(f"L{k}: {v}" for k, v in level_info["new_spell_slots"].items())
            result_text += f"\n(New spell slots: {slots_str})"

        state_changes["level_up"] = level_info

    return result_text


def _handle_enemy_attack(params: list, characters: list, dice_rolls: list, state_changes: dict) -> str:
    """Handle [ENEMY_ATTACK:attacker_name:target_name].
    Server handles EVERYTHING: lookup stats, roll attack, compare AC, roll damage, apply HP.
    Uses actual monster stats (attack_bonus, damage) from stored monster data."""
    from server.engine.combat import get_enemy_monster_data

    if len(params) < 2:
        return "[invalid enemy attack]"

    attacker_name = params[0]
    target_name = params[1]

    attacker = find_character(characters, attacker_name)
    target = find_character(characters, target_name)

    if not attacker:
        return f"[{attacker_name} not found]"
    if not target:
        return f"[{target_name} not found]"

    if target.hp_current <= 0:
        state_changes["player_down"] = True
        return f"\n{target.character_name} is already down!"

    if attacker.hp_current <= 0:
        return ""

    # Use actual monster stats instead of calculating from ability scores
    monster_data = get_enemy_monster_data(attacker)
    attack_mod = monster_data["attack_bonus"]
    damage_notation = monster_data["damage"]

    # Roll attack
    atk = attack_roll(attack_mod)
    hit = atk["critical"] or (not atk["fumble"] and atk["total"] >= target.ac)

    dice_rolls.append({
        "type": "enemy_attack",
        "attacker": attacker.character_name,
        "target": target.character_name,
        "roll": atk["rolls"][0],
        "modifier": atk["modifier"],
        "total": atk["total"],
        "target_ac": target.ac,
        "hit": hit,
        "critical": atk["critical"],
    })

    if atk["critical"]:
        crit_notation = _double_dice_notation(damage_notation)
        dmg = roll(crit_notation)
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

        return (
            f"\n{attacker.character_name} strikes at {target.character_name} — "
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

        return (
            f"\n{attacker.character_name} strikes at {target.character_name} — "
            f"Hit! (rolled {atk['rolls'][0]} + {atk['modifier']} = {atk['total']} vs AC {target.ac}) "
            f"dealing {total_dmg} damage. ({target.character_name}: HP {old_hp} -> {target.hp_current})"
            f"{down_msg}"
        )

    else:
        return (
            f"\n{attacker.character_name} strikes at {target.character_name} — "
            f"Miss! (rolled {atk['rolls'][0]} + {atk['modifier']} = {atk['total']} vs AC {target.ac})"
        )


def _double_dice_notation(notation: str) -> str:
    """Double dice count for critical hits. '1d6+2' -> '2d6+2'."""
    import re as _re
    match = _re.match(r"(\d*)d(\d+)([+-]\d+)?", notation.strip().lower())
    if not match:
        return notation
    count = int(match.group(1) or 1)
    sides = match.group(2)
    mod = match.group(3) or ""
    return f"{count * 2}d{sides}{mod}"


def _handle_player_attack(params: list, characters: list, dice_rolls: list, state_changes: dict) -> str:
    """Handle [PLAYER_ATTACK:player_name:target_name].
    Server handles everything for the player's basic attack."""
    if len(params) < 2:
        return "[invalid player attack]"

    attacker_name = params[0]
    target_name = params[1]

    attacker = find_character(characters, attacker_name)
    target = find_character(characters, target_name)

    if not attacker:
        return f"[{attacker_name} not found]"
    if not target:
        return f"[{target_name} not found]"

    # Get attacker's stats
    str_mod = ability_modifier(attacker.str_score)
    dex_mod = ability_modifier(attacker.dex_score)
    attack_mod = max(str_mod, dex_mod) + attacker.proficiency_bonus

    # Roll attack
    atk = attack_roll(attack_mod)
    hit = atk["critical"] or (not atk["fumble"] and atk["total"] >= target.ac)

    dice_rolls.append({
        "type": "player_attack",
        "attacker": attacker.character_name,
        "target": target.character_name,
        "roll": atk["rolls"][0],
        "modifier": atk["modifier"],
        "total": atk["total"],
        "target_ac": target.ac,
        "hit": hit,
        "critical": atk["critical"],
    })

    if atk["critical"]:
        dmg = roll("2d8")
        dmg_mod = max(str_mod, dex_mod)
        total_dmg = dmg["total"] + dmg_mod

        old_hp = target.hp_current
        target.hp_current = max(0, target.hp_current - total_dmg)

        dice_rolls.append({"type": "damage", "total": total_dmg, "critical": True})
        state_changes.setdefault("hp_changes", []).append({
            "target": target.character_name, "old": old_hp, "new": target.hp_current, "change": -total_dmg,
        })

        defeated = " The creature crumples to the ground!" if target.hp_current <= 0 else ""
        return (
            f"\nYou strike at {target.character_name} — "
            f"**CRITICAL HIT!** (rolled {atk['rolls'][0]} + {atk['modifier']} = {atk['total']} vs AC {target.ac}) "
            f"dealing {total_dmg} damage! ({target.character_name}: HP {old_hp} → {target.hp_current}){defeated}"
        )

    elif hit:
        dmg = roll("1d8")
        dmg_mod = max(str_mod, dex_mod)
        total_dmg = dmg["total"] + dmg_mod

        old_hp = target.hp_current
        target.hp_current = max(0, target.hp_current - total_dmg)

        dice_rolls.append({"type": "damage", "total": total_dmg})
        state_changes.setdefault("hp_changes", []).append({
            "target": target.character_name, "old": old_hp, "new": target.hp_current, "change": -total_dmg,
        })

        defeated = " The creature crumples to the ground!" if target.hp_current <= 0 else ""
        return (
            f"\nYou strike at {target.character_name} — "
            f"Hit! (rolled {atk['rolls'][0]} + {atk['modifier']} = {atk['total']} vs AC {target.ac}) "
            f"dealing {total_dmg} damage. ({target.character_name}: HP {old_hp} → {target.hp_current}){defeated}"
        )

    else:
        miss_text = "Critical fumble!" if atk["fumble"] else "Miss!"
        return (
            f"\nYou strike at {target.character_name} — "
            f"{miss_text} (rolled {atk['rolls'][0]} + {atk['modifier']} = {atk['total']} vs AC {target.ac})"
        )


def _handle_enemy_turn(params: list, characters: list, dice_rolls: list, state_changes: dict) -> str:
    """Handle [ENEMY_TURN:enemy_name]. Uses rule-based targeting from the enemy agent system.
    Note: The combat orchestrator is the preferred path for enemy turns now. This is a fallback
    for when the DM AI still emits these tags."""
    from server.ai.enemy_agent import _rule_decision

    if not params:
        return ""

    enemy_name = params[0]
    enemy = find_character(characters, enemy_name)
    if not enemy or enemy.hp_current <= 0:
        return ""

    # Use rule-based decision (proximity targeting, not lowest HP)
    decision = _rule_decision(enemy, characters)
    target_name = decision.get("target")

    if not target_name or decision.get("action") == "flee":
        return f"\n{enemy.character_name} turns and flees!"

    return _handle_enemy_attack([enemy_name, target_name], characters, dice_rolls, state_changes)
