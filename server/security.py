"""Security utilities: subscription gating, admin checks, rate limiting, headers."""

from fastapi import HTTPException
from slowapi import Limiter
from slowapi.util import get_remote_address


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


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://unpkg.com https://js.stripe.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "frame-src https://js.stripe.com; "
        "connect-src 'self' wss: ws:; "
        "img-src 'self' data: https:"
    ),
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}


class SecurityHeadersMiddleware:
    """Pure ASGI middleware — does NOT use BaseHTTPMiddleware.

    BaseHTTPMiddleware breaks WebSocket connections in Starlette/FastAPI.
    This raw ASGI implementation only adds headers to HTTP responses,
    passing WebSocket connections through untouched.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            # Pass WebSocket and lifespan through untouched
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                for key, value in SECURITY_HEADERS.items():
                    headers[key.lower().encode()] = value.encode()
                message["headers"] = list(headers.items())
            await send(message)

        await self.app(scope, receive, send_with_headers)
