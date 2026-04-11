"""Enemy Personality Tool — creative behavior layer via personality tiers.

One Haiku call per intelligent enemy turn. Mindless monsters skip AI entirely.
Receives pre-computed tactical analysis and returns action with narrative flavor.
"""

from __future__ import annotations

import json
import secrets
import traceback

from server.config import AI_BACKEND, ANTHROPIC_API_KEY, ENEMY_AGENT_MODEL

if AI_BACKEND == "claude":
    import anthropic
    _personality_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


# ---- Personality Tier System ----


def _classify_tier(tactics: str) -> dict:
    """Classify a monster into a personality tier based on its tactics string."""
    t = tactics.lower()
    if any(w in t for w in ("mindless", "shambles", "relentless")):
        return {
            "tier": "mindless",
            "skip_ai": True,
            "system_note": (
                "This creature is mindless. It CANNOT strategize, flee, or "
                "target intelligently. It attacks the closest creature."
            ),
        }
    if "cowardly" in t or ("flee" in t and "self" not in t):
        return {
            "tier": "cowardly",
            "skip_ai": False,
            "system_note": (
                "This creature is cowardly. It prefers ranged attacks from "
                "cover and flees when losing. If HP is below 30%, it MUST "
                "flee or disengage. Describe its nervousness."
            ),
        }
    if "pack" in t:
        return {
            "tier": "pack_hunter",
            "skip_ai": False,
            "system_note": (
                "This creature hunts in packs. It MUST attack the same target "
                "as its nearest ally. Describe the coordinated attack."
            ),
        }
    if "self-preserving" in t or "surrender" in t:
        return {
            "tier": "self_preserving",
            "skip_ai": False,
            "system_note": (
                "This creature values its life. When outnumbered or below 50% HP, "
                "it may attempt to surrender, negotiate, or disengage. Otherwise "
                "it fights opportunistically."
            ),
        }
    return {
        "tier": "tactical",
        "skip_ai": False,
        "system_note": "This creature fights intelligently, using its abilities to best effect.",
    }


# ---- Prompt construction ----


def _build_personality_prompt(
    enemy,
    monster_data: dict,
    tier: dict,
    tactical_analysis: dict,
    encounter_summary: dict,
    historical_patterns: dict,
) -> str:
    """Build a focused system prompt for the personality decision."""
    actions_desc = ""
    for a in monster_data.get("actions", []):
        actions_desc += f"  - {a['name']} ({a['type']}): +{a.get('attack_bonus', 3)} to hit, {a.get('damage', '1d6')} damage"
        if a.get("special"):
            actions_desc += f" [{a['special']}]"
        actions_desc += "\n"

    target_rec = tactical_analysis.get("recommended_target_name", "closest enemy")
    target_dist = "unknown"
    for t in tactical_analysis.get("target_priority_list", []):
        if t["character_name"] == target_rec:
            target_dist = f"{t['distance']} cells away"
            break

    threat = tactical_analysis.get("threat_assessment", {})
    hp_pct = int(threat.get("enemy_hp_fraction", 1.0) * 100)

    prompt = f"""You are a combat AI controlling {enemy.character_name} ({enemy.char_class}).
HP: {hp_pct}% | {tier['system_note']}

Available actions:
{actions_desc}
Battlefield analysis recommends targeting: {target_rec} ({target_dist}).
You may accept or override this recommendation based on personality.

"""

    # Within-encounter observations
    if encounter_summary.get("round", 1) > 1:
        spells = encounter_summary.get("observed_spells", [])
        if spells:
            prompt += f"This fight: players have used {', '.join(spells)}.\n"
        dmg_ratio = encounter_summary.get("damage_ratio", 1.0)
        if dmg_ratio > 2.0:
            prompt += "The party is dealing heavy damage — this creature should be cautious.\n"
        elif dmg_ratio < 0.5:
            prompt += "This creature's side is winning — press the advantage.\n"

    # Historical patterns (cross-campaign learning)
    if historical_patterns.get("total_encounters", 0) > 0:
        common_spells = historical_patterns.get("common_spells", [])
        if common_spells:
            prompt += f"Historical: players commonly use {', '.join(common_spells[:3])} against {enemy.char_class}s.\n"
        counters = historical_patterns.get("effective_counters", [])
        if counters:
            prompt += f"Known weaknesses of this creature type: {', '.join(counters[:2])}.\n"

    flanking = tactical_analysis.get("flanking_opportunity", {})
    if flanking.get("available"):
        prompt += f"Flanking opportunity available on {flanking.get('flank_target_name', 'a target')}!\n"

    prompt += """
Respond with ONLY valid JSON (no markdown):
{"target": "character name", "action": "action name from your list", "reasoning": "brief tactical note", "flavor_text": "1 sentence describing the creature's behavior/demeanor", "action_style": "cautious|aggressive|tactical|desperate"}

If fleeing: {"target": null, "action": "flee", "reasoning": "why", "flavor_text": "description of retreat", "action_style": "desperate"}"""

    return prompt


