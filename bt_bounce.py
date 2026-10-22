"""Отскок от уровня по японской свече: замер на нашем кэше.

Предрегистрация: research/BOUNCE_PREREG.md (написана 22.10 09:20 MSK,
до этого файла и до прогона). Все пороги ниже взяты оттуда и не подбираются.

Сигнал: касание уровня + свеча с длинной тенью от уровня + закрепление
следующей свечой; вход по закрытию подтверждающего бара, стоп за уровнем,
цель 1.5·ATR, горизонт ограничен.

Запуск: PYTHONPATH=.devlibs python3 bt_bounce.py
"""
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from levels import find_levels, nearest_support, nearest_resistance

ATR_PERIOD = 14
LEVELS_LOOKBACK = 120
LEVELS_EVERY = 6
TOUCH_ATR = 0.25
MIN_RANGE_ATR = 0.5
WICK_BODY = 2.0
CLOSE_TOP = 0.66
STOP_ATR = 0.5
TARGET_ATR = 1.5
COST_PCT = 0.115          # на круг: 2 филла x 0.0575%
CAPITAL = 10000.0
LOT = 1000.0
MAX_CONCURRENT = 3


def load(cache_dir):
    out = {}
    for fn in sorted(os.listdir(cache_dir)):
        if not fn.endswith(".json"):
            continue
        coin = fn[:-5]
        try:
            d = json.load(open(os.path.join(cache_dir, fn)))
        except (OSError, ValueError):
            continue
        cds = d.get("candles") or []
        if len(cds) < 400:
            continue
        out[coin] = [{"t": int(c["t"]), "o": float(c["o"]), "h": float(c["h"]),
                      "l": float(c["l"]), "c": float(c["c"]), "v": float(c["v"])}
                     for c in cds]
    return out


def aggregate(bars, bars_per):
    """Склеиваем в bars_per-часовые свечи (таймстемп кратен bars_per)."""
    if bars_per == 1:
        return bars
    step = bars_per * 3600 * 1000
    out = []
    bucket = None
    for b in bars:
        key = (b["t"] // step) * step
        if bucket is None or bucket["t"] != key:
            if bucket is not None:
                out.append(bucket)
            bucket = {"t": key, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]}
        else:
            bucket["h"] = max(bucket["h"], b["h"])
            bucket["l"] = min(bucket["l"], b["l"])
            bucket["c"] = b["c"]
            bucket["v"] += b["v"]
    if bucket is not None:
        out.append(bucket)
    return out


def atr_series(bars, period=ATR_PERIOD):
    trs = []
    out = []
    prev_c = None
    for b in bars:
        if prev_c is None:
            tr = b["h"] - b["l"]
        else:
            tr = max(b["h"] - b["l"], abs(b["h"] - prev_c), abs(b["l"] - prev_c))
        trs.append(tr)
        prev_c = b["c"]
        if len(trs) < period:
            out.append(None)
        else:
            out.append(sum(trs[-period:]) / period)
    return out


def levels_at(bars, i, atr):
    lo = max(0, i - LEVELS_LOOKBACK)
    sub = bars[lo:i + 1]
    if len(sub) < 30 or not atr or atr <= 0:
        return []
    highs = [b["h"] for b in sub]
    lows = [b["l"] for b in sub]
    vols = [b["v"] for b in sub]
    return find_levels(highs, lows, vols, atr)


def pattern(bars, i, atr, side):
    """Свеча-отскок на баре i. side='long' (у поддержки) или 'short' (у сопротивления)."""
    b = bars[i]
    rng = b["h"] - b["l"]
    if rng <= 0 or atr <= 0 or rng < MIN_RANGE_ATR * atr:
        return False
    body = abs(b["c"] - b["o"])
    if side == "long":
        lower_wick = min(b["o"], b["c"]) - b["l"]
        if lower_wick < WICK_BODY * body:
            return False
        return (b["c"] - b["l"]) >= CLOSE_TOP * rng
    upper_wick = b["h"] - max(b["o"], b["c"])
    if upper_wick < WICK_BODY * body:
        return False
    return (b["h"] - b["c"]) >= CLOSE_TOP * rng


