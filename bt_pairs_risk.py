"""Парный арбитраж: равный риск на пару (D) и ранний выход (E).

Предрегистрация: research/PAIRS_RISK_SIZE_PREREG.md (написана 23.10 ДО этого файла,
данных для D/E до прогона не смотрели).

Сравниваем на тех же 84 циклах × 48ч из bt_cache:
    A - как в живом боте: одинаковый нотионал на ногу для всех пар;
    D - H1: нотионал пары ∝ 1/σ спреда, clamp(σ_ref/σ_i, 0.5, 2.0),
            σ_i = ст.откл. 168 часовых доходностей спреда (лонг−шорт) до входа,
            σ_ref = медиана σ по парам этого цикла; сумма |нотионалов| не выше A;
    E - H2: D + выход пары раньше 48ч, если накопленное движение спреда
            ушло против неё на 1.0·σ_i·√h (h - часов с входа), хвост входит в P&L
            с издержками на выход.

Издержки те же: 0.23% на пару за круг (0.0575% на ногу за сторону).

Запуск: PYTHONPATH=.devlibs python3 bt_pairs_risk.py [A] [D] [E]
"""
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from bt_pairs_limits import (CAPITAL, CLUSTER_WIN_H, EXPOSURE, HOLD_H, LOOKBACK_H,
                             N_PAIRS, TAKER_PCT, load_cache, pick_pairs, returns)

RISK_WIN_H = 168          # окно σ спреда, часов (7 дней) - как в предрегистрации
CLAMP_LO, CLAMP_HI = 0.5, 2.0
EARLY_EXIT_K = 1.0        # порог раннего выхода: 1.0·σ·√h


def spread_sigma(returns_map, weak, strong, window=RISK_WIN_H):
    """σ часовых доходностей спреда (лонг − шорт) за окно ДО входа."""
    rl, rs = returns_map.get(weak), returns_map.get(strong)
    if not rl or not rs:
        return None
    n = min(len(rl), len(rs), window)
    if n < 30:
        return None
    d = [rl[-n + i] - rs[-n + i] for i in range(n)]
    m = sum(d) / n
    var = sum((x - m) ** 2 for x in d) / (n - 1)
    return math.sqrt(var) if var > 0 else None


def cycle_weights(returns_map, pairs):
    """Веса пар по H1: w_i = clamp(σ_ref/σ_i, 0.5, 2.0), нормированные к среднему 1."""
    sigmas, weights = [], []
    for weak, strong in pairs:
        s = spread_sigma(returns_map, weak, strong)
        sigmas.append(s)
        weights.append(s)
    have = [s for s in sigmas if s and s > 0]
    if len(have) < 2:
        n = len(pairs)
        return [1.0] * n, sigmas
    ref = statistics.median(have)
    raw = []
    for s in sigmas:
        if not s or s <= 0:
            raw.append(1.0)
        else:
            raw.append(min(CLAMP_HI, max(CLAMP_LO, ref / s)))
    mean_raw = sum(raw) / len(raw)
    norm = [w / mean_raw for w in raw]
    return norm, sigmas


