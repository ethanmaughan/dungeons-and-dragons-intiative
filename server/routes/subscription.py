"""Stripe subscription routes: checkout, webhook, account, billing portal."""

import stripe
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSession

from server.auth import get_current_player
from server.config import (
    STRIPE_PRICE_ID,
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
)
from server.db.database import get_db
from server.db.models import Player
from server.security import player_can_play

stripe.api_key = STRIPE_SECRET_KEY

templates = Jinja2Templates(directory="templates")
router = APIRouter()


@router.get("/account")
def account_page(request: Request, db: DBSession = Depends(get_db)):
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    reason = request.query_params.get("reason", "")
    return templates.TemplateResponse("account.html", {
        "request": request,
        "player": player,
        "can_play": player_can_play(player),
        "reason": reason,
    })


@router.post("/subscription/checkout")
def create_checkout(request: Request, db: DBSession = Depends(get_db)):
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        return RedirectResponse(url="/account?reason=payments_not_configured", status_code=303)

    # Create or reuse Stripe customer
    if not player.stripe_customer_id:
        customer = stripe.Customer.create(
            metadata={"player_id": str(player.id), "username": player.username},
            email=player.email or None,
            name=player.display_name or player.username,
        )
        player.stripe_customer_id = customer.id
        db.commit()

    # Create checkout session
    base_url = str(request.base_url).rstrip("/")
    checkout_session = stripe.checkout.Session.create(
        customer=player.stripe_customer_id,
        mode="subscription",
        line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
        success_url=f"{base_url}/account?reason=subscribed",
        cancel_url=f"{base_url}/account",
        metadata={"player_id": str(player.id)},
    )

    return RedirectResponse(url=checkout_session.url, status_code=303)


@router.post("/subscription/portal")
def billing_portal(request: Request, db: DBSession = Depends(get_db)):
    player = get_current_player(request, db)
    if not player or not player.stripe_customer_id:
        return RedirectResponse(url="/account", status_code=303)

    base_url = str(request.base_url).rstrip("/")
    portal_session = stripe.billing_portal.Session.create(
        customer=player.stripe_customer_id,
        return_url=f"{base_url}/account",
    )

    return RedirectResponse(url=portal_session.url, status_code=303)


@router.post("/account/email")
def update_email(
    request: Request,
    email: str = Form(""),
    db: DBSession = Depends(get_db),
):
    player = get_current_player(request, db)
    if not player:
        return RedirectResponse(url="/login", status_code=303)

    player.email = email.strip() or None

    # Sync email to Stripe customer if exists
    if player.stripe_customer_id and player.email:
        try:
            stripe.Customer.modify(player.stripe_customer_id, email=player.email)
        except Exception:
            pass

    db.commit()
    return RedirectResponse(url="/account", status_code=303)


@router.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events. No auth — verified by signature."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not STRIPE_WEBHOOK_SECRET:
        return JSONResponse({"error": "Webhook not configured"}, status_code=500)

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except (ValueError, stripe.error.SignatureVerificationError):
        return JSONResponse({"error": "Invalid signature"}, status_code=400)

    # Process events
    from server.db.database import SessionLocal
    db = SessionLocal()
    try:
        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            player_id = session.get("metadata", {}).get("player_id")
            if player_id:
                player = db.query(Player).filter(Player.id == int(player_id)).first()
                if player:
                    player.subscription_status = "active"
                    if session.get("subscription"):
                        player.stripe_subscription_id = session["subscription"]
                    db.commit()

        elif event["type"] == "customer.subscription.updated":
            sub = event["data"]["object"]
            player = db.query(Player).filter(
                Player.stripe_subscription_id == sub["id"]
            ).first()
            if player:
                status_map = {
                    "active": "active",
                    "past_due": "past_due",
                    "canceled": "canceled",
                    "incomplete": "none",
                    "incomplete_expired": "none",
                    "trialing": "active",
                    "unpaid": "past_due",
                }
                player.subscription_status = status_map.get(sub["status"], "none")
                db.commit()

        elif event["type"] == "customer.subscription.deleted":
            sub = event["data"]["object"]
            player = db.query(Player).filter(
                Player.stripe_subscription_id == sub["id"]
            ).first()
            if player:
                player.subscription_status = "canceled"
                db.commit()

        elif event["type"] == "invoice.payment_failed":
            invoice = event["data"]["object"]
            customer_id = invoice.get("customer")
            if customer_id:
                player = db.query(Player).filter(
                    Player.stripe_customer_id == customer_id
                ).first()
                if player:
                    player.subscription_status = "past_due"
                    db.commit()
    finally:
        db.close()

    return JSONResponse({"status": "ok"})
