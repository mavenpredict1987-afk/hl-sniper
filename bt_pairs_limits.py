"""Парный арбитраж: лимиты на монету и корреляционный кластер (A/B/C).

Предрегистрация: research/PAIRS_LIMITS_PREREG.md (написана 22.10 ДО этого файла).
Сравниваем три варианта на одних и тех же 208 днях из bt_cache:
    A - как в живом боте: 3 пары, лонг самой сильной, шорт самой слабой,
        без лимитов на кластеры и без отсева не-нейтральных пар;
    B - + лимит "не более одной ноги на корреляционный кластер"
        (кластеры строятся по 30-дневной корреляции часовых доходностей,
        порог 0.6, жадно по среднему сходству), классификация только на прошлом;
    C - + B и отсев пар, где корреляция лонг/шорт за 30 дней выше 0.5.

Издержки как в живом прогоне: 0.23% на пару за круг, то есть 0.0575% на ногу
за сторону (0.115% на ногу за круг).

Запуск: PYTHONPATH=.devlibs python3 bt_pairs_limits.py
"""
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

LOOKBACK_H = 168          # ранжировщик: 7 дней
HOLD_H = 48               # цикл: 48 часов
N_PAIRS = 3
EXPOSURE = 0.20           # как в живом боте после его прогонов
CAPITAL = 10000.0
TAKER_PCT = 0.0575        # на ногу за сторону
CLUSTER_WIN_H = 720       # окно корреляции для кластеров: 30 дней
CLUSTER_CORR = 0.6        # порог объединения в кластер
NEUTRAL_CORR = 0.5        # выше этого лонг и шорт - не пара, а ставка


def load_cache(cache_dir):
    """Возвращает {coin: [(t, close), ...]} плюс общий таймлайн."""
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
        if len(cds) < CLUSTER_WIN_H + 8:
            continue
        out[coin] = [(int(c["t"]), float(c["c"])) for c in cds]
    common = None
    for rows in out.values():
        s = set(t for t, _ in rows)
        common = s if common is None else (common & s)
    common = sorted(common or [])
    series = {}
    for coin, rows in out.items():
        m = dict(rows)
        series[coin] = [m[t] for t in common]
    return series, common


def returns(series, end_idx, window):
    """Часовые доходности за window часов ДО end_idx (только прошлое)."""
    out = {}
    start = end_idx - window
    for coin, px in series.items():
        seg = px[start:end_idx + 1]
        if len(seg) < window + 1 or min(seg) <= 0:
            continue
        out[coin] = [seg[i] / seg[i - 1] - 1.0 for i in range(1, len(seg))]
    return out


def corr(a, b):
    n = min(len(a), len(b))
    if n < 30:
        return 0.0
    a, b = a[-n:], b[-n:]
    ma = sum(a) / n
    mb = sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return 0.0
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return cov / (va ** 0.5 * vb ** 0.5)


def clusters_from(returns_map, coins, threshold=CLUSTER_CORR):
    """Жадная агломерация по среднему сходству с порогом threshold."""
    groups = {c: [c] for c in coins}
    cache = {}
    coins = [c for c in coins if c in returns_map]

    def avg_corr(g1, g2):
        key = (tuple(sorted(g1)), tuple(sorted(g2)))
        if key in cache:
            return cache[key]
        vals = [corr(returns_map[a], returns_map[b]) for a in g1 for b in g2]
        v = sum(vals) / len(vals) if vals else 0.0
        cache[key] = v
        return v

    changed = True
    while changed:
        changed = False
        best = None
        keys = sorted(groups)
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                v = avg_corr(groups[a], groups[b])
                if v >= threshold and (best is None or v > best[0]):
                    best = (v, a, b)
        if best:
            _, a, b = best
            groups[a] = groups[a] + groups[b]
            del groups[b]
            changed = True
    out = {}
    for root, members in groups.items():
        for m in members:
            out[m] = root
    return out


def pick_pairs(ranked, mode, returns_map, cluster_of):
    """Живой бот: лонг САМОЙ СЛАБОЙ монеты, шорт САМОЙ СИЛЬНОЙ (фейд движения).

    ranked - монеты по 7-дневному доходу по возрастанию (слабые впереди).
    A: первые N слабых и первые N сильных, пара i = weak[i] / strong[i].
    B: то же, но монета, чей кластер уже занят, пропускается -> берётся следующий
       кандидат с края ранга, если он из свободного кластера.
    C: + пропускаем пару, если корреляция лонг/шорт за 30 дней выше 0.5.
    """
    n = len(ranked)
    if n < 2 * N_PAIRS:
        return []
    weak_order = list(ranked)
    strong_order = list(reversed(ranked))
    used = set()
    used_clusters = set()
    pairs = []

    def free(coin):
        if coin in used:
            return False
        if mode in ("B", "C") and cluster_of.get(coin) in used_clusters:
            return False
        return True

    for weak in weak_order:
        if len(pairs) >= N_PAIRS:
            break
        if not free(weak):
            continue
        strong = None
        for cand in strong_order:
            if not free(cand) or cand == weak:
                continue
            if mode == "C":
                rl, rs = returns_map.get(weak), returns_map.get(cand)
                if rl and rs and corr(rl, rs) > NEUTRAL_CORR:
                    continue
            strong = cand
            break
        if strong is None:
            continue
        pairs.append((weak, strong))
        used.add(weak)
        used.add(strong)
        if mode in ("B", "C"):
            used_clusters.add(cluster_of.get(weak))
            used_clusters.add(cluster_of.get(strong))
    return pairs


