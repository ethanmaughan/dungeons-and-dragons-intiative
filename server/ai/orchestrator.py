import json
import traceback
from pathlib import Path

import ollama

# Load prompt templates
PROMPTS_DIR = Path(__file__).parent / "prompts"
CORE_PERSONA = (PROMPTS_DIR / "core_persona.txt").read_text()
CHARACTER_CREATION_PERSONA = (PROMPTS_DIR / "character_creation_persona.txt").read_text()


def build_system_prompt(campaign, game_state, characters, mode="play") -> str:
    """Assemble the system prompt based on game mode."""

    if mode == "character_creation":
        # Build choices JSON from the character being created
        pc = next((c for c in characters if not c.is_npc and not c.is_enemy), None)
        choices = {}
        if pc:
            if pc.race != "Human" or pc.char_class != "Fighter":
                choices["race"] = pc.race if pc.race != "Human" else None
                choices["class"] = pc.char_class if pc.char_class != "Fighter" else None
            if pc.character_name != "Unnamed Adventurer":
                choices["name"] = pc.character_name

        return CHARACTER_CREATION_PERSONA.format(
            creation_step=game_state.creation_step or "race",
            choices_json=json.dumps(choices) if choices else "None yet",
        )

    # Normal play mode
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


def build_messages(system_prompt: str, action: str, recent_logs) -> list[dict]:
    """Build the conversation history for Ollama from recent game logs."""
    messages = [{"role": "system", "content": system_prompt}]

    for log in recent_logs:
        if log.action_text:
            messages.append({"role": "user", "content": log.action_text})
        if log.narration_text:
            messages.append({"role": "assistant", "content": log.narration_text})

    messages.append({"role": "user", "content": action})

    return messages


async def process_player_action(
    action: str,
    campaign,
    game_state,
    characters,
    recent_logs,
    mode: str = "play",
) -> str:
    """Process a player action through Ollama and return the DM's narration."""
    try:
        system_prompt = build_system_prompt(campaign, game_state, characters, mode=mode)
        messages = build_messages(system_prompt, action, recent_logs)

        response = ollama.chat(
            model="mistral",
            messages=messages,
        )

        return response.message.content
    except Exception as e:
        traceback.print_exc()
        return f"[Connection error: {e}]"
