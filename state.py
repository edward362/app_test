from __future__ import annotations

import random
import string
from typing import Dict, Set, TYPE_CHECKING
from fastapi import WebSocket

# Only for type hints to avoid circular imports at runtime
if TYPE_CHECKING:
    from domain.models import LobbyState  # or wherever LobbyState lives

# ---- Connections / sessions ----
clients: Set[WebSocket] = set()                    # all connected sockets
user_by_ws: Dict[WebSocket, str] = {}              # ws -> userId
ws_by_user: Dict[str, WebSocket] = {}              # userId -> ws

# ---- Lobbies ----
lobbies: Dict[str, "LobbyState"] = {}              # lobbyId -> LobbyState
lobby_by_user: Dict[str, str] = {}                 # userId -> lobbyId

# ---- ID generators ----
def gen_user_id() -> str:
    """Generate a short opaque user id, e.g. 'k8z2q1m9d0'."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=10))

def gen_lobby_id() -> str:
    """Generate a 6-char lobby code, e.g. 'AB3Z9Q'."""
    alphabet = string.ascii_uppercase + "23456789"  # avoid 0/1 for readability
    return "".join(random.choices(alphabet, k=6))
