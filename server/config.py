import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# Use DATABASE_URL env var for production (PostgreSQL), fallback to SQLite for local dev
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/game.db")
SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY:
    import warnings
    warnings.warn("SECRET_KEY not set — using insecure default for development only!")
    SECRET_KEY = "dnd-initiative-dev-secret-DO-NOT-USE-IN-PROD"

# Stripe
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
# Comma-separated list of additional admin usernames
ADMIN_USERNAMES = {u.strip() for u in os.getenv("ADMIN_USERNAMES", "").split(",") if u.strip()}
if ADMIN_USERNAME:
    ADMIN_USERNAMES.add(ADMIN_USERNAME)

# AI backend: "ollama" (free, local) or "claude" (paid, better quality)
AI_BACKEND = os.getenv("AI_BACKEND", "ollama")
# Model names
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
# Lightweight model for enemy combat AI agents (fast + cheap)
ENEMY_AGENT_MODEL = os.getenv("ENEMY_AGENT_MODEL", "claude-haiku-4-5-20251001")
# Model for combat intent parsing (player action → structured intent)
COMBAT_INTENT_MODEL = os.getenv("COMBAT_INTENT_MODEL", "claude-haiku-4-5-20251001")
