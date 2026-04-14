"""Story Engine v2 — chapter context injection, beat tracking, flag management,
branch resolution, and session summaries.

Builds the chapter context that gets injected into the DM system prompt so the AI
stays on-story. Tracks beat completion via keyword scan + AI confirmation.
Handles chapter transitions with branching support.
"""

import json
import traceback

from server.config import AI_BACKEND, ANTHROPIC_API_KEY, COMBAT_INTENT_MODEL
from server.services.story_service import get_current_chapter

if AI_BACKEND == "claude":
    import anthropic
    _story_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

MILESTONE_MODEL = COMBAT_INTENT_MODEL


# ---- Flag evaluation ----


def evaluate_condition(condition: str, flags: dict) -> bool:
    """Evaluate a flag-based condition string.

    'flag:met_elder' → flags.get('met_elder', False)
    """
    if condition.startswith("flag:"):
        flag_name = condition[5:]
        return bool(flags.get(flag_name, False))
    return False


def _beat_prerequisites_met(beat: dict, completed_keys: set, flags: dict) -> bool:
    """Check if all prerequisites for a beat are satisfied."""
    for prereq in beat.get("prerequisites", []):
        if prereq.startswith("flag:"):
            if not evaluate_condition(prereq, flags):
                return False
        else:
            if prereq not in completed_keys:
                return False
    return True


# ---- Chapter context building ----


def build_chapter_context(campaign_id: int, db, characters: list | None = None) -> str | None:
    """Build the chapter context block for injection into the DM system prompt.

    Returns a formatted string, or None if no story assigned.
    Supports both v1 and v2 story formats.
    """
    chapter = get_current_chapter(campaign_id, db)
    if not chapter:
        return None

    if chapter.get("is_v2"):
        return _build_v2_context(chapter, campaign_id, db, characters)
    else:
        return _build_v1_context(chapter, campaign_id, db, characters)


def _build_v2_context(chapter: dict, campaign_id: int, db, characters: list | None) -> str:
    """Build DM prompt context for v2 stories (beats, truth, flags)."""
    lines = []
    lines.append(f"## Active Story: {chapter['story_title']}")
    lines.append(f"### Chapter {chapter['chapter_number']}: {chapter['chapter_title']}")
    lines.append(f"\n{chapter['chapter_summary']}")

    if chapter["setting_description"]:
        lines.append(f"\n### Setting\n{chapter['setting_description']}")

    # TRUTH — canonical facts the DM must follow
    truth = chapter.get("truth", {})
    if truth.get("facts"):
        lines.append("\n### TRUTH — You must NEVER contradict these facts")
        for fact in truth["facts"]:
            lines.append(f"- {fact}")

    if truth.get("secrets"):
        lines.append("\n### SECRETS — The players do NOT know these yet. Reveal only through natural discovery:")
        for secret in truth["secrets"]:
            lines.append(f"- {secret}")

    if truth.get("red_herrings"):
        lines.append("\n### RED HERRINGS — You may use these to mislead or add tension:")
        for herring in truth["red_herrings"]:
            lines.append(f"- {herring}")

    # Tone
    if chapter.get("tone"):
        lines.append(f"\n### Tone\n{chapter['tone']}")

    # Beats — categorized by status
    beats = chapter.get("beats", [])
    flags = chapter.get("flags", {})
    completed_keys = {b["key"] for b in beats if b["completed"]}

    available_beats = []
    locked_count = 0
    for beat in beats:
        if beat["completed"]:
            continue
        if _beat_prerequisites_met(beat, completed_keys, flags):
            available_beats.append(beat)
        else:
            locked_count += 1

    if available_beats:
        lines.append("\n### Available Beats — Weave these into the narrative")
        for beat in available_beats:
            req = "[REQUIRED]" if beat["required"] else "[OPTIONAL]"
            trigger = f" ({beat['trigger']})" if beat["trigger"] != "player_action" else ""
            lines.append(f"- {req} {beat['description']}{trigger}")
            if beat.get("hint"):
                lines.append(f"  Hint: {beat['hint']}")
            if beat.get("narration_hint"):
                lines.append(f"  Narration guide: {beat['narration_hint']}")
            if beat.get("combat"):
                enemies = beat["combat"].get("enemies", [])
                lines.append(f"  Combat: triggers fight with {', '.join(enemies)}")

    completed_beats = [b for b in beats if b["completed"]]
    if completed_beats:
        lines.append("\n### Completed Beats")
        for beat in completed_beats:
            lines.append(f"- \u2713 {beat['description']}")

    if locked_count > 0:
        lines.append(f"\n_{locked_count} more beat(s) unlock after completing current available beats._")

    # NPCs
    if chapter["npcs"]:
        lines.append("\n### Key NPCs This Chapter")
        _append_npc_context(lines, chapter, campaign_id, db, characters)

    # Resolution
    resolution = chapter.get("resolution", {})
    if resolution:
        req_beats = resolution.get("required_beats", [])
        done = [k for k in req_beats if k in completed_keys]
        remaining = [k for k in req_beats if k not in completed_keys]
        if remaining:
            lines.append(f"\n### Resolution — Chapter ends when these beats are complete:")
            for k in remaining:
                beat = next((b for b in beats if b["key"] == k), None)
                desc = beat["description"] if beat else k
                lines.append(f"- [ ] {desc}")
            for k in done:
                beat = next((b for b in beats if b["key"] == k), None)
                desc = beat["description"] if beat else k
                lines.append(f"- [\u2713] {desc}")
        if resolution.get("max_turns"):
            lines.append(f"  Max turns: {resolution['max_turns']}")

    # Story continuity
    summaries = chapter.get("chapter_summaries", {})
    if summaries:
        lines.append("\n### Story Continuity — Previous Chapters")
        for ch_num in sorted(summaries.keys(), key=int):
            lines.append(f"Chapter {ch_num}: {summaries[ch_num]}")

    # Active flags (for DM awareness)
    if flags:
        active = [k for k, v in flags.items() if v]
        if active:
            lines.append(f"\n### Campaign State Flags: {', '.join(active)}")

    # Player freedom
    lines.append(
        "\n### IMPORTANT: Player Freedom"
        "\nPlayers have complete freedom. If they ignore beats and do something "
        "unexpected, play along — describe the world reacting to their choices. "
        "Gently remind them of the chapter's situation through environmental cues, "
        "NPC dialogue, or events. Never break immersion by telling players what they should do."
    )

    return "\n".join(lines)


