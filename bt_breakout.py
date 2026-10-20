"""Independent replication of the friend's 120h breakout rule.

Spec and acceptance criteria were fixed in research/BREAKOUT_PREREG.md before
this file first ran. Do not change the rule after reading the numbers; if the
rule changes, the pre-registration has to be rewritten first.

Run: python3 bt_breakout.py
Reads bt_cache/*.json (52 coins, 5000 hourly candles each), writes nothing.
"""

import glob
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "bt_cache")

LOOKBACK_H = 120        # breakout window
VOL_MULT = 5.0
VOL_WIN = 20
OVERHEAT = 1.08         # close <= MA20 * 1.08
RETEST_WINDOW_H = 6     # his limit-on-retest window
TARGET = 0.03
STOP = 0.04
HOLD_H = 48
FEE = 0.00045
SLIP = 0.0005
COST = 2.0 * (FEE + SLIP)      # 0.19% round trip
MAX_OPEN = 2


def load_all():
    coins = {}
    for path in sorted(glob.glob(os.path.join(CACHE, "*.json"))):
        with open(path) as fh:
            d = json.load(fh)
        candles = d.get("candles") or []
        if len(candles) > LOOKBACK_H + HOLD_H + 10:
            coins[d["coin"]] = candles
    return coins


def rolling_mean(vals, win):
    """mean of the `win` values strictly before each index."""
    out = [None] * len(vals)
    acc = 0.0
    for i, v in enumerate(vals):
        if i >= win:
            out[i] = acc / win
        acc += v
        if i >= win:
            acc -= vals[i - win]
    return out


def btc_regime(candles):
    """ts -> True when BTC closed above its 50h mean on that hour."""
    closes = [c["c"] for c in candles]
    ma = rolling_mean(closes, 50)
    regime = {}
    for i, c in enumerate(candles):
        if ma[i] is not None:
            regime[c["t"]] = closes[i] > ma[i]
    return regime


def find_signals(candles, regime):
    closes = [c["c"] for c in candles]
    highs = [c["h"] for c in candles]
    vols = [c["v"] for c in candles]
    ma20 = rolling_mean(closes, 20)
    vavg = rolling_mean(vols, VOL_WIN)
    out = []
    for i in range(LOOKBACK_H, len(candles) - 1):
        if vavg[i] is None or ma20[i] is None:
            continue
        if closes[i] <= max(highs[i - LOOKBACK_H:i]):
            continue
        if vols[i] < VOL_MULT * vavg[i]:
            continue
        if not regime.get(candles[i]["t"]):
            continue
        if closes[i] > ma20[i] * OVERHEAT:
            continue
        out.append({"i": i, "level": max(highs[i - LOOKBACK_H:i]),
                    "ts": candles[i]["t"]})
    return out


def exit_trade(candles, entry_idx, entry_px):
    """Long from `entry_px`; stop checked before target inside the same hour."""
    target_px = entry_px * (1.0 + TARGET)
    stop_px = entry_px * (1.0 - STOP)
    last = min(entry_idx + HOLD_H, len(candles))
    for j in range(entry_idx, last):
        bar = candles[j]
        if bar["l"] <= stop_px:
            return stop_px, candles[j]["t"], "stop"
        if bar["h"] >= target_px:
            return target_px, candles[j]["t"], "target"
    j = last - 1
    return candles[j]["c"], candles[j]["t"], "time"


def build_trades(coins, regime, model):
    """model: 'market' (fill next open) or 'retest' (his limit, 6h window)."""
    trades = []
    unfilled = 0
    for coin, candles in coins.items():
        for sig in find_signals(candles, regime):
            i = sig["i"]
            if model == "market":
                entry_idx = i + 1
                entry_px = candles[entry_idx]["o"]
            else:
                entry_idx = None
                entry_px = sig["level"]
                stop_at = min(i + 1 + RETEST_WINDOW_H, len(candles))
                for j in range(i + 1, stop_at):
                    if candles[j]["l"] <= sig["level"]:
                        entry_idx = j
                        break
                if entry_idx is None:
                    unfilled += 1
                    continue
            exit_px, exit_ts, reason = exit_trade(candles, entry_idx, entry_px)
            gross = exit_px / entry_px - 1.0
            trades.append({
                "coin": coin, "entry_ts": candles[entry_idx]["t"],
                "exit_ts": exit_ts, "gross": gross, "net": gross - COST,
                "reason": reason})
    trades.sort(key=lambda t: t["entry_ts"])
    return trades, unfilled


def apply_max_open(trades):
    """Take signals in time order, at most MAX_OPEN live at once."""
    taken, busy, skipped = [], [], 0
    for t in trades:
        busy = [b for b in busy if b > t["entry_ts"]]
        if len(busy) >= MAX_OPEN:
            skipped += 1
            continue
        busy.append(t["exit_ts"])
        taken.append(t)
    return taken, skipped


