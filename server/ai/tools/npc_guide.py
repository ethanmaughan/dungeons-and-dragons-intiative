"""NPC Proactive Guide Tool — exploration-only NPC guidance.

Detects when players seem stuck or hit story beats, and generates
contextually relevant NPC dialogue to guide progression.
One Haiku call when triggered, max every 3 turns.
"""

from __future__ import annotations

import json
import traceback

from server.config import AI_BACKEND, ANTHROPIC_API_KEY, ENEMY_AGENT_MODEL

if AI_BACKEND == "claude":
    import anthropic
    _guide_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


# ---- Guardrails ----

COOLDOWN_TURNS = 3
STUCK_THRESHOLD = 5


def _check_guardrails(
    game_state,
    chapter_data: dict | None,
    recent_logs: list,
    db,
    campaign_id: int,
) -> tuple[bool, str, str]:
    """Check all guardrails. Returns (allowed, reason, trigger_type)."""
    # 1. Mode gate — exploration only
    if not game_state or getattr(game_state, "game_mode", None) != "exploration":
        return False, "not_exploration", ""

    # 2. NPC presence check
    if not chapter_data or not chapter_data.get("npcs"):
        return False, "no_npcs", ""

    # 3. Cooldown check
    effects = game_state.active_effects or {}
    if isinstance(effects, list):
        effects = {}
    last_guide_turn = effects.get("npc_guide_cooldown", 0)
    current_turn = len(recent_logs) if recent_logs else 0
    if current_turn - last_guide_turn < COOLDOWN_TURNS:
        return False, "cooldown", ""

    # 4. Trigger condition — at least one must fire
    trigger = _check_triggers(chapter_data, recent_logs, db, campaign_id)
    if not trigger:
        return False, "no_trigger", ""

    return True, "", trigger


def _check_triggers(
    chapter_data: dict,
    recent_logs: list,
    db,
    campaign_id: int,
) -> str | None:
    """Check if any trigger condition is met. Returns trigger type or None."""
    # Trigger A: Player seems stuck — no objective progress in last N turns
    if len(recent_logs) >= STUCK_THRESHOLD:
        recent_changes = recent_logs[-STUCK_THRESHOLD:]
        has_progress = any(
            (log.state_changes or {}).get("objective_completed")
            for log in recent_changes
        )
        if not has_progress:
            return "stuck_players"

    # Trigger B: Story beat keyword in recent action
    if recent_logs:
        last_action = (recent_logs[-1].action_text or "").lower()
        objectives = chapter_data.get("objectives", [])
        for obj in objectives:
            if obj.get("completed"):
                continue
            keywords = obj.get("detection_keywords", [])
            for kw in keywords:
                if kw.lower() in last_action:
                    return "story_beat"

    # Trigger C: NPC disposition shifted significantly
    try:
        from server.db.models import NPCState
        npc_states = db.query(NPCState).filter(
            NPCState.campaign_id == campaign_id
        ).all()
        for npc_state in npc_states:
            if npc_state.interaction_count > 0:
                # Check if disposition changed by 15+ since we last checked
                memories = npc_state.memories or []
                if memories and len(memories) >= 2:
                    recent_sentiments = [m.get("sentiment", "neutral") for m in memories[-3:]]
                    if any(s in ("aggressive", "threatening", "hostile") for s in recent_sentiments):
                        return "disposition_change"
    except Exception:
        pass

    return None


# ---- NPC selection ----


def _select_relevant_npc(
    chapter_data: dict,
    trigger_reason: str,
    db,
    campaign_id: int,
) -> tuple[dict | None, object | None]:
    """Pick the most contextually relevant NPC to speak."""
    npcs = chapter_data.get("npcs", [])
    if not npcs:
        return None, None

    from server.db.models import NPCState

    best_npc = None
    best_state = None
    best_score = -1

    for npc in npcs:
        npc_name = npc.get("name", "")
        state = db.query(NPCState).filter(
            NPCState.campaign_id == campaign_id,
            NPCState.npc_name == npc_name,
        ).first()

        score = 0
        disposition = state.disposition if state else 50

        if trigger_reason == "stuck_players":
            # Prefer NPC with highest disposition + most knowledge
            score = disposition + len(npc.get("knowledge", [])) * 10
        elif trigger_reason == "story_beat":
            # Prefer NPC with matching dialogue hooks
            score = disposition + len(npc.get("dialogue_hooks", [])) * 15
        elif trigger_reason == "disposition_change":
            # Pick the NPC whose disposition changed
            if state and state.interaction_count > 0:
                score = 100 + state.interaction_count

        if score > best_score:
            best_score = score
            best_npc = npc
            best_state = state

    return best_npc, best_state


# ---- AI dialogue generation ----


