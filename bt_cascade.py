"""Cascade-buy: is buying a sharp drop and holding 12 hours an edge?

The reference bot lists `cascade_buy` as switched off but with +0.94% per 12h and
t=2.03 - the only positive number among his disabled modules. He ships no code
for it, so this is not a replication: it is the natural long-only version of the
idea, frozen in research/CASCADE_PREREG.md before this run.

Two things decide whether such a number is real, and both are easy to get wrong:

1. overlapping windows. If every bar with a drop is an "event" and each is held
   12 hours, the windows overlap 12 deep and the t-statistic is fiction. Here
   events are non-overlapping: while a position is open nothing new is taken.
2. costs. A 12-hour hold pays taker on both sides plus slippage plus funding -
   about 0.19% round trip. That is a fifth of his claimed 0.94% and must be
   charged.

Run: PYTHONPATH=.devlibs python3 bt_cascade.py
"""

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from indicators import atr_series    # noqa: E402

CACHE = os.path.join(HERE, "bt_cache")
COINS = [f[:-5] for f in sorted(os.listdir(CACHE)) if f.endswith(".json")]

DROP_1H_ATR = 2.0
DROP_3H_ATR = 3.5
HOLD = 12
TAKER = 0.00045
SLIP = 0.0005
COST_ROUND = 2 * (TAKER + SLIP)      # in and out
CAPITAL = 1000.0


