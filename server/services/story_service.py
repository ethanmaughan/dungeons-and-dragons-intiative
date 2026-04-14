"""Story CRUD: import JSON stories (v1 + v2), assign to campaigns, read chapter state."""

import json
from pathlib import Path

from sqlalchemy.orm import Session as DBSession

from server.db.models import (
    Beat,
    Campaign,
    CampaignStory,
    Chapter,
    ChapterProgress,
    Objective,
    StoryEvent,
    StoryNPC,
    StoryTemplate,
)

STORIES_DIR = Path(__file__).parent.parent.parent / "data" / "stories"


def import_story(filepath: str | Path, db: DBSession) -> StoryTemplate:
    """Import a story from a JSON file into the database.

    Supports both v1 (objectives/events) and v2 (beats/truth/resolution) formats.
    If a story with the same slug already exists, updates it (increments version).
    """
    data = json.loads(Path(filepath).read_text())
    story_version = data.get("version", 1)

    # Check for existing story
    existing = db.query(StoryTemplate).filter(StoryTemplate.slug == data["slug"]).first()
    if existing:
        # Delete old chapters and all children
        for chapter in existing.chapters:
            for obj in chapter.objectives:
                db.delete(obj)
            for beat in chapter.beats:
                db.delete(beat)
            for npc in chapter.story_npcs:
                db.delete(npc)
            for event in chapter.story_events:
                db.delete(event)
            db.delete(chapter)
        db.flush()

        existing.title = data["title"]
        existing.author = data.get("author", "Foray Team")
        existing.synopsis = data["synopsis"]
        existing.setting = data.get("setting", "")
        existing.recommended_level = data.get("recommended_level", 1)
        existing.recommended_players = data.get("recommended_players", "1-4")
        existing.version += 1
        story = existing
    else:
        story = StoryTemplate(
            slug=data["slug"],
            title=data["title"],
            author=data.get("author", "Foray Team"),
            synopsis=data["synopsis"],
            setting=data.get("setting", ""),
            recommended_level=data.get("recommended_level", 1),
            recommended_players=data.get("recommended_players", "1-4"),
        )
        db.add(story)
        db.flush()

    # Create chapters
    for ch_data in data.get("chapters", []):
        chapter = Chapter(
            story_id=story.id,
            chapter_number=ch_data["chapter_number"],
            title=ch_data["title"],
            summary=ch_data["summary"],
            setting_description=ch_data.get("setting_description", ""),
            dm_guidance=ch_data.get("dm_guidance", ""),
            opening_narration=ch_data.get("opening_narration"),
            transition_narration=ch_data.get("transition_narration"),
            # v2 fields
            truth=ch_data.get("truth", {}),
            tone=ch_data.get("tone"),
            resolution=ch_data.get("resolution", {}),
            next_chapter=ch_data.get("next_chapter"),
            branches=ch_data.get("branches", []),
        )
        db.add(chapter)
        db.flush()

        # v2: import beats
        if story_version >= 2 and "beats" in ch_data:
            for i, beat_data in enumerate(ch_data.get("beats", [])):
                db.add(Beat(
                    chapter_id=chapter.id,
                    key=beat_data["key"],
                    description=beat_data["description"],
                    required=beat_data.get("required", True),
                    sort_order=beat_data.get("sort_order", i),
                    prerequisites=beat_data.get("prerequisites", []),
                    trigger=beat_data.get("trigger", "player_action"),
                    trigger_condition=beat_data.get("trigger_condition"),
                    detection_keywords=beat_data.get("detection_keywords", []),
                    detection_prompt=beat_data.get("detection_prompt"),
                    hint=beat_data.get("hint"),
                    on_complete=beat_data.get("on_complete"),
                    narration_hint=beat_data.get("narration_hint"),
                    combat=beat_data.get("combat"),
                ))
        else:
            # v1: import objectives (backward compat)
            for obj_data in ch_data.get("objectives", []):
                db.add(Objective(
                    chapter_id=chapter.id,
                    key=obj_data["key"],
                    description=obj_data["description"],
                    hint=obj_data.get("hint", ""),
                    required=obj_data.get("required", True),
                    sort_order=obj_data.get("sort_order", 0),
                    detection_keywords=obj_data.get("detection_keywords", []),
                    detection_prompt=obj_data.get("detection_prompt"),
                ))

        # NPCs (same for v1 and v2, with optional conditional_dialogue)
        for npc_data in ch_data.get("npcs", []):
            db.add(StoryNPC(
                chapter_id=chapter.id,
                name=npc_data["name"],
                role=npc_data.get("role", ""),
                race=npc_data.get("race", "human"),
                social_role=npc_data.get("social_role", "peasant"),
                default_disposition=npc_data.get("default_disposition"),
                personality=npc_data.get("personality", ""),
                appearance=npc_data.get("appearance", ""),
                dialogue_hooks=npc_data.get("dialogue_hooks", []),
                knowledge=npc_data.get("knowledge", []),
                conditional_dialogue=npc_data.get("conditional_dialogue", []),
            ))

        # Events (v1 only — v2 merges events into beats)
        if story_version < 2:
            for event_data in ch_data.get("events", []):
                db.add(StoryEvent(
                    chapter_id=chapter.id,
                    key=event_data["key"],
                    description=event_data["description"],
                    trigger=event_data.get("trigger", "dm_discretion"),
                    trigger_condition=event_data.get("trigger_condition"),
                    event_data=event_data.get("event_data", {}),
                ))

    db.commit()
    return story


def import_all_stories(db: DBSession) -> list[StoryTemplate]:
    """Import all JSON story files from data/stories/."""
    stories = []
    if not STORIES_DIR.exists():
        return stories
    for filepath in sorted(STORIES_DIR.glob("*.json")):
        stories.append(import_story(filepath, db))
    return stories


