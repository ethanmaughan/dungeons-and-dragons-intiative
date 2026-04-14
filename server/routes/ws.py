"""WebSocket endpoint for real-time multiplayer sessions."""

import json
import traceback

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session as DBSession

from server.db.database import get_db, SessionLocal
from server.db.models import Character, GameState, Player
from server.db.models import Session as GameSession
from server.security import player_can_play
from server.ws.manager import manager
from server.services.action_service import process_action
from server.engine.combat import validate_move, execute_move
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")
router = APIRouter()


def get_player_from_ws(ws: WebSocket, db: DBSession) -> Player | None:
    """Extract player from session cookie on WebSocket connection."""
    # Starlette session middleware stores session in cookies
    # WebSocket connections carry cookies from the browser
    session = ws.session if hasattr(ws, "session") else {}
    player_id = session.get("player_id")
    if not player_id:
        # Try to get from scope (Starlette puts session data here after middleware)
        scope_session = ws.scope.get("session", {})
        player_id = scope_session.get("player_id")
    if not player_id:
        return None
    return db.query(Player).filter(Player.id == player_id).first()


@router.websocket("/ws/session/{session_id}")
async def websocket_session(ws: WebSocket, session_id: int):
    """Main WebSocket endpoint for a game session."""
    print(f"[WS] Connection attempt for session {session_id}", flush=True)
    db = SessionLocal()
    try:
        await ws.accept()
        print(f"[WS] Accepted connection for session {session_id}", flush=True)

        # Get player from session cookie
        scope_session = ws.scope.get("session", {})
        player_id = scope_session.get("player_id")
        print(f"[WS] Player ID from session: {player_id}", flush=True)
        if not player_id:
            await ws.send_text(json.dumps({"type": "error", "message": "Not authenticated"}))
            await ws.close()
            return

        player = db.query(Player).filter(Player.id == player_id).first()
        if not player:
            await ws.send_text(json.dumps({"type": "error", "message": "Player not found"}))
            await ws.close()
            return

        if not player_can_play(player):
            print(f"[WS] Player {player_id} cannot play — subscription required", flush=True)
            await ws.send_text(json.dumps({"type": "error", "message": "Subscription required"}))
            await ws.close()
            return
        print(f"[WS] Player {player_id} ({player.username}) authorized", flush=True)

        # Verify player has a character in this session's campaign
        session = db.query(GameSession).filter(GameSession.id == session_id).first()
        if not session:
            await ws.send_text(json.dumps({"type": "error", "message": "Session not found"}))
            await ws.close()
            return

        my_character = (
            db.query(Character)
            .filter(
                Character.player_id == player_id,
                Character.campaign_id == session.campaign_id,
                Character.is_enemy == False,
                Character.is_npc == False,
            )
            .first()
        )

        # Allow campaign owner even without a character
        is_owner = session.campaign.owner_id == player_id
        if not my_character and not is_owner:
            await ws.send_text(json.dumps({"type": "error", "message": "No character in this campaign"}))
            await ws.close()
            return

        # Close the initial DB session — don't hold it during the listen loop
        # Each action will open its own fresh session
        db.close()

        # Register connection (we already accepted above, so use internal tracking)
        if session_id not in manager.active_connections:
            manager.active_connections[session_id] = []
        manager.active_connections[session_id].append((ws, player_id))

        # Notify others that player joined
        await manager.broadcast(session_id, {
            "type": "player_joined",
            "player_name": player.display_name or player.username,
            "character_name": my_character.character_name if my_character else None,
            "online_players": manager.get_online_players(session_id),
        }, exclude_ws=ws)

        # Send connection confirmation to the joining player
        await ws.send_text(json.dumps({
            "type": "connected",
            "player_id": player_id,
            "character_id": my_character.id if my_character else None,
            "online_players": manager.get_online_players(session_id),
        }))

        # Listen for messages
        while True:
            raw = await ws.receive_text()
            data = json.loads(raw)

            if data.get("type") == "action":
                action_text = data.get("text", "").strip()
                if not action_text:
                    continue

                # Close existing DB session and get fresh one for action processing
                db.close()
                db = SessionLocal()

                try:
                    result = await process_action(
                        session_id=session_id,
                        player_id=player_id,
                        action_text=action_text,
                        db=db,
                    )

                    if "error" in result:
                        await ws.send_text(json.dumps({
                            "type": "error",
                            "message": result["error"],
                        }))
                        continue

                    log = result["log"]

                    # Broadcast player's narrative (if present — may be None for stuck-turn recovery)
                    if log:
                        html = templates.get_template("partials/narrative_entry.html").render(
                            log=log,
                        )
                        await manager.broadcast(session_id, {
                            "type": "narrative",
                            "html": html,
                            "dice_rolls": log.dice_rolls or [],
                        })

                    # Broadcast combat start as a SEPARATE event (if combat just triggered)
                    import asyncio
                    if result.get("combat_start"):
                        await asyncio.sleep(1.0)  # Dramatic pause after narration
                        await manager.broadcast(session_id, {
                            "type": "combat_start",
                            "initiative_order": result["combat_start"]["initiative_order"],
                            "initiative_summary": result["combat_start"]["initiative_summary"],
                            "round": result["combat_start"]["round"],
                        })
                        await asyncio.sleep(1.5)  # Let players read initiative

                    # Broadcast enemy turns (if any, from combat auto-resolution)
                    for et in result.get("enemy_turns", []):
                        enemy_html = templates.get_template("partials/narrative_entry.html").render(
                            log=et["log"],
                        )
                        enemy_dice = et["log"].dice_rolls if hasattr(et["log"], "dice_rolls") else et.get("dice_rolls", [])
                        await manager.broadcast(session_id, {
                            "type": "narrative",
                            "html": enemy_html,
                            "dice_rolls": enemy_dice or [],
                        })
                        await asyncio.sleep(0.5)

                    # Broadcast final state update (after all events)
                    await manager.broadcast(session_id, {
                        "type": "state_update",
                        "characters": result["characters"],
                        "game_state": result["game_state"],
                    })

                except Exception:
                    traceback.print_exc()
                    await ws.send_text(json.dumps({
                        "type": "error",
                        "message": "Failed to process action",
                    }))

            elif data.get("type") == "move":
                direction = data.get("direction", "")
                if direction not in ("up", "down", "left", "right"):
                    continue
                if not my_character:
                    continue

                db.close()
                db = SessionLocal()

                try:
                    game_state = db.query(GameState).filter(
                        GameState.session_id == session_id
                    ).first()

                    if not game_state or game_state.game_mode != "combat":
                        continue

                    positions = dict(game_state.combat_positions or {})
                    ok, err = validate_move(my_character.id, direction, positions, game_state)

                    if not ok:
                        await ws.send_text(json.dumps({
                            "type": "move_rejected",
                            "message": err,
                        }))
                        continue

                    execute_move(my_character.id, direction, positions)
                    game_state.combat_positions = positions
                    db.commit()

                    await manager.broadcast(session_id, {
                        "type": "position_update",
                        "combat_positions": positions,
                    })

                except Exception:
                    traceback.print_exc()
                    await ws.send_text(json.dumps({
                        "type": "error",
                        "message": "Move failed",
                    }))

            elif data.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        pass
    except Exception:
        traceback.print_exc()
    finally:
        manager.disconnect(ws, session_id)
        db.close()

        # Notify others that player left
        try:
            player_name = player.display_name or player.username if player else "Unknown"
            char_name = my_character.character_name if my_character else None
            await manager.broadcast(session_id, {
                "type": "player_left",
                "player_name": player_name,
                "character_name": char_name,
                "online_players": manager.get_online_players(session_id),
            })
        except Exception:
            pass
