# server.py
import asyncio, random, math, time
from typing import Dict, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

# ---------- Config ----------
ASSETS = ["OIL", "GOLD", "ELECTRONICS", "RICE", "PLUMBER"]
TICK_SECONDS = 2  # demo speed
PRICE_TICK = 0.01
STARTING_CASH = 10_000.0

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- State ----------
clients: Set[WebSocket] = set()
prices: Dict[str, float] = {a: 100.0 for a in ASSETS}
_seed = random.randint(1, 10_000)
_rng = random.Random(_seed)
ticker_task = None

# Per-connection portfolios (keyed by WebSocket object)
# portfolio = { "cash": float, "positions": {asset: {"qty": int, "avg": float}} }
portfolios: Dict[WebSocket, Dict] = {}

def round_tick(x: float, tick: float = PRICE_TICK) -> float:
    return max(tick, round(x / tick) * tick)

def step_prices():
    for a in ASSETS:
        p = prices[a]
        drift = 0.02 if a == "GOLD" else 0.05
        sigma = 0.15 if a in ("GOLD","RICE") else 0.30
        u1 = max(1e-9, _rng.random()); u2 = _rng.random()
        z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2 * math.pi * u2)
        eps = z * sigma / math.sqrt(30)
        newp = p * (1 + drift/30 + eps)
        prices[a] = round_tick(newp, PRICE_TICK)

async def broadcast(payload: dict):
    dead = []
    for ws in clients:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)
        portfolios.pop(ws, None)

def pos_unrealized_upnl(qty: int, avg: float, price: float) -> float:
    if qty > 0:   # long
        return (price - avg) * qty
    if qty < 0:   # short
        return (avg - price) * (-qty)
    return 0.0

def snapshot_for(ws: WebSocket) -> dict:
    pf = portfolios.get(ws, {})
    cash = float(pf.get("cash", STARTING_CASH))
    positions = pf.get("positions", {})
    rows = []
    upnl_total = 0.0
    mkt_value_total = 0.0
    for a in ASSETS:
        pos = positions.get(a, {"qty": 0, "avg": 0.0})
        qty, avg = int(pos["qty"]), float(pos["avg"])
        price = prices[a]
        upnl = pos_unrealized_upnl(qty, avg, price)
        mkt_value = qty * price
        upnl_total += upnl
        mkt_value_total += mkt_value
        rows.append({
            "asset": a, "qty": qty, "avg": round(avg,2),
            "price": round(price,2),
            "mktValue": round(mkt_value,2),
            "uPnL": round(upnl, 2)
        })
    equity = cash + mkt_value_total
    return {
        "type": "PORTFOLIO",
        "cash": round(cash,2),
        "equity": round(equity,2),
        "uPnL": round(upnl_total,2),
        "positions": rows
    }

async def push_portfolio(ws: WebSocket):
    try:
        await ws.send_json(snapshot_for(ws))
    except Exception:
        pass

async def ticker():
    while True:
        await asyncio.sleep(TICK_SECONDS)
        step_prices()
        # 1) Public prices for everyone
        await broadcast({
            "type": "TICK",
            "ts": time.time(),
            "prices": prices,
        })
        # 2) Personal portfolio snapshot per client
        for ws in list(clients):
            await push_portfolio(ws)

@app.get("/")
async def index():
    return HTMLResponse(INDEX_HTML)

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    # init portfolio
    portfolios[ws] = {
        "cash": float(STARTING_CASH),
        "positions": {a: {"qty": 0, "avg": 0.0} for a in ASSETS}
    }
    # initial push
    await ws.send_json({"type":"STATE","prices":prices,"seed":_seed})
    await push_portfolio(ws)

    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "ORDER":
                asset = msg.get("asset")
                side  = msg.get("side")
                qty   = int(msg.get("qty", 0) or 0)
                if asset not in ASSETS or side not in ("BUY","SELL") or qty <= 0:
                    await ws.send_json({"type":"ORDER_REJECT","reason":"invalid"})
                    continue
                execute_market(ws, asset, side, qty, prices[asset])
                await ws.send_json({
                    "type":"ORDER_ACCEPTED",
                    "asset": asset, "side": side, "qty": qty,
                    "price": round(prices[asset],2)
                })
                await push_portfolio(ws)
    except WebSocketDisconnect:
        clients.discard(ws)
        portfolios.pop(ws, None)
    except Exception:
        clients.discard(ws)
        portfolios.pop(ws, None)

