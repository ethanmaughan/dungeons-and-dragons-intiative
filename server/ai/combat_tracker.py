"""Combat Tracker — replaces the DM AI during active combat.

Instead of sending player actions to the full DM AI and hoping it emits
correct tags, the Combat Tracker:
1. Parses player intent via a focused Haiku call (or keyword fallback)
2. Executes mechanics directly via action_processor handlers
3. Generates narration from the mechanical results

The DM AI is only called for questions/conversation during combat.
"""

import json
import traceback
from pathlib import Path

from server.config import AI_BACKEND, ANTHROPIC_API_KEY, COMBAT_INTENT_MODEL
from server.engine.action_processor import (
    _handle_combat_action,
    _handle_hp,
    _handle_player_attack,
    _handle_spell,
    find_character,
)
from server.engine.dice import ability_modifier, attack_roll, roll, saving_throw

# Load system prompt
PROMPTS_DIR = Path(__file__).parent / "prompts"
COMBAT_INTENT_SYSTEM = (PROMPTS_DIR / "combat_intent.txt").read_text()

# AI client (same pattern as enemy_agent.py)
if AI_BACKEND == "claude":
    import anthropic
    _tracker_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


# ---- Spell effects lookup ----

SPELL_EFFECTS = {
    # Healing
    "cure wounds":     {"type": "heal", "dice": "1d8", "mod": "wis"},
    "healing word":    {"type": "heal", "dice": "1d4", "mod": "wis"},
    # Damage cantrips — attack roll
    "fire bolt":       {"type": "damage", "dice": "1d10", "attack": True, "mod": "int"},
    "eldritch blast":  {"type": "damage", "dice": "1d10", "attack": True, "mod": "cha"},
    "produce flame":   {"type": "damage", "dice": "1d8", "attack": True, "mod": "wis"},
    "ray of frost":    {"type": "damage", "dice": "1d8", "attack": True, "mod": "int"},
    # Damage cantrips — saving throw
    "sacred flame":    {"type": "damage", "dice": "1d8", "save": "DEX", "dc_mod": "wis"},
    "vicious mockery": {"type": "damage", "dice": "1d4", "save": "WIS", "dc_mod": "cha"},
    # Damage leveled
    "magic missile":   {"type": "damage", "dice": "1d4+1", "auto_hit": True, "missiles": 3},
    "thunderwave":     {"type": "damage", "dice": "2d8", "save": "CON", "dc_mod": "wis"},
    # Stabilize
    "spare the dying": {"type": "stabilize"},
    # Buff / utility — slot consumed, narrated, no HP change
    "bless":           {"type": "buff"},
    "shield":          {"type": "buff"},
    "shield of faith": {"type": "buff"},
    "mage armor":      {"type": "buff"},
    "hex":             {"type": "buff"},
    "charm person":    {"type": "utility"},
    "detect magic":    {"type": "utility"},
    "sleep":           {"type": "utility"},
    "entangle":        {"type": "utility"},
    "faerie fire":     {"type": "utility"},
    "guidance":        {"type": "buff"},
    "light":           {"type": "utility"},
    "minor illusion":  {"type": "utility"},
    "prestidigitation": {"type": "utility"},
    "druidcraft":      {"type": "utility"},
    "mage hand":       {"type": "utility"},
}


# ---- Intent parsing ----

