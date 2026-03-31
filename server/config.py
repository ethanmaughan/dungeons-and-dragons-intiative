import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
# Use DATABASE_URL env var for production (PostgreSQL), fallback to SQLite for local dev
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/game.db")
SECRET_KEY = os.getenv("SECRET_KEY", "dnd-initiative-dev-secret-change-in-prod")

# AI backend: "ollama" (free, local) or "claude" (paid, better quality)
AI_BACKEND = os.getenv("AI_BACKEND", "ollama")
# Model names
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
