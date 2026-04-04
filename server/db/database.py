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
