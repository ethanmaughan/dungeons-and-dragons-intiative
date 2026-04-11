"""Centralized guardrail validation for all AI tools.

Each tool calls validate_tool_invocation() at its entry point.
Failures are soft — callers log and skip, never raise.
"""

from enum import Enum


class ToolDomain(str, Enum):
    BATTLEFIELD_TACTICS = "battlefield_tactics"
    ENEMY_PERSONALITY = "enemy_personality"
    ENEMY_LEARNING_READ = "enemy_learning_read"
    ENEMY_LEARNING_WRITE = "enemy_learning_write"
    NPC_GUIDE = "npc_guide"
    ENVIRONMENT_SHIFT = "environment_shift"


# Valid trigger sources per tool domain
_ALLOWED_TRIGGERS: dict[ToolDomain, set[str]] = {
    ToolDomain.BATTLEFIELD_TACTICS: {"player_action"},
    ToolDomain.ENEMY_PERSONALITY: {"player_action"},
    ToolDomain.ENEMY_LEARNING_READ: {"player_action"},
    ToolDomain.ENEMY_LEARNING_WRITE: {"combat_end"},
    ToolDomain.NPC_GUIDE: {"exploration_turn"},
    ToolDomain.ENVIRONMENT_SHIFT: {"round_end"},
}

# Domains that require combat mode
_COMBAT_DOMAINS = {
    ToolDomain.BATTLEFIELD_TACTICS,
    ToolDomain.ENEMY_PERSONALITY,
    ToolDomain.ENEMY_LEARNING_READ,
    ToolDomain.ENVIRONMENT_SHIFT,
}


def validate_tool_invocation(
    domain: ToolDomain,
    game_state,
    trigger_source: str,
) -> tuple[bool, str]:
    """Validate that a tool invocation is allowed.

    Returns (True, "") on success.
    Returns (False, reason) if validation fails.
    """
    # Check trigger source is valid for this domain
    allowed = _ALLOWED_TRIGGERS.get(domain, set())
    if trigger_source not in allowed:
        return False, f"trigger '{trigger_source}' not allowed for {domain.value}"

    # Check combat mode requirement
    if domain in _COMBAT_DOMAINS:
        if not game_state or getattr(game_state, "game_mode", None) != "combat":
            return False, f"{domain.value} requires combat mode"

    # NPC guide requires exploration mode
    if domain == ToolDomain.NPC_GUIDE:
        if not game_state or getattr(game_state, "game_mode", None) != "exploration":
            return False, "npc_guide requires exploration mode"

    return True, ""