async def _generate_npc_dialogue(
    npc_data: dict,
    npc_state,
    trigger_reason: str,
    chapter_data: dict,
    recent_action: str,
) -> dict | None:
    """Generate NPC guide dialogue via Haiku. Returns result dict or None."""
    if AI_BACKEND != "claude":
        return _rule_based_guide(npc_data, trigger_reason, chapter_data)

    npc_name = npc_data.get("name", "NPC")
    personality = npc_data.get("personality", "helpful")
    disposition_label = "neutral"
    if npc_state:
        d = npc_state.disposition
        if d >= 85:
            disposition_label = "allied"
        elif d >= 65:
            disposition_label = "friendly"
        elif d >= 45:
            disposition_label = "neutral"
        elif d >= 30:
            disposition_label = "unfriendly"
        else:
            disposition_label = "hostile"

    # Recent memories
    memories_str = ""
    if npc_state and npc_state.memories:
        recent_mems = npc_state.memories[-3:]
        memories_str = "\n".join(
            f"  - {m.get('summary', '')}" for m in recent_mems
        )

    # Incomplete objectives
    objectives = chapter_data.get("objectives", [])
    incomplete = [o for o in objectives if not o.get("completed")]
    obj_hints = "\n".join(
        f"  - {o.get('description', '')} (hint: {o.get('hint', 'none')})"
        for o in incomplete[:3]
    )

    trigger_desc = {
        "stuck_players": "The players seem stuck and haven't made progress on objectives recently.",
        "story_beat": f"A story beat was triggered by the player's action: \"{recent_action}\"",
        "disposition_change": f"The NPC's relationship with the party has shifted recently.",
    }.get(trigger_reason, "General guidance needed.")

    system_prompt = f"""You are generating a brief in-character moment for {npc_name}, an NPC in a D&D campaign.
Personality: {personality}
Disposition toward party: {disposition_label}
{f"Recent memories:{chr(10)}{memories_str}" if memories_str else ""}

Situation: {trigger_desc}

Incomplete objectives the players need to accomplish:
{obj_hints if obj_hints else "  (none specified)"}

RULES:
- Stay in character as {npc_name}.
- Do NOT directly tell players what to do. Instead, react naturally:
  notice something, remember something, express concern, make an observation.
- Keep it to 1-2 sentences maximum.
- The NPC's disposition should color their tone ({disposition_label}).

Respond with ONLY valid JSON:
{{"dialogue": "what the NPC says or does", "action_type": "observation|hint|reaction|memory_trigger"}}"""

    try:
        response = await _guide_client.messages.create(
            model=ENEMY_AGENT_MODEL,
            max_tokens=150,
            system=system_prompt,
            messages=[{"role": "user", "content": f"Player just did: {recent_action}"}],
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        result = json.loads(raw)
        return {
            "npc_name": npc_name,
            "dialogue": result.get("dialogue", ""),
            "action_type": result.get("action_type", "observation"),
        }
    except Exception:
        traceback.print_exc()
        return _rule_based_guide(npc_data, trigger_reason, chapter_data)


def _rule_based_guide(npc_data: dict, trigger_reason: str, chapter_data: dict) -> dict | None:
    """Fallback rule-based NPC guidance."""
    npc_name = npc_data.get("name", "NPC")
    knowledge = npc_data.get("knowledge", [])

    if trigger_reason == "stuck_players" and knowledge:
        hint = knowledge[0] if isinstance(knowledge[0], str) else str(knowledge[0])
        return {
            "npc_name": npc_name,
            "dialogue": f'{npc_name} clears their throat. "You know, I recall something that might help... {hint}"',
            "action_type": "hint",
        }

    return None


# ---- Main entry point ----


async def maybe_guide(
    campaign_id: int,
    game_state,
    characters: list,
    chapter_data: dict | None,
    recent_logs: list,
    db,
) -> dict | None:
    """Check guardrails and generate NPC guidance if appropriate.

    Returns None if guardrails block, or:
    {
        "npc_name": str,
        "dialogue": str,
        "action_type": str,
        "inject_into_prompt": str,  # formatted for DM system prompt
    }
    """
    allowed, reason, trigger = _check_guardrails(
        game_state, chapter_data, recent_logs, db, campaign_id,
    )
    if not allowed:
        return None

    # Select NPC
    npc_data, npc_state = _select_relevant_npc(
        chapter_data, trigger, db, campaign_id,
    )
    if not npc_data:
        return None

    # Get recent action text
    recent_action = ""
    if recent_logs:
        recent_action = recent_logs[-1].action_text or ""

    # Generate dialogue
    result = await _generate_npc_dialogue(
        npc_data, npc_state, trigger, chapter_data, recent_action,
    )
    if not result or not result.get("dialogue"):
        return None

    # Update cooldown
    effects = game_state.active_effects or {}
    if isinstance(effects, list):
        effects = {}
    effects["npc_guide_cooldown"] = len(recent_logs) if recent_logs else 0
    game_state.active_effects = effects

    # Format for DM prompt injection
    npc_name = result["npc_name"]
    dialogue = result["dialogue"]
    inject = (
        f"An NPC ({npc_name}) has something to contribute this turn. "
        f"Work this naturally into your narration:\n"
        f"{npc_name}: \"{dialogue}\""
    )

    return {
        "npc_name": npc_name,
        "dialogue": dialogue,
        "action_type": result["action_type"],
        "inject_into_prompt": inject,
    }