def run(series, timeline, mode, capital=CAPITAL):
    coins = sorted(series)
    n = len(timeline)
    warm = max(LOOKBACK_H, CLUSTER_WIN_H) + 1
    equity = capital
    peak = capital
    max_dd = 0.0
    cycle_rows = []
    cycle_idx = 0
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
        cluster_of = {}
        if mode in ("B", "C"):
            cluster_of = clusters_from(returns_map, list(rets))
        pairs = pick_pairs(ranked, mode, returns_map, cluster_of)

        per_leg = equity * EXPOSURE / (2.0 * N_PAIRS) if pairs else 0.0
        cycle = {"bar": idx, "t": timeline[idx], "pairs": [], "net": 0.0}
        for weak, strong in pairs:
            gross = 0.0
            for sym, side in ((weak, "long"), (strong, "short")):
                e = series[sym][idx]
                x = series[sym][idx + HOLD_H]
                r = (x / e - 1.0) * (1.0 if side == "long" else -1.0)
                gross += per_leg * r
            cost = 2.0 * per_leg * (TAKER_PCT / 100.0) * 2.0   # 2 ноги х вход+выход
            pnl = gross - cost
            cycle["pairs"].append({"long": strong, "short": weak,
                                   "gross": gross, "cost": cost, "pnl": pnl})
            cycle["net"] += pnl
        equity += cycle["net"]
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        cycle["equity"] = equity
        cycle["n_pairs"] = len(pairs)
        cycle_rows.append(cycle)
        cycle_idx += 1
        start += HOLD_H

    nets = [c["net"] for c in cycle_rows if c["n_pairs"] > 0]
    shares = []
    dominated = 0
    coverages = []
    for c in cycle_rows:
        if c["n_pairs"] == 0:
            continue
        pnls = [p["pnl"] for p in c["pairs"]]
        worst = max(pnls, key=abs)
        if abs(c["net"]) > 1e-9:
            shares.append(abs(worst) / abs(c["net"]))
            if abs(worst) > abs(c["net"]):
                dominated += 1
        for p in c["pairs"]:
            cost = 2.0 * (CAPITAL * EXPOSURE / (2.0 * N_PAIRS)) * (TAKER_PCT / 100.0) * 2.0
            if cost > 0:
                coverages.append(p["gross"] / cost)
    return {
        "mode": mode,
        "cycles": len(nets),
        "total_net": round(sum(nets), 2),
        "return_pct": round((equity / capital - 1.0) * 100.0, 2),
        "max_dd_pct": round(max_dd * 100.0, 2),
        "worst_cycle": round(min(nets), 2) if nets else None,
        "median_worst_share": round(statistics.median(shares), 3) if shares else None,
        "dominated_share": round(dominated / len(nets), 3) if nets else None,
        "median_coverage": round(statistics.median(coverages), 3) if coverages else None,
        "pairs_avg": round(sum(c["n_pairs"] for c in cycle_rows) / max(1, len(cycle_rows)), 2),
        "rows": cycle_rows,
    }


def main():
    cache = os.path.join(HERE, "bt_cache")
    series, timeline = load_cache(cache)
    if not series:
        print("нет данных в bt_cache")
        return 1
    print("монет: %d, часов: %d, окно %s..%s" % (
        len(series), len(timeline),
        timeline[0], timeline[-1]), flush=True)
    modes = tuple(sys.argv[1:]) or ("A", "B", "C")
    res = {}
    for mode in modes:
        r = run(series, timeline, mode)
        res[mode] = {k: v for k, v in r.items() if k != "rows"}
        print("%s | циклов %3d | нетто %+9.2f (%+6.2f%%) | DD %5.2f%% | худший цикл %+8.2f | "
              "медиана доли худшей пары %.2f | циклов-доминант %.1f%% | покрытие x%.2f | пар/цикл %.2f" % (
                  mode, r["cycles"], r["total_net"], r["return_pct"], r["max_dd_pct"],
                  r["worst_cycle"] or 0.0, r["median_worst_share"] or 0.0,
                  100.0 * (r["dominated_share"] or 0.0), r["median_coverage"] or 0.0,
                  r["pairs_avg"]), flush=True)
        json.dump(r["rows"], open(os.path.join(HERE, ".probe", "limits_%s.json" % mode), "w"))
    if "A" not in res:
        print("нет варианта A в этом прогоне - критерии сравнивать не с чем")
        json.dump(res, open(os.path.join(HERE, ".probe", "limits_summary.json"), "w"), indent=1)
        return 0
    a = res["A"]
    print("\nкритерии против A:")
    for mode in [m for m in ("B", "C") if m in res]:
        m = res[mode]
        l1 = (m["median_worst_share"] or 1.0) <= 0.5
        l2 = (m["dominated_share"] or 1.0) <= 0.15
        l3 = m["total_net"] >= 0.9 * a["total_net"]
        l4 = (m["worst_cycle"] or 0) >= (a["worst_cycle"] or 0)
        l5 = (m["median_coverage"] or 0) >= (a["median_coverage"] or 0)
        print("%s: L1 %s (%.2f) | L2 %s (%.1f%%) | L3 %s (%+.2f vs %+.2f) | L4 %s (%+.2f vs %+.2f) | L5 %s (x%.2f vs x%.2f)" % (
            mode, "OK" if l1 else "FAIL", m["median_worst_share"] or 0,
            "OK" if l2 else "FAIL", 100.0 * (m["dominated_share"] or 0),
            "OK" if l3 else "FAIL", m["total_net"], a["total_net"],
            "OK" if l4 else "FAIL", m["worst_cycle"] or 0, a["worst_cycle"] or 0,
            "OK" if l5 else "FAIL", m["median_coverage"] or 0, a["median_coverage"] or 0))
    json.dump(res, open(os.path.join(HERE, ".probe", "limits_summary.json"), "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