def execute_market(ws: WebSocket, asset: str, side: str, qty: int, price: float):
    pf = portfolios[ws]
    pos = pf["positions"][asset]
    cash = pf["cash"]

    if side == "BUY":
        # Cover short first
        if pos["qty"] < 0:
            cover = min(qty, -pos["qty"])
            if cover > 0:
                # pay to buy back; realized PnL flows implicitly through cash changes over time
                cash -= price * cover
                pos["qty"] += cover
                if pos["qty"] == 0:
                    pos["avg"] = 0.0
                qty -= cover
        if qty > 0:
            # extend/create long
            cost = price * qty
            if cash < cost:
                return             # reject silently for demo
            if pos["qty"] > 0:
                pos["avg"] = (pos["avg"]*pos["qty"] + price*qty) / (pos["qty"] + qty)
            else:
                pos["avg"] = price
            pos["qty"] += qty
            cash -= cost

    else:  # SELL
        # Sell existing long first
        if pos["qty"] > 0:
            close_qty = min(qty, pos["qty"])
            if close_qty > 0:
                cash += price * close_qty
                pos["qty"] -= close_qty
                if pos["qty"] == 0:
                    pos["avg"] = 0.0
                qty -= close_qty
        # Open/extend short with remaining qty
        if qty > 0:
            # weighted avg for negative quantities
            new_qty = pos["qty"] - qty
            if pos["qty"] < 0:
                pos["avg"] = (pos["avg"]*abs(pos["qty"]) + price*qty) / (abs(pos["qty"])+qty)
            elif pos["qty"] == 0:
                pos["avg"] = price
            else:
                # shouldn't happen (handled above), but guard anyway
                pos["avg"] = price
            pos["qty"] = new_qty
            cash += price * qty

    pf["cash"] = cash

