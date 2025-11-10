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
