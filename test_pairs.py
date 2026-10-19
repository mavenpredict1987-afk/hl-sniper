"""Self-check for the market-neutral pairs module.

Run: PYTHONPATH=.devlibs python3 test_pairs.py
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

os.environ["STATE_DIR"] = tempfile.mkdtemp(prefix="hl_pairs_")
os.environ["RESET_STATE"] = "1"

import config                                              # noqa: E402
from pairs import PairsModule, PairPosition                # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ((" :: " + str(detail)) if detail else ""))
    if not cond:
        failures.append(name)


class FakeClient:
    """Serves hourly candles whose first-to-last move is the given return."""

    def __init__(self, returns):
        self.returns = returns
        self.calls = 0

    def candles_recent(self, symbol, interval="1h", n_bars=168):
        self.calls += 1
        r = self.returns.get(symbol)
        if r is None:
            return []
        first = 100.0
        last = first * (1.0 + r)
        return [{"c": first}] * (n_bars - 1) + [{"c": last}]


class FakeBot:
    def __init__(self, returns, equity=10000.0):
        self.client = FakeClient(returns)
        self.active_symbols = list(returns)
        self.last_prices = {s: 100.0 * (1.0 + r) for s, r in returns.items()}
        self.equity = equity
        self.cash = equity
        self.events = []

    def _event(self, msg):
        self.events.append(msg)

    def _save_state(self, force=False):
        pass


def with_config(**overrides):
    prev = {k: getattr(config, k) for k in overrides}
    for k, v in overrides.items():
        setattr(config, k, v)
    return prev


# eight coins with clearly ordered seven day returns
RETURNS = {"AAA": -0.20, "BBB": -0.15, "CCC": -0.10, "DDD": -0.05,
           "EEE": 0.05, "FFF": 0.10, "GGG": 0.15, "HHH": 0.20}

# --- 1. ranking ------------------------------------------------------------
bot = FakeBot(RETURNS)
mod = PairsModule(bot)
ranked = mod.rank()
check("every coin is ranked", len(ranked) == 8, len(ranked))
check("weakest comes first", ranked[0]["symbol"] == "AAA", ranked[0])
check("strongest comes last", ranked[-1]["symbol"] == "HHH", ranked[-1])
check("the order is monotonic",
      all(ranked[i]["ret"] <= ranked[i + 1]["ret"] for i in range(len(ranked) - 1)))

# --- 2. a rebalance opens the right pairs ----------------------------------
prev = with_config(PAIRS_N=3, PAIRS_EXPOSURE=0.20, PAIRS_HOLD_H=48, PAIRS_ENABLED=True)
mod = PairsModule(bot)
mod._rebalance(1000.0)
check("three pairs are opened", len(mod.positions) == 3, len(mod.positions))
longs = [p.long_sym for p in mod.positions]
shorts = [p.short_sym for p in mod.positions]
check("the long legs are the three weakest", sorted(longs) == ["AAA", "BBB", "CCC"], longs)
check("the short legs are the three strongest", sorted(shorts) == ["FFF", "GGG", "HHH"], shorts)
check("each pair is long the weak and short the strong",
      all(RETURNS[p.long_sym] < RETURNS[p.short_sym] for p in mod.positions))
check("both legs carry the same notional",
      all(abs(p.notional - p.notional) < 1e-9 for p in mod.positions))
expected = 10000.0 * 0.20 / (2 * 3)
check("notional follows exposure and pair count",
      abs(mod.positions[0].notional - expected) < 1e-9, mod.positions[0].notional)
check("every pair is due in the configured hold time",
      all(abs(p.due - (1000.0 + 48 * 3600)) < 1e-6 for p in mod.positions))

# --- 3. too few coins ------------------------------------------------------
thin = FakeBot({"AAA": -0.1, "BBB": 0.1})
thin_mod = PairsModule(thin)
thin_mod._rebalance(1000.0)
check("a thin universe opens nothing", len(thin_mod.positions) == 0)
check("and says why", "not enough" in thin_mod.last_error, thin_mod.last_error)

# --- 4. closing books profit and cost --------------------------------------
bot2 = FakeBot(RETURNS)
mod2 = PairsModule(bot2)
mod2._rebalance(1000.0)
# long legs +5%, short legs -5%: both sides gain on a market-neutral pair
for pos in mod2.positions:
    bot2.last_prices[pos.long_sym] = pos.long_entry * 1.05
    bot2.last_prices[pos.short_sym] = pos.short_entry * 0.95
pos0 = mod2.positions[0]
gross = pos0.notional * 0.10
mod2._close(pos0, 1000.0 + 48 * 3600)
check("a winning pair is recorded", mod2.stats["trades"] == 1, mod2.stats)
check("winrate counts it", mod2.stats["wins"] == 1)
check("fees are charged on both legs", mod2.stats["pnl"] < gross,
      (round(mod2.stats["pnl"], 2), round(gross, 2)))
check("profit stays positive after costs", mod2.stats["pnl"] > 0, mod2.stats["pnl"])
check("the closed record carries both legs",
      mod2.closed[-1]["long"] and mod2.closed[-1]["short"], mod2.closed[-1])

# a losing pair must register as a loss
bot3 = FakeBot(RETURNS)
mod3 = PairsModule(bot3)
mod3._rebalance(1000.0)
for pos in mod3.positions:
    bot3.last_prices[pos.long_sym] = pos.long_entry * 0.95
    bot3.last_prices[pos.short_sym] = pos.short_entry * 1.05
mod3._close(mod3.positions[0], 1000.0 + 48 * 3600)
check("a losing pair is booked as a loss", mod3.stats["pnl"] < 0, mod3.stats["pnl"])
check("and does not count as a win", mod3.stats["wins"] == 0)

# --- 5. positions close when due, not before ------------------------------
bot4 = FakeBot(RETURNS)
mod4 = PairsModule(bot4)
mod4._rebalance(1000.0)
mod4._close_due(1000.0 + 3600)                 # one hour later
check("nothing closes early", len(mod4.positions) == 3, len(mod4.positions))
mod4._close_due(1000.0 + 49 * 3600)
check("everything closes once due", len(mod4.positions) == 0, len(mod4.positions))

# --- 5b. a pair that cannot be priced must not vanish ---------------------
class BlindClient(FakeClient):
    def all_mids(self):
        return {}


bot6 = FakeBot(RETURNS)
bot6.client = BlindClient(RETURNS)
mod6 = PairsModule(bot6)
mod6._rebalance(1000.0)
bot6.last_prices = {}                          # every coin left the universe
mod6._close_due(1000.0 + 49 * 3600)
check("an unpriceable pair is kept, not dropped", len(mod6.positions) == 3,
      len(mod6.positions))
check("and nothing is booked", mod6.stats["trades"] == 0, mod6.stats)
check("the reason is reported", "cannot price" in mod6.last_error, mod6.last_error)

# once prices come back it closes and books
bot6.last_prices = {p.long_sym: p.long_entry * 1.02 for p in mod6.positions}
bot6.last_prices.update({p.short_sym: p.short_entry * 0.98 for p in mod6.positions})
mod6._close_due(1000.0 + 50 * 3600)
check("it closes as soon as a price exists", len(mod6.positions) == 0,
      len(mod6.positions))
check("and the trades are booked", mod6.stats["trades"] == 3, mod6.stats)

# --- 6. the switch and the report -----------------------------------------
prev2 = with_config(PAIRS_ENABLED=False)
bot5 = FakeBot(RETURNS)
mod5 = PairsModule(bot5)
mod5.on_cycle()
check("a disabled module does nothing", len(mod5.positions) == 0)
with_config(**prev2)

stats = mod.get_stats()
for key in ("enabled", "trades", "winrate", "pnl", "unrealized", "open",
            "lookback_h", "hold_h", "n_pairs", "exposure", "next_rebalance_h",
            "open_pairs", "recent"):
    check("the report exposes %s" % key, key in stats, sorted(stats))
check("the report carries the parameters",
      stats["lookback_h"] == config.PAIRS_LOOKBACK_H and stats["hold_h"] == config.PAIRS_HOLD_H)
with_config(**prev)

# --- 7. realised PnL reaches the bot's cash -------------------------------
# The module used to keep its own scoreboard: a pair could win $50 and the
# account's capital never moved, which made the frozen 5% drawdown hypothesis
# unmeasurable.
bota = FakeBot(RETURNS)
moda = PairsModule(bota)
moda._rebalance(1000.0)
start_cash = bota.cash
for p in list(moda.positions):
    bota.last_prices[p.long_sym] = p.long_entry * 1.05
    bota.last_prices[p.short_sym] = p.short_entry * 0.95
moda._close_due(1000.0 + 50 * 3600)
check("closing pairs moves the bot's cash", abs(bota.cash - start_cash) > 1.0,
      (start_cash, bota.cash))
check("the cash move equals the booked PnL",
      abs((bota.cash - start_cash) - moda.stats["pnl"]) < 1e-6,
      (bota.cash - start_cash, moda.stats["pnl"]))
check("credited tracks the booked PnL",
      abs(moda.credited - moda.stats["pnl"]) < 1e-9, moda.credited)

# A state file from an older build has stats["pnl"] but no "credited" key.
botb = FakeBot(RETURNS, equity=5000.0)
modb = PairsModule(botb)
modb.stats["pnl"] = 8.0
modb.credited = 0.0
gap = modb.reconcile_cash()
check("a legacy state credits its whole PnL once", abs(botb.cash - 5008.0) < 1e-9, botb.cash)
check("the gap is what was missing", abs(gap - 8.0) < 1e-9, gap)
check("reconciling twice does nothing", abs(modb.reconcile_cash()) < 1e-9)
check("and the cash is unchanged the second time", abs(botb.cash - 5008.0) < 1e-9, botb.cash)

print()
if failures:
    print("FAILED:", ", ".join(failures))
    sys.exit(1)
print("ALL CHECKS PASSED")
