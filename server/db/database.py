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

    # Campaign: add visibility and max_players columns
    if "campaigns" in inspector.get_table_names():
        existing = {col["name"] for col in inspector.get_columns("campaigns")}
        with engine.begin() as conn:
            if "visibility" not in existing:
                conn.execute(text("ALTER TABLE campaigns ADD COLUMN visibility VARCHAR(20) DEFAULT 'open'"))
            if "max_players" not in existing:
                conn.execute(text("ALTER TABLE campaigns ADD COLUMN max_players INTEGER DEFAULT 4"))

    # GameLog: add character_id column
    if "game_log" in inspector.get_table_names():
        existing = {col["name"] for col in inspector.get_columns("game_log")}
        with engine.begin() as conn:
            if "character_id" not in existing:
                conn.execute(text("ALTER TABLE game_log ADD COLUMN character_id INTEGER"))
