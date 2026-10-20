"""What 30 grid cycles do to his account, measured on real prices.

Part 1 measured the trade itself: target +1.5%, stop at the grid bottom,
entry only while ER(20) < 0.35. The result is a very high winrate - which is
exactly why his dashboard can show 10 wins out of 10 while the account bleeds.
Winrate is not the question. The question is whether the wins are big enough
to pay for the losses, and how a sequence of them leaves the account.

Part 2 answers that: the distribution of outcomes over 30 cycles, on his own
sizing and his own 45% risk cap.
"""
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CACHE = "bt_cache"
TARGET = 0.015
ER_MAX = 0.35
STOP = 0.26
FEE_ROUND = 0.0007

EQUITY0 = 100.0
LOT_NOTIONAL = 45.0        # his average position: 44.81, 44.91, 90 for a double lot
LOTS = 30
ACCOUNTS = 200000


def candles(coin):
    d = json.load(open(os.path.join(CACHE, coin + ".json")))
    return d["candles"], d.get("funding") or {}


def er_series(closes, period=20):
    out = [None] * len(closes)
    for i in range(period, len(closes)):
        w = closes[i - period:i + 1]
        net = abs(w[-1] - w[0])
        path = sum(abs(w[j] - w[j - 1]) for j in range(1, len(w)))
        out[i] = (net / path) if path > 0 else 0.0
    return out


def funding_sum(funding, t0, t1):
    total = 0.0
    for k, v in funding.items():
        try:
            ts = int(k)
        except (TypeError, ValueError):
            continue
        if t0 <= ts < t1:
            total += float(v)
    return total


def resolve(closes, highs, lows, times, fund, start, horizon):
    """First barrier wins. Returns (kind, hours, return_before_costs).

    The low is checked before the high inside a bar, so a bar that touches both
    is scored as a loss. Without tick data that is the honest reading.
    """
    entry = closes[start]
    up = entry * (1.0 + TARGET)
    down = entry * (1.0 - STOP)
    end = min(start + horizon, len(closes) - 1)
    for i in range(start + 1, end + 1):
        if lows[i] <= down:
            return "loss", i - start, -STOP
        if highs[i] >= up:
            return "win", i - start, TARGET
    return "time", end - start, (closes[end] / entry - 1.0)


def measure(horizon):
    coins = sorted(f[:-5] for f in os.listdir(CACHE) if f.endswith(".json"))
    tally = {"win": 0, "loss": 0, "time": 0}
    rets = {"win": [], "loss": [], "time": []}
    hours = {"win": [], "loss": [], "time": []}
    for coin in coins:
        cs, fund = candles(coin)
        closes = [c["c"] for c in cs]
        highs = [c["h"] for c in cs]
        lows = [c["l"] for c in cs]
        times = [c["t"] for c in cs]
        ers = er_series(closes)
        for i in range(20, len(closes) - 2):
            if ers[i] is None or ers[i] >= ER_MAX:
                continue
            kind, h, ret = resolve(closes, highs, lows, times, fund, i, horizon)
            if kind == "time":
                ret = ret - FEE_ROUND - funding_sum(fund, times[i], times[min(i + h, len(times) - 1)])
            else:
                ret = ret - FEE_ROUND
            tally[kind] += 1
            rets[kind].append(ret)
            hours[kind].append(h)
    n = sum(tally.values())
    out = {"n": n}
    for k in tally:
        out[k] = tally[k]
        out[k + "_pct"] = tally[k] / n
        out[k + "_avg"] = (sum(rets[k]) / len(rets[k])) if rets[k] else 0.0
        out[k + "_h"] = (sum(hours[k]) / len(hours[k])) if hours[k] else 0.0
    out["ev"] = sum(rets["win"] + rets["loss"] + rets["time"]) / n
    return out


