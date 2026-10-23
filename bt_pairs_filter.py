"""H3: отсев узких спредов + проверка на ликвидности + стресс издержек.

Предрегистрация: research/PAIRS_SIGMA_FILTER_PREREG.md (04:20, ДО этого прогона;
помечено, что σ-таблицу из §30 уже видел — вердикт только по holdout).

Варианты на одних и тех же циклах из bt_cache:
    A - как в живом боте: 3 пары, равный нотионал, все пары допускаются;
    F - из 6 пар-кандидатов оставляем 3 с σ спреда ВЫШЕ медианы кандидатов
        (размер ноги тот же, экспозиция не меняется - иначе тест смешался бы
        с простым уменьшением риска).

Плюс диагностика H3a: P&L на единицу веса по терцилям ликвидности × терцилям σ,
чтобы отличить реальный эффект от недооценённых издержек на неликвиде.

Издержки: 0.23% на пару за круг, умноженные на --mult (1, 2, 3).

Запуск: PYTHONPATH=.devlibs python3 bt_pairs_filter.py
"""
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from bt_pairs_limits import (CAPITAL, EXPOSURE, HOLD_H, LOOKBACK_H, N_PAIRS,
                             TAKER_PCT, prep, corr_prep, returns)
from bt_pairs_risk import spread_sigma

CLUSTER_WIN_H = 720
LIQ_WIN_H = 720           # окно медианного объёма: 30 дней
CANDIDATES = 6            # пар-кандидатов для отбора в F
MULTS = (1, 2, 3)


def load_cache_vol(cache_dir):
    """Закрытия и USD-объём по общему таймлайну."""
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
        if len(cds) < CLUSTER_WIN_H + 8:
            continue
        raw[coin] = {int(c["t"]): (float(c["c"]), float(c["v"]) * float(c["c"]))
                     for c in cds}
    common = None
    for rows in raw.values():
        s = set(rows)
        common = s if common is None else (common & s)
    timeline = sorted(common or [])
    series, usdvol = {}, {}
    for coin, rows in raw.items():
        series[coin] = [rows[t][0] for t in timeline]
        usdvol[coin] = [rows[t][1] for t in timeline]
    return series, usdvol, timeline


def build_pairs(ranked, k):
    """Пары weak[i]/strong[i] как в живом боте, до k штук, без повторов монет."""
    weak_order, strong_order = list(ranked), list(reversed(ranked))
    used, pairs = set(), []
    for weak in weak_order:
        if len(pairs) >= k:
            break
        if weak in used:
            continue
        strong = None
        for cand in strong_order:
            if cand == weak or cand in used:
                continue
            strong = cand
            break
        if strong is None:
            continue
        pairs.append((weak, strong))
        used.add(weak)
        used.add(strong)
    return pairs


def precompute(series, timeline):
    """Один проход по циклам: ранжирование, карта доходностей, σ и ликвидность."""
    coins = sorted(series)
    n = len(timeline)
    warm = CLUSTER_WIN_H + 1
    out = []
    start = warm
    while start + HOLD_H <= n:
        idx = start
        rets = {}
        for c in coins:
            if series[c][idx - LOOKBACK_H] > 0:
                rets[c] = series[c][idx] / series[c][idx - LOOKBACK_H] - 1.0
        ranked = sorted(rets, key=rets.get)
        rmap = returns(series, idx, CLUSTER_WIN_H)
        out.append({"idx": idx, "t": timeline[idx], "ranked": ranked, "rmap": rmap})
        start += HOLD_H
    return out


def pair_liq(usdvol, idx, weak, strong):
    lo = max(0, idx - LIQ_WIN_H)
    vals = []
    for c in (weak, strong):
        seg = usdvol[c][lo:idx]
        if seg:
            vals.append(statistics.median(seg))
    return statistics.mean(vals) if vals else 0.0