def run(series, timeline, mode, capital=CAPITAL):
    """mode: 'A' - базовый; 'D' - равный риск; 'E' - равный риск + ранний выход."""
    coins = sorted(series)
    n = len(timeline)
    warm = max(LOOKBACK_H, CLUSTER_WIN_H) + 1
    equity = capital
    peak = capital
    max_dd = 0.0
    rows = []
    early_exits = 0
    start = warm
    while start + HOLD_H <= n:
        idx = start
        rets = {}
        for c in coins:
            px = series[c]
            if px[idx - LOOKBACK_H] > 0:
                rets[c] = px[idx] / px[idx - LOOKBACK_H] - 1.0
        ranked = sorted(rets, key=rets.get)
        returns_map = returns(series, idx, CLUSTER_WIN_H)
        pairs = pick_pairs(ranked, "A", returns_map, {})

        base_per_leg = equity * EXPOSURE / (2.0 * N_PAIRS) if pairs else 0.0
        weights, sigmas = ([1.0] * len(pairs), [None] * len(pairs))
        if mode in ("D", "E"):
            weights, sigmas = cycle_weights(returns_map, pairs)

        cycle = {"bar": idx, "t": timeline[idx], "pairs": [], "net": 0.0,
                 "weights": [round(w, 3) for w in weights]}
        for i, (weak, strong) in enumerate(pairs):
            per_leg = base_per_leg * weights[i]
            pnl = 0.0
            gross = 0.0
            closed_at = HOLD_H
            if mode == "E" and sigmas[i]:
                # путь спреда по часам: ранний выход при движении против пары
                # больше чем 1.0σ√h (в долях нотионала ноги)
                sig = sigmas[i]
                for h in range(1, HOLD_H + 1):
                    e_l, x_l = series[weak][idx], series[weak][idx + h]
                    e_s, x_s = series[strong][idx], series[strong][idx + h]
                    r = (x_l / e_l - 1.0) - (x_s / e_s - 1.0)
                    gross = per_leg * r
                    if r <= -EARLY_EXIT_K * sig * math.sqrt(h):
                        closed_at = h
                        early_exits += 1
                        break
                if closed_at == HOLD_H:
                    gross = 0.0
                    for sym, side in ((weak, "long"), (strong, "short")):
                        e = series[sym][idx]
                        x = series[sym][idx + HOLD_H]
                        gross += per_leg * (x / e - 1.0) * (1.0 if side == "long" else -1.0)
            else:
                for sym, side in ((weak, "long"), (strong, "short")):
                    e = series[sym][idx]
                    x = series[sym][idx + HOLD_H]
                    gross += per_leg * (x / e - 1.0) * (1.0 if side == "long" else -1.0)
            cost = 2.0 * per_leg * (TAKER_PCT / 100.0) * 2.0
            pnl = gross - cost
            cycle["pairs"].append({"long": weak, "short": strong, "gross": gross,
                                   "cost": cost, "pnl": pnl, "w": round(weights[i], 3),
                                   "sigma": sigmas[i], "hours": closed_at})
            cycle["net"] += pnl
        equity += cycle["net"]
        if equity > peak:
            peak = equity
        max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 0.0)
        cycle["equity"] = equity
        cycle["n_pairs"] = len(pairs)
        rows.append(cycle)
        start += HOLD_H

    nets = [c["net"] for c in rows if c["n_pairs"] > 0]
    shares, dominated = [], 0
    for c in rows:
        if c["n_pairs"] == 0:
            continue
        pnls = [p["pnl"] for p in c["pairs"]]
        worst = max(pnls, key=abs)
        if abs(c["net"]) > 1e-9:
            shares.append(abs(worst) / abs(c["net"]))
            if abs(worst) > abs(c["net"]):
                dominated += 1
    half = len(nets) // 2
    halves = {}
    for tag, chunk in (("h1", nets[:half]), ("h2", nets[half:])):
        eq, pk, dd = capital, capital, 0.0
        for x in chunk:
            eq += x
            pk = max(pk, eq)
            dd = max(dd, (pk - eq) / pk)
        halves[tag] = {"net": round(sum(chunk), 2), "dd_pct": round(dd * 100.0, 2)}
    return {
        "mode": mode,
        "cycles": len(nets),
        "total_net": round(sum(nets), 2),
        "return_pct": round((equity / capital - 1.0) * 100.0, 2),
        "max_dd_pct": round(max_dd * 100.0, 2),
        "worst_cycle": round(min(nets), 2) if nets else None,
        "median_cycle": round(statistics.median(nets), 2) if nets else None,
        "median_worst_share": round(statistics.median(shares), 3) if shares else None,
        "dominated_share": round(dominated / len(nets), 3) if nets else None,
        "halves": halves,
        "early_exits": early_exits,
        "weights_avg": round(statistics.mean([w for c in rows for w in c["weights"]]), 3) if rows else None,
        "rows": rows,
    }


