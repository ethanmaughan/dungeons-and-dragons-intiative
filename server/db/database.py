from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from server.config import DATABASE_URL

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """Dependency that provides a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    """Create all tables in the database."""
    from server.db import models  # noqa: F401 — import so models are registered
    Base.metadata.create_all(bind=engine)
    _run_migrations()


def _run_migrations():
    """Add columns to existing tables if they don't exist yet (safe to re-run)."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)

    # Campaign: add visibility, max_players, invite_code columns
    if "campaigns" in inspector.get_table_names():
        existing = {col["name"] for col in inspector.get_columns("campaigns")}
        with engine.begin() as conn:
            if "visibility" not in existing:
                conn.execute(text("ALTER TABLE campaigns ADD COLUMN visibility VARCHAR(20) DEFAULT 'open'"))
            if "max_players" not in existing:
                conn.execute(text("ALTER TABLE campaigns ADD COLUMN max_players INTEGER DEFAULT 4"))
            if "invite_code" not in existing:
                conn.execute(text("ALTER TABLE campaigns ADD COLUMN invite_code VARCHAR(10)"))

    # Backfill invite codes for existing campaigns that don't have one
    _backfill_invite_codes()

    # GameLog: add character_id column
    if "game_log" in inspector.get_table_names():
        existing = {col["name"] for col in inspector.get_columns("game_log")}
        with engine.begin() as conn:
            if "character_id" not in existing:
                conn.execute(text("ALTER TABLE game_log ADD COLUMN character_id INTEGER"))

    # StoryNPC: add demographic fields
    if "story_npcs" in inspector.get_table_names():
        existing = {col["name"] for col in inspector.get_columns("story_npcs")}
        with engine.begin() as conn:
            if "race" not in existing:
                conn.execute(text("ALTER TABLE story_npcs ADD COLUMN race VARCHAR(50) DEFAULT 'human'"))
            if "social_role" not in existing:
                conn.execute(text("ALTER TABLE story_npcs ADD COLUMN social_role VARCHAR(50) DEFAULT 'peasant'"))
            if "default_disposition" not in existing:
                conn.execute(text("ALTER TABLE story_npcs ADD COLUMN default_disposition INTEGER"))

    # GameState: add rolling_summary column for session continuity
    if "game_state" in inspector.get_table_names():
        existing = {col["name"] for col in inspector.get_columns("game_state")}
        with engine.begin() as conn:
            if "rolling_summary" not in existing:
                conn.execute(text("ALTER TABLE game_state ADD COLUMN rolling_summary TEXT"))
            if "combat_positions" not in existing:
                conn.execute(text("ALTER TABLE game_state ADD COLUMN combat_positions JSON"))

    # Character: add sprite_url for combat map sprites
    if "characters" in inspector.get_table_names():
        existing = {col["name"] for col in inspector.get_columns("characters")}
        with engine.begin() as conn:
            if "sprite_url" not in existing:
                conn.execute(text("ALTER TABLE characters ADD COLUMN sprite_url VARCHAR(500)"))
            if "backstory" not in existing:
                conn.execute(text("ALTER TABLE characters ADD COLUMN backstory TEXT"))
            if "motto" not in existing:
                conn.execute(text("ALTER TABLE characters ADD COLUMN motto VARCHAR(500)"))
            if "personality_tags" not in existing:
                conn.execute(text("ALTER TABLE characters ADD COLUMN personality_tags JSON DEFAULT '[]'"))
            if "title" not in existing:
                conn.execute(text("ALTER TABLE characters ADD COLUMN title VARCHAR(200)"))
            if "character_goals" not in existing:
                conn.execute(text("ALTER TABLE characters ADD COLUMN character_goals TEXT"))

    # Chapter: v2 story engine fields
    if "chapters" in inspector.get_table_names():
        existing = {col["name"] for col in inspector.get_columns("chapters")}
        with engine.begin() as conn:
            if "truth" not in existing:
                conn.execute(text("ALTER TABLE chapters ADD COLUMN truth JSON DEFAULT '{}'"))
            if "tone" not in existing:
                conn.execute(text("ALTER TABLE chapters ADD COLUMN tone TEXT"))
            if "resolution" not in existing:
                conn.execute(text("ALTER TABLE chapters ADD COLUMN resolution JSON DEFAULT '{}'"))
            if "next_chapter" not in existing:
                conn.execute(text("ALTER TABLE chapters ADD COLUMN next_chapter INTEGER"))
            if "branches" not in existing:
                conn.execute(text("ALTER TABLE chapters ADD COLUMN branches JSON DEFAULT '[]'"))

    # CampaignStory: flags for cross-chapter state
    if "campaign_stories" in inspector.get_table_names():
        existing = {col["name"] for col in inspector.get_columns("campaign_stories")}
        with engine.begin() as conn:
            if "flags" not in existing:
                conn.execute(text("ALTER TABLE campaign_stories ADD COLUMN flags JSON DEFAULT '{}'"))

    # StoryNPC: conditional dialogue
    if "story_npcs" in inspector.get_table_names():
        existing = {col["name"] for col in inspector.get_columns("story_npcs")}
        with engine.begin() as conn:
            if "conditional_dialogue" not in existing:
                conn.execute(text("ALTER TABLE story_npcs ADD COLUMN conditional_dialogue JSON DEFAULT '[]'"))

    # ChapterProgress: beats_completed
    if "chapter_progress" in inspector.get_table_names():
        existing = {col["name"] for col in inspector.get_columns("chapter_progress")}
        with engine.begin() as conn:
            if "beats_completed" not in existing:
                conn.execute(text("ALTER TABLE chapter_progress ADD COLUMN beats_completed JSON DEFAULT '{}'"))

    # Player: add email, admin, and subscription fields
    if "players" in inspector.get_table_names():
        existing = {col["name"] for col in inspector.get_columns("players")}
        with engine.begin() as conn:
            if "email" not in existing:
                conn.execute(text("ALTER TABLE players ADD COLUMN email VARCHAR(255)"))
            if "is_admin" not in existing:
                conn.execute(text("ALTER TABLE players ADD COLUMN is_admin BOOLEAN DEFAULT FALSE"))
            if "stripe_customer_id" not in existing:
                conn.execute(text("ALTER TABLE players ADD COLUMN stripe_customer_id VARCHAR(255)"))
            if "stripe_subscription_id" not in existing:
                conn.execute(text("ALTER TABLE players ADD COLUMN stripe_subscription_id VARCHAR(255)"))
            if "subscription_status" not in existing:
                conn.execute(text("ALTER TABLE players ADD COLUMN subscription_status VARCHAR(50) DEFAULT 'none'"))
            if "subscription_override" not in existing:
                conn.execute(text("ALTER TABLE players ADD COLUMN subscription_override BOOLEAN DEFAULT FALSE"))


def _backfill_invite_codes():
    """Generate invite codes for existing campaigns that don't have one."""
    import secrets
    import string

    from server.db.models import Campaign

    db = SessionLocal()
    try:
        campaigns = db.query(Campaign).filter(
            (Campaign.invite_code == None) | (Campaign.invite_code == "")
        ).all()
        if not campaigns:
            return

        alphabet = string.ascii_uppercase + string.digits
        existing_codes = set(
            c.invite_code for c in db.query(Campaign).filter(Campaign.invite_code != None).all()
        )

        for campaign in campaigns:
            for _ in range(50):
                code = "".join(secrets.choice(alphabet) for _ in range(6))
                if code not in existing_codes:
                    campaign.invite_code = code
                    existing_codes.add(code)
                    break

        db.commit()
    finally:
        db.close()
