@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket,
                      userId: Optional[str] = Query(default=None)):
  await ws.accept()
  clients.add(ws)

  # assign / restore userId
  uid = userId if userId else gen_user_id()
  user_by_ws[ws] = uid
  ws_by_user[uid] = ws

  # greet
  await send_json_safe(ws, {"type": "HELLO", "userId": uid})

  try:
    while True:
      msg = await ws.receive_json()
      mtype = msg.get("type")

      if mtype == "CREATE_LOBBY":
        name = msg.get("name") or f"User-{uid[:4]}"
        rules = msg.get("rules") or {}
        lobby_id = gen_lobby_id()
        lobby = LobbyState(lobby_id, host_id=uid, rules=rules)
        lobbies[lobby_id] = lobby

        # ensure player object
        if uid not in lobby.players:
          lobby.players[uid] = PlayerState(uid, name,
                                           lobby.rules["startingCapital"])
        lobby.players[uid].ready = False
        lobby_by_user[uid] = lobby_id

        # FIX: build invite URL using ws.url.scheme and headers['host']
        scheme = getattr(ws.url, "scheme", "ws")  # 'ws' or 'wss'
        http_scheme = "https" if scheme == "wss" else "http"
        host = ws.headers.get("host", "")
        invite_url = f"{http_scheme}://{host}/?join={lobby_id}"

        await send_json_safe(ws, {
            "type": "INVITE_CODE",
            "lobbyId": lobby_id,
            "inviteUrl": invite_url,
        })
        await broadcast_lobby(lobby, lobby_state_payload(lobby))

      elif mtype == "JOIN_LOBBY":
        lobby_id = (msg.get("lobbyId") or "").upper()
        name = msg.get("name") or f"User-{uid[:4]}"
        lobby = lobbies.get(lobby_id)
        if not lobby:
          await send_json_safe(ws, {
              "type": "ERROR",
              "code": "lobby_not_found"
          })
          continue
        if lobby.status != "LOBBY":
          await send_json_safe(ws, {
              "type": "ERROR",
              "code": "lobby_not_joinable"
          })
          continue

        if uid not in lobby.players:
          lobby.players[uid] = PlayerState(uid, name,
                                           lobby.rules["startingCapital"])
        else:
          lobby.players[uid].name = name
        lobby.players[uid].ready = False
        lobby_by_user[uid] = lobby_id

        await broadcast_lobby(lobby, lobby_state_payload(lobby))

      elif mtype == "SET_READY":
        lobby_id = lobby_by_user.get(uid)
        if not lobby_id: continue
        lobby = lobbies.get(lobby_id)
        if not lobby or lobby.status != "LOBBY": continue
        ready = bool(msg.get("ready", False))
        pl = lobby.players.get(uid)
        if pl:
          pl.ready = ready
          await broadcast_lobby(lobby, lobby_state_payload(lobby))

      elif mtype == "START_GAME":
        lobby_id = lobby_by_user.get(uid)
        if not lobby_id: continue
        lobby = lobbies.get(lobby_id)
        if not lobby or lobby.status != "LOBBY": continue
        if uid != lobby.host_id:
          await send_json_safe(ws, {"type": "ERROR", "code": "not_host"})
          continue
        # require everyone ready
        if not lobby.players or not all(p.ready
                                        for p in lobby.players.values()):
          await send_json_safe(ws, {
              "type": "ERROR",
              "code": "players_not_ready"
          })
          continue
        lobby.status = "RUNNING"
        await broadcast_lobby(lobby, lobby_state_payload(lobby))
        if not lobby.ticker_task:
          lobby.ticker_task = asyncio.create_task(lobby_ticker(lobby))

      elif mtype == "ORDER":
        lobby_id = lobby_by_user.get(uid)
        if not lobby_id: continue
        lobby = lobbies.get(lobby_id)
        if not lobby or lobby.status != "RUNNING": continue
        asset = msg.get("asset")
        side = msg.get("side")
        qty = int(msg.get("qty", 0) or 0)
        if asset not in ASSETS or side not in ("BUY", "SELL") or qty <= 0:
          await send_json_safe(ws, {
              "type": "ORDER_REJECT",
              "reason": "invalid"
          })
          continue
        pl = lobby.players.get(uid)
        ok, reason = execute_market(lobby, pl, asset, side, qty)
        if ok:
          await send_json_safe(
              ws, {
                  "type": "ORDER_ACCEPTED",
                  "asset": asset,
                  "side": side,
                  "qty": qty,
                  "price": round(lobby.prices[asset], 2)
              })
          await send_json_safe(ws, snapshot_portfolio(lobby, pl))
          await broadcast_lobby(lobby, leaderboard(lobby))
        else:
          await send_json_safe(ws, {
              "type": "ORDER_REJECT",
              "reason": reason or "unknown"
          })

      elif mtype == "LEAVE_LOBBY":
        lobby_id = lobby_by_user.get(uid)
        if not lobby_id: continue
        lobby = lobbies.get(lobby_id)
        if not lobby: continue
        lobby.players.pop(uid, None)
        lobby_by_user.pop(uid, None)
        await broadcast_lobby(lobby, lobby_state_payload(lobby))

      elif mtype == "PING":
        await send_json_safe(ws, {"type": "PONG", "ts": time.time()})

      else:
        # ignore unknown
        pass

  except WebSocketDisconnect:
    pass
  except Exception:
    pass
  finally:
    # cleanup maps
    clients.discard(ws)
    uid = user_by_ws.pop(ws, None)
    if uid and ws_by_user.get(uid) is ws:
      ws_by_user.pop(uid, None)
    # we keep the player in the lobby for reconnection (by userId)
