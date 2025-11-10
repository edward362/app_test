# domain/execution.py
from __future__ import annotations

import time
from typing import Optional, Tuple

from domain.models import LobbyState, PlayerState
from domain.portfolio import record_trade


def execute_market(
    lobby: LobbyState, pl: PlayerState, asset: str, side: str, qty: int
) -> Tuple[bool, Optional[str]]:
    """
    Execute a market BUY/SELL for `qty` on `asset` for player `pl` in `lobby`.
    - Closes opposite positions first (buy covers shorts, sell closes longs).
    - Averages price when extending a position.
    - Updates cash, position avg, and records realized PnL via record_trade.

    Returns:
        (ok, reason) where reason is None on success.
    """
    price = lobby.prices[asset]
    pos = pl.positions[asset]
    cash = pl.cash

    if side == "BUY":
        # cover short first
        if pos["qty"] < 0:
            cover = min(qty, -pos["qty"])
            if cover > 0:
                record_trade(
                    pl,
                    asset=asset,
                    side_open="SHORT",
                    qty=cover,
                    entry_price=pos["avg"],
                    exit_price=price,
                    entry_ts=pos["entry_ts"],
                )
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
                pos["avg"] = (pos["avg"] * pos["qty"] + price * qty) / (pos["qty"] + qty)
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
                record_trade(
                    pl,
                    asset=asset,
                    side_open="LONG",
                    qty=close_qty,
                    entry_price=pos["avg"],
                    exit_price=price,
                    entry_ts=pos["entry_ts"],
                )
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
                pos["avg"] = (pos["avg"] * abs(pos["qty"]) + price * qty) / (
                    abs(pos["qty"]) + qty
                )
            else:
                pos["avg"] = price
            pos["qty"] = new_qty
            cash += price * qty
            if pos["entry_ts"] is None:
                pos["entry_ts"] = time.time()

    pl.cash = cash
    return True, None
