"""Admin API endpoints for player management and stats."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session as DBSession

from server.auth import get_current_player
from server.db.database import get_db
from server.db.models import Campaign, Player
from server.security import require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/players")
def list_players(request: Request, db: DBSession = Depends(get_db)):
    player = get_current_player(request, db)
    require_admin(player)

    players = db.query(Player).order_by(Player.created_at.desc()).all()
    return JSONResponse([
        {
            "id": p.id,
            "username": p.username,
            "display_name": p.display_name,
            "email": p.email,
            "is_admin": p.is_admin,
            "subscription_status": p.subscription_status,
            "subscription_override": p.subscription_override,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in players
    ])


@router.post("/players/{player_id}/override")
async def toggle_override(
    player_id: int,
    request: Request,
    db: DBSession = Depends(get_db),
):
    admin = get_current_player(request, db)
    require_admin(admin)

    body = await request.json()
    target = db.query(Player).filter(Player.id == player_id).first()
    if not target:
        return JSONResponse({"error": "Player not found"}, status_code=404)

    target.subscription_override = bool(body.get("override", False))
    db.commit()
    return JSONResponse({
        "player_id": target.id,
        "username": target.username,
        "subscription_override": target.subscription_override,
    })


@router.post("/players/{player_id}/admin")
async def toggle_admin(
    player_id: int,
    request: Request,
    db: DBSession = Depends(get_db),
):
    admin = get_current_player(request, db)
    require_admin(admin)

    body = await request.json()
    target = db.query(Player).filter(Player.id == player_id).first()
    if not target:
        return JSONResponse({"error": "Player not found"}, status_code=404)

    target.is_admin = bool(body.get("is_admin", False))
    db.commit()
    return JSONResponse({
        "player_id": target.id,
        "username": target.username,
        "is_admin": target.is_admin,
    })


@router.get("/stats")
def admin_stats(request: Request, db: DBSession = Depends(get_db)):
    player = get_current_player(request, db)
    require_admin(player)

    total_players = db.query(Player).count()
    active_subs = db.query(Player).filter(Player.subscription_status == "active").count()
    comped = db.query(Player).filter(Player.subscription_override == True).count()
    total_campaigns = db.query(Campaign).count()

    return JSONResponse({
        "total_players": total_players,
        "active_subscriptions": active_subs,
        "comped_players": comped,
        "total_campaigns": total_campaigns,
    })
