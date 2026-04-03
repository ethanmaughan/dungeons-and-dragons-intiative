"""Story Engine — chapter context injection, milestone tracking, session summaries.

Builds the chapter context that gets injected into the DM system prompt so the AI
stays on-story. Tracks objective completion via keyword scan + AI confirmation.
Handles chapter transitions and session summaries.
"""

import json
import traceback

from server.config import AI_BACKEND, ANTHROPIC_API_KEY, COMBAT_INTENT_MODEL
from server.services.story_service import get_current_chapter

if AI_BACKEND == "claude":
    import anthropic
    _story_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# Use same lightweight model as combat intent / enemy agent
MILESTONE_MODEL = COMBAT_INTENT_MODEL


def build_chapter_context(campaign_id: int, db) -> str | None:
    """Build the chapter context block for injection into the DM system prompt.

    Returns a formatted string to append to the prompt, or None if no story assigned.
    """
    chapter = get_current_chapter(campaign_id, db)
    if not chapter:
        return None

    lines = []
    lines.append(f"## Active Story: {chapter['story_title']}")
    lines.append(f"### Chapter {chapter['chapter_number']}: {chapter['chapter_title']}")
    lines.append(f"\n{chapter['chapter_summary']}")

    if chapter["setting_description"]:
        lines.append(f"\n### Setting\n{chapter['setting_description']}")

    if chapter["dm_guidance"]:
        lines.append(f"\n### DM Guidance (FOLLOW THESE INSTRUCTIONS)\n{chapter['dm_guidance']}")

    # Objectives with completion status
    lines.append("\n### Chapter Objectives")
    lines.append("Players must complete these to advance. Guide the story toward them naturally — NEVER force players or tell them what to do.")
    for obj in chapter["objectives"]:
        status = "[COMPLETE]" if obj["completed"] else "[INCOMPLETE]"
        req = " (required)" if obj["required"] else " (optional)"
        hint = f" — Hint: {obj['hint']}" if obj["hint"] and not obj["completed"] else ""
        lines.append(f"- {status} {obj['description']}{req}{hint}")

    # NPCs
    if chapter["npcs"]:
        lines.append("\n### Key NPCs This Chapter")
        for npc in chapter["npcs"]:
            lines.append(f"\n**{npc['name']}** ({npc['role']}): {npc['personality']}")
            if npc["appearance"]:
                lines.append(f"  Appearance: {npc['appearance']}")
            for hook in npc.get("dialogue_hooks", []):
                lines.append(f"  When asked about {hook['topic']}: {hook['response_guidance']}")

    # Events
    untriggered_events = [e for e in chapter["events"]]
    if untriggered_events:
        lines.append("\n### Available Events")
        lines.append("Weave these into the narrative when appropriate:")
        for event in untriggered_events:
            hint = event["event_data"].get("narration_hint", event["description"])
            trigger_note = ""
            if event["trigger"] == "objective_complete":
                trigger_note = f" (trigger after: {event['trigger_condition']})"
            lines.append(f"- {event['description']}{trigger_note}: {hint}")

    # Story continuity
    summaries = chapter.get("chapter_summaries", {})
    if summaries:
        lines.append("\n### Story Continuity — Previous Chapters")
        for ch_num in sorted(summaries.keys(), key=int):
            lines.append(f"Chapter {ch_num}: {summaries[ch_num]}")

    # Player freedom reminder
    lines.append(
        "\n### IMPORTANT: Player Freedom"
        "\nPlayers have complete freedom. If they ignore objectives and do something "
        "unexpected, play along — describe the world reacting to their choices. "
        "Gently remind them of the chapter's situation through environmental cues, "
        "NPC dialogue, or events. Never break immersion by telling players what they should do."
    )

    return "\n".join(lines)


# ---- Milestone Tracking ----


def check_keyword_matches(narration: str, action_text: str, chapter_data: dict) -> list[dict]:
    """Fast keyword scan for potential objective completions. Returns objectives that matched."""
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
    """AI confirmation that an objective has been completed.

    Returns {"completed": True/False, "summary": "..."}.
    """
    if not objective.get("detection_prompt"):
        # No AI prompt defined — keyword match is sufficient
        return {"completed": True, "summary": "Objective completed (keyword match)"}

    if AI_BACKEND != "claude":
        # Without Claude, keyword match is sufficient
        return {"completed": True, "summary": "Objective completed (keyword match)"}

    try:
        recent = "\n".join(recent_narrations[-3:])
        user_msg = (
            f"Objective: {objective['detection_prompt']}\n\n"
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
        # On failure, trust the keyword match
        return {"completed": True, "summary": "Objective completed (fallback)"}


async def check_transition_ready(
    chapter_data: dict, recent_narrations: list[str],
) -> bool:
    """AI check: is the narrative at a natural stopping point for this chapter?"""
    if AI_BACKEND != "claude":
        return True  # Without AI, trust that objectives being done is enough

    try:
        completed = [obj["description"] for obj in chapter_data["objectives"] if obj["completed"]]
        recent = "\n".join(recent_narrations[-5:])

        user_msg = (
            f"Chapter: {chapter_data['chapter_title']}\n\n"
            f"Completed objectives:\n" + "\n".join(f"- {d}" for d in completed) + "\n\n"
            f"Last 5 narration entries:\n{recent}\n\n"
            "Is the narrative at a natural stopping point for this chapter? "
            "Are there loose ends the players are actively pursuing?\n"
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
        return True  # On failure, allow transition


def mark_objective_complete(
    campaign_story_id: int, chapter_number: int, objective_key: str, summary: str, turn_number: int, db,
):
    """Mark an objective as completed in the chapter progress."""
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


def advance_chapter(campaign_story_id: int, chapter_summary: str, db) -> int | None:
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

    # Check if there's a next chapter
    next_chapter = (
        db.query(Chapter)
        .filter(
            Chapter.story_id == cs.story_id,
            Chapter.chapter_number == cs.current_chapter_number + 1,
        )
        .first()
    )

    if not next_chapter:
        # Story complete!
        cs.status = "completed"
        cs.completed_at = datetime.now(timezone.utc)
        db.commit()
        return None

    # Advance
    cs.current_chapter_number = next_chapter.chapter_number

    # Create progress for new chapter
    new_progress = ChapterProgress(
        campaign_story_id=cs.id,
        chapter_number=next_chapter.chapter_number,
    )
    db.add(new_progress)
    db.commit()

    return next_chapter.chapter_number
