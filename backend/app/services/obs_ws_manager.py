"""
OBS WebSocket Connection Manager
=================================

Manages WebSocket connections from OBS Browser Source clients.
When the main page pushes speak-text to the queue, this manager
broadcasts the text to all connected OBS clients in real-time,
eliminating the 1.5-second polling delay.

Architecture:
  Main Page → POST /speak-queue/push → push_speak_text() → broadcast_to_obs()
                                                              ↓
                                              OBS WebSocket clients receive instantly
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class OBSWebSocketManager:
    """
    Manages WebSocket connections from OBS Browser Source clients.
    Thread-safe via asyncio (single event loop).
    """

    def __init__(self):
        self._connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        """Accept and register a new OBS WebSocket client."""
        await websocket.accept()
        self._connections.add(websocket)
        logger.info(
            f"[OBS-WS] Client connected. Total: {len(self._connections)}"
        )

    def disconnect(self, websocket: WebSocket):
        """Remove a disconnected OBS WebSocket client."""
        self._connections.discard(websocket)
        logger.info(
            f"[OBS-WS] Client disconnected. Total: {len(self._connections)}"
        )

    @property
    def client_count(self) -> int:
        return len(self._connections)

    async def broadcast_speak_text(self, item: dict):
        """
        Broadcast a speak-text item to all connected OBS clients.
        Item format: {"id": str, "text": str, "timestamp": float}
        """
        if not self._connections:
            return

        message = json.dumps({
            "type": "speak_text",
            "item": item,
        })

        # Send to all clients, remove dead connections
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self._connections.discard(ws)
            logger.info(
                f"[OBS-WS] Removed dead connection. Total: {len(self._connections)}"
            )

        if self._connections:
            logger.info(
                f"[OBS-WS] Broadcast speak_text #{item.get('id')} to {len(self._connections)} client(s)"
            )


# ──────────────────────────────────────────────
# Singleton instance
# ──────────────────────────────────────────────
_obs_ws_manager: OBSWebSocketManager | None = None


def get_obs_ws_manager() -> OBSWebSocketManager:
    global _obs_ws_manager
    if _obs_ws_manager is None:
        _obs_ws_manager = OBSWebSocketManager()
    return _obs_ws_manager
