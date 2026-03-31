from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from server.auth import hash_password, verify_password
from server.db.database import get_db
from server.db.models import Player

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
