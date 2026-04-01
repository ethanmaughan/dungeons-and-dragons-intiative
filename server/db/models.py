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
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    owner: Mapped["Player | None"] = relationship(back_populates="campaigns")
    sessions: Mapped[list["Session"]] = relationship(back_populates="campaign")
    characters: Mapped[list["Character"]] = relationship(back_populates="campaign")
    join_requests: Mapped[list["JoinRequest"]] = relationship(back_populates="campaign")


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
