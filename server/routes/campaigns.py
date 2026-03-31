from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DBSession

from server.auth import get_current_player
from server.db.database import get_db
from server.db.models import Campaign

router = APIRouter()


@router.post("/campaigns")
def create_campaign(
    request: Request,
    name: str = Form("New Adventure"),
    setting: str = Form("A classic fantasy world of swords and sorcery."),
    db: DBSession = Depends(get_db),
):
    """Create a new campaign (no character, no session — those come later)."""
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    campaign = Campaign(name=name, setting=setting, owner_id=player.id)
    db.add(campaign)
    db.commit()

    return RedirectResponse(url="/dashboard", status_code=303)