# ---- Rule-based fallback ----


def _personality_rule_decision(
    enemy,
    monster_data: dict,
    tactical_analysis: dict,
) -> dict:
    """Pure rule-based fallback. Accepts tactical analysis recommendation."""
    rec_target = tactical_analysis.get("recommended_target_name")
    rec_type = tactical_analysis.get("recommended_action_type", "melee")

    if rec_type == "flee" or not rec_target:
        return {
            "target": None,
            "action": "flee",
            "action_data": {},
            "reasoning": "self-preservation",
            "flavor_text": f"{enemy.character_name} turns and bolts!",
            "action_style": "desperate",
        }

    # Pick the best action matching the recommended type
    actions = monster_data.get("actions", [])
    chosen = None
    for a in actions:
        if a.get("type") == rec_type:
            chosen = a
            break
    if not chosen and actions:
        chosen = actions[0]
    if not chosen:
        chosen = {
            "name": "Attack",
            "type": "melee",
            "attack_bonus": monster_data.get("attack_bonus", 3),
            "damage": monster_data.get("damage", "1d6+1"),
            "reach": 5,
        }

    return {
        "target": rec_target,
        "action": chosen["name"],
        "action_data": chosen,
        "reasoning": "tactical analysis",
        "flavor_text": f"{enemy.character_name} attacks with {chosen['name']}.",
        "action_style": "tactical",
    }


# ---- Main entry point ----


async def get_personality_decision(
    enemy,
    monster_data: dict,
    tactical_analysis: dict,
    encounter_summary: dict | None = None,
    historical_patterns: dict | None = None,
) -> dict:
    """Get a personality-driven combat decision.

    Returns: {"target", "action", "action_data", "reasoning", "flavor_text", "action_style"}
    Falls back to rule-based if AI fails or monster is mindless.
    """
    if encounter_summary is None:
        encounter_summary = {"round": 1}
    if historical_patterns is None:
        historical_patterns = {}

    tactics = monster_data.get("tactics", "")
    tier = _classify_tier(tactics)

    # Mindless: skip AI entirely
    if tier["skip_ai"]:
        return _personality_rule_decision(enemy, monster_data, tactical_analysis)

    # AI path
    if AI_BACKEND != "claude":
        return _personality_rule_decision(enemy, monster_data, tactical_analysis)

    try:
        prompt = _build_personality_prompt(
            enemy, monster_data, tier, tactical_analysis,
            encounter_summary, historical_patterns,
        )

        response = await _personality_client.messages.create(
            model=ENEMY_AGENT_MODEL,
            max_tokens=200,
            system=prompt,
            messages=[{"role": "user", "content": "Choose your action."}],
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        decision = json.loads(raw)

        # Resolve action_data from monster's action list
        action_name = decision.get("action", "attack")
        action_data = _resolve_action(action_name, monster_data)

        return {
            "target": decision.get("target"),
            "action": action_name,
            "action_data": action_data,
            "reasoning": decision.get("reasoning", ""),
            "flavor_text": decision.get("flavor_text", ""),
            "action_style": decision.get("action_style", "tactical"),
        }

    except Exception:
        traceback.print_exc()
        return _personality_rule_decision(enemy, monster_data, tactical_analysis)


def _resolve_action(action_name: str, monster_data: dict) -> dict:
    """Find the action data from the monster's action list."""
    for action in monster_data.get("actions", []):
        if action["name"].lower() == action_name.lower():
            return action
    if monster_data.get("actions"):
        return monster_data["actions"][0]
    return {
        "name": "Attack",
        "type": "melee",
        "attack_bonus": monster_data.get("attack_bonus", 3),
        "damage": monster_data.get("damage", "1d6+1"),
        "reach": 5,
    }