def _build_v1_context(chapter: dict, campaign_id: int, db, characters: list | None) -> str:
    """Build DM prompt context for v1 stories (objectives, dm_guidance). Legacy path."""
    lines = []
    lines.append(f"## Active Story: {chapter['story_title']}")
    lines.append(f"### Chapter {chapter['chapter_number']}: {chapter['chapter_title']}")
    lines.append(f"\n{chapter['chapter_summary']}")

    if chapter["setting_description"]:
        lines.append(f"\n### Setting\n{chapter['setting_description']}")

    if chapter["dm_guidance"]:
        lines.append(f"\n### DM Guidance (FOLLOW THESE INSTRUCTIONS)\n{chapter['dm_guidance']}")

    # Objectives
    lines.append("\n### Chapter Objectives")
    lines.append("Players must complete these to advance.")
    for obj in chapter["objectives"]:
        status = "[COMPLETE]" if obj["completed"] else "[INCOMPLETE]"
        req = " (required)" if obj["required"] else " (optional)"
        hint = f" — Hint: {obj['hint']}" if obj["hint"] and not obj["completed"] else ""
        lines.append(f"- {status} {obj['description']}{req}{hint}")

    # NPCs
    if chapter["npcs"]:
        lines.append("\n### Key NPCs This Chapter")
        _append_npc_context(lines, chapter, campaign_id, db, characters)

    # Events
    events = chapter.get("events", [])
    if events:
        lines.append("\n### Available Events")
        lines.append("Weave these into the narrative when appropriate:")
        for event in events:
            hint = event["event_data"].get("narration_hint", event["description"])
            trigger_note = ""
            if event["trigger"] == "objective_complete":
                trigger_note = f" (trigger after: {event['trigger_condition']})"
            lines.append(f"- {event['description']}{trigger_note}: {hint}")

    # Continuity
    summaries = chapter.get("chapter_summaries", {})
    if summaries:
        lines.append("\n### Story Continuity — Previous Chapters")
        for ch_num in sorted(summaries.keys(), key=int):
            lines.append(f"Chapter {ch_num}: {summaries[ch_num]}")

    lines.append(
        "\n### IMPORTANT: Player Freedom"
        "\nPlayers have complete freedom. If they ignore objectives and do something "
        "unexpected, play along — describe the world reacting to their choices."
    )

    return "\n".join(lines)


