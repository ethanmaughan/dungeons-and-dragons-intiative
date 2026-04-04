"""Disposition engine — pure math for NPC attitudes toward the party.

Calculates starting disposition from demographics (race, class, social standing, region)
and applies shifts based on player behavior. No AI calls — just arithmetic.
"""

import json
from pathlib import Path

_demographics_cache = None
DEMOGRAPHICS_FILE = Path(__file__).parent.parent.parent / "data" / "demographics.json"


def load_demographics() -> dict:
    """Load and cache the demographics ruleset."""
    global _demographics_cache
    if _demographics_cache is None:
        _demographics_cache = json.loads(DEMOGRAPHICS_FILE.read_text())
    return _demographics_cache


def calculate_starting_disposition(
    npc_race: str,
    npc_social_role: str,
    player_characters: list,
    region: str | None = None,
    story_override: int | None = None,
) -> int:
    """Calculate starting disposition for an NPC toward the party.

    Formula: base(50) + avg(race_attitudes) + avg(class_attitudes) + avg(regional) + override
    Clamped to 0-100.
    """
    demographics = load_demographics()
    base = 50
    n = max(len(player_characters), 1)

    # Race-based attitudes
    race_modifiers = demographics.get("race_attitudes", {}).get(npc_race.lower(), {})
    race_total = sum(
        race_modifiers.get(pc.race.lower(), 0) for pc in player_characters
    )

    # Class-based attitudes (by NPC social role)
    class_modifiers = demographics.get("class_attitudes", {}).get(npc_social_role.lower(), {})
    class_total = sum(
        class_modifiers.get(pc.char_class.lower(), 0) for pc in player_characters
    )

    score = base + (race_total // n) + (class_total // n)

    # Regional modifiers
    if region:
        region_mods = demographics.get("regional_modifiers", {}).get(region.lower(), {})
        region_total = sum(
            region_mods.get(pc.char_class.lower(), 0) for pc in player_characters
        )
        score += region_total // n

    # Story author override
    if story_override is not None:
        score = story_override

    return max(0, min(100, score))


def apply_behavior_shift(npc_state, behavior_tag: str) -> int:
    """Shift an NPC's disposition based on a classified player behavior.

    Mutates npc_state.disposition and returns the new value.
    """
    demographics = load_demographics()
    delta = demographics.get("behavior_impact", {}).get(behavior_tag, 0)
    npc_state.disposition = max(0, min(100, npc_state.disposition + delta))
    return npc_state.disposition


def get_disposition_label(score: int) -> str:
    """Convert numeric disposition to human-readable label."""
    if score < 30:
        return "Hostile"
    if score < 45:
        return "Unfriendly"
    if score < 65:
        return "Neutral"
    if score < 85:
        return "Friendly"
    return "Allied"


def get_or_create_npc_state(
    campaign_id: int,
    npc_name: str,
    characters: list,
    db,
    npc_race: str = "human",
    npc_social_role: str = "peasant",
    story_npc_id: int | None = None,
    story_override: int | None = None,
    region: str | None = None,
):
    """Get existing NPC state or create one with calculated starting disposition."""
    from server.db.models import NPCState

    state = (
        db.query(NPCState)
        .filter(NPCState.campaign_id == campaign_id, NPCState.npc_name == npc_name)
        .first()
    )
    if state:
        return state

    pcs = [c for c in characters if not c.is_npc and not c.is_enemy]
    starting = calculate_starting_disposition(
        npc_race, npc_social_role, pcs, region=region, story_override=story_override,
    )

    state = NPCState(
        campaign_id=campaign_id,
        story_npc_id=story_npc_id,
        npc_name=npc_name,
        npc_race=npc_race,
        npc_social_role=npc_social_role,
        disposition=starting,
    )
    db.add(state)
    db.flush()
    return state


def add_npc_memory(npc_state, turn_number: int, summary: str, sentiment: str = "neutral", player: str = ""):
    """Add a memory to an NPC's interaction history. Max 20 memories."""
    memories = list(npc_state.memories or [])
    memories.append({
        "turn": turn_number,
        "summary": summary,
        "sentiment": sentiment,
        "player": player,
    })
    # Keep only the 20 most recent
    if len(memories) > 20:
        memories = memories[-20:]
    npc_state.memories = memories
    npc_state.interaction_count = (npc_state.interaction_count or 0) + 1
    npc_state.last_interaction_turn = turn_number
