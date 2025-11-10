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