def _append_npc_context(lines: list, chapter: dict, campaign_id: int, db, characters: list | None):
    """Shared NPC context builder for both v1 and v2."""
    from server.db.models import NPCState
    from server.engine.disposition import get_disposition_label, get_or_create_npc_state

    for npc in chapter["npcs"]:
        lines.append(f"\n**{npc['name']}** ({npc['role']}): {npc['personality']}")
        if npc.get("race"):
            lines.append(f"  Race: {npc['race'].title()} | Social Role: {npc.get('social_role', 'peasant').title()}")
        if npc["appearance"]:
            lines.append(f"  Appearance: {npc['appearance']}")

        npc_state = (
            db.query(NPCState)
            .filter(NPCState.campaign_id == campaign_id, NPCState.npc_name == npc["name"])
            .first()
        )
        if not npc_state and characters:
            npc_state = get_or_create_npc_state(
                campaign_id, npc["name"], characters, db,
                npc_race=npc.get("race", "human"),
                npc_social_role=npc.get("social_role", "peasant"),
                story_npc_id=npc.get("story_npc_id"),
                story_override=npc.get("default_disposition"),
            )
        if npc_state:
            label = get_disposition_label(npc_state.disposition)
            lines.append(f"  Disposition toward party: {label} ({npc_state.disposition}/100)")
            memories = npc_state.memories or []
            if memories:
                recent_mems = memories[-3:]
                mem_str = "; ".join(m["summary"] for m in recent_mems if m.get("summary"))
                if mem_str:
                    lines.append(f"  Memories: {mem_str}")

        for hook in npc.get("dialogue_hooks", []):
            lines.append(f"  When asked about {hook['topic']}: {hook['response_guidance']}")


# ---- Beat tracking (v2) ----


def check_beat_completions(
    narration: str, action_text: str, chapter_data: dict, db,
) -> list[dict]:
    """Check for beat completions based on keywords. Returns beats that matched.

    For v2 stories. Also processes auto-trigger beats whose prerequisites just became met.
    """
    if not chapter_data.get("is_v2"):
        # v1 fallback: use old keyword matching
        return check_keyword_matches(narration, action_text, chapter_data)

    beats = chapter_data.get("beats", [])
    flags = chapter_data.get("flags", {})
    completed_keys = {b["key"] for b in beats if b["completed"]}
    combined = (narration + " " + (action_text or "")).lower()

    matches = []

    for beat in beats:
        if beat["completed"]:
            continue
        if not _beat_prerequisites_met(beat, completed_keys, flags):
            continue

        # Auto-trigger beats complete immediately when prerequisites are met
        if beat["trigger"] == "auto":
            matches.append(beat)
            continue

        # Player action and dm_discretion beats use keyword detection
        keywords = beat.get("detection_keywords", [])
        if not keywords:
            continue
        hit_count = sum(1 for kw in keywords if kw.lower() in combined)
        threshold = min(2, len(keywords))
        if hit_count >= threshold:
            matches.append(beat)

    return matches


def process_beat_completion(
    beat_key: str, summary: str, turn_number: int,
    chapter_data: dict, db,
) -> list[str]:
    """Mark a beat complete and execute on_complete actions.

    Returns list of newly set flag names.
    """
    from server.db.models import CampaignStory, ChapterProgress

    cs = db.query(CampaignStory).filter(
        CampaignStory.id == chapter_data["campaign_story_id"]
    ).first()
    if not cs:
        return []

    # Mark beat complete in ChapterProgress
    progress = (
        db.query(ChapterProgress)
        .filter(
            ChapterProgress.campaign_story_id == cs.id,
            ChapterProgress.chapter_number == chapter_data["chapter_number"],
        )
        .first()
    )
    if not progress:
        return []

    beats_done = dict(progress.beats_completed or {})
    beats_done[beat_key] = {
        "completed": True,
        "summary": summary,
        "turn_number": turn_number,
    }
    progress.beats_completed = beats_done

    # Also write to objectives_completed for backward compat
    obj_done = dict(progress.objectives_completed or {})
    obj_done[beat_key] = {"completed": True, "summary": summary, "turn_number": turn_number}
    progress.objectives_completed = obj_done

    # Execute on_complete actions
    new_flags = []
    beat = next((b for b in chapter_data.get("beats", []) if b["key"] == beat_key), None)
    if beat and beat.get("on_complete"):
        on_complete = beat["on_complete"]

        # Set flags
        flags_to_set = on_complete.get("set_flags", [])
        if flags_to_set:
            campaign_flags = dict(cs.flags or {})
            for flag in flags_to_set:
                campaign_flags[flag] = True
                new_flags.append(flag)
            cs.flags = campaign_flags

    db.flush()
    return new_flags


