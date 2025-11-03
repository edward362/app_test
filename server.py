# server.py
import asyncio, random, math, time, string
from typing import Dict, Set, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

# ---------- Config ----------
ASSETS = ["OIL", "GOLD", "ELECTRONICS", "RICE", "PLUMBER"]
DEFAULT_TICK_SECONDS = 2
PRICE_TICK = 0.01
DEFAULT_STARTING_CASH = 10_000.0
DEFAULT_DURATION_SEC = 15 * 60
MAX_TRADE_HISTORY = 200

# ---------- App ----------
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Global routing (ws/users/lobbies) ----------
clients: Set[WebSocket] = set()
user_by_ws: Dict[WebSocket, str] = {}
ws_by_user: Dict[str, WebSocket] = {}


def gen_user_id() -> str:
  return "".join(random.choices(string.ascii_lowercase + string.digits, k=10))


def gen_lobby_id() -> str:
  alphabet = string.ascii_uppercase + "23456789"
  return "".join(random.choices(alphabet, k=6))


# ---------- Models in-memory ----------
class PlayerState:

  def __init__(self, user_id: str, name: str, starting_cash: float):
    self.user_id = user_id
    self.name = name
    self.ready = False
    self.cash = float(starting_cash)
    # positions[a] = {"qty": int, "avg": float, "entry_ts": Optional[float]}
    self.positions: Dict[str, Dict] = {
        a: {
            "qty": 0,
            "avg": 0.0,
            "entry_ts": None
        }
        for a in ASSETS
    }
    self.realized_pnl: float = 0.0
    self.trades: list = []  # closed trades


class LobbyState:

  def __init__(self, lobby_id: str, host_id: str, rules: dict):
    self.lobby_id = lobby_id
    self.host_id = host_id
    self.status = "LOBBY"  # LOBBY | RUNNING | ENDED
    self.rules = {
        "startingCapital":
        float(rules.get("startingCapital", DEFAULT_STARTING_CASH)),
        "tickSeconds":
        int(rules.get("tickSeconds", DEFAULT_TICK_SECONDS)),
        "durationSec":
        int(rules.get("durationSec", DEFAULT_DURATION_SEC)),
    }
    # market
    self.seed = random.randint(1, 10_000)
    self.rng = random.Random(self.seed)
    self.prices = {a: 100.0 for a in ASSETS}
    # players
    self.players: Dict[str, PlayerState] = {}  # userId -> PlayerState
    # timing
    self.start_ts: Optional[float] = None
    self.end_ts: Optional[float] = None
    # ticker
    self.ticker_task: Optional[asyncio.Task] = None


lobbies: Dict[str, LobbyState] = {}  # lobbyId -> LobbyState
lobby_by_user: Dict[str, str] = {}  # userId -> lobbyId


# ---------- Price engine (per-lobby) ----------
def round_tick(x: float, tick: float = PRICE_TICK) -> float:
  return max(tick, round(x / tick) * tick)


def step_prices(lobby: LobbyState):
  rng = lobby.rng
  for a in ASSETS:
    p = lobby.prices[a]
    drift = 0.02 if a == "GOLD" else 0.05
    sigma = 0.15 if a in ("GOLD", "RICE") else 0.30
    u1 = max(1e-9, rng.random())
    u2 = rng.random()
    z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2 * math.pi * u2)
    eps = z * sigma / math.sqrt(30)
    newp = p * (1 + drift / 30 + eps)
    lobby.prices[a] = round_tick(newp, PRICE_TICK)


# ---------- Broadcast helpers ----------
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


# ---------- PnL & portfolio ----------
def pos_unrealized_upnl(qty: int, avg: float, price: float) -> float:
  if qty > 0:  # long
    return (price - avg) * qty
  if qty < 0:  # short
    return (avg - price) * (-qty)
  return 0.0


