from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from server.auth import get_current_player
from server.db.database import get_db
from server.security import player_can_play
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
    """Process a player action and return all results (HTTP fallback for WebSocket)."""
    player = get_current_player(request, db)
    if not player:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    if not player_can_play(player):
        return JSONResponse({"error": "Subscription required"}, status_code=403)

    result = await process_action(
        session_id=session_id,
        player_id=player.id,
        action_text=action,
        db=db,
    )

    if "error" in result:
        return JSONResponse({"error": result["error"]})

    # Render player narrative HTML
    entries = []
    if result.get("log"):
        html = templates.get_template("partials/narrative_entry.html").render(
            log=result["log"],
        )
        entries.append({
            "html": html,
            "dice_rolls": result["log"].dice_rolls or [],
        })

    # Render enemy turn narratives
    for et in result.get("enemy_turns", []):
        enemy_html = templates.get_template("partials/narrative_entry.html").render(
            log=et["log"],
        )
        enemy_dice = et["log"].dice_rolls if hasattr(et["log"], "dice_rolls") else []
        entries.append({
            "html": enemy_html,
            "dice_rolls": enemy_dice or [],
        })

    # Combat start info
    combat_start = None
    if result.get("combat_start"):
        combat_start = {
            "initiative_order": result["combat_start"]["initiative_order"],
            "initiative_summary": result["combat_start"]["initiative_summary"],
            "round": result["combat_start"]["round"],
        }

    return JSONResponse({
        "entries": entries,
        "combat_start": combat_start,
        "characters": result.get("characters"),
        "game_state": result.get("game_state"),
    })
