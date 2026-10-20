"""What his grid actually is, measured on real prices.

His live state gives the exact shape of the trade: target +1.5% from entry,
stop at the grid bottom, 20-29% below entry. His own dashboard reported
10 wins out of 10 and a floating -$19.08, which looks contradictory and is not:
a grid with a +1.5% target and a -26% stop wins almost every time it is
allowed to resolve, and pays for all of it on the one that does not.

This script measures, on 52 coins of real hourly data, the only number that
decides whether that profile survives: how often price reaches +1.5% before
it falls to the stop, and what the expected value per lot is.

His entry filter is ER(20) < 0.35 - he only builds grids in ranging markets.
So the probability is computed only over those hours, and also over all hours,
to show whether the filter helps at all.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CACHE = "bt_cache"
TARGET = 0.015          # his target, measured from his own open positions
ER_MAX = 0.35           # his GRID_ER_MAX
HORIZON = 168           # hours we are willing to wait before marking to market
FEE_ROUND = 0.0007      # maker fee in and out, roughly


def candles(coin):
    d = json.load(open(os.path.join(CACHE, coin + ".json")))
    return d["candles"], d.get("funding") or {}


def er_series(closes, period=20):
    """Kaufman efficiency ratio: net move over the sum of the moves."""
    out = [None] * len(closes)
    for i in range(period, len(closes)):
        window = closes[i - period:i + 1]
        net = abs(window[-1] - window[0])
        path = sum(abs(window[j] - window[j - 1]) for j in range(1, len(window)))
        out[i] = (net / path) if path > 0 else 0.0
    return out


def funding_sum(funding, t0, t1):
    """Hourly funding paid by a long between two timestamps."""
    total = 0.0
    for k, v in funding.items():
        try:
            ts = int(k)
        except (TypeError, ValueError):
            continue
        if t0 <= ts < t1:
            total += float(v)
    return total


def barrier(closes, highs, lows, times, start, stop_pct, horizon):
    """Walk forward from `start`. Returns (outcome, hours, pnl_pct).

    outcome is 'win', 'loss' or 'time' (neither barrier reached).
    We check the low first inside each bar, so a bar that would touch both is
    scored as a loss - the pessimistic reading, which is the honest one when
    there is no tick data to order the two.
    """
    entry = closes[start]
    up = entry * (1.0 + TARGET)
    down = entry * (1.0 - stop_pct)
    end = min(start + horizon, len(closes) - 1)
    for i in range(start + 1, end + 1):
        if lows[i] <= down:
            return "loss", i - start, -stop_pct
        if highs[i] >= up:
            return "win", i - start, TARGET
    return "time", end - start, (closes[end] / entry - 1.0)


def main():
    coins = sorted(f[:-5] for f in os.listdir(CACHE) if f.endswith(".json"))
    stops = [0.20, 0.26, 0.29]

    print("=" * 74)
    print("HIS TRADE, MEASURED: target +1.5%, stop -20..-29%, entry only when ER<0.35")
    print("=" * 74)
    print()

    for stop_pct in stops:
        stats = {"win": 0, "loss": 0, "time": 0}
        pnl = 0.0
        hours_win = []
        samples = 0
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
                kind, hours, ret = barrier(closes, highs, lows, times, i, stop_pct, HORIZON)
                if kind == "time":
                    # still open: charge funding and fees on the exit
                    ret = ret - FEE_ROUND - funding_sum(fund, times[i], times[min(i + hours, len(times) - 1)])
                elif kind == "win":
                    ret = ret - FEE_ROUND
                    hours_win.append(hours)
                else:
                    ret = ret - FEE_ROUND
                stats[kind] += 1
                pnl += ret
                samples += 1

        winrate = 100.0 * stats["win"] / samples
        ev = 100.0 * pnl / samples
        be = 100.0 * stop_pct / (stop_pct + TARGET)
        avg_h = (sum(hours_win) / len(hours_win)) if hours_win else 0
        print("stop -%d%%" % round(stop_pct * 100))
        print("  samples      %6d   (every eligible hour on 52 coins)" % samples)
        print("  win / loss   %6d / %6d   (%.1f%% / %.1f%%)" % (
            stats["win"], stats["loss"], winrate, 100.0 - winrate))
        print("  still open   %6d   (%.1f%%)" % (stats["time"], 100.0 * stats["time"] / samples))
        print("  win needs    %5.1f hours on average" % avg_h)
        print("  EV per lot   %+6.3f%%   break-even needs %.1f%% winrate" % (ev, be))
        print("  at 6 lots    %+6.2f%% of the account per full grid" % (ev * 6))
        print()

    print("=" * 74)
    print("WHAT 30 CYCLES OF THAT LOOKS LIKE")
    print("=" * 74)


if __name__ == "__main__":
    main()