def record_trade(pl: PlayerState, *, asset: str, side_open: str, qty: int,
                 entry_price: float, exit_price: float,
                 entry_ts: Optional[float]):
  if side_open == "LONG":
    realized = (exit_price - entry_price) * qty
  else:
    realized = (entry_price - exit_price) * qty
  pl.realized_pnl += realized
  pl.trades.append({
      "ts":
      time.time(),
      "asset":
      asset,
      "side_open":
      side_open,
      "qty":
      qty,
      "entry_price":
      round(entry_price, 4),
      "exit_price":
      round(exit_price, 4),
      "realized_pnl":
      round(realized, 2),
      "duration_sec":
      round((time.time() - entry_ts), 2) if entry_ts else None
  })
  if len(pl.trades) > MAX_TRADE_HISTORY:
    pl.trades = pl.trades[-MAX_TRADE_HISTORY:]


def snapshot_portfolio(lobby: LobbyState, pl: PlayerState) -> dict:
  upnl_total = 0.0
  mkt_value_total = 0.0
  rows = []
  for a in ASSETS:
    pos = pl.positions[a]
    qty, avg = pos["qty"], pos["avg"]
    price = lobby.prices[a]
    upnl = pos_unrealized_upnl(qty, avg, price)
    mkt_value = qty * price
    upnl_total += upnl
    mkt_value_total += mkt_value
    rows.append({
        "asset": a,
        "qty": qty,
        "avg": round(avg, 2),
        "price": round(price, 2),
        "mktValue": round(mkt_value, 2),
        "uPnL": round(upnl, 2)
    })
  equity = pl.cash + mkt_value_total
  return {
      "type": "PORTFOLIO",
      "cash": round(pl.cash, 2),
      "equity": round(equity, 2),
      "uPnL": round(upnl_total, 2),
      "realizedPnL": round(pl.realized_pnl, 2),
      "positions": rows,
      "trades": pl.trades[-50:]
  }


def leaderboard(lobby: LobbyState):
  rows = []
  for uid, pl in lobby.players.items():
    # compute equity (mark-to-market)
    mv = 0.0
    for a in ASSETS:
      mv += pl.positions[a]["qty"] * lobby.prices[a]
    rows.append({
        "userId": uid,
        "name": pl.name,
        "equity": round(pl.cash + mv, 2),
        "realizedPnL": round(pl.realized_pnl, 2),
    })
  rows.sort(key=lambda r: r["equity"], reverse=True)
  return {"type": "LEADERBOARD", "rows": rows}


# ---------- Order matching (per player) ----------
def execute_market(lobby: LobbyState, pl: PlayerState, asset: str, side: str,
                   qty: int):
  price = lobby.prices[asset]
  pos = pl.positions[asset]
  cash = pl.cash

  if side == "BUY":
    # cover short first
    if pos["qty"] < 0:
      cover = min(qty, -pos["qty"])
      if cover > 0:
        record_trade(pl,
                     asset=asset,
                     side_open="SHORT",
                     qty=cover,
                     entry_price=pos["avg"],
                     exit_price=price,
                     entry_ts=pos["entry_ts"])
        cash -= price * cover
        pos["qty"] += cover
        if pos["qty"] == 0:
          pos["avg"] = 0.0
          pos["entry_ts"] = None
        qty -= cover
    # extend/create long
    if qty > 0:
      cost = price * qty
      if cash < cost:
        return False, "insufficient_cash"
      if pos["qty"] > 0:
        pos["avg"] = (pos["avg"] * pos["qty"] + price * qty) / (pos["qty"] +
                                                                qty)
      else:
        pos["avg"] = price
      pos["qty"] += qty
      cash -= cost
      if pos["entry_ts"] is None:
        pos["entry_ts"] = time.time()

  else:  # SELL
    # close long first
    if pos["qty"] > 0:
      close_qty = min(qty, pos["qty"])
      if close_qty > 0:
        record_trade(pl,
                     asset=asset,
                     side_open="LONG",
                     qty=close_qty,
                     entry_price=pos["avg"],
                     exit_price=price,
                     entry_ts=pos["entry_ts"])
        cash += price * close_qty
        pos["qty"] -= close_qty
        if pos["qty"] == 0:
          pos["avg"] = 0.0
          pos["entry_ts"] = None
        qty -= close_qty
    # open/extend short
    if qty > 0:
      new_qty = pos["qty"] - qty
      if pos["qty"] < 0:
        pos["avg"] = (pos["avg"] * abs(pos["qty"]) +
                      price * qty) / (abs(pos["qty"]) + qty)
      else:
        pos["avg"] = price
      pos["qty"] = new_qty
      cash += price * qty
      if pos["entry_ts"] is None:
        pos["entry_ts"] = time.time()

  pl.cash = cash
  return True, None


