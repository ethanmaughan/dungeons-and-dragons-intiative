from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from server.auth import hash_password, verify_password, get_current_player
from server.db.database import get_db
from server.db.models import Campaign, Character, GameLog, GameState, JoinRequest, Player
from server.db.models import Session as GameSession

templates = Jinja2Templates(directory="templates")
router = APIRouter()


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": None,
    })


@router.get("/register")
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {
        "request": request,
        "error": None,
    })


@router.post("/auth/register")
def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(""),
    db: DBSession = Depends(get_db),
):
    # Check if username already exists
    existing = db.query(Player).filter(Player.username == username).first()
    if existing:
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "Username already taken.",
        })

    if len(password) < 4:
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "Password must be at least 4 characters.",
        })

    # Create account
    player = Player(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name or username,
    )
    db.add(player)
    db.commit()

    # Log them in
    request.session["player_id"] = player.id
    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/auth/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: DBSession = Depends(get_db),
):
    player = db.query(Player).filter(Player.username == username).first()
    if not player or not verify_password(password, player.password_hash):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Invalid username or password.",
        })

    request.session["player_id"] = player.id
    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


@router.post("/api/delete/{delete_type}/{delete_id}")
def delete_item(
    request: Request,
    delete_type: str,
    delete_id: int,
    password: str = Form(...),
    db: DBSession = Depends(get_db),
):
    """Delete a character or campaign after password confirmation."""
    from fastapi.responses import JSONResponse

    player = get_current_player(request, db)
    if not player:
        return JSONResponse({"error": "Not logged in."}, status_code=401)

    # Verify password
    if not verify_password(password, player.password_hash):
        return JSONResponse({"error": "Incorrect password."}, status_code=403)

    if delete_type == "character":
        character = db.query(Character).filter(Character.id == delete_id).first()
        if not character or character.player_id != player.id:
            return JSONResponse({"error": "Character not found."}, status_code=404)
        # Null out FK references in game logs and join requests
        db.query(GameLog).filter(GameLog.character_id == delete_id).update({"character_id": None})
        db.query(JoinRequest).filter(JoinRequest.character_id == delete_id).delete()
        db.delete(character)
        db.commit()
        return JSONResponse({"success": True})

    elif delete_type == "campaign":
        campaign = db.query(Campaign).filter(Campaign.id == delete_id).first()
        if not campaign:
            return JSONResponse({"error": "Campaign not found."}, status_code=404)

        # Verify the player owns this campaign
        if campaign.owner_id != player.id:
            return JSONResponse({"error": "You don't own this campaign."}, status_code=403)

        # Delete join requests, sessions, logs, game_state. Unassign characters.
        db.query(JoinRequest).filter(JoinRequest.campaign_id == campaign.id).delete()
        sessions = db.query(GameSession).filter(GameSession.campaign_id == campaign.id).all()
        for session in sessions:
            db.query(GameLog).filter(GameLog.session_id == session.id).delete()
            db.query(GameState).filter(GameState.session_id == session.id).delete()
            db.delete(session)
        # Unassign player characters, delete enemy/NPC characters
        for char in db.query(Character).filter(Character.campaign_id == campaign.id).all():
            if char.is_enemy or char.is_npc:
                db.delete(char)
            else:
                char.campaign_id = None
        db.delete(campaign)
        db.commit()
        return JSONResponse({"success": True})

    return JSONResponse({"error": "Invalid delete type."}, status_code=400)
