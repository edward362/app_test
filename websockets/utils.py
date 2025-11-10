async def send_json_safe(ws: WebSocket, payload: dict):
  try:
    await ws.send_json(payload)
  except Exception:
    pass


async def broadcast_lobby(lobby: LobbyState, payload: dict):
  for uid, pl in list(lobby.players.items()):
    ws = ws_by_user.get(uid)
    if ws:
      await send_json_safe(ws, payload)


def lobby_state_payload(lobby: LobbyState):
  return {
      "type":
      "LOBBY_STATE",
      "lobbyId":
      lobby.lobby_id,
      "status":
      lobby.status,
      "hostId":
      lobby.host_id,
      "rules":
      lobby.rules,
      "players": [{
          "userId": uid,
          "name": pl.name,
          "ready": pl.ready
      } for uid, pl in lobby.players.items()],
      "seed":
      lobby.seed
  }
