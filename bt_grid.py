"""Long grid in a range, done honestly.

Two mistakes in the first attempt produced +1141%..+8435% at a 99% win rate,
which is not a result, it is a bug report:

1. look-ahead - the grid was built from bar i's close and then filled against
   bar i's low, which had already happened. Decisions now use bar i-1 and only
   the fills use bar i's range.
2. vanishing losses - dropping a grid (recentre, or the range filter turning
   off) deleted its open lots without closing them, so every losing lot was
   simply forgotten. Open lots are now closed at the current close and the loss
   is booked.

The reference bot's own grid shows the trap this exposes: 10 closed lots, all
winners, +$6.24 realised - while its open lots sat at -$16.42.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indicators import atr_series

COINS = open("bt_coins_all.txt").read().strip().split(",")


def load():
    out = {}
    for coin in COINS:
        p = os.path.join("bt_cache", coin + ".json")
        if os.path.exists(p):
            out[coin] = json.load(open(p))
    return out


def efficiency_ratio(closes, period):
    out = [None] * len(closes)
    for i in range(period, len(closes)):
        net = abs(closes[i] - closes[i - period])
        path = sum(abs(closes[j] - closes[j - 1]) for j in range(i - period + 1, i + 1))
        out[i] = net / path if path > 0 else 1.0
    return out


def ema_series(vals, period):
    out = [None] * len(vals)
    k = 2.0 / (period + 1)
    prev = None
    for i, v in enumerate(vals):
        prev = v if prev is None else v * k + prev * (1 - k)
        out[i] = prev
    return out


def run(step_pct=1.5, levels=4, er_max=0.30, stop_width=1.25, maker_fee=0.00015,
        lot_frac=0.02, max_lots=4, capital=10000.0, min_coverage=20.0):
    data = load()
    st = {}
    for coin, blob in data.items():
        c = [b["c"] for b in blob["candles"]]
        h = [b["h"] for b in blob["candles"]]
        l = [b["l"] for b in blob["candles"]]
        atr = atr_series(h, l, c)
        st[coin] = {"c": c, "h": h, "l": l,
                    "er": efficiency_ratio(c, 20),
                    "ema": ema_series(c, 50),
                    "atr": atr, "off": len(c) - len(atr)}
    length = min(len(s["c"]) for s in st.values())

    def atr_at(s, i):
        j = i - s["off"]
        return s["atr"][j] if 0 <= j < len(s["atr"]) else None

    # a step that does not cover the round-trip cost many times over is not a
    # grid, it is a fee pump
    cost_pct = 2.0 * maker_fee * 100.0
    step = step_pct / 100.0
    if step * 100.0 / cost_pct < min_coverage:
        return {"step": step_pct, "er_max": er_max, "skipped": "coverage"}

    equity = capital
    peak = capital
    max_dd = 0.0
    trades = []
    grids = {}

    def close_lot(coin, entry, px, equity_now):
        size = equity_now * lot_frac / entry
        pnl = (px - entry) * size
        fee = (entry + px) * size * maker_fee
        return pnl - fee

    for i in range(70, length):
        for coin, s in st.items():
            px = s["c"][i]
            hi, lo = s["h"][i], s["l"][i]
            # everything decided here comes from the previous close only
            er = s["er"][i - 1]
            a = atr_at(s, i - 1)
            if er is None or not a or a <= 0:
                continue
            g = grids.get(coin)

            if g and px <= g["stop"]:
                for entry in g["lots"]:
                    pnl = close_lot(coin, entry, px, equity)
                    equity += pnl
                    trades.append({"pnl": pnl, "reason": "stop"})
                del grids[coin]
                continue

            if g:
                keep = []
                for entry in g["lots"]:
                    target = entry * (1 + step)
                    if hi >= target:
                        pnl = close_lot(coin, entry, target, equity)
                        equity += pnl
                        trades.append({"pnl": pnl, "reason": "tp"})
                    else:
                        keep.append(entry)
                g["lots"] = keep
                # the range is over: close whatever is still open, book it, and
                # let the grid be rebuilt only when the market calms down again
                if er > er_max or px < g["stop"]:
                    for entry in g["lots"]:
                        pnl = close_lot(coin, entry, px, equity)
                        equity += pnl
                        trades.append({"pnl": pnl, "reason": "range_end"})
                    del grids[coin]
                    g = None

            if g is None:
                if er > er_max:
                    continue
                center = s["ema"][i - 1] or px
                if abs(px / center - 1) > step * levels:
                    continue
                g = {"center": center, "lots": [],
                     "stop": center * (1 - step * levels * stop_width)}
                grids[coin] = g

            for k in range(1, levels + 1):
                level = g["center"] * (1 - step * k)
                if lo <= level and level not in g["lots"] and len(g["lots"]) < max_lots:
                    g["lots"].append(level)

        mtm = equity
        for coin, g in grids.items():
            p = st[coin]["c"][i]
            for entry in g["lots"]:
                mtm += (p - entry) * (equity * lot_frac / entry)
        if mtm > peak:
            peak = mtm
        dd = (peak - mtm) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in losses)
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    return {
        "step": step_pct, "levels": levels, "er_max": er_max,
        "return_pct": round((equity / capital - 1) * 100, 2),
        "trades": len(trades),
        "winrate": round(100.0 * len(wins) / len(trades), 1) if trades else 0.0,
        "profit_factor": round(gw / gl, 2) if gl > 0 else None,
        "max_dd_pct": round(max_dd * 100, 2),
        "reasons": reasons,
        "open_grids": len(grids),
    }


if __name__ == "__main__":
    rows = []
    # his live grid: step 1.5%, 4 lines, stop 20% below centre, lot 45% of
    # capital per lot. Wide stop and fat lots are why his drawdown reached 14%.
    for step, sw in ((1.5, 1.25), (1.5, 3.33), (2.0, 2.5), (2.0, 3.33)):
        m = run(step_pct=step, stop_width=sw)
        m["stop_width"] = sw
        rows.append(m)
        if m.get("skipped"):
            print("step %.1f%% stop %.2fx -> пропущен" % (step, sw), flush=True)
            continue
        print("step %.1f%% stop %.2fx | %+8.2f%% | %5d | WR %5.1f%% | PF %-6s | DD %5.1f%% | %s" % (
            step, sw, m["return_pct"], m["trades"], m["winrate"], m["profit_factor"],
            m["max_dd_pct"], m["reasons"]), flush=True)
    json.dump(rows, open("bt_grid.json", "w"), indent=1)
    raise SystemExit(0)
    for step in (1.0, 1.5, 2.0, 3.0):
        m = run(step_pct=step)
        rows.append(m)
        if m.get("skipped"):
            print("step %.1f%% -> пропущен (%s)" % (step, m["skipped"]), flush=True)
            continue
        print("step %.1f%% | %+8.2f%% | %5d сделок | WR %5.1f%% | PF %-6s | DD %5.1f%% | %s" % (
            step, m["return_pct"], m["trades"], m["winrate"], m["profit_factor"],
            m["max_dd_pct"], m["reasons"]), flush=True)
    json.dump(rows, open("bt_grid.json", "w"), indent=1)
