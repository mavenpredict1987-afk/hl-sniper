"""Self-check for the guards and exit management taken from the reference bot.

Covers: shorts switch, continuous ATR trailing, Kelly without a floor,
consecutive-loss pause, signal reset on close, correlation clusters.

Run: PYTHONPATH=.devlibs python3 test_guards.py
"""

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

os.environ["STATE_DIR"] = tempfile.mkdtemp(prefix="hl_guards_")
os.environ["RESET_STATE"] = "1"

import config                      # noqa: E402
from bot import Bot, Position      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ((" :: " + str(detail)) if detail else ""))
    if not cond:
        failures.append(name)


def fresh():
    bot = Bot()
    bot.cash = bot.equity = bot.peak = 10000.0
    bot.trades, bot.positions = [], {}
    bot.cooldowns, bot.last_prices = {}, {}
    bot.win_count = bot.loss_count = 0
    bot.win_sum = bot.loss_sum = bot.r_sum = 0.0
    bot.consec_losses = 0
    bot.loss_pause_until = 0.0
    return bot


def with_config(**overrides):
    """Context-free helper: set config values, return the previous ones."""
    prev = {k: getattr(config, k) for k in overrides}
    for k, v in overrides.items():
        setattr(config, k, v)
    return prev


# --- 1. the switches exist and are sane -----------------------------------
check("SHORTS_ENABLED is a bool", isinstance(config.SHORTS_ENABLED, bool), config.SHORTS_ENABLED)
check("ATR_TRAIL_MULT is numeric", isinstance(config.ATR_TRAIL_MULT, float), config.ATR_TRAIL_MULT)
check("PARTIAL_RATIO is numeric", isinstance(config.PARTIAL_RATIO, float), config.PARTIAL_RATIO)
check("correlation clusters cover the universe", len(config.CLUSTERS) >= 5,
      sorted(config.CLUSTERS))
check("every cluster has members",
      all(isinstance(v, list) and v for v in config.CLUSTERS.values()))
check("cluster_of finds a member", "ai" in config.CLUSTERS)
check("blacklist and whitelist are sets",
      isinstance(config.BLACKLIST, set) and isinstance(config.WHITELIST, set))
check("daily stop is configurable", isinstance(config.DAILY_STOP_PCT, float), config.DAILY_STOP_PCT)

# --- 2. Kelly must de-risk when the measured edge is negative -------------
bot = fresh()
bot.win_count, bot.loss_count = 4, 20
bot.win_sum, bot.loss_sum = 0.04, 0.20          # avg win == avg loss
prev = with_config(KELLY_NEGATIVE_MULT=0.0)
check("negative edge with mult 0 stops risking", bot.kelly_risk() == 0.0, bot.kelly_risk())
with_config(KELLY_NEGATIVE_MULT=1.0)
check("negative edge with mult 1 risks the minimum",
      abs(bot.kelly_risk() - config.MIN_RISK_PCT) < 1e-12, bot.kelly_risk())
check("the old constant floor is gone", bot.kelly_risk() < 0.05 * config.KELLY_FRACTION,
      "%.5f" % bot.kelly_risk())
with_config(**prev)

# a strong record still sizes inside the configured band
winner = fresh()
winner.win_count, winner.loss_count = 30, 10
winner.win_sum, winner.loss_sum = 0.90, 0.10
risk = winner.kelly_risk()
check("positive edge risks within the band",
      config.MIN_RISK_PCT <= risk <= config.MAX_RISK_PCT, risk)

# --- 3. continuous ATR trailing -------------------------------------------
bot = fresh()
pos = Position("BTC", "long", 100.0, 10.0, 90.0, 200.0, 10.0, 5.0)
pos.initial_size, pos.initial_risk_usd = 10.0, 100.0
pos.partial_done, pos.stop = True, 100.0
bot.positions["BTC"] = pos
bot.last_prices["BTC"] = 120.0

prev = with_config(ATR_TRAIL_MULT=0)
bot.manage_positions()
check("trail off keeps the old fixed stop", pos.stop == 100.0, pos.stop)