def _build_combat_context(action_text: str, acting_character, characters: list) -> str:
    """Build the context string for the intent parser AI call."""
    pc = acting_character
    lines = [f"Player: {pc.character_name} ({pc.race} {pc.char_class} L{pc.level})"]
    lines.append(f"HP: {pc.hp_current}/{pc.hp_max}, AC: {pc.ac}")
    lines.append(
        f"STR {pc.str_score} DEX {pc.dex_score} CON {pc.con_score} "
        f"INT {pc.int_score} WIS {pc.wis_score} CHA {pc.cha_score}"
    )
    if pc.spells:
        lines.append(f"Spells known: {', '.join(pc.spells)}")
    if pc.spell_slots_current:
        slots = ", ".join(f"L{k}: {v}" for k, v in pc.spell_slots_current.items() if v > 0)
        lines.append(f"Spell slots: {slots}" if slots else "Spell slots: none remaining")
    if pc.inventory:
        lines.append(f"Inventory: {', '.join(pc.inventory[:8])}")

    enemies = [c for c in characters if c.is_enemy and c.hp_current > 0]
    if enemies:
        lines.append("\nAlive enemies:")
        for e in enemies:
            lines.append(f"  - {e.character_name}: HP {e.hp_current}/{e.hp_max}, AC {e.ac}")

    pcs = [c for c in characters if not c.is_enemy and not c.is_npc and c.hp_current > 0 and c.id != pc.id]
    if pcs:
        lines.append("\nAlive allies:")
        for p in pcs:
            lines.append(f"  - {p.character_name}: HP {p.hp_current}/{p.hp_max}")

    down_pcs = [c for c in characters if not c.is_enemy and not c.is_npc and c.hp_current <= 0]
    if down_pcs:
        lines.append("\nDowned allies:")
        for p in down_pcs:
            conds = ", ".join(p.conditions or [])
            lines.append(f"  - {p.character_name}: 0 HP ({conds or 'unconscious'})")

    lines.append(f'\nPlayer says: "{action_text}"')
    return "\n".join(lines)


def _resolve_target(text: str, characters: list, prefer_enemy: bool = True) -> str | None:
    """Find a character name mentioned in the text. Falls back to first alive enemy."""
    text_lower = text.lower()
    # Check all alive characters for name match
    for c in characters:
        if c.hp_current <= 0:
            continue
        if c.character_name.lower() in text_lower:
            return c.character_name

    # Partial match
    for c in characters:
        if c.hp_current <= 0:
            continue
        # Check if any word in the character name appears in the text
        for word in c.character_name.lower().split():
            if len(word) > 2 and word in text_lower:
                return c.character_name

    if prefer_enemy:
        enemies = [c for c in characters if c.is_enemy and c.hp_current > 0]
        if enemies:
            return enemies[0].character_name

    return None


def _fallback_parse_intent(action_text: str, acting_character, characters: list) -> dict:
    """Keyword-based intent parsing — fallback when AI is unavailable."""
    text = action_text.lower().strip()

    # Combat actions (unambiguous keywords)
    for action_type in ("dodge", "dash", "disengage", "help", "ready"):
        if action_type in text:
            return {"intent": "combat_action", "action_type": action_type}

    # Spell detection: check if any known spell name appears
    known_spells = acting_character.spells or []
    for spell in known_spells:
        if spell.lower() in text:
            target = _resolve_target(text, characters, prefer_enemy=False)
            # Determine slot level
            effect = SPELL_EFFECTS.get(spell.lower(), {})
            is_cantrip = effect.get("type") in ("utility",) or spell.lower() in (
                s for s, e in SPELL_EFFECTS.items() if e.get("type") == "damage" and not e.get("save") and not e.get("attack") and not e.get("auto_hit")
            )
            # Simple heuristic: if it's in the cantrips list, slot 0
            slots = acting_character.spell_slots_current or {}
            slot_level = 0 if not slots or spell.lower() in SPELL_EFFECTS and SPELL_EFFECTS.get(spell.lower(), {}).get("type") in ("damage", "utility", "buff", "stabilize") and not any(slots.values()) else 1
            # Better heuristic: if character has no spell slots at all, everything is a cantrip
            if not any(v > 0 for v in slots.values()):
                slot_level = 0
            return {
                "intent": "spell",
                "spell_name": spell,
                "slot_level": slot_level,
                "target": target or acting_character.character_name,
            }

    # "cast" keyword without specific spell match
    if "cast" in text or "spell" in text:
        # Try to find any spell name
        for spell in known_spells:
            if any(word in text for word in spell.lower().split() if len(word) > 3):
                target = _resolve_target(text, characters, prefer_enemy=False)
                return {
                    "intent": "spell",
                    "spell_name": spell,
                    "slot_level": 1,
                    "target": target or acting_character.character_name,
                }

    # Attack keywords
    attack_words = ("attack", "hit", "strike", "slash", "stab", "shoot", "swing", "smash", "fight", "axe", "sword", "bow")
    if any(w in text for w in attack_words):
        target = _resolve_target(text, characters, prefer_enemy=True)
        if target:
            return {"intent": "attack", "target": target}

    # Default: question
    return {"intent": "question"}


