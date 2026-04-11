"""Per-enemy AI agent — orchestrates the tool-based combat decision pipeline.

Pipeline: battlefield_tactics (pure Python) → enemy_learning (DB read)
→ enemy_personality (Haiku call or rule-based) → action resolution.

Falls back to a rule-based engine if any tool fails.
"""

import json
import traceback

from server.config import AI_BACKEND, ANTHROPIC_API_KEY, ENEMY_AGENT_MODEL
from server.engine.combat import get_enemy_monster_data
from server.engine.dice import ability_modifier


# ---- Frontline classification (kept for legacy compatibility) ----
FRONTLINE_CLASSES = {"fighter", "barbarian", "paladin", "ranger", "monk"}
BACKLINE_CLASSES = {"wizard", "sorcerer", "warlock", "bard", "cleric", "druid"}


async def get_enemy_decision(
    enemy,
    all_characters: list,
    positions: dict | None = None,
    encounter_state=None,
    db=None,
) -> dict:
    """Get a tactical decision for an enemy using the tool pipeline.

    Pipeline:
    1. battlefield_tactics.analyze_battlefield() — pure Python, no AI
    2. enemy_learning.get_player_patterns() — DB read, no AI
    3. enemy_personality.get_personality_decision() — Haiku call (or skip for mindless)

    Returns: {"target": str, "action": str, "action_data": dict, ...}
    Falls back to rule-based targeting if any step fails.
    """
    monster_data = get_enemy_monster_data(enemy)

    try:
        # Step 1: Battlefield analysis (pure Python)
        tactical_analysis = None
        if positions:
            from server.ai.tools.battlefield_tactics import analyze_battlefield
            tactical_analysis = analyze_battlefield(
                enemy.id, positions, all_characters, monster_data,
                round_number=encounter_state.round_number if encounter_state else 1,
            )

        # Step 2: Historical learning data (DB read)
        historical_patterns = {}
        if db:
            try:
                from server.ai.tools.enemy_learning import get_player_patterns
                historical_patterns = get_player_patterns(enemy.char_class, db)
            except Exception:
                traceback.print_exc()

        # Step 3: Personality decision (Haiku call or rule-based)
        encounter_summary = (
            encounter_state.get_current_fight_summary() if encounter_state else {"round": 1}
        )

        if tactical_analysis:
            from server.ai.tools.enemy_personality import get_personality_decision
            decision = await get_personality_decision(
                enemy, monster_data, tactical_analysis,
                encounter_summary, historical_patterns,
            )
        else:
            # No positions available — fall back to legacy path
            return await _legacy_decision(enemy, all_characters, monster_data)

        return decision

    except Exception:
        traceback.print_exc()
        return _rule_decision(enemy, all_characters)


async def _legacy_decision(enemy, all_characters: list, monster_data: dict) -> dict:
    """Legacy AI decision path — used when grid positions aren't available."""
    if AI_BACKEND == "claude":
        try:
            return await _ai_decision_legacy(enemy, all_characters, monster_data)
        except Exception:
            traceback.print_exc()
    return _rule_decision(enemy, all_characters)


