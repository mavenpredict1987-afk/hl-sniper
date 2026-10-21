"""Replication of the reference bot's grid design (v15.1) on our cached data.

Design as read from research/CONFIG_PRODUCTION_REFERENCE.txt and his live
/api/state (2026-10-21): long-only grid, 1.5% step below a centre, 4 levels,
take-profit +1.5%, 50% partial at +0.5%, stop 20% below the centre, lots of $45
notional, max 6 lots total / 2 per coin, maker buys.

This is NOT his code, it is the same idea with the same numbers. Two modelling
modes are reported because the difference between them is the whole answer:

  same_bar_exit=True   a limit buy fills at the level and the +1.5% target can
                       be hit inside the same hourly bar. Optimistic.
  same_bar_exit=False  no exit is allowed on the entry bar. Conservative: the
                       entry bar is the bar that touched the level, so assuming
                       a V-reversal in the same hour is a path assumption.

Accounting: every cash flow is booked (partials included), so realized P&L is
the sum of legs, not the sum of final exits. Portfolio equity is marked to
market every bar, open lots included, which is what a dashboard must show.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "bt_cache")

GRID_SYMBOLS = ("PUMP,ENA,FARTCOIN,NEAR,ZRO,HYPE,XRP,ADA,AVAX,kPEPE,ARB,MON,ZEC,"
                "UNI,VVV,ICP,DOGE,XPL,ONDO,GRASS,SUI,XMR,SOL,LINK,AAVE,LIT,PYTH,"
                "PENGU").split(",")

START = 180.0          # his GRID_BALANCE_USD
LOT_USD = 45.0
MAX_LOTS = 6
MAX_PER_COIN = 2
STEP_PCT = 0.015
MAKER_FEE = 0.00015
TAKER_FEE = 0.00045
SLIP_STOP = 0.0003
CENTRE_WIN = 24


def load(coins):
    out = {}
    for c in coins:
        p = os.path.join(CACHE, c + ".json")
        if os.path.exists(p):
            out[c] = json.load(open(p))
    return out


def run(data, stop_from_centre=0.20, target_pct=0.015, partial_pct=0.005,
        partial_share=0.5, same_bar_exit=True, entry_gate=None, label=""):
    coins = sorted(data)
    n = min(len(data[c]["candles"]) for c in coins)
    lots = {c: [] for c in coins}
    realized = fees = funding = 0.0
    closed = []                      # (cash_usd, hold_h)
    eq_curve = []
    peak = START
    max_dd = 0.0
    under = 0
    max_lots = 0

    for i in range(CENTRE_WIN, n):
        # ---------- entries ----------
        for c in coins:
            cd = data[c]
            if entry_gate is not None and not entry_gate(cd, i):
                continue
            bar, prev = cd["candles"][i], cd["candles"][i - 1]
            centre = max(b["h"] for b in cd["candles"][i - CENTRE_WIN:i])
            for k in (1, 2):
                if len(lots[c]) >= MAX_PER_COIN:
                    break
                if sum(len(v) for v in lots.values()) >= MAX_LOTS:
                    break
                lvl = centre * (1 - STEP_PCT * k)
                if bar["l"] <= lvl < prev["c"]:
                    lots[c].append({"entry": lvl, "left": 1.0, "cash": 0.0,
                                    "stop": lvl * (1 - stop_from_centre), "t": i})
                    fees += LOT_USD * MAKER_FEE
                    realized -= LOT_USD * MAKER_FEE
        # ---------- funding on open lots ----------
        for c in coins:
            f = data[c]["funding"].get(str(data[c]["candles"][i]["t"]))
            if f:
                for lt in lots[c]:
                    pay = LOT_USD * lt["left"] * float(f)
                    funding += pay
                    realized -= pay
        # ---------- exits ----------
        for c in coins:
            bar = data[c]["candles"][i]
            keep = []
            for lt in lots[c]:
                if not same_bar_exit and lt["t"] == i:
                    keep.append(lt)
                    continue
                size = LOT_USD * lt["left"]
                if bar["l"] <= lt["stop"]:
                    px = lt["stop"] * (1 - SLIP_STOP)
                    cash = size * (px / lt["entry"] - 1) - size * TAKER_FEE
                    lt["cash"] += cash
                    realized += cash
                    fees += size * TAKER_FEE
                    closed.append((lt["cash"], i - lt["t"]))
                    continue
                if lt["left"] == 1.0 and bar["h"] >= lt["entry"] * (1 + partial_pct):
                    part = LOT_USD * partial_share
                    cash = part * partial_pct - part * TAKER_FEE
                    lt["cash"] += cash
                    realized += cash
                    fees += part * TAKER_FEE
                    lt["left"] -= partial_share
                if bar["h"] >= lt["entry"] * (1 + target_pct):
                    size = LOT_USD * lt["left"]
                    cash = size * target_pct - size * TAKER_FEE
                    lt["cash"] += cash
                    realized += cash
                    fees += size * TAKER_FEE
                    closed.append((lt["cash"], i - lt["t"]))
                    continue
                keep.append(lt)
            lots[c] = keep
        # ---------- mark to market ----------
        unreal = sum((data[c]["candles"][i]["c"] / lt["entry"] - 1) * LOT_USD * lt["left"]
                     for c in coins for lt in lots[c])
        eq = START + realized + unreal
        eq_curve.append(eq)
        peak = max(peak, eq)
        max_dd = max(max_dd, 100.0 * (1 - eq / peak))
        under += eq < START
        max_lots = max(max_lots, sum(len(v) for v in lots.values()))

    n_c = len(closed)
    gains = sum(c for c, _ in closed if c > 0)
    losses = -sum(c for c, _ in closed if c <= 0)
    wins = sum(1 for c, _ in closed if c > 0)
    return {
        "label": label,
        "closed_lots": n_c,
        "winrate_pct": round(100.0 * wins / n_c, 1) if n_c else 0.0,
        "profit_factor": round(gains / losses, 3) if losses else None,
        "avg_win_usd": round(gains / wins, 2) if wins else 0.0,
        "avg_loss_usd": round(-losses / (n_c - wins), 2) if n_c - wins else 0.0,
        "expectancy_pct_of_lot": round(100.0 * sum(c for c, _ in closed) / (n_c * LOT_USD), 3) if n_c else 0.0,
        "realized_usd": round(realized, 2),
        "fees_usd": round(fees, 2),
        "funding_usd": round(funding, 2),
        "avg_hold_h": round(sum(h for _, h in closed) / n_c, 1) if n_c else 0.0,
        "max_lots_open": max_lots,
        "final_equity": round(eq_curve[-1], 2),
        "total_pct": round(100.0 * (eq_curve[-1] / START - 1), 1),
        "max_dd_pct": round(max_dd, 1),
        "min_equity": round(min(eq_curve), 2),
        "pct_bars_below_start": round(100.0 * under / len(eq_curve), 1),
    }


VARIANTS = (
    ("friend: TP1.5 stop20 partial, same-bar exits", dict()),
    ("friend: TP1.5 stop20 partial, NEXT-BAR exits", dict(same_bar_exit=False)),
    ("TP1.5 stop20 partial, NEXT-BAR, stop30", dict(same_bar_exit=False, stop_from_centre=0.30)),
    ("TP1.5 stop20 partial, NEXT-BAR, stop5", dict(same_bar_exit=False, stop_from_centre=0.05)),
    ("TP1.5 no stop, NEXT-BAR", dict(same_bar_exit=False, stop_from_centre=0.99)),
    ("TP1.5 stop20, NO partial, NEXT-BAR", dict(same_bar_exit=False, partial_share=0.0)),
)


def fmt(r):
    return ("%-46s lots=%3d WR=%5.1f%% PF=%-5s E=%+7.3f%%/lot realized=%+7.2f$ "
            "DD=%5.1f%% fin=%6.2f below=%4.1f%% fees=%5.2f fund=%5.2f hold=%4.0fh" % (
                r["label"], r["closed_lots"], r["winrate_pct"], r["profit_factor"],
                r["expectancy_pct_of_lot"], r["realized_usd"], r["max_dd_pct"],
                r["final_equity"], r["pct_bars_below_start"], r["fees_usd"],
                r["funding_usd"], r["avg_hold_h"]))


def main():
    data = load(GRID_SYMBOLS)
    n = min(len(d["candles"]) for d in data.values())
    print("coins %d, bars %d (%.0f days)" % (len(data), n, n / 24.0))
    res = []
    for label, kw in VARIANTS:
        r = run(data, label=label, **kw)
        res.append(r)
        print(fmt(r))
    halves = [
        ("H1 (first 2500 bars, NEXT-BAR)", {c: {"candles": d["candles"][:2500], "funding": d["funding"]} for c, d in data.items()}),
        ("H2 (last 2500 bars, NEXT-BAR)", {c: {"candles": d["candles"][2500:], "funding": d["funding"]} for c, d in data.items()}),
    ]
    print()
    for label, sub in halves:
        r = run(sub, label=label, same_bar_exit=False)
        res.append(r)
        print(fmt(r))
    json.dump(res, open(os.path.join(HERE, "bt_friend_grid.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
