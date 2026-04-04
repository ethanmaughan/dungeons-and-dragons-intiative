"""Security utilities: subscription gating, admin checks, rate limiting, headers."""

from fastapi import HTTPException
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware


limiter = Limiter(key_func=get_remote_address)


def player_can_play(player) -> bool:
    """Check if a player has access to play (subscribed, admin, or comped)."""
    if not player:
        return False
    if player.is_admin:
        return True
    if player.subscription_override:
        return True
    if player.subscription_status == "active":
        return True
    return False


def require_admin(player):
    """Raise 403 if player is not an admin."""
    if not player or not player.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://unpkg.com https://js.stripe.com; "
            "style-src 'self' 'unsafe-inline'; "
            "frame-src https://js.stripe.com; "
            "connect-src 'self' wss: ws:; "
            "img-src 'self' data: https:; "
        )
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response