async def _ai_decision_legacy(enemy, all_characters: list, monster_data: dict) -> dict:
    """Legacy Haiku call — kept for backward compat when positions are missing."""
    import anthropic
    _client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    battlefield = _build_battlefield_summary_legacy(enemy, all_characters, monster_data)

    response = await _client.messages.create(
        model=ENEMY_AGENT_MODEL,
        max_tokens=150,
        system=_LEGACY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": battlefield}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    decision = json.loads(raw)
    action_data = _resolve_action(decision.get("action", "attack"), monster_data)

    return {
        "target": decision.get("target"),
        "action": decision.get("action", "attack"),
        "action_data": action_data,
        "reasoning": decision.get("reasoning", ""),
    }


_LEGACY_SYSTEM_PROMPT = """You are a combat AI controlling a single monster in a D&D 5e encounter.
Choose the best tactical action based on the monster's personality and the battlefield.

RULES:
- Melee creatures should attack the closest opponent (front-line first).
- Ranged creatures may target back-line opponents.
- Unintelligent creatures (INT <= 5) attack the closest target, period.
- Pack hunters focus on the same target as their allies.
- Self-preserving creatures may flee when below 25% HP.
- Use special abilities when they'd be most effective.

Respond with ONLY valid JSON (no markdown):
{"target": "character name", "action": "action name from your action list", "reasoning": "brief tactical note"}

If you would flee instead of fight:
{"target": null, "action": "flee", "reasoning": "why fleeing"}"""


def _build_battlefield_summary_legacy(enemy, all_characters: list, monster_data: dict) -> str:
    """Build a concise battlefield description (legacy path)."""
    lines = [
        f"You are {enemy.character_name} ({enemy.char_class}).",
        f"HP: {enemy.hp_current}/{enemy.hp_max}, AC: {enemy.ac}, Speed: {enemy.speed}",
        f"Tactics: {monster_data['tactics']}",
    ]

    if monster_data["actions"]:
        action_strs = []
        for a in monster_data["actions"]:
            s = f"  - {a['name']} ({a['type']}): +{a.get('attack_bonus', 3)} to hit, {a.get('damage', '1d6')} damage"
            if a.get("special"):
                s += f" [{a['special']}]"
            action_strs.append(s)
        lines.append("Your actions:\n" + "\n".join(action_strs))

    if monster_data["traits"]:
        lines.append(f"Traits: {', '.join(monster_data['traits'])}")

    allies = [c for c in all_characters if c.is_enemy and c.id != enemy.id and c.hp_current > 0]
    if allies:
        ally_strs = [f"  - {a.character_name}: HP {a.hp_current}/{a.hp_max}" for a in allies]
        lines.append("Your allies:\n" + "\n".join(ally_strs))

    pcs = [c for c in all_characters if not c.is_npc and not c.is_enemy and c.hp_current > 0]
    if pcs:
        opp_strs = []
        for pc in pcs:
            cls = pc.char_class.lower()
            pos = "front" if cls in FRONTLINE_CLASSES else ("back" if cls in BACKLINE_CLASSES else "front")
            cond_str = f", conditions: {', '.join(pc.conditions)}" if pc.conditions else ""
            opp_strs.append(
                f"  - {pc.character_name} ({pc.race} {pc.char_class}): "
                f"HP {pc.hp_current}/{pc.hp_max}, AC {pc.ac}, position: {pos}{cond_str}"
            )
        lines.append("Opponents:\n" + "\n".join(opp_strs))

    return "\n".join(lines)


def _rule_decision(enemy, all_characters: list) -> dict:
    """Deterministic rule-based targeting (fallback / Ollama mode).
    Targets the closest enemy by class position, not lowest HP."""
    monster_data = get_enemy_monster_data(enemy)
    pcs = [c for c in all_characters if not c.is_npc and not c.is_enemy and c.hp_current > 0]

    if not pcs:
        return {"target": None, "action": "none", "action_data": {}}

    int_score = enemy.int_score or 10

    # Check if enemy should flee (self-preserving + below 25% HP)
    if "flee" in monster_data["tactics"].lower() or "retreat" in monster_data["tactics"].lower():
        if enemy.hp_current <= enemy.hp_max * 0.25:
            return {"target": None, "action": "flee", "action_data": {}}

    # Unintelligent (INT <= 5): attack closest (front-line first)
    # Intelligent: can prioritize based on tactics
    if int_score <= 5:
        target = _pick_closest(pcs)
    elif "pack" in monster_data["tactics"].lower():
        target = _pick_pack_target(enemy, pcs, all_characters)
    elif "weakest" in monster_data["tactics"].lower() or "caster" in monster_data["tactics"].lower():
        target = _pick_weakest_armor(pcs)
    else:
        target = _pick_closest(pcs)

    # Pick the best action
    action_data = _pick_best_action(monster_data)

    return {
        "target": target.character_name,
        "action": action_data.get("name", "Attack"),
        "action_data": action_data,
    }


def _pick_closest(pcs: list):
    """Pick the front-line PC (simulates proximity). Randomize among front-liners."""
    import secrets
    front = [c for c in pcs if _classify_position(c.char_class) == "front"]
    if front:
        return front[secrets.randbelow(len(front))]
    # No front-liners — pick randomly
    return pcs[secrets.randbelow(len(pcs))]


def _pick_pack_target(enemy, pcs: list, all_characters: list):
    """Pick the same target another allied enemy is already targeting (or closest)."""
    # Look for PCs that other allies might be engaging (heuristic: PCs that took damage recently)
    # For now, just pick the same target as a random ally would — front-line
    return _pick_closest(pcs)


def _pick_weakest_armor(pcs: list):
    """Pick the PC with the lowest AC (casters, lightly armored)."""
    return min(pcs, key=lambda c: c.ac)


def _resolve_action(action_name: str, monster_data: dict) -> dict:
    """Find the action data from the monster's action list."""
    for action in monster_data.get("actions", []):
        if action["name"].lower() == action_name.lower():
            return action
    # Default to first action or generic
    if monster_data.get("actions"):
        return monster_data["actions"][0]
    return {
        "name": "Attack",
        "type": "melee",
        "attack_bonus": monster_data.get("attack_bonus", 3),
        "damage": monster_data.get("damage", "1d6+1"),
        "reach": 5,
    }


def _pick_best_action(monster_data: dict) -> dict:
    """Pick the best action for a rule-based enemy (defaults to first melee action)."""
    actions = monster_data.get("actions", [])
    # Prefer melee actions
    melee = [a for a in actions if a.get("type") == "melee"]
    if melee:
        return melee[0]
    if actions:
        return actions[0]
    return {
        "name": "Attack",
        "type": "melee",
        "attack_bonus": monster_data.get("attack_bonus", 3),
        "damage": monster_data.get("damage", "1d6+1"),
        "reach": 5,
    }
