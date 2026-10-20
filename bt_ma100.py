"""Replication of the friend's MA100 trend+turbulence cell.

Cell definition and acceptance criteria were fixed in research/MA100_PREREG.md
before this file first ran. The four runs are the point of the test: the cell, no
filter, each cell separately, and the cell on a wider universe.

Run: python3 bt_ma100.py
"""

import math
import sys

from bt_breakout import COST, load_all, show, stats

HIS_UNIVERSE = ["ETH", "SOL", "AVAX", "ARB", "AAVE", "ADA", "LINK",
                "DOGE", "SUI", "NEAR", "XRP"]

ER_WIN = 20
ER_TREND = 0.25
ATR_WIN = 14
VOL_PCT_WIN = 720          # 30 days of hours
VOL_PCT_CUT = 60.0
EMA_LEN = 100
EMA_DATA = 110             # he feeds 110 closes into a 100-period EMA
STOP_ATR = 2.0
TARGET_ATR = 3.0
HOLD_H = 72
MAX_OPEN = 2


def atr_series(candles, win=ATR_WIN):
    trs = [None] * len(candles)
    for k in range(1, len(candles)):
        h, l, pc = candles[k]["h"], candles[k]["l"], candles[k - 1]["c"]
        trs[k] = max(h - l, abs(h - pc), abs(l - pc))
    out = [None] * len(candles)
    for k in range(win, len(candles)):
        window = trs[k - win + 1:k + 1]
        if all(v is not None for v in window):
            out[k] = sum(window) / win
    return out


def er_series(closes, win=ER_WIN):
    out = [None] * len(closes)
    for k in range(win + 1, len(closes)):
        net = abs(closes[k] - closes[k - win])
        path = sum(abs(closes[j] - closes[j - 1]) for j in range(k - win + 1, k + 1))
        out[k] = net / path if path > 0 else 0.0
    return out


def ema_as_he_does(closes, length=EMA_LEN, feed=EMA_DATA):
    """Re-seeded each bar from the last `feed` closes, exactly like _ema(cl[-110:], 100)."""
    out = [None] * len(closes)
    k = 2.0 / (length + 1)
    for i in range(feed - 1, len(closes)):
        e = closes[i - feed + 1]
        for v in closes[i - feed + 2:i + 1]:
            e = v * k + e * (1.0 - k)
        out[i] = e
    return out


def regime_table(btc):
    """ts -> (is_trend, is_turbulent) or absent when the cell cannot be computed."""
    closes = [c["c"] for c in btc]
    atr = atr_series(btc)
    atrp = [None if atr[k] is None else atr[k] / closes[k] * 100.0
            for k in range(len(btc))]
    er = er_series(closes)
    table = {}
    for k in range(len(btc)):
        if er[k] is None or atrp[k] is None or k - (VOL_PCT_WIN - 1) < ATR_WIN:
            continue
        hist = atrp[k - VOL_PCT_WIN + 1:k + 1]
        pct = 100.0 * sum(1 for x in hist if x <= atrp[k]) / len(hist)
        table[btc[k]["t"]] = (er[k] >= ER_TREND, pct >= VOL_PCT_CUT)
    return table


def candidates(coins, universe, regime, cell):
    """cell: (want_trend, want_turb) or None for no regime filter."""
    out = []
    for coin in universe:
        candles = coins.get(coin)
        if not candles or len(candles) < EMA_DATA + HOLD_H:
            continue
        closes = [c["c"] for c in candles]
        atr = atr_series(candles)
        ema = ema_as_he_does(closes)
        for k in range(EMA_DATA, len(candles) - 1):
            if ema[k] is None or atr[k] is None or closes[k] <= ema[k]:
                continue
            if cell is not None:
                r = regime.get(candles[k]["t"])
                if r is None or r != cell:
                    continue
            out.append({"coin": coin, "i": k, "ts": candles[k]["t"],
                        "entry": closes[k], "atr": atr[k]})
    out.sort(key=lambda c: (c["ts"], c["coin"]))
    return out


def simulate(coins, cands):
    """Max MAX_OPEN positions, one per coin, stop before target inside an hour."""
    open_until = {}          # coin -> exit ts
    taken, skipped_open, skipped_coin = [], 0, 0
    for c in cands:
        candles = coins[c["coin"]]
        if c["coin"] in open_until and open_until[c["coin"]] > c["ts"]:
            skipped_coin += 1
            continue
        live = [v for v in open_until.values() if v > c["ts"]]
        if len(live) >= MAX_OPEN:
            skipped_open += 1
            continue
        entry, atr = c["entry"], c["atr"]
        stop_px, target_px = entry - STOP_ATR * atr, entry + TARGET_ATR * atr
        last = min(c["i"] + HOLD_H, len(candles) - 1)
        exit_px, exit_ts, why = candles[last]["c"], candles[last]["t"], "time"
        for j in range(c["i"] + 1, last + 1):
            if candles[j]["l"] <= stop_px:
                exit_px, exit_ts, why = stop_px, candles[j]["t"], "stop"
                break
            if candles[j]["h"] >= target_px:
                exit_px, exit_ts, why = target_px, candles[j]["t"], "target"
                break
        gross = exit_px / entry - 1.0
        taken.append({"coin": c["coin"], "entry_ts": c["ts"], "exit_ts": exit_ts,
                      "net": gross - COST, "reason": why})
        open_until[c["coin"]] = exit_ts
    return taken, skipped_open, skipped_coin