with_config(ATR_TRAIL_MULT=1.0)
bot.manage_positions()
check("trail on moves the stop with price", abs(pos.stop - 115.0) < 1e-9, pos.stop)
bot.last_prices["BTC"] = 130.0
bot.manage_positions()
check("trail keeps ratcheting", abs(pos.stop - 125.0) < 1e-9, pos.stop)
bot.last_prices["BTC"] = 120.0
bot.manage_positions()
check("trail never loosens", abs(pos.stop - 125.0) < 1e-9, pos.stop)
with_config(**prev)

# --- 4. consecutive-loss pause --------------------------------------------
bot = fresh()
prev = with_config(CONSEC_LOSS_PAUSE=2, CONSEC_LOSS_PAUSE_SEC=3600.0)
for i in range(2):
    p = Position("X%d" % i, "long", 100.0, 10.0, 90.0, 200.0, 10.0, 5.0)
    p.initial_size, p.initial_risk_usd = 10.0, 100.0
    bot.positions["X%d" % i] = p
    bot.close_position("X%d" % i, 95.0, "stop")
check("two losses arm the pause", bot.loss_pause_until > 0, bot.loss_pause_until)
check("consecutive losses counted", bot.consec_losses == 2, bot.consec_losses)
bot2 = fresh()
p = Position("Y", "long", 100.0, 10.0, 90.0, 200.0, 10.0, 5.0)
p.initial_size, p.initial_risk_usd = 10.0, 100.0
bot2.positions["Y"] = p
bot2.close_position("Y", 150.0, "target")
check("a win resets the counter", bot2.consec_losses == 0, bot2.consec_losses)
check("a win clears no pause", bot2.loss_pause_until == 0.0)
with_config(**prev)

# --- 5. signals are dropped when a position closes ------------------------
bot = fresh()
from sniper import generate_trend_signal  # noqa: E402
bot.sniper.convergence.add_signal("BTC", generate_trend_signal("long", 40.0, 25.0))
check("a signal exists before the close", "BTC" in bot.sniper.convergence.signals)
p = Position("BTC", "long", 100.0, 10.0, 90.0, 200.0, 10.0, 5.0)
p.initial_size, p.initial_risk_usd = 10.0, 100.0
bot.positions["BTC"] = p
bot.close_position("BTC", 110.0, "target")
check("closing drops the coin's signals",
      "BTC" not in bot.sniper.convergence.signals, list(bot.sniper.convergence.signals))

# --- 6. whitelist size bonus ---------------------------------------------
bot = fresh()
base = bot.position_size(1000.0, 900.0, 0.60, "normal")
prev = with_config(WHITELIST={"ZEC"}, WHITELIST_SIZE_BONUS=1.5)
boosted = bot.position_size(1000.0, 900.0, 0.60, "normal", 0.0, "long", "ZEC")
check("whitelisted coin is sized larger", boosted > base, "%.6f vs %.6f" % (boosted, base))
check("the bonus is exactly applied", abs(boosted / base - 1.5) < 1e-6, boosted / base)
with_config(**prev)

# --- 7. regime raises the entry bar --------------------------------------
check("high-vol regime has a stricter multiplier",
      config.REGIME_FILTER_MULT.get("high", 1.0) > 1.0, config.REGIME_FILTER_MULT)
check("calm regimes are unchanged",
      config.REGIME_FILTER_MULT.get("normal") == 1.0
      and config.REGIME_FILTER_MULT.get("low") == 1.0)

# --- 8. sniper stats keys and the bounded history window ------------------
bot = fresh()
sm = bot.sniper
sm.update_order_book("BTC", [(100.0, 100.0)] * 10, [(100.1, 100.0)] * 10)
conv = {"score": 0.60, "strength": "normal", "size_multiplier": 1.0}
rounds = sm.HISTORY_MAX + 25
for _ in range(rounds):
    sm.execute_snipe("BTC", "long", 1.0, conv)
