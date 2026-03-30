from fastapi import APIRouter, Depends, Form, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from server.db.database import get_db
from server.db.models import Campaign, Character, GameLog, GameState
from server.db.models import Session as GameSession
from server.ai.orchestrator import process_player_action

templates = Jinja2Templates(directory="templates")
router = APIRouter()


@router.post("/action/{session_id}")
async def submit_action(
    request: Request,
    session_id: int,
    action: str = Form(...),
    db: DBSession = Depends(get_db),
):
    """Process a player action through the DM orchestrator."""
    session = db.query(GameSession).filter(GameSession.id == session_id).first()
    if not session:
        return {"error": "Session not found"}

    game_state = db.query(GameState).filter(GameState.session_id == session_id).first()
    characters = session.campaign.characters
    recent_logs = (
        db.query(GameLog)
        .filter(GameLog.session_id == session_id)
        .order_by(GameLog.id.desc())
        .limit(20)
        .all()
    )
    recent_logs.reverse()

    # Get the current turn number
    turn_number = len(recent_logs) + 1

    # Call the DM orchestrator
    narration = await process_player_action(
        action=action,
        campaign=session.campaign,
        game_state=game_state,
        characters=characters,
        recent_logs=recent_logs,
    )

    # Save to game log
    log_entry = GameLog(
        session_id=session_id,
        turn_number=turn_number,
        actor=characters[0].player_name if characters else "Player",
        action_text=action,
        narration_text=narration,
        game_mode=game_state.game_mode if game_state else "exploration",
    )
    db.add(log_entry)
    db.commit()

    # Return the new log entries as HTML partial (for HTMX)
    return templates.TemplateResponse("partials/narrative_entry.html", {
        "request": request,
        "log": log_entry,
    })
