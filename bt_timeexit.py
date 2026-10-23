"""bt_timeexit.py — вход «V» (часовое падение >= 2σ) с чистым временным выходом.

Предрегистрация: research/TIME_EXIT_PREREG.md (23.10, 05:28).
Контроль: тот же вход со стопом/тейком 3% (как §28), чтобы понять, не режут ли
стоп и цель прибыль. Стоимость 0.115% на сделку за круг, стресс ×2.
"""
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from bt_pairs_limits import TAKER_PCT, load_cache  # noqa: E402

SIG_WIN = 168
Z_ENTRY = -2.0
COST_PCT = 2.0 * TAKER_PCT          # 0.115% за круг
BUCKET_MIN = 1


def prep(series, timeline):
    """Часовые доходности и каузальная σ за 168ч для каждой монеты."""
    out = {}
    for c, px in series.items():
        n = len(px)
        r = [0.0] * n
        sd = [0.0] * n
        for i in range(1, n):
            if px[i - 1] > 0:
                r[i] = px[i] / px[i - 1] - 1.0
        for i in range(SIG_WIN + 1, n):
            w = r[i - SIG_WIN:i]
            m = sum(w) / SIG_WIN
            sd[i] = (sum((x - m) ** 2 for x in w) / SIG_WIN) ** 0.5
        out[c] = (px, r, sd)
    return out


def collect(data, timeline, hold, offset, stop_pct=None, take_pct=None):
    """Все сделки: (индекс входа, доходность нетто в %)."""
    n = len(timeline)
    trades = []
    for c, (px, r, sd) in data.items():
        i = SIG_WIN + 2 + offset
        while i + hold < n:
            if sd[i] > 0 and r[i] / sd[i] <= Z_ENTRY:
                if stop_pct is None:
                    net = px[i + hold] / px[i] - 1.0
                else:
                    net = None
                    for j in range(i + 1, i + hold + 1):
                        ch = px[j] / px[i] - 1.0
                        if ch <= -stop_pct:
                            net = -stop_pct
                            break
                        if ch >= take_pct:
                            net = take_pct
                            break
                    if net is None:
                        net = px[i + hold] / px[i] - 1.0
                trades.append((i, 100.0 * net - COST_PCT))
                i += hold          # не входим повторно, пока позиция держится
            else:
                i += 1
    return trades


def bucket_stats(trades):
    """Средняя доходность по часовым корзинам (независимые наблюдения)."""
    by = {}
    for i, net in trades:
        by.setdefault(i, []).append(net)
    buckets = [sum(v) / len(v) for v in by.values()]
    n = len(buckets)
    m = sum(buckets) / n
    s = statistics.stdev(buckets)
    return m, m / (s / math.sqrt(n)), n


def summarize(trades, label, hold):
    if not trades:
        print("  %s: сделок нет" % label)
        return {}
    nets = [t[1] for t in trades]
    m = sum(nets) / len(nets)
    bm, bt, bn = bucket_stats(trades)
    wins = sum(1 for x in nets if x > 0)
    s = sorted(nets)
    print("  %-22s сделок %5d | ср %+0.4f%% | медиана %+0.4f%% | WR %4.1f%% | "
          "корзины t %+5.2f (n=%d)" % (label, len(nets), m, statistics.median(nets),
                                       100.0 * wins / len(nets), bt, bn))
    return {"label": label, "hold": hold, "trades": len(nets), "mean_pct": round(m, 4),
            "bucket_t": round(bt, 2), "buckets": bn,
            "wr": round(100.0 * wins / len(nets), 1), "median": round(statistics.median(nets), 4)}


def main():
    cache = os.path.join(HERE, "bt_cache")
    series, timeline = load_cache(cache)
    data = prep(series, timeline)
    res = []
    for hold in (12, 24, 48):
        tr = collect(data, timeline, hold, 0)
        res.append(summarize(tr, "время %dч" % hold, hold))
    # контроль: стоп/тейк 3% при удержании 24ч
    res.append(summarize(collect(data, timeline, 24, 0, 0.03, 0.03), "стоп/тейк 3% 24ч", 24))
    # устойчивость к фазе часа
    print("\nсдвиги фазы (24ч):")
    offs = []
    for off in range(24):
        tr = collect(data, timeline, 24, off)
        offs.append(summarize(tr, "сдвиг %2d" % off, 24)["mean_pct"])
    pos = sum(1 for x in offs if x > 0)
    print("  медиана по 24 сдвигам %+0.4f%% | плюсовых %d/24" % (statistics.median(offs), pos))
    json.dump({"primary": res, "offsets": offs}, open(os.path.join(HERE, ".probe", "timeexit.json"), "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