def stats(vals, label):
    n = len(vals)
    if not n:
        return {"label": label, "n": 0}
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    t = mean / (sd / math.sqrt(n)) if sd > 0 and n > 1 else 0.0
    return {"label": label, "n": n, "wins": len(wins), "winrate": 100.0 * len(wins) / n,
            "mean_pct": 100.0 * mean, "total_pct": 100.0 * sum(vals),
            "pf": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
            "t": t, "sd_pct": 100.0 * sd,
            "profit_factor_note": ""}


def show(s, indent=""):
    if not s.get("n"):
        print("%s%-34s n=0" % (indent, s["label"]))
        return
    print("%s%-34s n=%-4d WR %5.1f%%  avg %+6.2f%%  total %+8.2f%%  PF %5.2f  t %+5.2f"
          % (indent, s["label"], s["n"], s["winrate"], s["mean_pct"],
             s["total_pct"], s["pf"], s["t"]))


def report(model, coins, regime):
    trades, unfilled = build_trades(coins, regime, model)
    print()
    print("=" * 96)
    print("MODEL %s   signals=%d  filled=%d  unfilled=%d"
          % (model, len(trades) + (unfilled if model == "retest" else 0),
             len(trades) if model == "retest" else len(trades), unfilled))
    print("=" * 96)
    if not trades:
        print("  no trades")
        return None

    all_net = [t["net"] for t in trades]
    overall = stats(all_net, "per trade (all signals)")
    show(overall)

    if model == "retest" and unfilled:
        with_zero = all_net + [0.0] * unfilled
        show(stats(with_zero, "per signal (unfilled = 0)"))
        print("      fill rate %.1f%%" % (100.0 * len(trades) / (len(trades) + unfilled)))

    ts = [t["entry_ts"] for t in trades]
    mid = sorted(ts)[len(ts) // 2]
    h1 = stats([t["net"] for t in trades if t["entry_ts"] < mid], "first half")
    h2 = stats([t["net"] for t in trades if t["entry_ts"] >= mid], "second half")
    show(h1)
    show(h2)

    taken, skipped = apply_max_open(trades)
    port = stats([t["net"] for t in taken], "portfolio (MAX_OPEN=%d)" % MAX_OPEN)
    show(port)
    print("      skipped by MAX_OPEN: %d" % skipped)

    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    print("      exits: %s" % ", ".join("%s=%d" % kv for kv in sorted(reasons.items())))

    by_coin = {}
    for t in trades:
        by_coin[t["coin"]] = by_coin.get(t["coin"], 0.0) + t["net"]
    top = sorted(by_coin.items(), key=lambda kv: -kv[1])[:3]
    total = sum(all_net)
    print("      top coins: %s" % ", ".join("%s %+.1f%%" % (c, 100 * v) for c, v in top))
    if total != 0:
        best = max(all_net)
        print("      best trade %+.2f%% = %.0f%% of total result"
              % (100 * best, 100 * best / total))
    return {"overall": overall, "h1": h1, "h2": h2, "port": port}


def main():
    coins = load_all()
    if "BTC" not in coins:
        print("no BTC in cache, cannot apply the regime filter")
        return 1
    regime = btc_regime(coins["BTC"])
    span = len(coins["BTC"])
    print("coins=%d  bars/coin=%d (~%.0f days hourly)"
          % (len(coins), span, span / 24.0))
    print("cost per trade %.2f%%   rule: close > %dh high, vol >= %.0fx, "
          "BTC > MA50, close <= MA20*%.2f" % (100 * COST, LOOKBACK_H, VOL_MULT, OVERHEAT))
    print("exit: +%.0f%% / -%.0f%% / %dh" % (100 * TARGET, 100 * STOP, HOLD_H))

    m_market = report("market", coins, regime)
    m_retest = report("retest", coins, regime)

    print()
    print("=" * 96)
    print("ACCEPTANCE (all four required, fixed in research/BREAKOUT_PREREG.md)")
    print("=" * 96)
    checks = [
        ("1. PF >= 1.2 in his fill model",
         bool(m_retest) and m_retest["overall"]["pf"] >= 1.2,
         "PF %.2f" % (m_retest["overall"]["pf"] if m_retest else 0.0)),
        ("2. both halves positive",
         bool(m_retest) and m_retest["h1"]["total_pct"] > 0 and m_retest["h2"]["total_pct"] > 0,
         "%+.1f%% / %+.1f%%" % (m_retest["h1"]["total_pct"], m_retest["h2"]["total_pct"])
         if m_retest else "n/a"),
        ("3. positive on the portfolio",
         bool(m_retest) and m_retest["port"]["total_pct"] > 0,
         "%+.1f%%" % m_retest["port"]["total_pct"] if m_retest else "n/a"),
        ("4. sign holds with market entry",
         bool(m_market) and m_market["overall"]["mean_pct"] > 0,
         "%+.2f%% per trade" % m_market["overall"]["mean_pct"] if m_market else "n/a"),
    ]
    for label, ok, detail in checks:
        print("  %s  %-34s %s" % ("PASS" if ok else "FAIL", label, detail))
    print()
    print("VERDICT: %s" % ("accepted" if all(c[1] for c in checks) else "REJECTED"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
