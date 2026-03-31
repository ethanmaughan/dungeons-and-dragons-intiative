import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DATABASE_URL = "sqlite:///data/game.db"
SECRET_KEY = os.getenv("SECRET_KEY", "dnd-initiative-dev-secret-change-in-prod")
