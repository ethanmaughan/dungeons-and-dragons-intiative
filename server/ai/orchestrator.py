from pathlib import Path

import anthropic

from server.config import ANTHROPIC_API_KEY

# Load prompt template
PROMPTS_DIR = Path(__file__).parent / "prompts"
CORE_PERSONA = (PROMPTS_DIR / "core_persona.txt").read_text()

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def build_system_prompt(campaign, game_state, characters) -> str:
    """Assemble the system prompt from the core persona and current game state."""
    # Format character summaries
    party_lines = []
    for c in characters:
        if not c.is_npc and not c.is_enemy:
            party_lines.append(
                f"- {c.character_name} ({c.race} {c.char_class} {c.level}): "
                f"HP {c.hp_current}/{c.hp_max}, AC {c.ac}, "
                f"STR {c.str_score} DEX {c.dex_score} CON {c.con_score} "
                f"INT {c.int_score} WIS {c.wis_score} CHA {c.cha_score}"
            )

    party_summary = "\n".join(party_lines) if party_lines else "No characters yet."

    # Fill in the template
    prompt = CORE_PERSONA.format(
        campaign_name=campaign.name,
        setting=campaign.setting or "A classic fantasy world.",
        game_mode=game_state.game_mode if game_state else "exploration",
        environment=game_state.environment_description if game_state else "Unknown",
    )

    prompt += f"\n\n## Party\n{party_summary}"

    if campaign.synopsis:
        prompt += f"\n\n## Story So Far\n{campaign.synopsis}"

    return prompt


def build_messages(action: str, recent_logs) -> list[dict]:
    """Build the conversation history for Claude from recent game logs."""
    messages = []

    for log in recent_logs:
        if log.action_text:
            messages.append({"role": "user", "content": log.action_text})
        if log.narration_text:
            messages.append({"role": "assistant", "content": log.narration_text})

    # Add the current player action
    messages.append({"role": "user", "content": action})

    return messages


async def process_player_action(
    action: str,
    campaign,
    game_state,
    characters,
    recent_logs,
) -> str:
    """Process a player action through Claude and return the DM's narration."""
    system_prompt = build_system_prompt(campaign, game_state, characters)
    messages = build_messages(action, recent_logs)

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        system=system_prompt,
        messages=messages,
    )

    return response.content[0].text
