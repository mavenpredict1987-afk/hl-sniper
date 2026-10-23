"""bt_benchmark.py — эталон: что даёт пассивное удержание корзины на наших данных.

research/BENCHMARK_NOTE.md (23.10). Не стратегия, а база сравнения.
"""
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from bt_pairs_limits import load_cache  # noqa: E402

ANN = 24 * 365


def metrics(curve):
    n = len(curve) - 1
    total = curve[-1] / curve[0] - 1.0
    years = n / ANN
    cagr = (curve[-1] / curve[0]) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    rets = [curve[i] / curve[i - 1] - 1.0 for i in range(1, len(curve)) if curve[i - 1] > 0]
    mu = sum(rets) / len(rets)
    sd = statistics.stdev(rets)
    sharpe = (mu / sd) * math.sqrt(ANN) if sd else 0.0
    peak, dd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        dd = max(dd, 1.0 - v / peak)
    return {"total_pct": round(100 * total, 2), "cagr_pct": round(100 * cagr, 2),
            "max_dd_pct": round(100 * dd, 2), "sharpe": round(sharpe, 2),
            "calmar": round(cagr / dd, 2) if dd else 0.0}


def main():
    series, timeline = load_cache(os.path.join(HERE, "bt_cache"))
    coins = sorted(series)
    n = len(timeline)
    # --- 1. равные веса, ребаланс раз в сутки (00:00 UTC)
    w = {c: 1.0 / len(coins) for c in coins}
    px0 = {c: series[c][0] for c in coins}
    val = 1.0
    units = {c: val * w[c] / px0[c] for c in coins}
    curve = [val]
    day = timeline[1] // 86400000
    for i in range(1, n):
        val = sum(units[c] * series[c][i] for c in coins)
        curve.append(val)
        d = timeline[i] // 86400000
        if d != day:
            day = d
            units = {c: val * w[c] / series[c][i] for c in coins}
    res = [("корзина 52, ребаланс/сутки", metrics(curve))]
    # --- 2. корзина без ребаланса
    units = {c: (1.0 / len(coins)) / series[c][0] for c in coins}
    curve2 = [sum(units[c] * series[c][i] for c in coins) for i in range(n)]
    res.append(("корзина без ребаланса", metrics(curve2)))
    # --- 3. BTC
    res.append(("BTC", metrics(series["BTC"])))
    # --- 4. корзина с экспозицией 20% (остальное кэш, доход 0)
    curve4 = [1.0 + 0.20 * (v - 1.0) for v in curve]
    res.append(("корзина 20% экспозиции", metrics(curve4)))
    # --- 5. vol targeting: цель 30% годовых по 7-дневной волатильности
    vol_win = 168
    rets = [0.0] + [curve[i] / curve[i - 1] - 1.0 for i in range(1, len(curve))]
    curve5 = [1.0]
    val = 1.0
    for i in range(1, len(curve)):
        w = 0.0
        if i > vol_win:
            r = rets[i - vol_win:i]
            m = sum(r) / len(r)
            sd = (sum((x - m) ** 2 for x in r) / len(r)) ** 0.5
            if sd > 0:
                w = min(1.0, 0.30 / (sd * math.sqrt(ANN)) ) if sd * math.sqrt(ANN) > 0 else 0.0
        val *= 1.0 + w * rets[i]
        curve5.append(val)
    res.append(("корзина + vol targeting 30%", metrics(curve5)))
    print("%-28s %10s %10s %10s %8s %8s" % ("вариант", "итог %", "CAGR %", "максDD %", "Шарп", "Calmar"))
    for name, m in res:
        print("%-28s %10.2f %10.2f %10.2f %8.2f %8.2f" % (
            name, m["total_pct"], m["cagr_pct"], m["max_dd_pct"], m["sharpe"], m["calmar"]))
    # худший месяц корзины
    bym = {}
    for i in range(1, n):
        k = timeline[i] // 86400000 // 30
        bym.setdefault(k, []).append(rets[i] if name else 0)
    months = {}
    for i in range(1, n):
        key = timeline[i] // (86400000 * 30)
        months.setdefault(key, 1.0)
        months[key] *= 1.0 + (curve[i] / curve[i - 1] - 1.0)
    mm = sorted(months.values())
    print("\nкорзина: худший месяц %+0.1f%%, лучший %+0.1f%%, месяцев %d" % (
        100 * (mm[0] - 1.0), 100 * (mm[-1] - 1.0), len(mm)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
