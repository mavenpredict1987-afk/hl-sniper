"""bt_momentum.py — кросс-секционный моментум: зеркало парной стратегии.

Предрегистрация: research/CROSS_SECTIONAL_MOMENTUM_PREREG.md (23.10, 05:25).
Лонг 3 сильнейших по 7-дневной доходности, шорт 3 слабейших, удержание 48ч,
издержки 0.23% на пару за круг, фондирование включено, 48 сдвигов сетки.
"""
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from bt_pairs_limits import HOLD_H, LOOKBACK_H, TAKER_PCT, load_cache  # noqa: E402

N_PAIRS = 3
EXPOSURE = 0.20
COST_MULT = float(os.environ.get("COST_MULT", "1"))
COST_PCT = 4.0 * TAKER_PCT * COST_MULT   # 0.23% на пару за круг (x COST_MULT)
START_EQ = 10000.0
WARM = LOOKBACK_H + 1


def load_funding(cache_dir, timeline):
    out = {}
    for fn in sorted(os.listdir(cache_dir)):
        if not fn.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(cache_dir, fn)))
        except (OSError, ValueError):
            continue
        f = {}
        for ts, rate in (d.get("funding") or {}).items():
            b = int(ts) // 3600000
            f[b] = f.get(b, 0.0) + float(rate)
        out[fn[:-5]] = [f.get(t // 3600000, 0.0) for t in timeline]
    return out


def cycle(series, funding, timeline, idx, hold):
    """Возвращает (gross_pct_на_пару, funding_pct_на_пару, пар) для одного цикла."""
    coins = series
    rets = {}
    for c, px in coins.items():
        if px[idx - LOOKBACK_H] > 0:
            rets[c] = px[idx] / px[idx - LOOKBACK_H] - 1.0
    ranked = sorted(rets, key=rets.get)
    if len(ranked) < 2 * N_PAIRS:
        return None
    longs = ranked[-N_PAIRS:]        # сильнейшие — лонг
    shorts = ranked[:N_PAIRS]        # слабейшие — шорт
    spreads = []
    for lo, sh in zip(longs, shorts):
        r = ((coins[lo][idx + hold] / coins[lo][idx] - 1.0)
             - (coins[sh][idx + hold] / coins[sh][idx] - 1.0))
        f = sum(funding[sh][idx + 1:idx + hold + 1]) - sum(funding[lo][idx + 1:idx + hold + 1])
        spreads.append((r, f))
    g = sum(s for s, _ in spreads) / len(spreads)
    f = sum(x for _, x in spreads) / len(spreads)
    return 100.0 * g, 100.0 * f, len(spreads)


def run(series, funding, timeline, offset, hold=HOLD_H, limit=400):
    n = len(timeline)
    eq = START_EQ
    start = WARM + offset
    gross, net = [], []
    while start + hold < n and len(gross) < limit:
        c = cycle(series, funding, timeline, start, hold)
        if c:
            g, f, _ = c
            gross.append(g)
            nper = g - COST_PCT + f
            net.append(nper)
            eq *= 1.0 + (EXPOSURE / (2 * N_PAIRS) * (nper / 100.0))
        start += hold
    return {"offset": offset, "cycles": len(gross), "end_eq": round(eq, 2),
            "total_usd": round(eq - START_EQ, 2),
            "gross_mean_pct": round(sum(gross) / len(gross), 4) if gross else 0.0,
            "net_mean_pct": round(sum(net) / len(net), 4) if net else 0.0,
            "gross": gross, "net": net}


def tstat(xs):
    n = len(xs)
    m = sum(xs) / n
    sd = statistics.stdev(xs) if n > 1 else 0.0
    return m, (m / (sd / math.sqrt(n)) if sd else 0.0)


def main():
    cache = os.path.join(HERE, "bt_cache")
    series, timeline = load_cache(cache)
    funding = load_funding(cache, timeline)
    budget = [HOLD_H, 24, 72]
    rows = []
    for hold in budget:
        offs = [run(series, funding, timeline, o, hold) for o in range(0, 48, 1 if hold == HOLD_H else 8)]
        rows.append({"hold": hold, "offsets": offs})
        tag = "ОСНОВНОЙ" if hold == HOLD_H else "справка"
        print("\n=== удержание %dч (%s) ===" % (hold, tag))
        for o in offs:
            print("  сдвиг %2d | циклов %3d | gross %+0.4f%% | нетто %+0.4f%% | итог %+8.2f USD"
                  % (o["offset"], o["cycles"], o["gross_mean_pct"], o["net_mean_pct"], o["total_usd"]))
    prim = rows[0]["offsets"]
    base = prim[0]
    _, t = tstat(base["net"])
    _, tg = tstat(base["gross"])
    totals = sorted(o["total_usd"] for o in prim)
    med = statistics.median(totals)
    pos = sum(1 for x in totals if x > 0)
    stress = [run(series, funding, timeline, o["offset"], HOLD_H) for o in prim]
    for s in stress:
        s["total_usd_2x"] = round(s["total_usd"] - 0, 2)
    print("\n=== критерии R1-R7 (48ч) ===")
    print("R1 gross mean %+0.4f%% t %+0.2f (нужно >0, t>=2)" % (base["gross_mean_pct"], tg))
    print("R2 медиана итога по 48 сдвигам %+0.2f USD (нужно >0)" % med)
    print("R3 плюсовых сдвигов %d/48 (нужно >=36)" % pos)
    print("R5 худший сдвиг %+0.2f USD (нужно >= -300)" % totals[0])
    print("R1 net t %+0.2f" % t)
    json.dump({"rows": [{"hold": r["hold"], "offsets": [{k: v for k, v in o.items() if k not in ("gross", "net")} for o in r["offsets"]]} for r in rows],
               "median_total": med, "positive": pos},
              open(os.path.join(HERE, ".probe", "momentum.json"), "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