async def parse_combat_intent(
    action_text: str,
    acting_character,
    characters: list,
    game_state,
) -> dict:
    """Parse a player's combat action into structured intent.

    Returns dict with 'intent' key: 'attack', 'spell', 'combat_action', or 'question'.
    """
    if AI_BACKEND != "claude":
        return _fallback_parse_intent(action_text, acting_character, characters)

    try:
        context = _build_combat_context(action_text, acting_character, characters)

        response = await _tracker_client.messages.create(
            model=COMBAT_INTENT_MODEL,
            max_tokens=150,
            system=COMBAT_INTENT_SYSTEM,
            messages=[{"role": "user", "content": context}],
        )

        raw = response.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        intent = json.loads(raw)

        # Validate required fields
        if "intent" not in intent:
            return _fallback_parse_intent(action_text, acting_character, characters)

        return intent

    except Exception:
        traceback.print_exc()
        return _fallback_parse_intent(action_text, acting_character, characters)


# ---- Turn execution ----

def execute_combat_turn(
    intent: dict,
    acting_character,
    characters: list,
    game_state,
    db,
) -> dict:
    """Execute a parsed combat intent mechanically. No DM AI involved.

    Returns: {"narration": str, "dice_rolls": list, "state_changes": dict, "turn_consumed": bool}
    """
    intent_type = intent.get("intent", "question")

    if intent_type == "question":
        return {
            "narration": None,
            "dice_rolls": [],
            "state_changes": {},
            "turn_consumed": False,
            "route_to_dm": True,
        }

    if intent_type == "attack":
        return _execute_attack(intent, acting_character, characters)

    if intent_type == "spell":
        return _execute_spell(intent, acting_character, characters)

    if intent_type == "combat_action":
        return _execute_combat_action(intent, acting_character, characters)

    # Unknown intent — treat as question
    return {
        "narration": None,
        "dice_rolls": [],
        "state_changes": {},
        "turn_consumed": False,
        "route_to_dm": True,
    }


def _execute_attack(intent: dict, acting_character, characters: list) -> dict:
    """Execute a player attack using _handle_player_attack directly."""
    dice_rolls = []
    state_changes = {}
    target_name = intent.get("target", "")

    # Validate target exists
    target = find_character(characters, target_name)
    if not target:
        # Pick first alive enemy
        enemies = [c for c in characters if c.is_enemy and c.hp_current > 0]
        if enemies:
            target_name = enemies[0].character_name
        else:
            return {
                "narration": "There are no enemies to attack!",
                "dice_rolls": [],
                "state_changes": {},
                "turn_consumed": False,
            }

    narration = _handle_player_attack(
        [acting_character.character_name, target_name],
        characters, dice_rolls, state_changes,
    )

    return {
        "narration": narration,
        "dice_rolls": dice_rolls,
        "state_changes": state_changes,
        "turn_consumed": True,
    }