def main():
    cache = os.path.join(HERE, "bt_cache")
    series, timeline = load_cache(cache)
    if not series:
        print("нет данных в bt_cache")
        return 1
    print("монет: %d, часов: %d" % (len(series), len(timeline)), flush=True)
    modes = tuple(sys.argv[1:]) or ("A", "D", "E")
    res = {}
    for mode in modes:
        r = run(series, timeline, mode)
        res[mode] = {k: v for k, v in r.items() if k != "rows"}
        res[mode]["rows"] = r["rows"]
        print("%s | нетто %+9.2f (%+6.2f%%) | DD %5.2f%% | худший %+8.2f | медиана %+7.2f | "
              "доля худшей пары %.2f | доминант %.1f%% | веса x%.2f | ранних выходов %d" % (
                  mode, r["total_net"], r["return_pct"], r["max_dd_pct"], r["worst_cycle"] or 0,
                  r["median_cycle"] or 0, r["median_worst_share"] or 0,
                  100.0 * (r["dominated_share"] or 0), r["weights_avg"] or 1.0,
                  r["early_exits"]), flush=True)
        print("     половины: h1 %+.2f (DD %.2f%%) | h2 %+.2f (DD %.2f%%)" % (
            r["halves"]["h1"]["net"], r["halves"]["h1"]["dd_pct"],
            r["halves"]["h2"]["net"], r["halves"]["h2"]["dd_pct"]), flush=True)
        json.dump(r["rows"], open(os.path.join(HERE, ".probe", "risk_%s.json" % mode), "w"))

    if "A" not in res:
        json.dump(res, open(os.path.join(HERE, ".probe", "risk_summary.json"), "w"), indent=1)
        return 0
    a = res["A"]
    print("\nкритерии предрегистрации против A:")
    for mode in [m for m in ("D", "E") if m in res]:
        m = res[mode]
        l1 = m["total_net"] >= 0.90 * a["total_net"]
        l2 = m["max_dd_pct"] <= 0.85 * a["max_dd_pct"]
        l3 = (m["worst_cycle"] or 0) >= (a["worst_cycle"] or 0)
        l4 = (m["median_cycle"] or 0) >= (a["median_cycle"] or 0)
        worse = sum(1 for x, y in zip(m["rows"], a["rows"]) if x["net"] < y["net"])
        l5 = worse / max(1, m["cycles"]) <= 0.55
        l6 = (m["halves"]["h1"]["dd_pct"] <= a["halves"]["h1"]["dd_pct"]
              and m["halves"]["h2"]["dd_pct"] <= a["halves"]["h2"]["dd_pct"])
        print("%s: L1 %s (%.2f vs %.2f) | L2 %s (%.2f%% vs %.2f%%) | L3 %s (%+.2f vs %+.2f) | "
              "L4 %s (%+.2f vs %+.2f) | L5 %s (%d/%d хуже) | L6 %s" % (
                  mode, "OK" if l1 else "FAIL", m["total_net"], a["total_net"],
                  "OK" if l2 else "FAIL", m["max_dd_pct"], a["max_dd_pct"],
                  "OK" if l3 else "FAIL", m["worst_cycle"] or 0, a["worst_cycle"] or 0,
                  "OK" if l4 else "FAIL", m["median_cycle"] or 0, a["median_cycle"] or 0,
                  "OK" if l5 else "FAIL", worse, m["cycles"], "OK" if l6 else "FAIL"))
        if mode == "E":
            print("     E-дополнение: DD<=0.75A %s (%.2f%% vs %.2f%%) или нетто>=A %s (%+.2f vs %+.2f)" % (
                "OK" if m["max_dd_pct"] <= 0.75 * a["max_dd_pct"] else "FAIL",
                m["max_dd_pct"], 0.75 * a["max_dd_pct"],
                "OK" if m["total_net"] >= a["total_net"] else "FAIL",
                m["total_net"], a["total_net"]))
        res[mode]["criteria"] = {"L1": l1, "L2": l2, "L3": l3, "L4": l4, "L5": l5,
                                 "L6": l6, "worse_cycles": worse}
    json.dump({k: {kk: vv for kk, vv in v.items() if kk != "rows"} for k, v in res.items()},
              open(os.path.join(HERE, ".probe", "risk_summary.json"), "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