check("every snipe is counted", sm.total_snipes == rounds, sm.total_snipes)
check("the history window is bounded", len(sm.execution_history) == sm.HISTORY_MAX,
      len(sm.execution_history))
stats = sm.get_stats()
for key in ("total_snipes", "rejected_snipes", "strong_snipes", "avg_slippage_bps"):
    check("stats expose the dashboard key %s" % key, key in stats, sorted(stats))
check("total_snipes is the counter, not the window",
      stats["total_snipes"] == rounds, stats["total_snipes"])
check("slippage is averaged into bps", stats["avg_slippage_bps"] > 0,
      stats["avg_slippage_bps"])
check("the old key names still work",
      "rejected" in stats and "avg_slippage_pct" in stats)

# --- 9. the entry bar must stay reachable ---------------------------------
# A 1.5 multiplier put the bar at 0.55 * 1.5 = 0.825 while the normalised
# score ceiling is about 0.80, so every high-volatility entry was rejected.
worst = config.CONVERGENCE_THRESHOLD * max(config.REGIME_FILTER_MULT.values())
check("the old 1.5 multiplier really was unreachable", config.CONVERGENCE_THRESHOLD * 1.5 > 0.80,
      config.CONVERGENCE_THRESHOLD * 1.5)
check("the current worst regime is reachable", worst < 0.80, worst)
check("the hard cap sits below the score ceiling", config.MAX_ENTRY_THRESHOLD < 0.80,
      config.MAX_ENTRY_THRESHOLD)
check("the cap still bites on a hypothetical 1.5",
      min(config.CONVERGENCE_THRESHOLD * 1.5, config.MAX_ENTRY_THRESHOLD)
      == config.MAX_ENTRY_THRESHOLD)

# --- 10. cluster risk must stay inside its own cluster --------------------
bot = fresh()
for sym in ("BTC", "ETH", "UNI"):
    p = Position(sym, "long", 100.0, 10.0, 90.0, 200.0, 10.0, 5.0)
    p.initial_size, p.initial_risk_usd = 10.0, 100.0
    bot.positions[sym] = p
check("same-cluster risk is summed", abs(bot.cluster_risk("BTC") - 200.0) < 1e-9,
      bot.cluster_risk("BTC"))
check("another cluster is not counted", abs(bot.cluster_risk("UNI") - 100.0) < 1e-9,
      bot.cluster_risk("UNI"))
check("the clusters cover the live universe",
      all(c in {x for v in config.CLUSTERS.values() for x in v}
          for c in ("DOGE", "ZEC", "UNI", "HYPE", "XMR", "PAXG", "ASTER", "AERO")),
      sorted({x for v in config.CLUSTERS.values() for x in v}))

# --- 11. directional cap --------------------------------------------------
bot = fresh()
for sym, dirn in (("BTC", "short"), ("ETH", "long")):
    p = Position(sym, dirn, 100.0, 10.0, 90.0, 200.0, 10.0, 5.0)
    p.initial_size, p.initial_risk_usd = 10.0, 100.0
    bot.positions[sym] = p
check("direction risk counts only shorts", abs(bot.direction_risk("short") - 100.0) < 1e-9,
      bot.direction_risk("short"))
check("direction risk counts only longs", abs(bot.direction_risk("long") - 100.0) < 1e-9,
      bot.direction_risk("long"))


def try_open(cap):
    b = fresh()
    b._save_state = lambda *a, **k: None
    b.universe = {"SOL": {"tradable": True, "risk_pct_cap": 100.0, "class_max_leverage": 100}}
    b.active_symbols = ["SOL"]
    prev = with_config(MAX_DIRECTION_RISK_PCT=cap, MAX_TOTAL_RISK_PCT=0.50,
                       MAX_CLUSTER_RISK_PCT=0.50, BASE_RISK_PER_TRADE=0.01,
                       MIN_RISK_PCT=0.01, MAX_RISK_PCT=0.01, ATR_STOP_MULT=1.0,
                       MAX_MARGIN_UTILIZATION=1.0, LEVERAGE=10)
    try:
        return b.open_position("SOL", "long", 100.0, 10.0,
                               {"score": 0.60, "strength": "normal"}, 0.01)
    finally:
        with_config(**prev)