def _execute_spell(intent: dict, acting_character, characters: list) -> dict:
    """Execute a spell: consume slot + apply effect."""
    dice_rolls = []
    state_changes = {}
    spell_name = intent.get("spell_name", "")
    slot_level = str(intent.get("slot_level", 1))
    target_name = intent.get("target", acting_character.character_name)

    # Check if character knows this spell
    known_spells = [s.lower() for s in (acting_character.spells or [])]
    if spell_name.lower() not in known_spells:
        return {
            "narration": f"({acting_character.character_name} doesn't know {spell_name}!)",
            "dice_rolls": [],
            "state_changes": {},
            "turn_consumed": False,
        }

    # Spare the Dying special case (handled by _handle_spell with 4th param)
    if spell_name.lower() == "spare the dying":
        spell_params = [acting_character.character_name, spell_name, "0", target_name]
        narration = _handle_spell(spell_params, characters, state_changes)
        return {
            "narration": narration,
            "dice_rolls": dice_rolls,
            "state_changes": state_changes,
            "turn_consumed": True,
        }

    # Consume spell slot
    spell_params = [acting_character.character_name, spell_name, slot_level]
    slot_narration = _handle_spell(spell_params, characters, state_changes)

    # Check if slot consumption failed (no slots remaining)
    if "no level" in slot_narration.lower() and "remaining" in slot_narration.lower():
        return {
            "narration": slot_narration,
            "dice_rolls": [],
            "state_changes": state_changes,
            "turn_consumed": False,
        }

    # Look up spell effect
    effect = SPELL_EFFECTS.get(spell_name.lower())

    if not effect or effect["type"] in ("buff", "utility"):
        # Buff/utility — slot consumed, that's it
        return {
            "narration": slot_narration,
            "dice_rolls": dice_rolls,
            "state_changes": state_changes,
            "turn_consumed": True,
        }

    # Healing spells
    if effect["type"] == "heal":
        mod_attr = f"{effect['mod']}_score"
        mod = ability_modifier(getattr(acting_character, mod_attr, 10))
        heal_roll = roll(effect["dice"])
        amount = max(1, heal_roll["total"] + mod)
        dice_rolls.append({
            "type": "healing",
            "notation": effect["dice"],
            "rolls": heal_roll["rolls"],
            "modifier": mod,
            "total": amount,
        })
        hp_narration = _handle_hp([target_name, f"+{amount}"], characters, state_changes)
        narration = f"{slot_narration}\n{hp_narration}"
        return {
            "narration": narration,
            "dice_rolls": dice_rolls,
            "state_changes": state_changes,
            "turn_consumed": True,
        }

    # Damage spells
    if effect["type"] == "damage":
        return _execute_damage_spell(
            effect, spell_name, acting_character, target_name,
            characters, dice_rolls, state_changes, slot_narration,
        )

    # Stabilize is handled above; fallback
    return {
        "narration": slot_narration,
        "dice_rolls": dice_rolls,
        "state_changes": state_changes,
        "turn_consumed": True,
    }