# ---------- Lobby lifecycle ----------
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


# ---------- HTTP ----------
@app.get("/")
async def index():
  return HTMLResponse(INDEX_HTML,
                      headers={
                          "Cache-Control":
                          "no-cache, no-store, must-revalidate",
                          "Pragma": "no-cache",
                          "Expires": "0"
                      })


# ---------- WebSocket ----------
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


# ---------- HTML UI (lobby + game + charts) ----------
INDEX_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Multiplayer Trading Game</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body { font-family: system-ui, sans-serif; margin: 24px; max-width: 1200px; }
    h1 { margin-top: 0; }
    .row { display:flex; gap:16px; flex-wrap:wrap; }
    .card { border:1px solid #eaeaea; border-radius: 10px; padding: 12px; }
    .wide { flex: 1 1 60%; }
    .narrow { flex: 1 1 35%; }
    table { border-collapse: collapse; width: 100%; margin-top: 12px; }
    th, td { border: 1px solid #ddd; padding: 6px 8px; text-align: left; }
    th { background: #f6f6f6; }
    .controls { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
    .badge { padding: 2px 6px; border-radius: 6px; background:#eef; font-size:12px; }
    input[type="number"] { width: 80px; }
    #log { border:1px solid #ddd; height:120px; overflow:auto; padding:8px; margin-top:12px; }
    .kpis { display:flex; gap:12px; flex-wrap:wrap; }
    .kpis div { padding:8px 12px; border:1px solid #eee; border-radius:8px; background:#fafafa; }
    .muted { color:#666; font-size: 12px; }
    .hidden { display:none; }
    .green { background:#e8ffe8; }
    .red { background:#ffe8e8; }
  </style>
</head>
<body>
  <h1>Multiplayer Trading Game</h1>

  <div class="card">
    <div class="controls">
      <label>Name <input id="name" placeholder="Your name" /></label>
      <button id="btnConnect">Connect</button>
      <span>Status: <span id="wsStatus" class="badge red">disconnected</span></span>
      <span id="userIdBox" class="muted"></span>
    </div>
    <div class="controls" style="margin-top:8px;">
      <button id="btnCreate">Create Lobby</button>
      <label>Join code <input id="joinCode" placeholder="ABC123" style="width:100px" /></label>
      <button id="btnJoin">Join Lobby</button>
    </div>
    <div style="margin-top:6px;" class="muted">Tip: share your invite code with friends so they can join.</div>
  </div>

  <div id="lobbyCard" class="card hidden">
    <h3>Lobby: <span id="lobbyIdTxt">-</span> <span id="lobbyStatus" class="badge">LOBBY</span></h3>
    <div class="controls">
      <div>Host: <b id="hostIdTxt"></b></div>
      <div>Rules:
        <span class="badge">Start Cap: <span id="ruleCap">-</span></span>
        <span class="badge">Tick: <span id="ruleTick">-</span>s</span>
        <span class="badge">Duration: <span id="ruleDur">-</span>s</span>
      </div>
      <div id="inviteBox" class="muted"></div>
    </div>
    <div class="controls" style="margin-top:8px;">
      <label><input type="checkbox" id="readyChk"/> Ready</label>
      <button id="btnStart" disabled>Start Game (host)</button>
      <span class="muted">All players must be ready.</span>
    </div>
    <table>
      <thead><tr><th>Name</th><th>UserId</th><th>Ready</th></tr></thead>
      <tbody id="playersTbody"></tbody>
    </table>
  </div>

  <div id="gameArea" class="hidden">
    <div class="row">
      <div class="card wide">
        <h3>Market</h3>
        <div class="card" style="margin:8px 0 12px 0;">
          <div class="controls">
            <label for="assetSel"><b>Chart asset:</b></label>
            <select id="assetSel"><option>OIL</option><option>GOLD</option><option>ELECTRONICS</option><option>RICE</option><option>PLUMBER</option></select>
            <div class="badge">Time left: <span id="timeLeft">-</span>s</div>
          </div>
          <canvas id="priceChart" height="120"></canvas>
        </div>

        <table>
          <thead><tr><th>Asset</th><th>Price</th><th>Qty</th><th>Side</th><th></th></tr></thead>
          <tbody id="rows"></tbody>
        </table>
        <div id="log"></div>
      </div>

      <div class="card narrow">
        <h3>Portfolio</h3>
        <div class="kpis">
          <div>Cash: <b id="k_cash">-</b></div>
          <div>Equity: <b id="k_equity">-</b></div>
          <div>Unrealized PnL: <b id="k_upnl">-</b></div>
          <div>Realized PnL: <b id="k_rpnl">-</b></div>
        </div>
        <table style="margin-top:10px;">
          <thead><tr><th>Asset</th><th>Qty</th><th>Avg</th><th>Price</th><th>Value</th><th>uPnL</th></tr></thead>
          <tbody id="pos_rows"></tbody>
        </table>

        <h3 style="margin-top:16px;">Leaderboard</h3>
        <table>
          <thead><tr><th>Name</th><th>Equity</th><th>Realized</th></tr></thead>
          <tbody id="lb_rows"></tbody>
        </table>

        <h3 style="margin-top:16px;">Trade History</h3>
        <table>
          <thead><tr><th>Time</th><th>Asset</th><th>Side</th><th>Qty</th><th>Entry</th><th>Exit</th><th>Realized</th><th>Duration (s)</th></tr></thead>
          <tbody id="trades_rows"></tbody>
        </table>
      </div>
    </div>
  </div>

<script>
let ws = null;
let userId = null;
let lobbyId = null;
let isHost = false;
let prices = {};

const assets = ["OIL","GOLD","ELECTRONICS","RICE","PLUMBER"];

function log(m){
  const el = document.getElementById('log'); if(!el) return;
  const time = new Date().toLocaleTimeString();
  el.innerHTML = "["+time+"] " + m + "<br/>" + el.innerHTML;
}
function setWsStatus(ok){
  const s = document.getElementById('wsStatus');
  s.textContent = ok ? "connected" : "disconnected";
  s.className = "badge " + (ok ? "green" : "red");
}
function byId(id){ return document.getElementById(id); }

function showLobbyCard(show){
  byId('lobbyCard').classList.toggle('hidden', !show);
}
function showGameArea(show){
  byId('gameArea').classList.toggle('hidden', !show);
}

function renderPlayers(list){
  const tb = byId('playersTbody'); tb.innerHTML = "";
  list.forEach(p=>{
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${p.name}</td><td class="muted">${p.userId}</td><td>${p.ready ? "✅" : "❌"}</td>`;
    tb.appendChild(tr);
  });
}

/* ------- Market table ------- */
function renderOrderRows(){
  const rows = byId('rows'); rows.innerHTML = "";
  assets.forEach(a=>{
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${a}</td>
      <td id="p_${a}">-</td>
      <td><input id="q_${a}" type="number" min="1" value="10" style="width:80px"/></td>
      <td>
        <select id="s_${a}">
          <option>BUY</option><option>SELL</option>
        </select>
      </td>
      <td><button onclick="sendOrder('${a}')">Send</button></td>`;
    rows.appendChild(tr);
  });
}
function updatePrices(p){
  prices = p;
  assets.forEach(a=>{
    const cell = byId('p_'+a);
    if(cell && prices[a] !== undefined){
      cell.textContent = prices[a].toFixed(2);
    }
  });
}

/* ------- Portfolio render ------- */
function renderPortfolio(p){
  byId('k_cash').textContent = p.cash.toFixed(2);
  byId('k_equity').textContent = p.equity.toFixed(2);
  byId('k_upnl').textContent = (p.uPnL>=0? "+" : "") + p.uPnL.toFixed(2);
  byId('k_rpnl').textContent = (p.realizedPnL>=0? "+" : "") + p.realizedPnL.toFixed(2);

  const tbody = byId('pos_rows'); tbody.innerHTML = "";
  p.positions.forEach(row=>{
    const tr = document.createElement('tr');
    const upnlStr = (row.uPnL>=0? "+" : "") + row.uPnL.toFixed(2);
    tr.innerHTML = `
      <td>${row.asset}</td><td>${row.qty}</td>
      <td>${row.avg.toFixed(2)}</td><td>${row.price.toFixed(2)}</td>
      <td>${row.mktValue.toFixed(2)}</td><td>${upnlStr}</td>`;
    tbody.appendChild(tr);
  });

  const tbody2 = byId('trades_rows'); tbody2.innerHTML = "";
  (p.trades || []).slice().reverse().forEach(trd=>{
    const trEl = document.createElement('tr');
    const ts = new Date(trd.ts * 1000).toLocaleTimeString();
    trEl.innerHTML = `
      <td>${ts}</td><td>${trd.asset}</td><td>${trd.side_open}</td><td>${trd.qty}</td>
      <td>${Number(trd.entry_price).toFixed(2)}</td><td>${Number(trd.exit_price).toFixed(2)}</td>
      <td>${(trd.realized_pnl>=0? '+':'') + Number(trd.realized_pnl).toFixed(2)}</td>
      <td>${trd.duration_sec ?? ''}</td>`;
    tbody2.appendChild(trEl);
  });
}

/* ------- Leaderboard ------- */
function renderLeaderboard(lb){
  const tb = byId('lb_rows'); tb.innerHTML = "";
  (lb.rows || []).forEach(r=>{
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${r.name}</td><td>${r.equity.toFixed(2)}</td><td>${(r.realizedPnL>=0?'+':'')+r.realizedPnL.toFixed(2)}</td>`;
    tb.appendChild(tr);
  });
}

/* ------- Chart ------- */
let chart = null;
let chartAsset = "OIL";
const MAX_POINTS = 300;
const history = { OIL:[], GOLD:[], ELECTRONICS:[], RICE:[], PLUMBER:[] };
const labels  = [];

function initChart(){
  const ctx = byId('priceChart').getContext('2d');
  chart = new Chart(ctx, {
    type: 'line',
    data: { labels: labels, datasets: [{ label: chartAsset, data: history[chartAsset], borderWidth: 1, pointRadius: 0, tension: 0.2 }] },
    options: {
      animation: false, responsive: true,
      scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { beginAtZero: false } },
      plugins: { legend: { display: false } }
    }
  });
}
function updateChartAsset(newAsset){
  chartAsset = newAsset;
  chart.data.datasets[0].label = chartAsset;
  chart.data.datasets[0].data  = history[chartAsset];
  chart.update();
}
function pushTickToHistory(){
  const ts = new Date().toLocaleTimeString();
  labels.push(ts); if (labels.length > MAX_POINTS) labels.shift();
  Object.keys(history).forEach(a=>{
    const arr = history[a]; arr.push(prices[a] ?? null);
    if (arr.length > MAX_POINTS) arr.shift();
  });
  if (chart){ chart.update(); }
}

/* ------- WS connect / actions ------- */
function connectWS(){
  if(ws && ws.readyState === WebSocket.OPEN) return;
  const proto = (location.protocol === "https:") ? "wss" : "ws";
  const qs = userId ? ("?userId=" + encodeURIComponent(userId)) : "";
  ws = new WebSocket(`${proto}://${location.host}/ws${qs}`);
  ws.onopen = ()=> { setWsStatus(true); log("WebSocket connected"); };
  ws.onclose = ()=> { setWsStatus(false); log("WebSocket disconnected"); };
  ws.onmessage = (ev)=> {
    const msg = JSON.parse(ev.data);
    if (msg.type === "HELLO"){
      userId = msg.userId;
      byId('userIdBox').textContent = "userId: " + userId;
    } else if (msg.type === "INVITE_CODE"){
      lobbyId = msg.lobbyId;
      byId('inviteBox').textContent = "Invite code: " + lobbyId;
    } else if (msg.type === "LOBBY_STATE"){
      // update lobby panel
      showLobbyCard(true);
      byId('lobbyIdTxt').textContent = msg.lobbyId; lobbyId = msg.lobbyId;
      byId('lobbyStatus').textContent = msg.status;
      byId('hostIdTxt').textContent = msg.hostId;
      byId('ruleCap').textContent = msg.rules.startingCapital ?? msg.rules["startingCapital"];
      byId('ruleTick').textContent = msg.rules.tickSeconds ?? msg.rules["tickSeconds"];
      byId('ruleDur').textContent = msg.rules.durationSec ?? msg.rules["durationSec"];
      renderPlayers(msg.players);
      isHost = (userId === msg.hostId);
      byId('btnStart').disabled = !(isHost && msg.status === "LOBBY");
    } else if (msg.type === "GAME_STARTED"){
      showGameArea(true);
      byId('lobbyStatus').textContent = "RUNNING";
      if(!chart) initChart();
      renderOrderRows();
    } else if (msg.type === "GAME_ENDED"){
      byId('lobbyStatus').textContent = "ENDED";
      log("Game ended.");
    } else if (msg.type === "TICK"){
      updatePrices(msg.prices);
      byId('timeLeft').textContent = msg.remainingSec ?? "-";
      pushTickToHistory();
    } else if (msg.type === "PORTFOLIO"){
      renderPortfolio(msg);
    } else if (msg.type === "LEADERBOARD"){
      renderLeaderboard(msg);
    } else if (msg.type === "ORDER_ACCEPTED"){
      log(`Order accepted @ ${msg.price}: ${msg.side} ${msg.asset} x${msg.qty}`);
    } else if (msg.type === "ORDER_REJECT"){
      log(`Order rejected: ${msg.reason}`);
    }
  };
}

function createLobby(){
  const name = byId('name').value || "";
  // FIX: actually send the JSON (removed the erroneous .replace ? undefined : undefined)
  ws.send(JSON.stringify({
    type: "CREATE_LOBBY",
    name,
    rules: { startingCapital: %STARTCAP%, tickSeconds: %TICKSEC%, durationSec: %DURSEC% }
  }));
}

function joinLobby(){
  const name = byId('name').value || "";
  const code = (byId('joinCode').value || "").toUpperCase();
  if(!code){ alert("Enter a lobby code."); return; }
  ws.send(JSON.stringify({type:"JOIN_LOBBY", lobbyId: code, name}));
}

function setReady(){
  ws.send(JSON.stringify({type:"SET_READY", ready: byId('readyChk').checked }));
}

function startGame(){
  ws.send(JSON.stringify({type:"START_GAME"}));
}

function sendOrder(asset){
  if(!ws || ws.readyState !== WebSocket.OPEN){ log("Not connected."); return; }
  const qty = parseInt(byId('q_'+asset).value, 10);
  const side = byId('s_'+asset).value;
  ws.send(JSON.stringify({type:"ORDER", asset, side, qty}));
}

/* Hook up UI */
byId('btnConnect').onclick = connectWS;
byId('btnCreate').onclick = createLobby;
byId('btnJoin').onclick = joinLobby;
byId('readyChk').onchange = setReady;
byId('btnStart').onclick = startGame;
byId('assetSel').onchange = ()=> { if(chart){ chart.data.datasets[0].label = byId('assetSel').value; chart.data.datasets[0].data = history[byId('assetSel').value]; chart.update(); } };

/* Parse ?join=CODE for quick-join */
(function(){
  const url = new URL(location.href);
  const j = url.searchParams.get("join");
  if (j) { byId('joinCode').value = j; }
})();
</script>
</body>
</html>
"""

# Small templating to set default rules in createLobby() button
INDEX_HTML = INDEX_HTML.replace("%STARTCAP%", str(DEFAULT_STARTING_CASH)) \
                       .replace("%TICKSEC%", str(DEFAULT_TICK_SECONDS)) \
                       .replace("%DURSEC%", str(DEFAULT_DURATION_SEC))
