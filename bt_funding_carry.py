"""Funding carry: can the hourly funding rate be earned, not just paid?

Hyperliquid settles funding every hour. Coins with high positive funding are
expensive to hold long, so short them and collect; coins with negative funding
pay longs, so buy those. Legs are equal notional, so the basket is roughly delta
neutral and the return has to come from funding - not from direction.

Frozen in research/FUNDING_CARRY_PREREG.md before this run: weekly rebalance,
top/bottom 3 by trailing 24h funding, 0.095% per side per leg, funding charged
from the actual hourly rate.

Run: PYTHONPATH=.devlibs python3 bt_funding_carry.py
"""

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "bt_cache")
FEE_SIDE = 0.00045 + 0.0005      # taker + slippage, per side per leg
CAPITAL = 1000.0
K = 3
HOLD_H = 168                     # one week
SIGNAL_H = 24
GROSS = 1.0                      # total notional across both legs


def load(coin):
    blob = json.load(open(os.path.join(CACHE, coin + ".json")))
    close, fund, ts = {}, {}, {}
    for b in blob["candles"]:
        t = int(b["t"]) // 3_600_000 * 3_600_000
        close[t] = b["c"]
    for k, v in (blob.get("funding") or {}).items():
        t = int(k) // 3_600_000 * 3_600_000
        fund[t] = float(v)
    for b in blob["candles"]:
        ts[int(b["t"]) // 3_600_000 * 3_600_000] = None
    return close, fund


def build():
    coins = {}
    for f in sorted(os.listdir(CACHE)):
        if not f.endswith(".json"):
            continue
        try:
            coins[f[:-5]] = load(f[:-5])
        except Exception:
            continue
    # master timeline: the coin with the most complete hourly series
    best = max(coins, key=lambda c: len(coins[c][0]))
    timeline = sorted(coins[best][0])
    return coins, timeline


def weekly(coins, timeline, hold=HOLD_H, k=K, label="неделя"):
    """Rebalance every `hold` hours; record per-period funding, price and cost.

    Every leg is charged 0.095% per side (taker + slippage) on the way in and on
    the way out, funding accrues from the actual hourly rate, and the basket is
    marked to market each hour so the drawdown is a real drawdown.
    """
    equity = CAPITAL
    peak = equity
    max_dd = 0.0
    weeks = []
    prev = {}                      # coin -> (side, notional_frac, entry_price)
    per_coin = {}
    idx = SIGNAL_H
    while idx + hold < len(timeline):
        now = timeline[idx]
        scores = []
        for coin, (close, fund) in coins.items():
            if now not in close:
                continue
            vals = [fund.get(timeline[j]) for j in range(idx - SIGNAL_H + 1, idx + 1)]
            vals = [v for v in vals if v is not None]
            if len(vals) < SIGNAL_H // 2:
                continue
            scores.append((sum(vals), coin))
        scores.sort()
        if len(scores) < 2 * k:
            idx += hold
            continue
        target = {}
        for _, coin in scores[:k]:
            target[coin] = ("long", GROSS / 2 / k)
        for _, coin in scores[-k:]:
            target[coin] = ("short", GROSS / 2 / k)

        week = {"funding": 0.0, "price": 0.0, "price_long": 0.0,
                "price_short": 0.0, "cost": 0.0, "ts": now}

        def close_leg(coin, px):
            side, notional, entry = prev.pop(coin)
            pnl = notional * CAPITAL * (px / entry - 1.0) * (1 if side == "long" else -1)
            cost = notional * CAPITAL * FEE_SIDE
            week["price"] += pnl
            week["price_" + side] += pnl
            week["cost"] += cost
            per_coin[coin] = per_coin.get(coin, 0.0) + pnl - cost

        for coin in list(prev):
            if coin not in target:
                close_leg(coin, coins[coin][0].get(now, prev[coin][2]))

        for coin, (side, notional) in target.items():
            px = coins[coin][0].get(now)
            if not px:
                continue
            if coin in prev:
                if prev[coin][0] == side:
                    continue
                close_leg(coin, px)
            week["cost"] += notional * CAPITAL * FEE_SIDE
            per_coin[coin] = per_coin.get(coin, 0.0) - notional * CAPITAL * FEE_SIDE
            prev[coin] = (side, notional, px)

        equity_start = equity + 0.0
        # the costs of this rebalance are paid now
        equity -= week["cost"]
        for j in range(idx, min(idx + hold, len(timeline))):
            t = timeline[j]
            fund_hour = 0.0
            mtm = 0.0
            for coin, (side, notional, entry) in prev.items():
                f = coins[coin][1].get(t)
                if f is not None:
                    sign = 1 if side == "short" else -1
                    fund_hour += notional * CAPITAL * sign * f
                px = coins[coin][0].get(t, entry)
                mtm += notional * CAPITAL * (px / entry - 1.0) * (1 if side == "long" else -1)
            week["funding"] += fund_hour
            equity += fund_hour
            mark = equity + mtm
            peak = max(peak, mark)
            if peak > 0:
                max_dd = max(max_dd, 1.0 - mark / peak)
        # realise the price leg at the end of the hold
        for coin, (side, notional, entry) in list(prev.items()):
            px = coins[coin][0].get(timeline[min(idx + hold, len(timeline) - 1)], entry)
            pnl = notional * CAPITAL * (px / entry - 1.0) * (1 if side == "long" else -1)
            cost = notional * CAPITAL * FEE_SIDE
            week["price"] += pnl
            week["price_" + side] += pnl
            week["cost"] += cost
            equity += pnl - cost
            per_coin[coin] = per_coin.get(coin, 0.0) + pnl - cost
            # reopen at the new price with the same side for the next period
            prev[coin] = (side, notional, px)
        week["net"] = week["funding"] + week["price"] - week["cost"] - (equity_start - equity_start)
        week["equity_end"] = equity
        weeks.append(week)
        idx += hold

    nets = [w["net"] for w in weeks]
    n = len(nets)
    mean = sum(nets) / n if n else 0.0
    sd = math.sqrt(sum((v - mean) ** 2 for v in nets) / (n - 1)) if n > 1 else 0.0
    t = mean / (sd / math.sqrt(n)) if sd > 0 else 0.0
    half = n // 2
    def mean_of(xs):
        return sum(xs) / len(xs) if xs else 0.0
    total_fund = sum(w["funding"] for w in weeks)
    total_price = sum(w["price"] for w in weeks)
    total_cost = sum(w["cost"] for w in weeks)
    net_total = total_fund + total_price - total_cost
    best_coin = max(per_coin.items(), key=lambda kv: abs(kv[1])) if per_coin else ("", 0.0)
    return {"label": label, "weeks": n,
            "mean_usd": round(mean, 2), "mean_pct": round(100 * mean / CAPITAL, 3),
            "median_pct": round(100 * sorted(nets)[n // 2] / CAPITAL, 3) if n else 0.0,
            "sd_pct": round(100 * sd / CAPITAL, 3), "t": round(t, 2),
            "win_pct": round(100.0 * sum(1 for v in nets if v > 0) / n, 1) if n else 0.0,
            "net_total_pct": round(100 * net_total / CAPITAL, 2),
            "funding_pct": round(100 * total_fund / CAPITAL, 2),
            "price_pct": round(100 * total_price / CAPITAL, 2),
            "cost_pct": round(100 * total_cost / CAPITAL, 2),
            "price_long_pct": round(100 * sum(w["price_long"] for w in weeks) / CAPITAL, 2),
            "price_short_pct": round(100 * sum(w["price_short"] for w in weeks) / CAPITAL, 2),
            "h1_mean_pct": round(100 * mean_of(nets[:half]) / CAPITAL, 3),
            "h2_mean_pct": round(100 * mean_of(nets[half:]) / CAPITAL, 3),
            "max_dd_pct": round(100 * max_dd, 2),
            "best_coin": best_coin[0], "best_coin_usd": round(best_coin[1], 2),
            "best_coin_share": round(100 * best_coin[1] / net_total, 1) if net_total else 0.0,
            "worst_week_pct": round(100 * min(nets) / CAPITAL, 2) if n else 0.0}


if __name__ == "__main__":
    coins, timeline = build()
    print("coins %d, hours %d (%.0f дней)" % (len(coins), len(timeline), len(timeline) / 24))
    res = {}
    for label, kw in (("неделя K=3", {}),
                      ("день K=3", {"hold": 24}),
                      ("неделя K=5", {"k": 5})):
        r = weekly(coins, timeline, **kw)
        res[label] = r
        print("%-12s недель %2d  средн %+7.3f%%  t %5.2f  win %4.1f%%  net %+7.2f%%  "
              "(фандинг %+6.2f%%, цена %+6.2f%%, издержки -%.2f%%)  DD %.2f%%" % (
                  label, r["weeks"], r["mean_pct"], r["t"], r["win_pct"],
                  r["net_total_pct"], r["funding_pct"], r["price_pct"], r["cost_pct"],
                  r["max_dd_pct"]))
        print("             цена: лонги %+.2f%%, шорты %+.2f%%" % (
                  r["price_long_pct"], r["price_short_pct"]))
        print("             половины %+.3f%% / %+.3f%%  худшая неделя %+.2f%%  "
              "лучшая монета %s (%.1f%% PnL)" % (
                  r["h1_mean_pct"], r["h2_mean_pct"], r["worst_week_pct"],
                  r["best_coin"], r["best_coin_share"]))
    base = res["неделя K=3"]
    ok = (base["mean_pct"] > 0 and base["t"] >= 2.0
          and base["h1_mean_pct"] > 0 and base["h2_mean_pct"] > 0
          and base["funding_pct"] > 0.5 * max(base["net_total_pct"], 1e-9)
          and abs(base["best_coin_share"]) <= 35 and base["max_dd_pct"] <= 15)
    print("VERDICT:", "ACCEPT" if ok else "REJECT")
    json.dump(res, open(os.path.join(HERE, "bt_funding_carry.json"), "w"), indent=1)
