"""Market-neutral pairs: long the strong leg, short the weak one.

The reference bot rates this its best-validated module: +0.5% per 48h over
n=1584, correlation to BTC about zero. Its live journal shows a concrete
example - "long ARB / short ADA, over 72h: +1.3% against -7.1%" - so the entry
is relative strength over a 72 hour lookback, held for a day or two.

Because both legs carry the same notional, the market cancels out and only the
spread between the two coins matters. That is the whole point: every strategy
we have tried so far was a bet on direction, and direction is what kept losing.

The inverted variant (long the loser, short the winner) is tested too, because
the reference bot notes that the sign of such mean-reversion tests keeps
flipping.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

COINS = open("bt_coins_all.txt").read().strip().split(",")
TAKER = 0.00045


def load():
    out = {}
    for coin in COINS:
        p = os.path.join("bt_cache", coin + ".json")
        if os.path.exists(p):
            out[coin] = [b["c"] for b in json.load(open(p))["candles"]]
    return out


def run(lookback=72, hold=24, n_pairs=3, exposure=0.30, invert=False,
        capital=10000.0, data=None, slip=0.0005, bar_range=None):
    px = data or load()
    names = sorted(px)
    length = min(len(px[n]) for n in names)
    lo, hi = (0, length) if bar_range is None else bar_range

    equity = capital
    peak = capital
    max_dd = 0.0
    legs_pnl = []
    open_pos = []
    series = []          # equity per bar, for correlation to the market
    market = []          # equal-weight basket per bar

    warm = max(lookback, 24) + 1
    for t in range(max(warm, lo), min(length, hi)):
        still = []
        for pos in open_pos:
            if t >= pos["close_bar"]:
                pnl = 0.0
                for leg in pos["legs"]:
                    c1 = px[leg["sym"]][t]
                    ret = (c1 / leg["entry_px"] - 1.0) * (1 if leg["side"] == "long" else -1)
                    fee = leg["notional"] * (TAKER + slip) * (1.0 + c1 / leg["entry_px"])
                    legs_pnl.append(leg["notional"] * ret - fee)
                    pnl += leg["notional"] * ret - fee
                equity += pnl
            else:
                still.append(pos)
        open_pos = still

        if (t - warm) % hold == 0 and t - lookback >= 0:
            rets = {}
            for n in names:
                rets[n] = px[n][t] / px[n][t - lookback] - 1.0
            ranked = sorted(rets, key=rets.get)
            if len(ranked) >= 2 * n_pairs:
                per_leg = equity * exposure / (2.0 * n_pairs)
                legs = []
                for i in range(n_pairs):
                    weak, strong = ranked[i], ranked[-1 - i]
                    if invert:
                        weak, strong = strong, weak
                    legs.append({"sym": strong, "side": "long", "notional": per_leg,
                                 "entry_px": px[strong][t]})
                    legs.append({"sym": weak, "side": "short", "notional": per_leg,
                                 "entry_px": px[weak][t]})
                open_pos.append({"close_bar": t + hold, "legs": legs})

        mtm = equity
        for pos in open_pos:
            for leg in pos["legs"]:
                c1 = px[leg["sym"]][t]
                ret = (c1 / leg["entry_px"] - 1.0) * (1 if leg["side"] == "long" else -1)
                mtm += leg["notional"] * ret
        if mtm > peak:
            peak = mtm
        dd = (peak - mtm) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
        series.append(mtm)
        market.append(sum(px[n][t] / px[n][t - 1] - 1.0 for n in names) / len(names))

    wins = [p for p in legs_pnl if p > 0]
    losses = [p for p in legs_pnl if p <= 0]
    gw = sum(wins)
    gl = -sum(losses)

    # correlation of per-bar strategy returns against the equal-weight market
    corr = None
    if len(series) > 30:
        sr = [series[i] / series[i - 1] - 1.0 for i in range(1, len(series))]
        mr = market[1:]
        n = min(len(sr), len(mr))
        sr, mr = sr[-n:], mr[-n:]
        ms, mm = sum(sr) / n, sum(mr) / n
        cov = sum((a - ms) * (b - mm) for a, b in zip(sr, mr)) / n
        vs = sum((a - ms) ** 2 for a in sr) / n
        vm = sum((b - mm) ** 2 for b in mr) / n
        if vs > 0 and vm > 0:
            corr = cov / (vs ** 0.5 * vm ** 0.5)

    return {"lookback": lookback, "hold": hold, "n_pairs": n_pairs,
            "invert": invert,
            "return_pct": round((equity / capital - 1) * 100, 2),
            "legs": len(legs_pnl),
            "winrate": round(100.0 * len(wins) / len(legs_pnl), 1) if legs_pnl else 0.0,
            "profit_factor": round(gw / gl, 2) if gl > 0 else None,
            "max_dd_pct": round(max_dd * 100, 2),
            "corr_to_market": round(corr, 3) if corr is not None else None}


if __name__ == "__main__":
    data = load()
    print("монет:", len(data), flush=True)
    rows = []
    # robustness: vary how many pairs and how much exposure, on the config that
    # survived the half split
    for n_pairs in (2, 3, 5, 8):
        for exposure in (0.20, 0.40):
            m = run(lookback=168, hold=48, n_pairs=n_pairs, exposure=exposure,
                    invert=True, data=data, slip=0.0005)
            m.update({"n_pairs": n_pairs, "exposure": exposure})
            rows.append(m)
            print("пар %d  экспозиция %2.0f%% | %+7.2f%% | %4d ног | WR %5.1f%% | PF %-5s | DD %5.1f%% | corr %s" % (
                n_pairs, exposure * 100, m["return_pct"], m["legs"], m["winrate"],
                m["profit_factor"], m["max_dd_pct"], m["corr_to_market"]), flush=True)
    json.dump(rows, open("bt_pairs.json", "w"), indent=1)
