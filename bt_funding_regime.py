"""H5: фондирование как режим — персистентность (H5a) и кросс-секционная ставка (H5b).

Предрегистрация: research/FUNDING_REGIME_PREREG.md (05:15, до этого прогона).
Хеджированный кэрри на HL-only неисполним (спот есть у 2 из 52 монет) — это
зафиксировано в предрегистрации как вывод до замера.

H5a: trailing 24ч среднее фондирования -> сумма следующих 24ч (regression + sign).
H5b: каждые 24ч лонг 3 монет с наименьшим фондированием, шорт 3 с наибольшим,
     держим 24ч, фондирование начисляется в P&L, издержки 0.23% на пару за круг,
     стресс 2x.

Запуск: PYTHONPATH=.devlibs python3 bt_funding_regime.py
"""
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

HOLD_H = 24
N_LEGS = 3
COST_PAIR_PCT = 0.23       # на пару за круг, база
CAPITAL = 10000.0
EXPOSURE = 0.20


def load(cache_dir):
    raw = {}
    for fn in sorted(os.listdir(cache_dir)):
        if not fn.endswith(".json"):
            continue
        coin = fn[:-5]
        try:
            d = json.load(open(os.path.join(cache_dir, fn)))
        except (OSError, ValueError):
            continue
        cds = d.get("candles") or []
        if len(cds) < 800:
            continue
        fund = {}
        for ts, rate in (d.get("funding") or {}).items():
            bucket = int(ts) // 3600000
            fund[bucket] = fund.get(bucket, 0.0) + float(rate)
        raw[coin] = ([(int(c["t"]), float(c["c"])) for c in cds], fund)
    common = None
    for rows, _ in raw.values():
        s = set(t for t, _ in rows)
        common = s if common is None else (common & s)
    timeline = sorted(common or [])
    prices, funding = {}, {}
    for coin, (rows, fund) in raw.items():
        m = dict(rows)
        prices[coin] = [m[t] for t in timeline]
        funding[coin] = [fund.get(t // 3600000, 0.0) for t in timeline]
    return prices, funding, timeline


def h5a(prices, funding, timeline):
    """Персистентность: среднее за 24ч -> сумма следующих 24ч, по всем монетам."""
    xs, ys, n_same = [], [], 0
    for coin, f in funding.items():
        for i in range(24, len(f) - 24):
            prev = sum(f[i - 23:i + 1]) / 24.0
            nxt = sum(f[i + 1:i + 25])
            xs.append(prev)
            ys.append(nxt)
            if prev != 0 and nxt != 0 and (prev > 0) == (nxt > 0):
                n_same += 1
    n = len(xs)
    if n < 100:
        return {"n": n}
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx if sxx > 0 else 0.0
    resid = [y - (my + slope * (x - mx)) for x, y in zip(xs, ys)]
    s2 = sum(r * r for r in resid) / (n - 2)
    se = math.sqrt(s2 / sxx) if sxx > 0 else float("inf")
    return {"n": n, "slope": round(slope, 4), "t": round(slope / se, 2) if se else None,
            "corr": round(sxy / math.sqrt(sxx * sum((y - my) ** 2 for y in ys)), 3),
            "sign_same_pct": round(100.0 * n_same / n, 1)}


def run(prices, funding, timeline, mult=1.0):
    coins = sorted(prices)
    n = len(timeline)
    equity, peak, max_dd = CAPITAL, CAPITAL, 0.0
    cycles = []
    start = 24
    while start + HOLD_H <= n:
        idx = start
        trail = {}
        for c in coins:
            f = funding[c][idx - 23:idx + 1]
            trail[c] = sum(f) / len(f)
        ranked = sorted(trail, key=trail.get)
        if len(ranked) < 2 * N_LEGS:
            start += HOLD_H
            continue
        longs, shorts = ranked[:N_LEGS], ranked[-N_LEGS:]
        per_leg = equity * EXPOSURE / (2.0 * N_LEGS)
        pnl = 0.0
        detail = []
        for c in longs + shorts:
            e, x = prices[c][idx], prices[c][idx + HOLD_H]
            ret = x / e - 1.0
            fund_sum = sum(funding[c][idx + 1:idx + HOLD_H + 1])
            side = 1.0 if c in longs else -1.0
            leg = per_leg * (side * ret - side * fund_sum)
            cost = per_leg * (COST_PAIR_PCT / 100.0) * mult   # нога: половина стоимости пары
            pnl += leg - cost
            detail.append({"coin": c, "side": "long" if side > 0 else "short",
                           "ret_pct": round(100.0 * ret, 3),
                           "fund_pct": round(100.0 * fund_sum, 3),
                           "pnl": round(leg - cost, 2)})
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 0.0)
        cycles.append({"t": timeline[idx], "net": pnl, "equity": equity, "legs": detail})
        start += HOLD_H

    nets = [c["net"] for c in cycles]
    if not nets:
        return {"cycles": 0}
    mean = sum(nets) / len(nets)
    var = sum((x - mean) ** 2 for x in nets) / (len(nets) - 1) if len(nets) > 1 else 0.0
    se = math.sqrt(var / len(nets)) if len(nets) > 1 else float("inf")
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    half = len(nets) // 2
    srt = sorted(nets, reverse=True)
    return {
        "cycles": len(nets),
        "mean_usd": round(mean, 2),
        "mean_pct": round(100.0 * mean / CAPITAL, 3),
        "t": round(mean / se, 2) if se else None,
        "pf": round(sum(wins) / abs(sum(losses)), 3) if losses and sum(losses) else None,
        "total": round(sum(nets), 2),
        "return_pct": round(100.0 * (equity / CAPITAL - 1.0), 2),
        "max_dd_pct": round(max_dd * 100.0, 2),
        "h1": round(sum(nets[:half]), 2),
        "h2": round(sum(nets[half:]), 2),
        "drop5_mean": round((sum(nets) - sum(srt[:5])) / max(1, len(nets) - 5), 2),
        "rows": cycles,
    }


def main():
    cache = os.path.join(HERE, "bt_cache")
    prices, funding, timeline = load(cache)
    if not prices:
        print("нет данных")
        return 1
    print("монет %d, часов %d" % (len(prices), len(timeline)), flush=True)

    a = h5a(prices, funding, timeline)
    print("H5a персистентность фондирования: n=%d, наклон %.4f, t %s, corr %s, знак совпал %s%%" % (
        a.get("n"), a.get("slope", 0), a.get("t"), a.get("corr"), a.get("sign_same_pct")), flush=True)

    base = run(prices, funding, timeline, 1.0)
    stress = run(prices, funding, timeline, 2.0)
    for tag, r in (("1x", base), ("2x", stress)):
        print("%s | циклов %d | средний %+.2f$ (%+.3f%%) | t %s | PF %s | нетто %+.2f (%+.2f%%) "
              "DD %.2f%% | половины %+.2f / %+.2f | без 5 лучших циклов %+.2f$" % (
                  tag, r["cycles"], r["mean_usd"], r["mean_pct"], r["t"], r["pf"], r["total"],
                  r["return_pct"], r["max_dd_pct"], r["h1"], r["h2"], r["drop5_mean"]), flush=True)

    checks = [
        ("P1 средний нетто > 0", base["mean_usd"] > 0, base["mean_usd"]),
        ("P2 t >= 2.0", (base["t"] or 0) >= 2.0, base["t"]),
        ("P3 PF >= 1.15", (base["pf"] or 0) >= 1.15, base["pf"]),
        ("P4 обе половины > 0", base["h1"] > 0 and base["h2"] > 0, (base["h1"], base["h2"])),
        ("P5 при 2x нетто > 0", stress["mean_usd"] > 0, stress["mean_usd"]),
        ("P6 без 5 лучших > 0", base["drop5_mean"] > 0, base["drop5_mean"]),
    ]
    print("\nкритерии H5b:", flush=True)
    for name, ok, val in checks:
        print("  %-24s %s  %s" % (name, "OK" if ok else "FAIL", val))
    out = {"h5a": a,
           "base": {k: v for k, v in base.items() if k != "rows"},
           "stress": {k: v for k, v in stress.items() if k != "rows"},
           "criteria": {name: bool(ok) for name, ok, _ in checks}}
    json.dump(out, open(os.path.join(HERE, ".probe", "funding_regime.json"), "w"), indent=1)
    json.dump(base.get("rows", [])[:200], open(os.path.join(HERE, ".probe", "funding_cycles.json"), "w"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
