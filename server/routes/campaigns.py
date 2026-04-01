import secrets
import string

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session as DBSession

from server.auth import get_current_player
from server.db.database import get_db
from server.db.models import Campaign, Character, JoinRequest
from server.db.models import Session as GameSession

router = APIRouter()


def _generate_invite_code(db: DBSession) -> str:
    """Generate a unique 6-character invite code like 'A7K3XP'."""
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(50):  # Retry limit
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        existing = db.query(Campaign).filter(Campaign.invite_code == code).first()
        if not existing:
            return code
    raise RuntimeError("Could not generate unique invite code")


@router.post("/campaigns")
def create_campaign(
    request: Request,
    name: str = Form("New Adventure"),
    setting: str = Form("A classic fantasy world of swords and sorcery."),
    visibility: str = Form("open"),
    max_players: int = Form(4),
    db: DBSession = Depends(get_db),
):
    """Create a new campaign."""
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    if visibility not in ("open", "private"):
        visibility = "open"
    max_players = max(1, min(8, max_players))

    invite_code = _generate_invite_code(db)
    campaign = Campaign(
        name=name,
        setting=setting,
        owner_id=player.id,
        visibility=visibility,
        max_players=max_players,
        invite_code=invite_code,
    )
    db.add(campaign)
    db.commit()

    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/campaigns/{campaign_id}/visibility")
def toggle_visibility(
    request: Request,
    campaign_id: int,
    visibility: str = Form(...),
    db: DBSession = Depends(get_db),
):
    """Toggle campaign visibility between open and private."""
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign or campaign.owner_id != player.id:
        return RedirectResponse(url="/dashboard", status_code=303)

    if visibility in ("open", "private"):
        campaign.visibility = visibility
        db.commit()

    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/campaigns/{campaign_id}/join")
def join_campaign(
    request: Request,
    campaign_id: int,
    character_id: int = Form(...),
    db: DBSession = Depends(get_db),
):
    """Join an open campaign or request to join a private one."""
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign:
        return RedirectResponse(url="/browse", status_code=303)

    # Verify the character belongs to this player and is unassigned
    character = db.query(Character).filter(Character.id == character_id).first()
    if not character or character.player_id != player.id:
        return RedirectResponse(url="/browse", status_code=303)
    if character.campaign_id is not None:
        return RedirectResponse(url="/browse", status_code=303)

    # Check max players
    current_player_count = (
        db.query(Character)
        .filter(
            Character.campaign_id == campaign_id,
            Character.is_enemy == False,
            Character.is_npc == False,
        )
        .count()
    )
    if current_player_count >= campaign.max_players:
        return RedirectResponse(url="/browse", status_code=303)

    # Can't join your own campaign through browse (you assign directly)
    if campaign.owner_id == player.id:
        return RedirectResponse(url="/dashboard", status_code=303)

    if campaign.visibility == "open":
        # Auto-join: assign character to campaign
        character.campaign_id = campaign_id
        db.commit()
        return RedirectResponse(url="/dashboard", status_code=303)
    else:
        # Private: create join request
        existing = (
            db.query(JoinRequest)
            .filter(
                JoinRequest.campaign_id == campaign_id,
                JoinRequest.player_id == player.id,
                JoinRequest.status == "pending",
            )
            .first()
        )
        if not existing:
            jr = JoinRequest(
                campaign_id=campaign_id,
                player_id=player.id,
                character_id=character_id,
            )
            db.add(jr)
            db.commit()
        return RedirectResponse(url="/browse", status_code=303)


@router.post("/campaigns/{campaign_id}/approve/{request_id}")
def approve_join(
    request: Request,
    campaign_id: int,
    request_id: int,
    db: DBSession = Depends(get_db),
):
    """Approve a join request (campaign owner only)."""
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign or campaign.owner_id != player.id:
        return RedirectResponse(url="/dashboard", status_code=303)

    jr = db.query(JoinRequest).filter(JoinRequest.id == request_id).first()
    if not jr or jr.campaign_id != campaign_id or jr.status != "pending":
        return RedirectResponse(url="/dashboard", status_code=303)

    # Assign the character to the campaign
    character = db.query(Character).filter(Character.id == jr.character_id).first()
    if character and character.campaign_id is None:
        character.campaign_id = campaign_id
        jr.status = "approved"
    else:
        jr.status = "rejected"

    db.commit()
    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/campaigns/{campaign_id}/reject/{request_id}")
def reject_join(
    request: Request,
    campaign_id: int,
    request_id: int,
    db: DBSession = Depends(get_db),
):
    """Reject a join request (campaign owner only)."""
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign or campaign.owner_id != player.id:
        return RedirectResponse(url="/dashboard", status_code=303)

    jr = db.query(JoinRequest).filter(JoinRequest.id == request_id).first()
    if jr and jr.campaign_id == campaign_id and jr.status == "pending":
        jr.status = "rejected"
        db.commit()

    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/campaigns/join-by-code")
def join_by_code(
    request: Request,
    invite_code: str = Form(...),
    character_id: int = Form(...),
    db: DBSession = Depends(get_db),
):
    """Join a campaign using an invite code."""
    from fastapi.responses import JSONResponse

    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    code = invite_code.strip().upper()
    campaign = db.query(Campaign).filter(Campaign.invite_code == code).first()
    if not campaign:
        return JSONResponse({"error": "Invalid invite code."}, status_code=404)

    character = db.query(Character).filter(Character.id == character_id).first()
    if not character or character.player_id != player.id:
        return JSONResponse({"error": "Character not found."}, status_code=400)
    if character.campaign_id is not None:
        return JSONResponse({"error": "Character is already assigned to a campaign."}, status_code=400)

    current_player_count = (
        db.query(Character)
        .filter(
            Character.campaign_id == campaign.id,
            Character.is_enemy == False,
            Character.is_npc == False,
        )
        .count()
    )
    if current_player_count >= campaign.max_players:
        return JSONResponse({"error": "Campaign is full."}, status_code=400)

    if campaign.owner_id == player.id:
        return JSONResponse({"error": "You own this campaign — assign directly from the dashboard."}, status_code=400)

    # Invite codes bypass visibility — always auto-join
    character.campaign_id = campaign.id

    # Create a session if none exists
    existing_session = (
        db.query(GameSession)
        .filter(GameSession.campaign_id == campaign.id, GameSession.status == "active")
        .first()
    )
    if not existing_session:
        from server.db.models import GameState
        session = GameSession(campaign_id=campaign.id, session_number=1)
        db.add(session)
        db.flush()
        game_state = GameState(
            session_id=session.id,
            game_mode="exploration",
            environment_description="Your adventure begins...",
        )
        db.add(game_state)

    db.commit()
    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/campaigns/{campaign_id}/regenerate-code")
def regenerate_invite_code(
    request: Request,
    campaign_id: int,
    db: DBSession = Depends(get_db),
):
    """Regenerate the invite code for a campaign (owner only)."""
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not campaign or campaign.owner_id != player.id:
        return RedirectResponse(url="/dashboard", status_code=303)

    campaign.invite_code = _generate_invite_code(db)
    db.commit()

    return RedirectResponse(url="/dashboard", status_code=303)
