import hashlib
import secrets

from fastapi import Depends, Request
from sqlalchemy.orm import Session as DBSession

from server.db.database import get_db
from server.db.models import Player


def hash_password(password: str) -> str:
    """Hash password with salt using pbkdf2 (no extra dependencies)."""
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}${hashed.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored hash."""
    salt, hashed = stored.split("$")
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return check.hex() == hashed


def get_current_player(request: Request, db: DBSession = Depends(get_db)) -> Player | None:
    """FastAPI dependency: returns the logged-in Player or None."""
    player_id = request.session.get("player_id")
    if not player_id:
        return None
    return db.query(Player).filter(Player.id == player_id).first()