def run(cycles, series, usdvol, mode, mult=1):
    equity, peak, max_dd = CAPITAL, CAPITAL, 0.0
    rows = []
    for cy in cycles:
        idx, rmap = cy["idx"], cy["rmap"]
        ranked = cy["ranked"]
        if mode == "F":
            cand = build_pairs(ranked, CANDIDATES)
            sig = [(spread_sigma(rmap, w, s), w, s) for w, s in cand]
            sig = [x for x in sig if x[0]]
            if len(sig) >= 2:
                med = statistics.median([x[0] for x in sig])
                keep = sorted([x for x in sig if x[0] >= med], key=lambda x: -x[0])[:N_PAIRS]
                pairs = [(w, s) for _, w, s in keep]
                sig_of = {w: sg for sg, w, s in keep}
            else:
                pairs, sig_of = [], {}
        else:
            pairs = build_pairs(ranked, N_PAIRS)
            sig_of = {w: spread_sigma(rmap, w, s) for w, s in pairs}

        per_leg = equity * EXPOSURE / (2.0 * N_PAIRS) if pairs else 0.0
        cycle = {"t": cy["t"], "pairs": [], "net": 0.0}
        for weak, strong in pairs:
            gross = 0.0
            for sym, side in ((weak, "long"), (strong, "short")):
                e, x = series[sym][idx], series[sym][idx + HOLD_H]
                gross += per_leg * (x / e - 1.0) * (1.0 if side == "long" else -1.0)
            cost = 2.0 * per_leg * (TAKER_PCT / 100.0) * 2.0 * mult
            pnl = gross - cost
            cycle["pairs"].append({"long": weak, "short": strong, "gross": gross,
                                   "cost": cost, "pnl": pnl,
                                   "sigma": sig_of.get(weak),
                                   "liq": pair_liq(usdvol, idx, weak, strong)})
            cycle["net"] += pnl
        equity += cycle["net"]
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak if peak > 0 else 0.0)
        cycle["equity"] = equity
        cycle["n_pairs"] = len(pairs)
        rows.append(cycle)

    nets = [c["net"] for c in rows if c["n_pairs"] > 0]
    half = len(rows) // 2

    def slice_stats(chunk):
        eq, pk, dd = CAPITAL, CAPITAL, 0.0
        for c in chunk:
            eq += c["net"]
            pk = max(pk, eq)
            dd = max(dd, (pk - eq) / pk)
        ns = [c["net"] for c in chunk if c["n_pairs"] > 0]
        return {"net": round(sum(ns), 2), "dd_pct": round(dd * 100.0, 2),
                "cycles": len(ns),
                "median": round(statistics.median(ns), 2) if ns else None}

    return {
        "mode": mode, "mult": mult, "cycles": len(nets),
        "total_net": round(sum(nets), 2),
        "max_dd_pct": round(max_dd * 100.0, 2),
        "worst_cycle": round(min(nets), 2) if nets else None,
        "median_cycle": round(statistics.median(nets), 2) if nets else None,
        "full": slice_stats(rows),
        "holdout": slice_stats(rows[half:]),
        "first_half": slice_stats(rows[:half]),
        "rows": rows,
    }


def h3a_diagnostic(rows):
    """P&L на единицу веса по терцилям (ликвидность × σ) на варианте A."""
    pairs = [p for c in rows for p in c["pairs"] if p.get("sigma") and p.get("liq")]
    if len(pairs) < 30:
        return []
    # вес не масштабировался в A, поэтому P&L уже "на единицу размера"
    by_liq = sorted(pairs, key=lambda p: p["liq"])
    out = []
    q = len(by_liq) // 3
    for i, name in enumerate(("низкая ликв.", "средняя ликв.", "высокая ликв.")):
        chunk = by_liq[i * q:(i + 1) * q] if i < 2 else by_liq[2 * q:]
        by_sig = sorted(chunk, key=lambda p: p["sigma"])
        m = len(by_sig) // 3
        cells = []
        for j, sname in enumerate(("σ низ", "σ сер", "σ выс")):
            cell = by_sig[j * m:(j + 1) * m] if j < 2 else by_sig[2 * m:]
            cells.append((sname, len(cell), round(sum(x["pnl"] for x in cell), 2)))
        out.append((name, len(chunk), cells))
    return out


