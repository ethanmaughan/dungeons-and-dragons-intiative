from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.db.database import Base


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    characters: Mapped[list["Character"]] = relationship(back_populates="player")
    campaigns: Mapped[list["Campaign"]] = relationship(back_populates="owner")


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    synopsis: Mapped[str | None] = mapped_column(Text, default="")
    setting: Mapped[str | None] = mapped_column(Text, default="")
    active_threads: Mapped[dict | None] = mapped_column(JSON, default=list)
    world_day: Mapped[int] = mapped_column(Integer, default=1)
    world_hour: Mapped[int] = mapped_column(Integer, default=8)
    visibility: Mapped[str] = mapped_column(String(20), default="open")
    max_players: Mapped[int] = mapped_column(Integer, default=4)
    invite_code: Mapped[str | None] = mapped_column(String(10), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    owner: Mapped["Player | None"] = relationship(back_populates="campaigns")
    sessions: Mapped[list["Session"]] = relationship(back_populates="campaign")
    characters: Mapped[list["Character"]] = relationship(back_populates="campaign")
    join_requests: Mapped[list["JoinRequest"]] = relationship(back_populates="campaign")
    campaign_story: Mapped["CampaignStory | None"] = relationship(back_populates="campaign", uselist=False)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    session_number: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="active")
    summary: Mapped[str | None] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    campaign: Mapped["Campaign"] = relationship(back_populates="sessions")
    game_state: Mapped["GameState | None"] = relationship(back_populates="session", uselist=False)
    game_logs: Mapped[list["GameLog"]] = relationship(back_populates="session")


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"), nullable=True)
    player_name: Mapped[str] = mapped_column(String(100), default="GM")
    character_name: Mapped[str] = mapped_column(String(100), nullable=False)
    race: Mapped[str] = mapped_column(String(50), default="Human")
    char_class: Mapped[str] = mapped_column(String(50), default="Fighter")
    level: Mapped[int] = mapped_column(Integer, default=1)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    hp_current: Mapped[int] = mapped_column(Integer, default=10)
    hp_max: Mapped[int] = mapped_column(Integer, default=10)
    ac: Mapped[int] = mapped_column(Integer, default=10)
    speed: Mapped[int] = mapped_column(Integer, default=30)

    # Ability scores
    str_score: Mapped[int] = mapped_column(Integer, default=10)
    dex_score: Mapped[int] = mapped_column(Integer, default=10)
    con_score: Mapped[int] = mapped_column(Integer, default=10)
    int_score: Mapped[int] = mapped_column(Integer, default=10)
    wis_score: Mapped[int] = mapped_column(Integer, default=10)
    cha_score: Mapped[int] = mapped_column(Integer, default=10)

    proficiency_bonus: Mapped[int] = mapped_column(Integer, default=2)
    skills: Mapped[dict | None] = mapped_column(JSON, default=dict)
    features: Mapped[dict | None] = mapped_column(JSON, default=list)
    inventory: Mapped[dict | None] = mapped_column(JSON, default=list)
    spells: Mapped[dict | None] = mapped_column(JSON, default=list)
    spell_slots: Mapped[dict | None] = mapped_column(JSON, default=dict)
    spell_slots_current: Mapped[dict | None] = mapped_column(JSON, default=dict)
    conditions: Mapped[dict | None] = mapped_column(JSON, default=list)
    death_saves: Mapped[dict | None] = mapped_column(JSON, default=lambda: {"successes": 0, "failures": 0})

    is_npc: Mapped[bool] = mapped_column(Boolean, default=False)
    is_enemy: Mapped[bool] = mapped_column(Boolean, default=False)
    npc_personality: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    npc_relationship: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sprite_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    creation_complete: Mapped[bool] = mapped_column(Boolean, default=False)

    campaign: Mapped["Campaign | None"] = relationship(back_populates="characters")
    player: Mapped["Player | None"] = relationship(back_populates="characters")


class GameState(Base):
    __tablename__ = "game_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    game_mode: Mapped[str] = mapped_column(String(20), default="exploration")
    current_turn_character_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    initiative_order: Mapped[dict | None] = mapped_column(JSON, default=list)
    round_number: Mapped[int] = mapped_column(Integer, default=0)
    environment_description: Mapped[str] = mapped_column(
        Text, default="You find yourself in a dimly lit tavern."
    )
    active_effects: Mapped[dict | None] = mapped_column(JSON, default=list)
    creation_step: Mapped[str | None] = mapped_column(String(50), nullable=True)
    rolling_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    combat_positions: Mapped[dict | None] = mapped_column(JSON, default=dict)

    session: Mapped["Session"] = relationship(back_populates="game_state")


class GameLog(Base):
    __tablename__ = "game_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    character_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"), nullable=True)
    turn_number: Mapped[int] = mapped_column(Integer, default=0)
    actor: Mapped[str] = mapped_column(String(100), default="system")
    action_text: Mapped[str | None] = mapped_column(Text, default="")
    narration_text: Mapped[str | None] = mapped_column(Text, default="")
    dice_rolls: Mapped[dict | None] = mapped_column(JSON, default=list)
    state_changes: Mapped[dict | None] = mapped_column(JSON, default=dict)
    game_mode: Mapped[str] = mapped_column(String(20), default="exploration")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    session: Mapped["Session"] = relationship(back_populates="game_logs")
    character: Mapped["Character | None"] = relationship()


class JoinRequest(Base):
    __tablename__ = "join_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    campaign: Mapped["Campaign"] = relationship(back_populates="join_requests")
    player: Mapped["Player"] = relationship()
    character: Mapped["Character"] = relationship()