def assign_story(campaign_id: int, story_slug: str, db: DBSession) -> CampaignStory:
    """Assign a story to a campaign. Creates CampaignStory + first ChapterProgress."""
    story = db.query(StoryTemplate).filter(StoryTemplate.slug == story_slug).first()
    if not story:
        raise ValueError(f"Story '{story_slug}' not found")

    # Check if campaign already has a story
    existing = db.query(CampaignStory).filter(CampaignStory.campaign_id == campaign_id).first()
    if existing:
        raise ValueError("Campaign already has an assigned story")

    campaign_story = CampaignStory(
        campaign_id=campaign_id,
        story_id=story.id,
        current_chapter_number=1,
    )
    db.add(campaign_story)
    db.flush()

    # Create progress for chapter 1
    progress = ChapterProgress(
        campaign_story_id=campaign_story.id,
        chapter_number=1,
    )
    db.add(progress)

    # Update campaign synopsis and setting from story
    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if campaign:
        campaign.synopsis = story.synopsis
        if story.setting:
            campaign.setting = story.setting

    db.commit()
    return campaign_story


def get_current_chapter(campaign_id: int, db: DBSession) -> dict | None:
    """Get the current chapter, its beats/objectives, NPCs, and progress.

    Returns None if the campaign has no assigned story.
    Supports both v1 (objectives) and v2 (beats) formats.
    """
    cs = db.query(CampaignStory).filter(CampaignStory.campaign_id == campaign_id).first()
    if not cs:
        return None

    chapter = (
        db.query(Chapter)
        .filter(
            Chapter.story_id == cs.story_id,
            Chapter.chapter_number == cs.current_chapter_number,
        )
        .first()
    )
    if not chapter:
        return None

    progress = (
        db.query(ChapterProgress)
        .filter(
            ChapterProgress.campaign_story_id == cs.id,
            ChapterProgress.chapter_number == cs.current_chapter_number,
        )
        .first()
    )

    obj_completed = progress.objectives_completed if progress else {}
    beats_completed = progress.beats_completed if progress else {}
    campaign_flags = cs.flags or {}

    # Determine if this is a v2 chapter (has beats)
    is_v2 = bool(chapter.beats)

    result = {
        "story_title": cs.story.title,
        "story_slug": cs.story.slug,
        "chapter_number": chapter.chapter_number,
        "chapter_title": chapter.title,
        "chapter_summary": chapter.summary,
        "setting_description": chapter.setting_description,
        "dm_guidance": chapter.dm_guidance,
        "opening_narration": chapter.opening_narration,
        "transition_narration": chapter.transition_narration,
        "chapter_summaries": cs.chapter_summaries or {},
        "campaign_story_id": cs.id,
        "status": cs.status,
        "is_v2": is_v2,
        # v2 fields
        "truth": chapter.truth or {},
        "tone": chapter.tone,
        "resolution": chapter.resolution or {},
        "next_chapter": chapter.next_chapter,
        "branches": chapter.branches or [],
        "flags": campaign_flags,
    }

    if is_v2:
        result["beats"] = [
            {
                "key": beat.key,
                "description": beat.description,
                "required": beat.required,
                "prerequisites": beat.prerequisites or [],
                "trigger": beat.trigger,
                "trigger_condition": beat.trigger_condition,
                "detection_keywords": beat.detection_keywords or [],
                "detection_prompt": beat.detection_prompt,
                "hint": beat.hint,
                "on_complete": beat.on_complete,
                "narration_hint": beat.narration_hint,
                "combat": beat.combat,
                "completed": beats_completed.get(beat.key, {}).get("completed", False),
            }
            for beat in chapter.beats
        ]
        # Also populate objectives for backward compat in action_service
        result["objectives"] = [
            {
                "key": b["key"],
                "description": b["description"],
                "hint": b["hint"],
                "required": b["required"],
                "completed": b["completed"],
                "detection_keywords": b["detection_keywords"],
                "detection_prompt": b["detection_prompt"],
            }
            for b in result["beats"]
            if b["trigger"] == "player_action"
        ]
    else:
        result["beats"] = []
        result["objectives"] = [
            {
                "key": obj.key,
                "description": obj.description,
                "hint": obj.hint,
                "required": obj.required,
                "completed": obj_completed.get(obj.key, {}).get("completed", False),
                "detection_keywords": obj.detection_keywords or [],
                "detection_prompt": obj.detection_prompt,
            }
            for obj in chapter.objectives
        ]

    # NPCs with conditional dialogue filtered by active flags
    result["npcs"] = []
    for npc in chapter.story_npcs:
        npc_data = {
            "name": npc.name,
            "role": npc.role,
            "race": npc.race or "human",
            "social_role": npc.social_role or "peasant",
            "default_disposition": npc.default_disposition,
            "story_npc_id": npc.id,
            "personality": npc.personality,
            "appearance": npc.appearance,
            "dialogue_hooks": list(npc.dialogue_hooks or []),
            "knowledge": npc.knowledge or [],
        }
        # Add conditional dialogue where flags are met
        for cd in (npc.conditional_dialogue or []):
            required_flag = cd.get("requires_flag", "")
            if required_flag and campaign_flags.get(required_flag):
                npc_data["dialogue_hooks"].append({
                    "topic": cd["topic"],
                    "response_guidance": cd["response_guidance"],
                })
        result["npcs"].append(npc_data)

    # Events (v1 only)
    result["events"] = [
        {
            "key": event.key,
            "description": event.description,
            "trigger": event.trigger,
            "trigger_condition": event.trigger_condition,
            "event_data": event.event_data or {},
        }
        for event in chapter.story_events
    ]

    return result
