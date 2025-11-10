clients: Set[WebSocket] = set()
user_by_ws: Dict[WebSocket, str] = {}
ws_by_user: Dict[str, WebSocket] = {}

lobbies: Dict[str, LobbyState] = {}  # lobbyId -> LobbyState
lobby_by_user: Dict[str, str] = {}  # userId -> lobbyId


def gen_user_id() -> str:
  return "".join(random.choices(string.ascii_lowercase + string.digits, k=10))


def gen_lobby_id() -> str:
  alphabet = string.ascii_uppercase + "23456789"
  return "".join(random.choices(alphabet, k=6))