def load(coin):
    blob = json.load(open(os.path.join(CACHE, coin + ".json")))
    candles = blob["candles"]
    o = [b["o"] for b in candles]
    h = [b["h"] for b in candles]
    l = [b["l"] for b in candles]
    c = [b["c"] for b in candles]
    atr = atr_series(h, l, c)
    off = len(c) - len(atr)
    atr = [None] * off + list(atr)
    # Funding ticks carry millisecond noise in their timestamps
    # ("1791345600013" against a candle at 1791345600000), so an exact integer
    # match silently misses ~98% of the bars. Align to the hour instead.
    hour = {}
    for k, v in (blob.get("funding") or {}).items():
        try:
            hour[int(k) // 3_600_000 * 3_600_000] = float(v)
        except (TypeError, ValueError):
            continue
    fund = [hour.get(int(b["t"]) // 3_600_000 * 3_600_000, 0.0) for b in candles]
    return {"o": o, "c": c, "atr": atr, "fund": fund, "t": [b["t"] for b in candles]}


def events(data):
    """Hours (index of the last bar of the drop) where a cascade happened."""
    out = set()
    for coin, s in data.items():
        atr, c = s["atr"], s["c"]
        for i in range(3, len(c)):
            a = atr[i]
            if not a or a <= 0:
                continue
            if (c[i] - c[i - 1]) / a <= -DROP_1H_ATR:
                out.add(i)
            elif (c[i] - c[i - 3]) / a <= -DROP_3H_ATR:
                out.add(i)
    return out


def trade(data, coin, bar):
    """Return the net result of buying at the next open and selling 12 bars later."""
    s = data[coin]
    entry_i = bar + 1
    exit_i = entry_i + HOLD
    if exit_i >= len(s["o"]):
        return None
    entry, exit_ = s["o"][entry_i], s["o"][exit_i]
    if entry <= 0 or exit_ <= 0:
        return None
    gross = exit_ / entry - 1.0
    funding = sum(s["fund"][k] for k in range(entry_i, exit_i + 1))
    return {"coin": coin, "bar": bar, "gross": gross, "funding": funding,
            "net": gross - COST_ROUND - funding}


def portfolio(data, bar, coins):
    """Equal-weight basket of every coin cascading in this hour."""
    results = [trade(data, c, bar) for c in coins]
    results = [r for r in results if r]
    if not results:
        return None
    net = sum(r["net"] for r in results) / len(results)
    gross = sum(r["gross"] for r in results) / len(results)
    return {"bar": bar, "n": len(results), "net": net, "gross": gross}


def stats(values):
    n = len(values)
    if n == 0:
        return {"n": 0}
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    return {"n": n, "mean_pct": round(100 * mean, 3),
            "median_pct": round(100 * sorted(values)[n // 2], 3),
            "sd_pct": round(100 * sd, 3), "t": round(t, 2),
            "win_pct": round(100.0 * sum(1 for v in values if v > 0) / n, 1),
            "total_pct": round(100 * sum(values), 2)}


def run_all():
    data = {}
    for coin in COINS:
        try:
            data[coin] = load(coin)
        except Exception as exc:
            print("skip %s: %s" % (coin, exc))
    length = min(len(s["c"]) for s in data.values())
    trigger_bars = events(data)
    # who triggered at each hour
    triggers = {}
    for coin, s in data.items():
        for i in range(3, length):
            a = s["atr"][i]
            if not a or a <= 0:
                continue
            if ((s["c"][i] - s["c"][i - 1]) / a <= -DROP_1H_ATR
                    or (s["c"][i] - s["c"][i - 3]) / a <= -DROP_3H_ATR):
                triggers.setdefault(i, []).append(coin)

    # --- portfolio, non-overlapping ---
    bars = sorted(triggers)
    trades = []
    i = 0
    while i < len(bars):
        bar = bars[i]
        row = portfolio(data, bar, triggers[bar])
        if row:
            trades.append(row)
            i = next((k for k in range(i + 1, len(bars)) if bars[k] >= bar + HOLD + 1), len(bars))
        else:
            i += 1

    nets = [t["net"] for t in trades]
    gross_list = [t["gross"] for t in trades]
    half = len(trades) // 2
    total_net = sum(nets)
    best = max(nets) if nets else 0.0
    worst = min(nets) if nets else 0.0

    out = {"events": len(bars), "trades": len(trades),
           "net": stats(nets), "gross": stats(gross_list),
           "h1": stats(nets[:half]), "h2": stats(nets[half:]),
           "avg_coins_per_trade": round(sum(t["n"] for t in trades) / len(trades), 1) if trades else 0,
           "best_trade_pct": round(100 * best, 2),
           "worst_trade_pct": round(100 * worst, 2),
           "cost_round_pct": round(100 * COST_ROUND, 3)}

    # --- per coin, non-overlapping per coin ---
    per_coin = {}
    for coin in data:
        coins_triggers = {}
        s = data[coin]
        for i in range(3, length):
            a = s["atr"][i]
            if not a or a <= 0:
                continue
            if ((s["c"][i] - s["c"][i - 1]) / a <= -DROP_1H_ATR
                    or (s["c"][i] - s["c"][i - 3]) / a <= -DROP_3H_ATR):
                coins_triggers.setdefault(i, []).append(coin)
        rows = []
        last = -10 ** 9
        for bar in sorted(coins_triggers):
            if bar < last + HOLD + 1:
                continue
            r = trade(data, coin, bar)
            if r:
                rows.append(r)
                last = bar
        if rows:
            per_coin[coin] = stats([r["net"] for r in rows])
    out["per_coin"] = per_coin
    return out


if __name__ == "__main__":
    res = run_all()
    print("coins %d, event-hours %d, non-overlapping portfolio trades %d, "
          "avg coins per trade %.1f" % (len(COINS), res["events"], res["trades"],
                                        res["avg_coins_per_trade"]))
    for label in ("net", "gross"):
        s = res[label]
        print("%-5s n=%3d mean %+7.3f%% median %+7.3f%% sd %6.3f%% t %6.2f win %5.1f%% "
              "total %+8.2f%%" % (label, s.get("n", 0), s.get("mean_pct", 0),
                                  s.get("median_pct", 0), s.get("sd_pct", 0),
                                  s.get("t", 0), s.get("win_pct", 0), s.get("total_pct", 0)))
    print("half1: n=%d mean %+.3f%% t %s | half2: n=%d mean %+.3f%% t %s" % (
        res["h1"].get("n", 0), res["h1"].get("mean_pct", 0), res["h1"].get("t"),
        res["h2"].get("n", 0), res["h2"].get("mean_pct", 0), res["h2"].get("t")))
    print("round-trip cost %.3f%%, best trade %+.2f%%, worst %+.2f%%" % (
        res["cost_round_pct"], res["best_trade_pct"], res["worst_trade_pct"]))
    ok = (res["net"].get("mean_pct", 0) > 0 and res["net"].get("t", 0) >= 2.0
          and res["h1"].get("mean_pct", 0) > 0 and res["h2"].get("mean_pct", 0) > 0
          and res["trades"] >= 40
          and (res["net"].get("total_pct", 0) <= 0
               or res["best_trade_pct"] <= 0.25 * res["net"]["total_pct"]))
    print("VERDICT:", "ACCEPT" if ok else "REJECT")
    json.dump(res, open(os.path.join(HERE, "bt_cascade.json"), "w"), indent=1)
