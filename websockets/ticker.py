async def lobby_ticker(lobby: LobbyState):
  tick_s = lobby.rules["tickSeconds"]
  lobby.start_ts = time.time()
  lobby.end_ts = lobby.start_ts + lobby.rules["durationSec"]
  await broadcast_lobby(lobby, {
      "type": "GAME_STARTED",
      "startTs": lobby.start_ts,
      "endTs": lobby.end_ts
  })

  try:
    while True:
      now = time.time()
      if now >= lobby.end_ts:
        lobby.status = "ENDED"
        await broadcast_lobby(lobby, {
            "type": "GAME_ENDED",
            "lobbyId": lobby.lobby_id
        })
        await broadcast_lobby(lobby, leaderboard(lobby))
        break

      step_prices(lobby)
      await broadcast_lobby(
          lobby, {
              "type": "TICK",
              "ts": now,
              "prices": lobby.prices,
              "remainingSec": int(lobby.end_ts - now)
          })
      # push each portfolio + leaderboard
      for uid, pl in lobby.players.items():
        ws = ws_by_user.get(uid)
        if ws:
          await send_json_safe(ws, snapshot_portfolio(lobby, pl))
      await broadcast_lobby(lobby, leaderboard(lobby))
      await asyncio.sleep(tick_s)
  finally:
    lobby.ticker_task = None