def check_resolution_ready(chapter_data: dict) -> bool:
    """Check if all required beats are complete (resolution conditions met)."""
    resolution = chapter_data.get("resolution", {})
    required_beat_keys = resolution.get("required_beats", [])

    if not required_beat_keys:
        return False

    beats = chapter_data.get("beats", [])
    completed_keys = {b["key"] for b in beats if b["completed"]}

    return all(k in completed_keys for k in required_beat_keys)


def resolve_chapter_branch(chapter_data: dict) -> int | None:
    """Determine the next chapter number based on flags and branches.

    Returns next chapter number, or None if story is complete.
    """
    flags = chapter_data.get("flags", {})
    branches = chapter_data.get("branches", [])

    # Check branches in order — first match wins
    for branch in branches:
        condition = branch.get("condition", "")
        if evaluate_condition(condition, flags):
            return branch.get("next_chapter")

    # Default: next_chapter field
    return chapter_data.get("next_chapter")


# ---- Legacy v1 milestone tracking (kept for backward compat) ----


def check_keyword_matches(narration: str, action_text: str, chapter_data: dict) -> list[dict]:
    """Fast keyword scan for potential objective completions (v1). Returns objectives that matched."""
    combined = (narration + " " + (action_text or "")).lower()
    matches = []

    for obj in chapter_data.get("objectives", []):
        if obj["completed"]:
            continue
        keywords = obj.get("detection_keywords", [])
        if not keywords:
            continue
        hit_count = sum(1 for kw in keywords if kw.lower() in combined)
        threshold = min(2, len(keywords))
        if hit_count >= threshold:
            matches.append(obj)

    return matches