def simulate_coin(coin, bars, atr, max_hold, levels_cache):
    trades = []
    n = len(bars)
    i = LEVELS_LOOKBACK + 2
    while i < n - 2:
        a = atr[i]
        if not a:
            i += 1
            continue
        # уровни пересчитываем раз в LEVELS_EVERY баров и внутри корзины не трогаем
        bucket = i // LEVELS_EVERY
        lv = levels_cache.get(bucket)
        if lv is None:
            at = bucket * LEVELS_EVERY
            lv = levels_at(bars, at, atr[at]) if atr[at] else []
            levels_cache[bucket] = lv
        if not lv:
            i += 1
            continue
        b = bars[i]
        side = None
        level = None
        if pattern(bars, i, a, "long"):
            sup = nearest_support(lv, b["c"])
            if sup and (b["l"] - sup["price"]) <= TOUCH_ATR * a and (b["l"] - sup["price"]) >= -1.0 * a:
                side, level = "long", sup["price"]
        if side is None and pattern(bars, i, a, "short"):
            res = nearest_resistance(lv, b["c"])
            if res and (res["price"] - b["h"]) <= TOUCH_ATR * a and (res["price"] - b["h"]) >= -1.0 * a:
                side, level = "short", res["price"]
        if side is None:
            i += 1
            continue

        # подтверждение следующей свечой: закрытие за экстремум бара-отскока
        nxt = bars[i + 1]
        if side == "long":
            if nxt["c"] <= b["h"]:
                i += 1
                continue
            entry = nxt["c"]
            stop = level - STOP_ATR * a
            target = entry + TARGET_ATR * a
        else:
            if nxt["c"] >= b["l"]:
                i += 1
                continue
            entry = nxt["c"]
            stop = level + STOP_ATR * a
            target = entry - TARGET_ATR * a
        if (side == "long" and stop >= entry) or (side == "short" and stop <= entry):
            i += 1
            continue

        exit_px = None
        exit_i = None
        reason = "time"
        for j in range(i + 2, min(n, i + 2 + max_hold)):
            bj = bars[j]
            if side == "long":
                if bj["l"] <= stop:
                    exit_px, exit_i, reason = min(stop, bj["o"]), j, "stop"
                    break
                if bj["h"] >= target:
                    exit_px, exit_i, reason = max(target, bj["o"]), j, "target"
                    break
            else:
                if bj["h"] >= stop:
                    exit_px, exit_i, reason = max(stop, bj["o"]), j, "stop"
                    break
                if bj["l"] <= target:
                    exit_px, exit_i, reason = min(target, bj["o"]), j, "target"
                    break
        if exit_px is None:
            exit_i = min(n - 1, i + 1 + max_hold)
            exit_px = bars[exit_i]["c"]
        if side == "long":
            gross = (exit_px / entry - 1.0) * 100.0
        else:
            gross = (1.0 - exit_px / entry) * 100.0
        trades.append({"coin": coin, "side": side, "t_in": nxt["t"], "t_out": bars[exit_i]["t"],
                       "entry": entry, "exit": exit_px, "bars": exit_i - i - 1,
                       "gross_pct": round(gross, 4), "net_pct": round(gross - COST_PCT, 4),
                       "reason": reason})
        i = exit_i + 1
    return trades


def stats(trades):
    nets = [t["net_pct"] for t in trades]
    if not nets:
        return {"n": 0}
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    mean = sum(nets) / len(nets)
    sd = statistics.pstdev(nets) if len(nets) > 1 else 0.0
    t = mean / (sd / math.sqrt(len(nets))) if sd > 0 else 0.0
    half = len(trades) // 2
    ordered = sorted(trades, key=lambda x: x["t_in"])
    first = [t["net_pct"] for t in ordered[:half]]
    second = [t["net_pct"] for t in ordered[half:]]
    return {
        "n": len(nets),
        "net_sum_pct": round(sum(nets), 2),
        "mean_pct": round(mean, 4),
        "t": round(t, 2),
        "winrate": round(100.0 * len(wins) / len(nets), 1),
        "pf": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else None,
        "first_half_mean": round(sum(first) / len(first), 4) if first else None,
        "second_half_mean": round(sum(second) / len(second), 4) if second else None,
        "longs": sum(1 for t in trades if t["side"] == "long"),
        "shorts": sum(1 for t in trades if t["side"] == "short"),
        "stops": sum(1 for t in trades if t["reason"] == "stop"),
        "targets": sum(1 for t in trades if t["reason"] == "target"),
    }