def monte_carlo(m, correlated_lots=1, accounts=ACCOUNTS, lots=LOTS, seed=7):
    """30 cycles, one lot at a time, drawn from the measured distribution.

    correlated_lots models the real grid: when a coin trends, every lot in it
    is stopped at once, so one bad draw can cost several lots at once.
    """
    rng = random.Random(seed)
    p_win, p_loss, p_time = m["win_pct"], m["loss_pct"], m["time_pct"]
    w, l, t = m["win_avg"], m["loss_avg"], m["time_avg"]
    finals = []
    maxdds = []
    ruined = 0
    for _ in range(accounts):
        eq = EQUITY0
        peak = eq
        worst = 0.0
        i = 0
        while i < lots and eq > EQUITY0 * 0.10:
            r = rng.random()
            if r < p_win:
                ret = w
            elif r < p_win + p_loss:
                ret = l
            else:
                ret = t
            lots_lost = correlated_lots if ret == l else 1
            eq += ret * LOT_NOTIONAL * lots_lost
            peak = max(peak, eq)
            worst = max(worst, (peak - eq) / peak)
            i += lots_lost
        finals.append(eq)
        maxdds.append(worst)
        if eq <= EQUITY0 * 0.10:
            ruined += 1
    finals.sort()
    maxdds.sort()

    def q(a, f):
        return a[min(len(a) - 1, int(len(a) * f))]

    return {
        "median": q(finals, 0.50), "p5": q(finals, 0.05), "p95": q(finals, 0.95),
        "p_push": q(finals, 0.25), "p_pull": q(finals, 0.75),
        "worst": finals[0], "best": finals[-1],
        "dd_median": q(maxdds, 0.50), "dd_p95": q(maxdds, 0.95),
        "ruin": ruined / accounts,
        "prob_dd20": sum(1 for d in maxdds if d >= 0.20) / accounts,
    }


def main():
    print("=" * 78)
    print("PART 1 - HIS TRADE ON REAL PRICES: 52 coins, 5000 hourly candles each")
    print("         entry only while ER(20) < %.2f, target +%.1f%%, stop -%.0f%%" % (
        ER_MAX, TARGET * 100, STOP * 100))
    print("=" * 78)
    m = measure(horizon=168)
    print()
    print("  resolved within a week : %d samples" % m["n"])
    print("  reached +1.5%%  (win)   : %7d  %5.1f%%   average +%.2f%%" % (
        m["win"], m["win_pct"] * 100, m["win_avg"] * 100))
    print("  hit the stop  (loss)   : %7d  %5.1f%%   average %.2f%%" % (
        m["loss"], m["loss_pct"] * 100, m["loss_avg"] * 100))
    print("  still open    (stuck)  : %7d  %5.1f%%   average %+.2f%%  after %.0f hours" % (
        m["time"], m["time_pct"] * 100, m["time_avg"] * 100, m["time_h"]))
    print()
    print("  EV per lot             : %+.3f%% of notional = %+.2f%% of a $100 account" % (
        m["ev"] * 100, m["ev"] * LOT_NOTIONAL))
    print("  average win takes      : %.0f hours" % m["win_h"])
    print()

    print("=" * 78)
    print("PART 2 - 30 CYCLES, %d simulated accounts, his sizing" % ACCOUNTS)
    print("         lot = $%.0f notional, account = $%.0f" % (LOT_NOTIONAL, EQUITY0))
    print("=" * 78)
    print()
    for corr in (1, 3, 6):
        s = monte_carlo(m, correlated_lots=corr)
        label = {1: "independence (1 lot per event)",
                 3: "3 lots stopped together",
                 6: "6 lots stopped together (full grid)"}[corr]
        print("  %s" % label)
        print("    медиана            $%6.2f" % s["median"])
        print("    25 / 75 перцентиль $%6.2f / $%6.2f" % (s["p_push"], s["p_pull"]))
        print("    5 / 95 перцентиль  $%6.2f / $%6.2f" % (s["p5"], s["p95"]))
        print("    худший из %d      $%6.2f" % (ACCOUNTS, s["worst"]))
        print("    просадка медиана   %5.1f%%   p95 %5.1f%%" % (
            s["dd_median"] * 100, s["dd_p95"] * 100))
        print("    P(просадка > 20%%)  %5.1f%%" % (s["prob_dd20"] * 100))
        print("    P(счёт сгорел)     %5.2f%%" % (s["ruin"] * 100))
        print()


if __name__ == "__main__":
    main()
