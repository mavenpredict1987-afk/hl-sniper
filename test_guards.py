"""Self-check for the guards and exit management taken from the reference bot.

Covers: shorts switch, continuous ATR trailing, Kelly without a floor,
consecutive-loss pause, signal reset on close, correlation clusters.

Run: PYTHONPATH=.devlibs python3 test_guards.py
"""

import os
import sys
import tempfile

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
check("correlation clusters cover five groups", len(config.CLUSTERS) == 5, sorted(config.CLUSTERS))
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

print()
if failures:
    print("FAILED:", ", ".join(failures))
    sys.exit(1)
print("ALL CHECKS PASSED")
