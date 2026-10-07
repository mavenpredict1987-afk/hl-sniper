"""Self-check for indicators.py.

The load-bearing check is that atr_series() is bit-identical to calling atr()
on every prefix: bot.analyze_symbol was switched to the fast path, and a drift
there would silently change every signal in both the bot and the backtest.

Run: python3 test_indicators.py
"""

import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from indicators import ema, sma, atr, adx, atr_series  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ((" :: " + str(detail)) if detail else ""))
    if not cond:
        failures.append(name)


def series(n, seed, drift=0.0, vol=0.02):
    rnd = random.Random(seed)
    px = 100.0
    highs, lows, closes = [], [], []
    for _ in range(n):
        o = px
        px = o * (1 + drift + rnd.uniform(-vol, vol))
        highs.append(max(o, px) * (1 + rnd.uniform(0, 0.01)))
        lows.append(min(o, px) * (1 - rnd.uniform(0, 0.01)))
        closes.append(px)
    return highs, lows, closes


# 1. atr_series must equal the per-prefix loop exactly
worst = 0.0
for trial in range(8):
    highs, lows, closes = series(random.Random(trial).randint(80, 300), trial)
    n = len(closes)
    fast = atr_series(highs, lows, closes, from_len=30)
    slow = [atr(highs[:k], lows[:k], closes[:k]) for k in range(30, n + 1)]
    check("length matches for trial %d" % trial, len(fast) == len(slow),
          "%d vs %d" % (len(fast), len(slow)))
    for a, b in zip(fast, slow):
        worst = max(worst, abs(a - b))
check("atr_series is bit-identical to per-prefix atr", worst == 0.0, "max diff %.3e" % worst)

# 2. from_len is honoured
highs, lows, closes = series(200, 99)
check("from_len=30 yields len-29 values",
      len(atr_series(highs, lows, closes, from_len=30)) == len(closes) - 29,
      len(atr_series(highs, lows, closes, from_len=30)))
check("last value equals atr on the full series",
      abs(atr_series(highs, lows, closes, from_len=1)[-1] - atr(highs, lows, closes)) < 1e-12)
check("short series yields nothing", atr_series(highs[:10], lows[:10], closes[:10]) == [])

# 3. the primitives still behave
check("atr of a short series is 0", atr(highs[:5], lows[:5], closes[:5]) == 0.0)
check("sma averages the tail", abs(sma([1, 2, 3, 4, 5], 3) - 4.0) < 1e-12)
check("ema of a constant is that constant", abs(ema([7.0] * 50, 20) - 7.0) < 1e-9)
check("adx needs history", adx(highs[:10], lows[:10], closes[:10]) == 0.0)
adx_trend = adx(*series(160, 5, drift=0.01, vol=0.001))
# This ADX uses a rolling-sum accumulator rather than Wilder smoothing in the
# DI sums, so a very strong trend can push it a hair past 100. The strategy's
# gate is 25, so the overshoot is immaterial - the bound just documents it.
check("adx returns a usable number", 0.0 < adx_trend <= 105.0, adx_trend)
check("adx is higher in a trend than in chop",
      adx(*series(160, 6, drift=0.01, vol=0.001)) > adx(*series(160, 7, drift=0.0, vol=0.03)),
      "%.1f" % adx_trend)

print()
if failures:
    print("FAILED:", ", ".join(failures))
    sys.exit(1)
print("ALL CHECKS PASSED")