def portfolio(trades, max_concurrent=MAX_CONCURRENT):
    active = []
    equity = CAPITAL
    peak = CAPITAL
    max_dd = 0.0
    taken = 0
    for t in sorted(trades, key=lambda x: x["t_in"]):
        active = [a for a in active if a[0] > t["t_in"]]
        if len(active) >= max_concurrent:
            continue
        pnl = LOT * t["net_pct"] / 100.0
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 0.0)
        active.append((t["t_out"], pnl))
        taken += 1
    return {"trades": taken, "net_usd": round(equity - CAPITAL, 2),
            "return_pct": round((equity / CAPITAL - 1.0) * 100.0, 2),
            "max_dd_pct": round(max_dd * 100.0, 2)}


def run_tf(data, bars_per, max_hold, label):
    all_trades = []
    for coin, bars in data.items():
        agg = aggregate(bars, bars_per)
        if len(agg) < LEVELS_LOOKBACK + 10:
            continue
        atr = atr_series(agg)
        cache = {}
        all_trades.extend(simulate_coin(coin, agg, atr, max_hold, cache))
    st = stats(all_trades)
    pf = portfolio(all_trades)
    print("\n== %s: баров в свече %d, горизонт %d ==" % (label, bars_per, max_hold))
    if not st.get("n"):
        print("сделок нет")
        return {"label": label, "stats": st, "portfolio": pf}
    print("сделок %d (лонг %d / шорт %d, стопов %d, целей %d)" % (
        st["n"], st["longs"], st["shorts"], st["stops"], st["targets"]))
    print("среднее нетто %+.4f%% | сумма %+.2f%% | t %.2f | WR %.1f%% | PF %s" % (
        st["mean_pct"], st["net_sum_pct"], st["t"], st["winrate"], st["pf"]))
    print("половины: %+.4f%% / %+.4f%%" % (st["first_half_mean"], st["second_half_mean"]))
    print("портфель: %d сделок по $%.0f | %+.2f$ (%+.2f%%) | DD %.2f%%" % (
        pf["trades"], LOT, pf["net_usd"], pf["return_pct"], pf["max_dd_pct"]))
    return {"label": label, "stats": st, "portfolio": pf, "trades": all_trades}


def verdict(res, min_trades):
    st = res["stats"]
    pf = res["portfolio"]
    if not st.get("n"):
        return ["B0 FAIL: сделок нет"]
    checks = [
        ("B1 PF>=1.25", (st["pf"] or 0) >= 1.25),
        ("B2 t>=2.0", st["t"] >= 2.0),
        ("B3 обе половины >0", (st["first_half_mean"] or 0) > 0 and (st["second_half_mean"] or 0) > 0),
        ("B4 DD<=10%", pf["max_dd_pct"] <= 10.0),
        ("B5 сделок>=%d" % min_trades, st["n"] >= min_trades),
    ]
    return ["%s %s" % ("OK" if ok else "FAIL", name) for name, ok in checks]


def main():
    data = load(os.path.join(HERE, "bt_cache"))
    if not data:
        print("нет данных")
        return 1
    print("монет %d" % len(data), flush=True)
    out = {}
    for bars_per, max_hold, label in ((1, 24, "H1"), (4, 6, "H4")):
        res = run_tf(data, bars_per, max_hold, label)
        out[label] = {k: v for k, v in res.items() if k != "trades"}
        for line in verdict(res, 100):
            print("  ", line)
        json.dump(res.get("trades") or [],
                  open(os.path.join(HERE, ".probe", "bounce_%s.json" % label), "w"))
    json.dump(out, open(os.path.join(HERE, ".probe", "bounce_summary.json"), "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
