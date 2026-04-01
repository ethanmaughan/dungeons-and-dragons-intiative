from fastapi import APIRouter, Depends, Form, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from server.auth import get_current_player
from server.db.database import get_db
from server.services.action_service import process_action

templates = Jinja2Templates(directory="templates")
router = APIRouter()


@router.post("/action/{session_id}")
async def submit_action(
    request: Request,
    session_id: int,
    action: str = Form(...),
    db: DBSession = Depends(get_db),
):
    """Process a player action through the DM orchestrator (HTTP fallback)."""
    player = get_current_player(request, db)
    player_id = player.id if player else 0

    result = await process_action(
        session_id=session_id,
        player_id=player_id,
        action_text=action,
        db=db,
    )

    if "error" in result:
        return {"error": result["error"]}

    return templates.TemplateResponse("partials/narrative_entry.html", {
        "request": request,
        "log": result["log"],
    })