# ---- Story Engine Models ----


class StoryTemplate(Base):
    """An authored campaign story — canonical Foray lore."""
    __tablename__ = "story_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    author: Mapped[str] = mapped_column(String(200), default="Foray Team")
    synopsis: Mapped[str] = mapped_column(Text, nullable=False)
    setting: Mapped[str] = mapped_column(Text, default="")
    recommended_level: Mapped[int] = mapped_column(Integer, default=1)
    recommended_players: Mapped[str] = mapped_column(String(20), default="1-4")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    chapters: Mapped[list["Chapter"]] = relationship(
        back_populates="story", order_by="Chapter.chapter_number"
    )


class Chapter(Base):
    """A chapter within a story — its own focused context for the DM."""
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("story_templates.id"), nullable=False)
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    setting_description: Mapped[str] = mapped_column(Text, default="")
    dm_guidance: Mapped[str] = mapped_column(Text, default="")
    opening_narration: Mapped[str | None] = mapped_column(Text, nullable=True)
    transition_narration: Mapped[str | None] = mapped_column(Text, nullable=True)

    story: Mapped["StoryTemplate"] = relationship(back_populates="chapters")
    objectives: Mapped[list["Objective"]] = relationship(
        back_populates="chapter", order_by="Objective.sort_order"
    )
    story_npcs: Mapped[list["StoryNPC"]] = relationship(back_populates="chapter")
    story_events: Mapped[list["StoryEvent"]] = relationship(back_populates="chapter")


class Objective(Base):
    """A chapter objective that must be completed to advance the story."""
    __tablename__ = "objectives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    hint: Mapped[str] = mapped_column(Text, default="")
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    detection_keywords: Mapped[dict | None] = mapped_column(JSON, default=list)
    detection_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    chapter: Mapped["Chapter"] = relationship(back_populates="objectives")


class StoryNPC(Base):
    """A key NPC in a chapter with personality, dialogue hooks, and knowledge."""
    __tablename__ = "story_npcs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(100), default="")
    race: Mapped[str] = mapped_column(String(50), default="human")
    social_role: Mapped[str] = mapped_column(String(50), default="peasant")
    default_disposition: Mapped[int | None] = mapped_column(Integer, nullable=True)
    personality: Mapped[str] = mapped_column(Text, default="")
    appearance: Mapped[str] = mapped_column(Text, default="")
    dialogue_hooks: Mapped[dict | None] = mapped_column(JSON, default=list)
    knowledge: Mapped[dict | None] = mapped_column(JSON, default=list)

    chapter: Mapped["Chapter"] = relationship(back_populates="story_npcs")


class StoryEvent(Base):
    """A triggered event/encounter within a chapter."""
    __tablename__ = "story_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[str] = mapped_column(String(50), default="dm_discretion")
    trigger_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_data: Mapped[dict | None] = mapped_column(JSON, default=dict)

    chapter: Mapped["Chapter"] = relationship(back_populates="story_events")


class CampaignStory(Base):
    """Links a Campaign to a StoryTemplate and tracks chapter progress."""
    __tablename__ = "campaign_stories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), unique=True, nullable=False)
    story_id: Mapped[int] = mapped_column(ForeignKey("story_templates.id"), nullable=False)
    current_chapter_number: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="active")
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    chapter_summaries: Mapped[dict | None] = mapped_column(JSON, default=dict)

    campaign: Mapped["Campaign"] = relationship(back_populates="campaign_story")
    story: Mapped["StoryTemplate"] = relationship()
    chapter_progress: Mapped[list["ChapterProgress"]] = relationship(back_populates="campaign_story")


class ChapterProgress(Base):
    """Tracks objective completion for a specific chapter in a campaign playthrough."""
    __tablename__ = "chapter_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_story_id: Mapped[int] = mapped_column(ForeignKey("campaign_stories.id"), nullable=False)
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active")
    objectives_completed: Mapped[dict | None] = mapped_column(JSON, default=dict)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    campaign_story: Mapped["CampaignStory"] = relationship(back_populates="chapter_progress")


class NPCState(Base):
    """Persistent per-campaign NPC state: disposition, memories, interaction history."""
    __tablename__ = "npc_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), nullable=False)
    story_npc_id: Mapped[int | None] = mapped_column(ForeignKey("story_npcs.id"), nullable=True)
    npc_name: Mapped[str] = mapped_column(String(200), nullable=False)
    npc_race: Mapped[str] = mapped_column(String(50), default="human")
    npc_social_role: Mapped[str] = mapped_column(String(50), default="peasant")
    disposition: Mapped[int] = mapped_column(Integer, default=50)
    memories: Mapped[dict | None] = mapped_column(JSON, default=list)
    interaction_count: Mapped[int] = mapped_column(Integer, default=0)
    last_interaction_turn: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    campaign: Mapped["Campaign"] = relationship()


class PartyProfile(Base):
    """Per-campaign behavioral aggregates — tracks how the party plays."""
    __tablename__ = "party_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), unique=True, nullable=False)
    behavior_counts: Mapped[dict | None] = mapped_column(JSON, default=dict)
    total_actions_classified: Mapped[int] = mapped_column(Integer, default=0)
    dominant_tendency: Mapped[str] = mapped_column(String(50), default="neutral")
    recent_tendency: Mapped[str] = mapped_column(String(50), default="neutral")
    recent_actions: Mapped[dict | None] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    campaign: Mapped["Campaign"] = relationship()