check("a 0.5% directional cap refuses a 1% position", try_open(0.005) is False)
check("a 5% directional cap allows it", try_open(0.05) is True)

# --- 12. liquidity is a relative test, not a flat floor -------------------
from sniper import SniperModule  # noqa: E402
sm = SniperModule()
# HYPE as measured live: about $4,800 resting on the bid across 20 levels,
# which the old flat $5,000 floor rejected as illiquid.
sm.update_order_book("HYPE", [(81.60, 2.95)] * 20, [(81.62, 522.0)] * 20)
r = sm.analyze_entry("HYPE", "short", 8.0)
check("a liquid coin clears the old flat floor", r["can_enter"], r.get("reason"))

sm.update_order_book("THIN", [(1.0, 100.0)], [(1.01, 100.0)])
r = sm.analyze_entry("THIN", "short", 10.0)
check("a genuinely thin book is refused", not r["can_enter"], r.get("reason"))
check("the reason names the failing check plainly",
      "liquidity" in r["reason"] and "_ok" not in r["reason"], r["reason"])

sm.update_order_book("BIG", [(100.0, 250.0)] * 20, [(100.1, 250.0)] * 20)
small = sm.analyze_entry("BIG", "long", 1.0)
large = sm.analyze_entry("BIG", "long", 4000.0)
check("a small order fits a deep book", small["can_enter"], small.get("reason"))
check("an oversized order is refused", not large["can_enter"], large.get("reason"))

# --- 13. prices print with enough digits ---------------------------------
from utils import price as fmt_price  # noqa: E402
check("large prices stay short", fmt_price(82178.5) == "82178.50", fmt_price(82178.5))
check("mid prices keep four digits", fmt_price(7.4154) == "7.4154", fmt_price(7.4154))
check("cheap prices keep five", fmt_price(0.0812) == "0.08120", fmt_price(0.0812))
check("DOGE entry stop and target no longer look identical",
      len({fmt_price(0.0810), fmt_price(0.0795), fmt_price(0.0888)}) == 3)

# --- 14. auto-blacklist ---------------------------------------------------
bot = fresh()
now = time.time()
check("a fresh coin is not benched", not bot.symbol_benched("DOGE"))
bot.symbol_stops["DOGE"] = [now]
check("one stop does not bench", not bot.symbol_benched("DOGE"))
bot.symbol_stops["DOGE"] = [now, now]
check("two stops bench the coin", bot.symbol_benched("DOGE"))
check("the bench appears in the list", "DOGE" in bot.benched_symbols())

old = now - (config.AUTO_BLACKLIST_WINDOW_DAYS + 1) * 86400.0
bot.symbol_stops["ZEC"] = [old, old]
check("stops outside the window do not bench", not bot.symbol_benched("ZEC"))
bot.symbol_stops["UNI"] = [old, old, old]
check("only recent stops count", not bot.symbol_benched("UNI"))

gone = now - (config.AUTO_BLACKLIST_PAUSE_DAYS + 1) * 86400.0
bot.symbol_stops["ADA"] = [gone, gone]
check("the pause expires on its own", not bot.symbol_benched("ADA"))

prev = with_config(AUTO_BLACKLIST_STOPS=0)
check("zero stops switches the feature off", not bot.symbol_benched("DOGE"))
with_config(**prev)

# stops are recorded by the close path, wins are not
bot = fresh()
for expected in (1, 2):
    p = Position("SOL", "long", 100.0, 10.0, 90.0, 200.0, 10.0, 5.0)
    p.initial_size, p.initial_risk_usd = 10.0, 100.0
    bot.positions["SOL"] = p
    bot.close_position("SOL", 90.0, "stop")
    check("stop %d is recorded" % expected,
          len(bot.symbol_stops.get("SOL") or []) == expected,
          bot.symbol_stops.get("SOL"))
check("the second stop benches the coin", bot.symbol_benched("SOL"))

