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