# ---------- Minimal inline HTML UI (now with Portfolio panel) ----------
INDEX_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>WebSocket Trading Demo</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 24px; max-width: 980px; }
    table { border-collapse: collapse; width: 100%; margin-top: 12px; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    th { background: #f6f6f6; }
    #log { border:1px solid #ddd; height:140px; overflow:auto; padding:8px; margin-top:12px; }
    .controls { display:flex; gap:8px; align-items:center; margin-top:12px; }
    input[type="number"] { width: 80px; }
    .badge { padding: 2px 6px; border-radius: 6px; background:#eef; font-size:12px; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items:start; }
    .card { border:1px solid #eaeaea; border-radius: 8px; padding: 12px; }
    .kpis { display:flex; gap:16px; }
    .kpis div { padding:8px 12px; border:1px solid #eee; border-radius:8px; background:#fafafa; }
  </style>
</head>
<body>
  <h1>Trading Demo (FastAPI + WebSocket)</h1>
  <div class="controls">
    <button id="connect">Connect</button>
    <span>Connection: <span id="status" class="badge">disconnected</span></span>
  </div>

  <div class="grid">
    <div class="card">
      <h3>Market</h3>
      <table>
        <thead>
          <tr><th>Asset</th><th>Price</th><th>Qty</th><th>Side</th><th></th></tr>
        </thead>
        <tbody id="rows"></tbody>
      </table>
      <div id="log"></div>
    </div>

    <div class="card">
      <h3>Portfolio</h3>
      <div class="kpis">
        <div>Cash: <b id="k_cash">-</b></div>
        <div>Equity: <b id="k_equity">-</b></div>
        <div>Unrealized PnL: <b id="k_upnl">-</b></div>
      </div>
      <table style="margin-top:10px;">
        <thead>
          <tr><th>Asset</th><th>Qty</th><th>Avg</th><th>Price</th><th>Value</th><th>uPnL</th></tr>
        </thead>
        <tbody id="pos_rows"></tbody>
      </table>
    </div>
  </div>

<script>
const assets = ["OIL","GOLD","ELECTRONICS","RICE","PLUMBER"];
let ws = null;
let prices = {};

function log(m){
  const el = document.getElementById('log');
  const time = new Date().toLocaleTimeString();
  el.innerHTML = "["+time+"] " + m + "<br/>" + el.innerHTML;
}

function setStatus(s){
  const el = document.getElementById('status');
  el.textContent = s;
  el.style.background = (s==="connected") ? "#e8ffe8" : "#ffe8e8";
}

function renderRows(){
  const rows = document.getElementById('rows');
  rows.innerHTML = "";
  assets.forEach(a=>{
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${a}</td>
      <td id="p_${a}">${prices[a] ? prices[a].toFixed(2) : "-"}</td>
      <td><input id="q_${a}" type="number" min="1" value="10"/></td>
      <td>
        <select id="s_${a}">
          <option>BUY</option>
          <option>SELL</option>
        </select>
      </td>
      <td><button onclick="sendOrder('${a}')">Send</button></td>
    `;
    rows.appendChild(tr);
  });
}

function updatePrices(p){
  prices = p;
  assets.forEach(a=>{
    const cell = document.getElementById('p_'+a);
    if(cell && prices[a] !== undefined){
      cell.textContent = prices[a].toFixed(2);
    }
  });
}

function renderPortfolio(p){
  document.getElementById('k_cash').textContent = p.cash.toFixed(2);
  document.getElementById('k_equity').textContent = p.equity.toFixed(2);
  const up = (p.uPnL>=0? "+" : "") + p.uPnL.toFixed(2);
  document.getElementById('k_upnl').textContent = up;

  const tbody = document.getElementById('pos_rows');
  tbody.innerHTML = "";
  p.positions.forEach(row=>{
    const tr = document.createElement('tr');
    const upnlStr = (row.uPnL>=0? "+" : "") + row.uPnL.toFixed(2);
    tr.innerHTML = `
      <td>${row.asset}</td>
      <td>${row.qty}</td>
      <td>${row.avg.toFixed(2)}</td>
      <td>${row.price.toFixed(2)}</td>
      <td>${row.mktValue.toFixed(2)}</td>
      <td>${upnlStr}</td>
    `;
    tbody.appendChild(tr);
  });
}

function connectWS(){
  if(ws && ws.readyState === WebSocket.OPEN) return;
  const proto = (location.protocol === "https:") ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = ()=> { setStatus("connected"); log("WebSocket connected"); };
  ws.onclose = ()=> { setStatus("disconnected"); log("WebSocket disconnected"); };
  ws.onmessage = (ev)=> {
    const msg = JSON.parse(ev.data);
    if(msg.type === "STATE"){
      log("Initial state received (seed "+msg.seed+")");
      updatePrices(msg.prices);
      renderRows();
    } else if (msg.type === "TICK"){
      updatePrices(msg.prices);
    } else if (msg.type === "PORTFOLIO"){
      renderPortfolio(msg);
    } else if (msg.type === "ORDER_ACCEPTED"){
      log(`Order accepted @ ${msg.price}: ${msg.side} ${msg.asset} x${msg.qty}`);
    } else if (msg.type === "ORDER_REJECT"){
      log(`Order rejected: ${msg.reason}`);
    }
  };
}

function sendOrder(asset){
  if(!ws || ws.readyState !== WebSocket.OPEN){
    log("Not connected.");
    return;
  }
  const qty = parseInt(document.getElementById('q_'+asset).value, 10);
  const side = document.getElementById('s_'+asset).value;
  ws.send(JSON.stringify({type:"ORDER", asset, side, qty}));
}

document.getElementById('connect').onclick = connectWS;
renderRows();
</script>
</body>
</html>
"""

# Start background ticker on startup
@app.on_event("startup")
async def on_start():
    global ticker_task
    if ticker_task is None:
        ticker_task = asyncio.create_task(ticker())