win = fresh()
p = Position("XRP", "long", 100.0, 10.0, 90.0, 200.0, 10.0, 5.0)
p.initial_size, p.initial_risk_usd = 10.0, 100.0
win.positions["XRP"] = p
win.close_position("XRP", 150.0, "target")
check("a winning exit is not counted", not win.symbol_stops.get("XRP"))

check("stops are written into the state snapshot",
      "SOL" in (bot._state_snapshot().get("symbol_stops") or {}))

# --- 15. a trade record must equal the cash it moved ---------------------
# The entry fee used to be taken from cash at open and left out of the record,
# which overstated PnL, R, winrate and profit factor, and produced the
# impossible pairing of a positive average R with a losing account.
def open_close(exit_px, partial_at=None):
    b = fresh()
    b._save_state = lambda *a, **k: None
    b.universe = {"SOL": {"tradable": True, "risk_pct_cap": 100.0, "class_max_leverage": 100}}
    b.active_symbols = ["SOL"]
    prev = with_config(MAX_DIRECTION_RISK_PCT=0.50, MAX_TOTAL_RISK_PCT=0.50,
                       MAX_CLUSTER_RISK_PCT=0.50, BASE_RISK_PER_TRADE=0.01,
                       MIN_RISK_PCT=0.01, MAX_RISK_PCT=0.01, ATR_STOP_MULT=1.0,
                       MAX_MARGIN_UTILIZATION=1.0, LEVERAGE=10)
    try:
        before = b.cash
        b.open_position("SOL", "long", 100.0, 10.0,
                        {"score": 0.60, "strength": "normal"}, 0.01)
        if partial_at is not None:
            b.book_partial(b.positions["SOL"], "SOL", partial_at)
        b.close_position("SOL", exit_px, "target")
        return b, before
    finally:
        with_config(**prev)


b, before = open_close(150.0)
check("a winning trade matches the cash it moved",
      abs(b.trades[-1]["pnl"] - (b.cash - before)) < 1e-9,
      (round(b.trades[-1]["pnl"], 6), round(b.cash - before, 6)))

b, before = open_close(90.0)
check("a losing trade matches the cash it moved",
      abs(b.trades[-1]["pnl"] - (b.cash - before)) < 1e-9,
      (round(b.trades[-1]["pnl"], 6), round(b.cash - before, 6)))

b, before = open_close(150.0, partial_at=130.0)
check("a trade with a partial matches the cash it moved",
      abs(b.trades[-1]["pnl"] - (b.cash - before)) < 1e-9,
      (round(b.trades[-1]["pnl"], 6), round(b.cash - before, 6)))


def entry_fee_of_a_new_position():
    b = fresh()
    b._save_state = lambda *a, **k: None
    b.universe = {"SOL": {"tradable": True, "risk_pct_cap": 100.0, "class_max_leverage": 100}}
    b.active_symbols = ["SOL"]
    prev = with_config(MAX_DIRECTION_RISK_PCT=0.50, MAX_TOTAL_RISK_PCT=0.50,
                       MAX_CLUSTER_RISK_PCT=0.50, BASE_RISK_PER_TRADE=0.01,
                       MIN_RISK_PCT=0.01, MAX_RISK_PCT=0.01, ATR_STOP_MULT=1.0,
                       MAX_MARGIN_UTILIZATION=1.0, LEVERAGE=10)
    try:
        b.open_position("SOL", "long", 100.0, 10.0,
                        {"score": 0.60, "strength": "normal"}, 0.01)
        return b.positions["SOL"].entry_fee
    finally:
        with_config(**prev)


check("the entry fee is stored on the position",
      entry_fee_of_a_new_position() > 0, entry_fee_of_a_new_position())
check("a fresh Position defaults to no entry fee",
      Position("S", "long", 1.0, 1.0, 1.0, 1.0, 1.0, 1.0).entry_fee == 0.0)

print()
if failures:
    print("FAILED:", ", ".join(failures))
    sys.exit(1)
print("ALL CHECKS PASSED")
