import json
import traceback
from pathlib import Path

from server.config import AI_BACKEND, ANTHROPIC_API_KEY, CLAUDE_MODEL, OLLAMA_MODEL

# Load prompt templates
PROMPTS_DIR = Path(__file__).parent / "prompts"
CORE_PERSONA = (PROMPTS_DIR / "core_persona.txt").read_text()
CHARACTER_CREATION_PERSONA = (PROMPTS_DIR / "character_creation_persona.txt").read_text()

# Initialize the appropriate client
if AI_BACKEND == "claude":
    import anthropic
    claude_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
else:
    import ollama


def build_system_prompt(campaign, game_state, characters, mode="play", chapter_context: str | None = None) -> str:
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
            line = (
                f"- {c.character_name} ({c.race} {c.char_class} {c.level}): "
                f"HP {c.hp_current}/{c.hp_max}, AC {c.ac}, XP {c.xp}, "
                f"STR {c.str_score} DEX {c.dex_score} CON {c.con_score} "
                f"INT {c.int_score} WIS {c.wis_score} CHA {c.cha_score}"
            )
            # Death save status
            if c.hp_current <= 0:
                conditions = c.conditions or []
                if "dead" in conditions:
                    line += " [DEAD]"
                elif "stable" in conditions:
                    line += " [STABLE — unconscious]"
                else:
                    saves = c.death_saves or {"successes": 0, "failures": 0}
                    line += f" [DYING — saves: {saves['successes']} successes, {saves['failures']} failures]"
            if c.spell_slots_current:
                slots_str = ", ".join(
                    f"L{k}: {v}/{c.spell_slots.get(k, '?')}"
                    for k, v in c.spell_slots_current.items()
                )
                line += f"\n  Spell Slots: {slots_str}"
            if c.spells:
                line += f"\n  Spells: {', '.join(c.spells)}"
            if c.inventory:
                line += f"\n  Inventory: {', '.join(c.inventory[:10])}"
            party_lines.append(line)

    party_summary = "\n".join(party_lines) if party_lines else "No characters yet."

    pcs = [c for c in characters if not c.is_npc and not c.is_enemy]
    pc_count = len(pcs)
    avg_level = sum(c.level for c in pcs) / max(pc_count, 1)
    max_enemies = max(1, pc_count + int(avg_level // 2))
    prompt = CORE_PERSONA.format(
        campaign_name=campaign.name,
        setting=campaign.setting or "A classic fantasy world.",
        game_mode=game_state.game_mode if game_state else "exploration",
        environment=game_state.environment_description if game_state else "Unknown",
        party_size=pc_count,
        max_enemies=max_enemies,
    )

    prompt += f"\n\n## Party\n{party_summary}"

    # Multiplayer note
    if pc_count > 1:
        prompt += (
            "\n\n## Multiplayer Session"
            f"\nThis is a multiplayer session with {pc_count} player characters."
            "\nPlayer actions are prefixed with [CharacterName]: to identify who is acting."
            "\nAddress the acting character by name in your narration."
            "\nUse character names (not 'you') when describing actions so all players know who did what."
            "\nIn combat, use the correct character name in tags: [PLAYER_ATTACK:CharName:Target]"
        )

    if chapter_context:
        prompt += f"\n\n{chapter_context}"
    elif campaign.synopsis:
        prompt += f"\n\n## Story So Far\n{campaign.synopsis}"

    return prompt


def build_messages(system_prompt: str, action: str, recent_logs) -> list[dict]:
    """Build the conversation history from recent game logs."""
    messages = [{"role": "system", "content": system_prompt}]

    for log in recent_logs:
        if log.action_text:
            messages.append({"role": "user", "content": log.action_text})
        if log.narration_text:
            messages.append({"role": "assistant", "content": log.narration_text})

    messages.append({"role": "user", "content": action})

    return messages


async def _call_ollama(messages: list[dict]) -> str:
    """Call Ollama (local) for a response."""
    response = ollama.chat(
        model=OLLAMA_MODEL,
        messages=messages,
    )
    return response.message.content


async def _call_claude(system_prompt: str, messages: list[dict]) -> str:
    """Call Claude API for a response."""
    # Claude uses a separate system parameter, not a system message
    # Filter out the system message from the messages list
    chat_messages = [m for m in messages if m["role"] != "system"]

    response = await claude_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=chat_messages,
    )
    return response.content[0].text


async def process_player_action(
    action: str,
    campaign,
    game_state,
    characters,
    recent_logs,
    mode: str = "play",
    chapter_context: str | None = None,
) -> str:
    """Process a player action and return the DM's narration."""
    try:
        system_prompt = build_system_prompt(
            campaign, game_state, characters, mode=mode, chapter_context=chapter_context,
        )
        messages = build_messages(system_prompt, action, recent_logs)

        if AI_BACKEND == "claude":
            return await _call_claude(system_prompt, messages)
        else:
            return await _call_ollama(messages)
    except Exception as e:
        traceback.print_exc()
        return f"[Connection error: {e}]"