def halves(trades):
    ts = sorted(t["entry_ts"] for t in trades)
    mid = ts[len(ts) // 2]
    return ([t["net"] for t in trades if t["entry_ts"] < mid],
            [t["net"] for t in trades if t["entry_ts"] >= mid])


def welch(a, b):
    if len(a) < 2 or len(b) < 2:
        return 0.0
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    den = math.sqrt(va / len(a) + vb / len(b))
    return (ma - mb) / den if den > 0 else 0.0


def run(label, coins, universe, regime, cell):
    cands = candidates(coins, universe, regime, cell)
    trades, skip_open, skip_coin = simulate(coins, cands)
    s = stats([t["net"] for t in trades], label)
    h1, h2 = halves(trades)
    sh1, sh2 = stats(h1, "h1"), stats(h2, "h2")
    show(s)
    if s.get("n"):
        print("%s    halves %+.1f%% / %+.1f%%   cands %d  skipped(maxopen) %d  "
              "(same coin) %d" % (" " * 2, sh1["total_pct"], sh2["total_pct"],
                                  len(cands), skip_open, skip_coin))
        reasons = {}
        for t in trades:
            reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
        print("%s    exits %s" % (" " * 2,
                                  ", ".join("%s=%d" % kv for kv in sorted(reasons.items()))))
    return {"label": label, "stats": s, "h1": sh1, "h2": sh2,
            "nets": [t["net"] for t in trades], "n_cands": len(cands)}


def main():
    coins = load_all()
    btc = coins.get("BTC")
    if not btc:
        print("no BTC in cache")
        return 1
    regime = regime_table(btc)
    first_ts = min(regime)
    last_ts = max(regime)
    hours = (last_ts - first_ts) / 3600000.0
    print("his universe: %d coins   wider: %d coins" % (len(HIS_UNIVERSE), len(coins)))
    print("regime computable on %d hours (~%.0f days) — needs %.0f days of history "
          "before the first cell" % (len(regime), hours / 24.0, VOL_PCT_WIN / 24.0))
    print("cost per trade %.2f%%   MA100: close > EMA100, stop %.0fxATR, "
          "target %.0fxATR, hold %dh, max %d open"
          % (100 * COST, STOP_ATR, TARGET_ATR, HOLD_H, MAX_OPEN))
    print()

    a = run("A. cell trend+turb (his claim)", coins, HIS_UNIVERSE, regime, (True, True))
    print()
    b = run("B. no regime filter", coins, HIS_UNIVERSE, regime, None)
    print()
    for want, name in (((False, False), "range/slow"), ((False, True), "range/turb"),
                       ((True, False), "trend/slow")):
        run("C. cell %s" % name, coins, HIS_UNIVERSE, regime, want)
    print()
    d = run("D. cell trend+turb, 52 coins", coins, list(coins), regime, (True, True))

    print()
    print("=" * 96)
    print("ACCEPTANCE (fixed in research/MA100_PREREG.md before the run)")
    print("=" * 96)
    sa, sb = a["stats"], b["stats"]
    diff_t = welch(a["nets"], b["nets"]) if a["nets"] and b["nets"] else 0.0
    checks = [
        ("1. mean net per trade > 0 in A", bool(sa.get("n")) and sa["mean_pct"] > 0,
         "%+.3f%%" % (sa.get("mean_pct") or 0.0)),
        ("2. both halves positive in A",
         bool(sa.get("n")) and a["h1"]["total_pct"] > 0 and a["h2"]["total_pct"] > 0,
         "%+.1f%% / %+.1f%%" % (a["h1"].get("total_pct", 0), a["h2"].get("total_pct", 0))),
        ("3. portfolio positive in A", bool(sa.get("n")) and sa["total_pct"] > 0,
         "%+.1f%%" % (sa.get("total_pct") or 0.0)),
        ("4. cell beats no filter (t > 2)",
         bool(sa.get("n")) and bool(sb.get("n")) and diff_t > 2.0,
         "A %+.3f%% vs B %+.3f%% per trade, t of diff %+.2f"
         % ((sa.get("mean_pct") or 0.0), (sb.get("mean_pct") or 0.0), diff_t)),
    ]
    for label, ok, detail in checks:
        print("  %s  %-36s %s" % ("PASS" if ok else "FAIL", label, detail))
    print()
    print("VERDICT: %s" % ("accepted" if all(c[1] for c in checks) else "REJECTED"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
