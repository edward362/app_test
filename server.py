
# server.py
import asyncio, random, math, time
from typing import Dict, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

# ---------- Config ----------
ASSETS = ["OIL", "GOLD", "ELECTRONICS", "RICE", "PLUMBER"]
TICK_SECONDS = 2  # faster demo
PRICE_TICK = 0.01

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

def round_tick(x: float, tick: float = PRICE_TICK) -> float:
    # Round to nearest tick and clamp at tick minimum
    return max(tick, round(x / tick) * tick)

def step_prices():
    # Simple drift + noise random walk per asset
    for a in ASSETS:
        p = prices[a]
        # small drift and noise for demo
        drift = 0.02 if a == "GOLD" else 0.05
        sigma = 0.15 if a in ("GOLD","RICE") else 0.30
        # Box-Muller normal noise scaled to 2s "tick"
        u1 = max(1e-9, _rng.random()); u2 = _rng.random()
        z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2 * math.pi * u2)
        eps = z * sigma / math.sqrt(30)  # pretend 30 steps per "unit time"
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
        try:
            clients.remove(ws)
        except KeyError:
            pass

async def ticker():
    # Background task: every 2s push prices to all connected clients
    while True:
        await asyncio.sleep(TICK_SECONDS)
        step_prices()
        await broadcast({
            "type": "TICK",
            "ts": time.time(),
            "prices": prices,
        })

@app.get("/")
async def index():
    return HTMLResponse(INDEX_HTML)

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    # Immediately send current prices
    await ws.send_json({"type":"STATE","prices":prices,"seed":_seed})
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "ORDER":
                # Ack the "order" for demo
                await ws.send_json({
                    "type":"ORDER_ACCEPTED",
                    "asset": msg.get("asset"),
                    "side": msg.get("side"),
                    "qty": msg.get("qty"),
                })
    except WebSocketDisconnect:
        clients.discard(ws)
    except Exception:
        clients.discard(ws)

# Start background ticker on startup
@app.on_event("startup")
async def on_start():
    global ticker_task
    if ticker_task is None:
        ticker_task = asyncio.create_task(ticker())

# Minimal inline HTML UI
INDEX_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>WebSocket Trading Demo</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 24px; }
    table { border-collapse: collapse; width: 520px; margin-top: 12px; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    th { background: #f6f6f6; }
    #log { border:1px solid #ddd; height:140px; overflow:auto; padding:8px; width:520px; margin-top:12px; }
    .controls { display:flex; gap:8px; align-items:center; margin-top:12px; }
    input[type="number"] { width: 80px; }
    .badge { padding: 2px 6px; border-radius: 6px; background:#eef; font-size:12px; }
  </style>
</head>
<body>
  <h1>Trading Demo (FastAPI + WebSocket)</h1>
  <div class="controls">
    <button id="connect">Connect</button>
    <span>Connection: <span id="status" class="badge">disconnected</span></span>
  </div>

  <table>
    <thead>
      <tr><th>Asset</th><th>Price</th><th>Qty</th><th>Side</th><th></th></tr>
    </thead>
    <tbody id="rows"></tbody>
  </table>

  <div id="log"></div>

<script>
const assets = ["OIL","GOLD","ELECTRONICS","RICE","PLUMBER"];
let ws = null;
let prices = {};

function log(m){
  const el = document.getElementById('log');
  const time = new Date().toLocaleTimeString();
  el.innerHTML += "["+time+"] " + m + "<br/>";
  el.scrollTop = el.scrollHeight;
}

function setStatus(s){
  document.getElementById('status').textContent = s;
  document.getElementById('status').style.background = (s==="connected") ? "#e8ffe8" : "#ffe8e8";
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

function connectWS(){
  if(ws && ws.readyState === WebSocket.OPEN) return;
  ws = new WebSocket(`ws://${location.host}/ws`);
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
    } else if (msg.type === "ORDER_ACCEPTED"){
      log(`Order accepted: ${msg.side} ${msg.asset} x${msg.qty}`);
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
