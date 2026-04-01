"""WebSocket ConnectionManager — tracks connections per session."""

import json

from fastapi import WebSocket


class ConnectionManager:
    """In-memory WebSocket connection tracker. Maps session_id → list of (ws, player_id)."""

    def __init__(self):
        self.active_connections: dict[int, list[tuple[WebSocket, int]]] = {}

    async def connect(self, ws: WebSocket, session_id: int, player_id: int):
        await ws.accept()
        if session_id not in self.active_connections:
            self.active_connections[session_id] = []
        self.active_connections[session_id].append((ws, player_id))

    def disconnect(self, ws: WebSocket, session_id: int):
        if session_id in self.active_connections:
            self.active_connections[session_id] = [
                (w, p) for w, p in self.active_connections[session_id] if w is not ws
            ]
            if not self.active_connections[session_id]:
                del self.active_connections[session_id]

    async def broadcast(self, session_id: int, message: dict, exclude_ws: WebSocket | None = None):
        """Send a JSON message to all connections in a session."""
        if session_id not in self.active_connections:
            return
        data = json.dumps(message)
        dead = []
        for ws, player_id in self.active_connections[session_id]:
            if ws is exclude_ws:
                continue
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        # Clean up dead connections
        for ws in dead:
            self.disconnect(ws, session_id)

    async def send_personal(self, ws: WebSocket, message: dict):
        """Send a JSON message to a single connection."""
        await ws.send_text(json.dumps(message))

    def get_online_players(self, session_id: int) -> list[int]:
        """Return list of player_ids currently connected to a session."""
        if session_id not in self.active_connections:
            return []
        return list({p for _, p in self.active_connections[session_id]})

    def get_connection_count(self, session_id: int) -> int:
        if session_id not in self.active_connections:
            return 0
        return len(self.active_connections[session_id])


# Global singleton
manager = ConnectionManager()