async def confirm_objective(
    objective: dict, recent_narrations: list[str], action_text: str,
) -> dict:
    """AI confirmation that a beat/objective has been completed.

    Returns {"completed": True/False, "summary": "..."}.
    """
    detection_prompt = objective.get("detection_prompt")
    if not detection_prompt:
        return {"completed": True, "summary": "Completed (keyword match)"}

    if AI_BACKEND != "claude":
        return {"completed": True, "summary": "Completed (keyword match)"}

    try:
        recent = "\n".join(recent_narrations[-3:])
        user_msg = (
            f"Objective: {detection_prompt}\n\n"
            f"Recent narration:\n{recent}\n\n"
            f"Player's latest action: {action_text}\n\n"
            "Has this objective been meaningfully completed? "
            "Respond with ONLY valid JSON:\n"
            '{"completed": true, "summary": "brief description"}\n'
            "or\n"
            '{"completed": false}'
        )

        response = await _story_client.messages.create(
            model=MILESTONE_MODEL,
            max_tokens=100,
            system="You are an objective tracker for a D&D campaign. Determine if a specific objective has been completed based on the narrative.",
            messages=[{"role": "user", "content": user_msg}],
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(raw)

    except Exception:
        traceback.print_exc()
        return {"completed": True, "summary": "Completed (fallback)"}


async def check_transition_ready(
    chapter_data: dict, recent_narrations: list[str],
) -> bool:
    """AI check: is the narrative at a natural stopping point for this chapter?"""
    # v2: use resolution_prompt if available
    resolution = chapter_data.get("resolution", {})
    res_prompt = resolution.get("resolution_prompt")

    if AI_BACKEND != "claude":
        return True

    try:
        completed_descs = []
        if chapter_data.get("is_v2"):
            completed_descs = [
                b["description"] for b in chapter_data.get("beats", []) if b["completed"]
            ]
        else:
            completed_descs = [
                obj["description"] for obj in chapter_data["objectives"] if obj["completed"]
            ]

        recent = "\n".join(recent_narrations[-5:])

        user_msg = (
            f"Chapter: {chapter_data['chapter_title']}\n\n"
            f"Completed:\n" + "\n".join(f"- {d}" for d in completed_descs) + "\n\n"
            f"Last 5 narration entries:\n{recent}\n\n"
        )
        if res_prompt:
            user_msg += f"Resolution question: {res_prompt}\n\n"
        user_msg += (
            "Is the narrative at a natural stopping point for this chapter? "
            "Respond with ONLY valid JSON:\n"
            '{"ready": true}\nor\n{"ready": false, "reason": "..."}'
        )

        response = await _story_client.messages.create(
            model=MILESTONE_MODEL,
            max_tokens=100,
            system="You determine if a D&D campaign chapter has reached a natural conclusion point.",
            messages=[{"role": "user", "content": user_msg}],
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = json.loads(raw)
        return result.get("ready", False)

    except Exception:
        traceback.print_exc()
        return True


def mark_objective_complete(
    campaign_story_id: int, chapter_number: int, objective_key: str, summary: str, turn_number: int, db,
):
    """Mark an objective as completed in the chapter progress (v1 compat)."""
    from server.db.models import ChapterProgress

    progress = (
        db.query(ChapterProgress)
        .filter(
            ChapterProgress.campaign_story_id == campaign_story_id,
            ChapterProgress.chapter_number == chapter_number,
        )
        .first()
    )
    if not progress:
        return

    completed = dict(progress.objectives_completed or {})
    completed[objective_key] = {
        "completed": True,
        "summary": summary,
        "turn_number": turn_number,
    }
    progress.objectives_completed = completed
    db.flush()


def advance_chapter(campaign_story_id: int, chapter_summary: str, db, next_chapter_override: int | None = None) -> int | None:
    """Advance to the next chapter. Returns the new chapter number, or None if story is complete."""
    from datetime import datetime, timezone
    from server.db.models import CampaignStory, Chapter, ChapterProgress

    cs = db.query(CampaignStory).filter(CampaignStory.id == campaign_story_id).first()
    if not cs:
        return None

    # Save chapter summary
    summaries = dict(cs.chapter_summaries or {})
    summaries[str(cs.current_chapter_number)] = chapter_summary
    cs.chapter_summaries = summaries

    # Mark current chapter progress as completed
    current_progress = (
        db.query(ChapterProgress)
        .filter(
            ChapterProgress.campaign_story_id == cs.id,
            ChapterProgress.chapter_number == cs.current_chapter_number,
        )
        .first()
    )
    if current_progress:
        current_progress.status = "completed"
        current_progress.summary = chapter_summary
        current_progress.completed_at = datetime.now(timezone.utc)

    # Determine next chapter (branch override or default sequential)
    target = next_chapter_override
    if target is None:
        # Check for next sequential chapter
        next_chapter = (
            db.query(Chapter)
            .filter(
                Chapter.story_id == cs.story_id,
                Chapter.chapter_number == cs.current_chapter_number + 1,
            )
            .first()
        )
        target = next_chapter.chapter_number if next_chapter else None

    if target is None:
        # Story complete
        cs.status = "completed"
        cs.completed_at = datetime.now(timezone.utc)
        db.commit()
        return None

    # Verify the target chapter exists
    target_chapter = (
        db.query(Chapter)
        .filter(Chapter.story_id == cs.story_id, Chapter.chapter_number == target)
        .first()
    )
    if not target_chapter:
        cs.status = "completed"
        cs.completed_at = datetime.now(timezone.utc)
        db.commit()
        return None

    # Advance
    cs.current_chapter_number = target
    new_progress = ChapterProgress(
        campaign_story_id=cs.id,
        chapter_number=target,
    )
    db.add(new_progress)
    db.commit()

    return target


# ---- Session Summarizer ----

SUMMARY_INTERVAL = 20


async def maybe_summarize(game_state, turn_number: int, recent_logs, db):
    """Every SUMMARY_INTERVAL turns, summarize recent narration and append to rolling_summary."""
    if turn_number % SUMMARY_INTERVAL != 0 or turn_number == 0:
        return

    narrations = [
        log.narration_text for log in recent_logs
        if log.narration_text and log.narration_text.strip()
    ]
    if not narrations:
        return

    combined = "\n\n".join(narrations[-SUMMARY_INTERVAL:])

    if AI_BACKEND == "claude":
        try:
            response = await _story_client.messages.create(
                model=MILESTONE_MODEL,
                max_tokens=200,
                system=(
                    "You are a campaign chronicler. Summarize the recent events of this "
                    "D&D session in 2-3 concise sentences. Focus on: what the players did, "
                    "key discoveries, NPC interactions, and combat outcomes. "
                    "Write in past tense, third person."
                ),
                messages=[{"role": "user", "content": combined}],
            )
            segment_summary = response.content[0].text.strip()
        except Exception:
            traceback.print_exc()
            segment_summary = f"[Turns {turn_number - SUMMARY_INTERVAL + 1}-{turn_number}: events occurred]"
    else:
        segment_summary = f"[Turns {turn_number - SUMMARY_INTERVAL + 1}-{turn_number}: events occurred]"

    existing = game_state.rolling_summary or ""
    if existing:
        game_state.rolling_summary = existing + "\n" + segment_summary
    else:
        game_state.rolling_summary = segment_summary
    db.flush()
