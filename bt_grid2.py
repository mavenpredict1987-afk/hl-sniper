"""Faithful port of the reference bot's gridder.py.

His grid is not a fixed-percent ladder: the width is measured in ATR, so the
ladder adapts to the coin's volatility.

    center = EMA50(1h)
    width  = GRID_WIDTH_ATR * ATR14(1h)      (3.0 x ATR)
    step   = width / GRID_LEVELS             (3 lines)
    stop   = center - width * GRID_STOP_MULT (1.25)
    build only while ER(20) < GRID_ER_MAX    (0.30)
    buy    when price falls to center - step*i
    sell   a lot one step above its entry
    grid stop -> close every lot, coin benched until the end of the UTC day
    rebuild when |price - center| > width * GRID_RECENTER_ATR

His own backtest: LIT +28.9%, UNI +17.7%, NEAR +15.4%, XMR +9.2%.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from indicators import atr_series

import os as _os
# his own grid pool, from GRID_SYMBOLS in config.py: LIT UNI NEAR XMR DOGE VVV
# ZRO AAVE CRV. Only the ones already cached are used.
HIS = "LIT,UNI,NEAR,XMR,DOGE,VVV,ZRO,AAVE,CRV".split(",")
CACHED = set(f[:-5] for f in _os.listdir("bt_cache") if f.endswith(".json"))
if _os.environ.get("HIS_COINS") == "1":
    COINS = [c for c in HIS if c in CACHED]
else:
    COINS = open("bt_coins_all.txt").read().strip().split(",")
TAKER = 0.00045
SLIP = 0.0005


def load():
    out = {}
    for coin in COINS:
        p = os.path.join("bt_cache", coin + ".json")
        if os.path.exists(p):
            out[coin] = json.load(open(p))
    return out


def ema_last(vals, period):
    e = vals[0]
    k = 2.0 / (period + 1)
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def er_last(closes, period=20):
    if len(closes) < period + 1:
        return 1.0
    seg = closes[-period - 1:]
    net = abs(seg[-1] - seg[0])
    path = sum(abs(seg[i] - seg[i - 1]) for i in range(1, len(seg)))
    return net / path if path else 1.0


def run(width_atr=3.0, levels=3, stop_mult=1.25, er_max=0.30, recenter=1.0,
        lot_pct=0.08, max_lots=4, capital=10000.0, target_tf="1h"):
    data = load()
    st = {}
    for coin, blob in data.items():
        c = [b["c"] for b in blob["candles"]]
        h = [b["h"] for b in blob["candles"]]
        l = [b["l"] for b in blob["candles"]]
        atr = atr_series(h, l, c)
        st[coin] = {"c": c, "atr": atr, "off": len(c) - len(atr)}
    length = min(len(s["c"]) for s in st.values())

    def atr_at(s, i):
        j = i - s["off"]
        return s["atr"][j] if 0 <= j < len(s["atr"]) else None

    equity = capital
    peak = capital
    max_dd = 0.0
    trades = []
    grids = {}

    for i in range(80, length):
        today = i // 24
        for coin, s in st.items():
            px = s["c"][i]
            g = grids.get(coin)

            need_build = (g is None or g.get("center") is None
                          or (g.get("width") and abs(px - g["center"])
                              > g["width"] * recenter))
            built = None
            if need_build:
                a = atr_at(s, i - 1)          # previous close only
                if not a or a <= 0:
                    continue
                closes = s["c"][:i]
                built = {"atr": a, "er": er_last(closes),
                         "ema": ema_last(closes[-200:], 50)}

            if g and g.get("stop") and px <= g["stop"] and g["lots"]:
                for lot in list(g["lots"]):
                    fee = lot["size"] * (px + lot["level"]) * (TAKER + SLIP)
                    pnl = (px - lot["level"]) * lot["size"] - fee
                    equity += pnl
                    trades.append({"pnl": pnl, "reason": "grid_stop"})
                g["lots"] = []
                g["cooldown"] = today
                g["center"] = None
                continue

            if g:
                for lot in list(g["lots"]):
                    if px >= lot["target"]:
                        fill = lot["target"]
                        fee = lot["size"] * (fill + lot["level"]) * (TAKER + SLIP)
                        pnl = (fill - lot["level"]) * lot["size"] - fee
                        equity += pnl
                        trades.append({"pnl": pnl, "reason": "grid_sell"})
                        g["lots"].remove(lot)

            if need_build and built:
                if built["er"] >= er_max and not (g and g["lots"]):
                    continue
                g = {"center": built["ema"], "width": width_atr * built["atr"],
                     "er": built["er"], "lots": [], "cooldown": None}
                g["step"] = g["width"] / levels
                g["stop"] = g["center"] - g["width"] * stop_mult
                grids[coin] = g

            if g is None or g.get("center") is None:
                continue
            if g.get("cooldown") == today:
                continue
            if g["er"] >= er_max:
                continue

            for k in range(1, levels + 1):
                level = g["center"] - g["step"] * k
                if px > level:
                    continue
                if any(abs(l["level"] - level) < g["step"] * 0.3 for l in g["lots"]):
                    continue
                if sum(len(x["lots"]) for x in grids.values()) >= max_lots:
                    break
                notional = equity * lot_pct
                size = notional / level
                g["lots"].append({"level": level, "target": level + g["step"],
                                  "size": size})

        mtm = equity
        for coin, g in grids.items():
            if not g.get("center"):
                continue
            p = st[coin]["c"][i]
            for lot in g["lots"]:
                mtm += (p - lot["level"]) * lot["size"]
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
    return {"return_pct": round((equity / capital - 1) * 100, 2),
            "trades": len(trades),
            "winrate": round(100.0 * len(wins) / len(trades), 1) if trades else 0.0,
            "profit_factor": round(gw / gl, 2) if gl > 0 else None,
            "max_dd_pct": round(max_dd * 100, 2),
            "reasons": reasons, "open_grids": sum(1 for g in grids.values() if g.get("center"))}


if __name__ == "__main__":
    rows = []
    for wp in (0.04, 0.08, 0.15):
        m = run(lot_pct=wp)
        rows.append(m)
        print("лот %.0f%% equity | %+8.2f%% | %5d | WR %5.1f%% | PF %-6s | DD %5.1f%% | %s" % (
            wp * 100, m["return_pct"], m["trades"], m["winrate"],
            m["profit_factor"], m["max_dd_pct"], m["reasons"]), flush=True)
    json.dump(rows, open("bt_grid2.json", "w"), indent=1)
