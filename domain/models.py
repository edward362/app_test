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
