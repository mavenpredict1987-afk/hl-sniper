"""bt_pairs_regimes.py — как пары (и их зеркало) ведут себя в разных режимах.

Проверка к A/B: пары — контроль A. На данных HL было только одно бычье окно
(§35: артефакт сетки). Здесь — дневная история Binance 2019–2026, 20-монетная
корзина по ликвидности (только прошлое), лонг 3 слабейших / шорт 3 сильнейших
по 7-дневной доходности, удержание 2 дня, издержки 0.23% на пару за круг.

Также считаю зеркало (моментум) — чтобы видеть знак в каждом режиме.
"""
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bt_basket_regimes as R  # noqa: E402

N_PAIRS = 3
LOOKBACK = 7          # дней
HOLD = 2              # дня (48ч)
COST_PCT = 0.23       # на пару за круг, %
EXPOSURE = 0.20


def cycles(px, qv, days, ai, bi, mirror=False):
    """Возвращает список доходностей пары за цикл, % (до издержек и после)."""
    out = []
    i = ai
    while i + HOLD < bi:
        uni = R.universe(px, qv, days, i)
        if len(uni) < 2 * N_PAIRS:
            i += HOLD
            continue
        i0, i1 = days[i - LOOKBACK], days[i]
        i2 = days[i + HOLD]
        rets = {}
        for c in uni:
            if i0 in px[c] and i1 in px[c]:
                rets[c] = px[c][i1] / px[c][i0] - 1.0
        ranked = sorted(rets, key=rets.get)
        if len(ranked) < 2 * N_PAIRS:
            i += HOLD
            continue
        weak = ranked[:N_PAIRS]
        strong = ranked[-N_PAIRS:]
        longs, shorts = (weak, strong) if not mirror else (strong, weak)
        spreads = []
        for lo, sh in zip(longs, shorts):
            if i2 in px[lo] and i2 in px[sh]:
                spreads.append(px[lo][i2] / px[lo][i1] - 1.0 - (px[sh][i2] / px[sh][i1] - 1.0))
        if len(spreads) == N_PAIRS:
            g = 100.0 * sum(spreads) / len(spreads)
            out.append((g, g - COST_PCT))
        i += HOLD
    return out


def stats(rows, label):
    if not rows:
        print("  %s: нет данных" % label)
        return None
    gross = [g for g, _ in rows]
    net = [n for _, n in rows]
    m = sum(net) / len(net)
    sd = statistics.stdev(net) if len(net) > 1 else 0.0
    t = m / (sd / math.sqrt(len(net))) if sd else 0.0
    # портфельная кривая: экспозиция 20%, капитал компаундится
    eq = 1.0
    curve = [1.0]
    for _, n in rows:
        eq *= 1.0 + EXPOSURE * (n / 100.0)
        curve.append(eq)
    peak, dd = 1.0, 0.0
    for v in curve:
        peak = max(peak, v)
        dd = max(dd, 1.0 - v / peak)
    yr = len(rows) * HOLD / 365.0
    cagr = (eq ** (1 / yr) - 1.0) if yr > 0.1 else None
    print("  %-10s циклов %3d | gross %+0.3f%% | нетто %+0.3f%% | t %+5.2f | "
          "итог %+7.2f%% | DD %5.2f%% | CAGR %s" % (
              label, len(rows), sum(gross) / len(gross), m, t, 100 * (eq - 1), 100 * dd,
              ("%+6.1f%%" % (100 * cagr)) if cagr is not None else "  n/a"))
    return {"cycles": len(rows), "gross_mean": sum(gross) / len(gross), "net_mean": m,
            "t": t, "total_pct": 100 * (eq - 1), "dd_pct": 100 * dd,
            "cagr_pct": (100 * cagr) if cagr is not None else None}


def main():
    px, qv, days = R.load()
    from datetime import date

    def dn(iso):
        y, m, d = [int(x) for x in iso.split("-")]
        return (date(y, m, d) - date(1970, 1, 1)).days

    regimes = [
        ("2020 COVID", "2020-01-01", "2021-01-01"),
        ("2021 бык", "2021-01-01", "2022-01-01"),
        ("2022 медведь", "2022-01-01", "2023-01-01"),
        ("2023 рост", "2023-01-01", "2024-01-01"),
        ("2024-26", "2024-01-01", "2026-10-20"),
        ("вся история", "2019-07-01", "2026-10-20"),
    ]
    res = []
    for name, a, b in regimes:
        an, bn = dn(a), dn(b)
        ai = next(k for k, d in enumerate(days) if d >= an)
        bi = next(k for k, d in enumerate(days) if d >= bn)
        print("\n=== %s (%d дней) ===" % (name, bi - ai))
        for mirror, label in ((False, "пары A"), (True, "моментум")):
            st = stats(cycles(px, qv, days, ai, bi, mirror), label)
            if st:
                res.append({"regime": name, "mode": label, **st})
    json.dump(res, open(os.path.join(HERE, ".probe", "pairs_regimes.json"), "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