def main():
    cache = os.path.join(HERE, "bt_cache")
    series, usdvol, timeline = load_cache_vol(cache)
    if not series:
        print("нет данных в bt_cache")
        return 1
    print("монет: %d, часов: %d" % (len(series), len(timeline)), flush=True)
    cycles = precompute(series, timeline)
    print("циклов: %d" % len(cycles), flush=True)

    res = {}
    for mode in ("A", "F"):
        for mult in MULTS:
            r = run(cycles, series, usdvol, mode, mult)
            res["%s_x%d" % (mode, mult)] = r
            print("%s x%d | полный: нетто %+9.2f (%+6.2f%%) DD %5.2f%% худший %+8.2f | "
                  "holdout: нетто %+8.2f DD %5.2f%% медиана %+6.2f | 1-я половина: %+8.2f" % (
                      mode, mult, r["full"]["net"], 100.0 * r["full"]["net"] / CAPITAL,
                      r["full"]["dd_pct"], r["worst_cycle"] or 0,
                      r["holdout"]["net"], r["holdout"]["dd_pct"], r["holdout"]["median"] or 0,
                      r["first_half"]["net"]), flush=True)

    diag = h3a_diagnostic(res["A_x1"]["rows"])
    print("\nH3a: P&L по терцилям ликвидности и σ (вариант A, 1x издержки):", flush=True)
    for name, n, cells in diag:
        print("  %-16s n=%3d | %s" % (
            name, n, " | ".join("%s: n=%d %+8.2f" % (s, cn, p) for s, cn, p in cells)), flush=True)

    a2, f2 = res["A_x2"], res["F_x2"]
    f3, a1, f1 = res["F_x3"], res["A_x1"], res["F_x1"]
    m1 = f2["holdout"]["net"] >= a2["holdout"]["net"]
    m2 = f2["holdout"]["dd_pct"] <= a2["holdout"]["dd_pct"]
    m3 = f3["full"]["net"] > 0
    m4 = (f2["holdout"]["median"] or 0) >= (a2["holdout"]["median"] or 0)
    illiquid_only = bool(diag) and all(
        (cells[2][2] <= 0 or cells[2][2] <= cells[0][2]) for _, _, cells in diag[:1])
    m5 = not illiquid_only
    print("\nкритерии H3b (holdout, 2x издержки):", flush=True)
    print("  M1 нетто(F)>=нетто(A): %s (%+.2f vs %+.2f)" % (
        "OK" if m1 else "FAIL", f2["holdout"]["net"], a2["holdout"]["net"]))
    print("  M2 DD(F)<=DD(A): %s (%.2f%% vs %.2f%%)" % (
        "OK" if m2 else "FAIL", f2["holdout"]["dd_pct"], a2["holdout"]["dd_pct"]))
    print("  M3 нетто(F)>0 при 3x: %s (%+.2f)" % (
        "OK" if m3 else "FAIL", f3["full"]["net"]))
    print("  M4 медиана F>=A на holdout: %s (%+.2f vs %+.2f)" % (
        "OK" if m4 else "FAIL", f2["holdout"]["median"] or 0, a2["holdout"]["median"] or 0))
    print("  M5 эффект не только в нижней ликвидности: %s" % ("OK" if m5 else "FAIL"))
    print("  (контекст 1x: holdout A %+.2f / F %+.2f)" % (
        a1["holdout"]["net"], f1["holdout"]["net"]), flush=True)

    summary = {k: {kk: vv for kk, vv in v.items() if kk != "rows"} for k, v in res.items()}
    summary["criteria"] = {"M1": m1, "M2": m2, "M3": m3, "M4": m4, "M5": m5}
    json.dump(summary, open(os.path.join(HERE, ".probe", "filter_summary.json"), "w"), indent=1)
    json.dump({"h3a": [{"liq": n, "n": c, "cells": cells} for n, c, cells in diag]},
              open(os.path.join(HERE, ".probe", "filter_h3a.json"), "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
