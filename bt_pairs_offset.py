"""Устойчивость парной стратегии к сдвигу сетки 48-часовых циклов.

Предрегистрация: research/PAIRS_GRID_OFFSET_PREREG.md (05:30, до этого файла).
Логика ровно как bt_pairs_limits.py режим A (без фондирования, издержки 0.23%),
но старт сдвигается на s = 0..47 часов, чтобы проверить, не держится ли плюс
на выравнивании сетки.

Запуск: PYTHONPATH=.devlibs python3 bt_pairs_offset.py
"""
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from bt_pairs_limits import (CAPITAL, CLUSTER_WIN_H, EXPOSURE, HOLD_H, LOOKBACK_H,
                             N_PAIRS, TAKER_PCT, load_cache, pick_pairs, returns)

OFFSETS = 48
STRESS_MULT = 2.0


def run(series, timeline, offset, mult=1.0):
    coins = sorted(series)
    n = len(timeline)
    warm = max(LOOKBACK_H, CLUSTER_WIN_H) + 1
    equity, peak, max_dd = CAPITAL, CAPITAL, 0.0
    rows = []
    start = warm + offset
    while start + HOLD_H < n:
        idx = start
        rets = {}
        for c in coins:
            px = series[c]
            if px[idx - LOOKBACK_H] > 0:
                rets[c] = px[idx] / px[idx - LOOKBACK_H] - 1.0
        ranked = sorted(rets, key=rets.get)
        returns_map = returns(series, idx, CLUSTER_WIN_H)
        pairs = pick_pairs(ranked, "A", returns_map, {})
        per_leg = equity * EXPOSURE / (2.0 * N_PAIRS) if pairs else 0.0
        net = 0.0
        for weak, strong in pairs:
            gross = 0.0
            for sym, side in ((weak, 1.0), (strong, -1.0)):
                e, x = series[sym][idx], series[sym][idx + HOLD_H]
                gross += per_leg * (x / e - 1.0) * side
            net += gross - 2.0 * per_leg * (TAKER_PCT / 100.0) * 2.0 * mult
        equity += net
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
        rows.append(net)
        start += HOLD_H
    half = len(rows) // 2
    return {"offset": offset, "net": round(sum(rows), 2), "cycles": len(rows),
            "return_pct": round(100.0 * (equity / CAPITAL - 1.0), 2),
            "max_dd_pct": round(100.0 * max_dd, 2),
            "h1": round(sum(rows[:half]), 2), "h2": round(sum(rows[half:]), 2)}


def main():
    series, timeline = load_cache(os.path.join(HERE, "bt_cache"))
    base, stress = [], []
    for s in range(OFFSETS):
        b = run(series, timeline, s, 1.0)
        x = run(series, timeline, s, STRESS_MULT)
        base.append(b)
        stress.append(x)
        print("сдвиг %2d: нетто %+8.2f (%+6.2f%%) DD %5.2f%% | 2x нетто %+8.2f" % (
            s, b["net"], b["return_pct"], b["max_dd_pct"], x["net"]), flush=True)

    nets = [b["net"] for b in base]
    dds = [b["max_dd_pct"] for b in base]
    pos = sum(1 for v in nets if v > 0)
    pos2 = sum(1 for x in stress if x["net"] > 0)
    checks = [
        ("Q1 медиана нетто > 0", statistics.median(nets) > 0, round(statistics.median(nets), 2)),
        ("Q2 >= 36/48 сдвигов > 0", pos >= 36, pos),
        ("Q3 медианный DD <= 8%", statistics.median(dds) <= 8.0, round(statistics.median(dds), 2)),
        ("Q4 нет сдвига < -300", min(nets) >= -300.0, round(min(nets), 2)),
        ("Q5 >= 30/48 при 2x > 0", pos2 >= 30, pos2),
    ]
    print("\nсвод: медиана %+.2f, мин %+.2f, макс %+.2f, положительных %d/48, "
          "при 2x положительных %d/48, медианный DD %.2f%%" % (
              statistics.median(nets), min(nets), max(nets), pos, pos2,
              statistics.median(dds)))
    print("критерии:")
    for name, ok, val in checks:
        print("  %-26s %s  %s" % (name, "OK" if ok else "FAIL", val))
    json.dump({"base": base, "stress": stress,
               "criteria": {n: bool(o) for n, o, _ in checks},
               "median": round(statistics.median(nets), 2), "min": min(nets),
               "max": max(nets), "positive": pos, "positive_x2": pos2},
              open(os.path.join(HERE, ".probe", "offset_summary.json"), "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