def _execute_damage_spell(
    effect: dict, spell_name: str, caster, target_name: str,
    characters: list, dice_rolls: list, state_changes: dict,
    slot_narration: str,
) -> dict:
    """Handle damage spells: attack roll, saving throw, or auto-hit."""
    name = caster.character_name

    # Auto-hit spells (Magic Missile)
    if effect.get("auto_hit"):
        missiles = effect.get("missiles", 3)
        total_dmg = 0
        all_rolls = []
        for _ in range(missiles):
            dmg = roll(effect["dice"])
            total_dmg += dmg["total"]
            all_rolls.extend(dmg["rolls"])
        dice_rolls.append({"type": "spell_damage", "total": total_dmg, "rolls": all_rolls})
        hp_narration = _handle_hp([target_name, f"-{total_dmg}"], characters, state_changes)
        narration = (
            f"{slot_narration}\n"
            f"{name} sends {missiles} glowing darts streaking toward {target_name}, "
            f"dealing {total_dmg} force damage!\n{hp_narration}"
        )
        return {"narration": narration, "dice_rolls": dice_rolls, "state_changes": state_changes, "turn_consumed": True}

    # Spell attack roll (Fire Bolt, Eldritch Blast, etc.)
    if effect.get("attack"):
        mod_attr = f"{effect.get('mod', 'int')}_score"
        spell_mod = ability_modifier(getattr(caster, mod_attr, 10))
        spell_atk_bonus = spell_mod + caster.proficiency_bonus

        atk = attack_roll(spell_atk_bonus)
        target = find_character(characters, target_name)
        target_ac = target.ac if target else 13

        hit = atk["critical"] or (not atk["fumble"] and atk["total"] >= target_ac)
        dice_rolls.append({
            "type": "spell_attack",
            "roll": atk["rolls"][0],
            "modifier": atk["modifier"],
            "total": atk["total"],
            "target_ac": target_ac,
            "hit": hit,
            "critical": atk["critical"],
        })

        if atk["critical"]:
            # Double dice on crit
            import re
            match = re.match(r"(\d*)d(\d+)", effect["dice"])
            count = int(match.group(1) or 1) * 2 if match else 2
            sides = match.group(2) if match else "6"
            dmg = roll(f"{count}d{sides}")
            total_dmg = dmg["total"]
            dice_rolls.append({"type": "spell_damage", "total": total_dmg, "critical": True})
            hp_narration = _handle_hp([target_name, f"-{total_dmg}"], characters, state_changes)
            narration = (
                f"{slot_narration}\n"
                f"{name} casts {spell_name} at {target_name} — "
                f"**CRITICAL HIT!** (rolled {atk['rolls'][0]} + {atk['modifier']} = {atk['total']} vs AC {target_ac}) "
                f"dealing {total_dmg} damage!\n{hp_narration}"
            )
        elif hit:
            dmg = roll(effect["dice"])
            total_dmg = dmg["total"]
            dice_rolls.append({"type": "spell_damage", "total": total_dmg})
            hp_narration = _handle_hp([target_name, f"-{total_dmg}"], characters, state_changes)
            narration = (
                f"{slot_narration}\n"
                f"{name} casts {spell_name} at {target_name} — "
                f"Hit! (rolled {atk['rolls'][0]} + {atk['modifier']} = {atk['total']} vs AC {target_ac}) "
                f"dealing {total_dmg} damage.\n{hp_narration}"
            )
        else:
            narration = (
                f"{slot_narration}\n"
                f"{name} casts {spell_name} at {target_name} — "
                f"Miss! (rolled {atk['rolls'][0]} + {atk['modifier']} = {atk['total']} vs AC {target_ac})"
            )

        return {"narration": narration, "dice_rolls": dice_rolls, "state_changes": state_changes, "turn_consumed": True}

    # Saving throw spells (Sacred Flame, Vicious Mockery, Thunderwave)
    if effect.get("save"):
        dc_attr = f"{effect['dc_mod']}_score"
        dc_mod = ability_modifier(getattr(caster, dc_attr, 10))
        spell_dc = 8 + dc_mod + caster.proficiency_bonus

        target = find_character(characters, target_name)
        if not target:
            return {"narration": f"[{target_name} not found]", "dice_rolls": [], "state_changes": state_changes, "turn_consumed": True}

        save_ability = effect["save"].upper()
        save_attr = {"STR": "str_score", "DEX": "dex_score", "CON": "con_score", "INT": "int_score", "WIS": "wis_score", "CHA": "cha_score"}
        target_score = getattr(target, save_attr.get(save_ability, "dex_score"), 10)
        save_result = saving_throw(target_score)

        saved = save_result["total"] >= spell_dc
        dice_rolls.append({
            "type": "saving_throw",
            "ability": save_ability,
            "dc": spell_dc,
            "roll": save_result["rolls"][0],
            "total": save_result["total"],
            "success": saved,
        })

        if saved:
            narration = (
                f"{slot_narration}\n"
                f"{name} casts {spell_name} on {target_name} — "
                f"{target_name} makes the {save_ability} save! "
                f"(rolled {save_result['rolls'][0]} + {save_result['modifier']} = {save_result['total']} vs DC {spell_dc})"
            )
        else:
            dmg = roll(effect["dice"])
            total_dmg = dmg["total"]
            dice_rolls.append({"type": "spell_damage", "total": total_dmg})
            hp_narration = _handle_hp([target_name, f"-{total_dmg}"], characters, state_changes)
            narration = (
                f"{slot_narration}\n"
                f"{name} casts {spell_name} on {target_name} — "
                f"{target_name} fails the {save_ability} save! "
                f"(rolled {save_result['rolls'][0]} + {save_result['modifier']} = {save_result['total']} vs DC {spell_dc}) "
                f"dealing {total_dmg} damage.\n{hp_narration}"
            )

        return {"narration": narration, "dice_rolls": dice_rolls, "state_changes": state_changes, "turn_consumed": True}

    # Fallback: slot consumed, generic narration
    return {"narration": slot_narration, "dice_rolls": dice_rolls, "state_changes": state_changes, "turn_consumed": True}


def _execute_combat_action(intent: dict, acting_character, characters: list) -> dict:
    """Execute a non-attack combat action (dodge, dash, etc.)."""
    state_changes = {}
    action_type = intent.get("action_type", "dodge")

    narration = _handle_combat_action(
        [acting_character.character_name, action_type],
        characters, state_changes,
    )

    return {
        "narration": narration,
        "dice_rolls": [],
        "state_changes": state_changes,
        "turn_consumed": True,
    }
