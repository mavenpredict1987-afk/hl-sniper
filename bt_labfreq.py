"""How often is the cell even open, and how many signals does that leave us?

Answers what the lab can realistically accrue per week, using the same 30-day
percentile and ER(20) definitions as regime_lab.py on the cached hourly history.
"""
import json
import os

import regime_lab as R

CACHE = "bt_cache"
UNIVERSE = ["ETH", "SOL", "AVAX", "ARB", "AAVE", "ADA", "LINK", "DOGE", "SUI", "NEAR", "XRP"]


def load(coin):
    with open(os.path.join(CACHE, coin + ".json"), encoding="utf-8") as fh:
        return json.load(fh)["candles"]


def cells(btc):
    """Cell for every hour from PCT_WIN onward, using only past data."""
    closes = [b["c"] for b in btc]
    atr = R.atr_series(btc)
    atrp = [None if atr[i] is None else atr[i] / closes[i] * 100.0 for i in range(len(closes))]
    out = []
    for i in range(R.PCT_WIN + 1, len(btc)):
        hist = [x for x in atrp[i - R.PCT_WIN:i] if x is not None]
        if not hist or atrp[i] is None:
            continue
        pct = 100.0 * sum(1 for x in hist if x <= atrp[i]) / len(hist)
        er = R.er_last(closes[:i + 1])
        if er is None:
            continue
        out.append((btc[i]["t"], er >= R.ER_TREND, pct >= R.PCT_CUT))
    return out


btc = load("BTC")
rows = cells(btc)
n = len(rows)
trend = sum(1 for _, t, _ in rows if t)
turb = sum(1 for _, _, v in rows if v)
both = sum(1 for _, t, v in rows if t and v)
print("hours scored: %d (%.0f days)" % (n, n / 24.0))
print("trend  %.1f%%" % (100.0 * trend / n))
print("turb   %.1f%%" % (100.0 * turb / n))
print("cell   %.1f%%  (%.0f hours)" % (100.0 * both / n, both))

cell_hours = {ts for ts, t, v in rows if t and v}

sigs = 0
sigs_cell = 0
for coin in UNIVERSE:
    bars = load(coin)
    closes = [b["c"] for b in bars]
    atr = R.atr_series(bars)
    for i in range(EMA := R.EMA_FEED, len(bars)):
        ema = R.ema_last(closes[:i + 1])
        if ema is None or atr[i] is None or closes[i] <= ema:
            continue
        sigs += 1
        if bars[i]["t"] in cell_hours:
            sigs_cell += 1
print()
print("MA100 signals, 11 coins, all hours: %d (%.2f/day)" % (sigs, sigs / (n / 24.0)))
print("MA100 signals inside the cell:      %d (%.2f/day, %.0f%% of all)"
      % (sigs_cell, sigs_cell / (n / 24.0), 100.0 * sigs_cell / max(1, sigs)))
print()
print("lab arms, 2 slots, 72h hold, one position per coin:")
print("  cell     <= 2 concurrent -> about %.1f closes/week" % (2.0 * 168 / 72))
print("  control  <= 2 concurrent -> about %.1f closes/week" % (2.0 * 168 / 72))
print("  cell_all signal-level   -> about %.1f closes/week (capped only by signals)"
      % (sigs_cell / (n / 24.0) * 7))
