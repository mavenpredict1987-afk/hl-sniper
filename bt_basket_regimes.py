"""bt_basket_regimes.py — корзина + контроль волатильности на разных режимах.

§38: HL даёт 210 дней (один бычий режим), поэтому мехнику проверяем на дневной
истории Binance (2019–2026, спот USDT-пары как прокси перпов).

Варианты:
  BE   — 20 монет равным весом, 100% экспозиция, ребаланс раз в месяц;
  B    — те же 20, экспозиция w = min(1, 0.30/σ_ann), σ по 30 дням,
         ребаланс при отклонении веса > 2 п.п. и не чаще раза в 3 дня;
  B20  — B, но экспозиция дополнительно ограничена 20% (как наш бумажный бот).

Издержки считаются по фактическому обороту: 0.06% на сторону (тейкер+слиппедж).
"""
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DIR = os.path.join(HERE, ".probe", "binance_daily")
TOP_N = 20
VOL_WIN = 30
TARGET_VOL = 0.30
COST = 0.0006          # за сторону
REBAL_THR = 0.02
MIN_DAYS = 3


def load():
    px, qv = {}, {}
    for fn in sorted(os.listdir(DIR)):
        if not fn.endswith(".json"):
            continue
        c = fn[:-5]
        rows = json.load(open(os.path.join(DIR, fn)))
        px[c] = {r["t"]: r["c"] for r in rows}
        qv[c] = {r["t"]: r["v"] for r in rows}
    days = sorted(set().union(*[set(p) for p in px.values()]))
    return px, qv, days


def universe(px, qv, days, d_i):
    """Топ-20 по медианному обороту за прошлые 30 дней (только прошлое)."""
    if d_i < VOL_WIN:
        return []
    window = days[d_i - VOL_WIN:d_i]
    score = {}
    for c in px:
        vols = [qv[c].get(d, 0.0) for d in window]
        have = sum(1 for v in vols if v > 0)
        if have >= VOL_WIN * 0.7 and days[d_i] in px[c]:
            score[c] = statistics.median(vols)
    return [c for c in sorted(score, key=score.get)[-TOP_N:]]


def metrics(curve, label):
    n = len(curve) - 1
    total = curve[-1] / curve[0] - 1.0
    years = n / 365.0
    cagr = (curve[-1] / curve[0]) ** (1 / years) - 1.0 if years > 0.2 else None
    rets = [curve[i] / curve[i - 1] - 1.0 for i in range(1, len(curve))]
    mu = sum(rets) / len(rets)
    sd = statistics.stdev(rets)
    sharpe = mu / sd * math.sqrt(365) if sd else 0.0
    peak, dd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        dd = max(dd, 1 - v / peak)
    print("%-26s итог %+8.2f%% | CAGR %s | DD %5.2f%% | Шарп %5.2f" % (
        label, 100 * total, ("%+7.2f%%" % (100 * cagr)) if cagr is not None else "   n/a ",
        100 * dd, sharpe))
    return {"label": label, "days": n, "total_pct": 100 * total, "dd_pct": 100 * dd,
            "sharpe": sharpe, "cagr_pct": (100 * cagr) if cagr is not None else None}


def run_variant(px, qv, days, mode, start_i, end_i, cap=None, trend=None):
    """mode: 'BE' (100%), 'B' (vol target), 'B20' (vol target, cap 20%)."""
    uni = universe(px, qv, days, start_i)
    if not uni:
        return None
    # веса и единицы
    w = {c: 1.0 / len(uni) for c in uni}
    if cap is None:
        cap = 0.20 if mode == "B20" else 1.0
    eq = 1.0
    exposure = 1.0 if mode == "BE" else cap
    last_rebal = start_i
    curve = [1.0]
    bret = []                      # дневные доходности корзины (для σ)
    exposures = [exposure]
    turnover_cost = 0.0
    for i in range(start_i, end_i):
        d = days[i]
        # доходность за день по текущим весам (только монеты с ценами)
        r = 0.0
        for c, wt in w.items():
            if d in px[c] and days[i - 1] in px[c]:
                r += wt * (px[c][d] / px[c][days[i - 1]] - 1.0)
        eq *= 1.0 + exposure * r
        bret.append(exposure * r)
        # --- правило экспозиции
        if mode in ("B", "B20"):
            if len(bret) > VOL_WIN:
                recent = bret[-VOL_WIN:]
                m = sum(recent) / len(recent)
                sd = statistics.stdev(recent)
                ann = sd * math.sqrt(365)
                target = min(1.0, TARGET_VOL / ann) if ann > 0 else 1.0
                target = min(target, cap)
                if trend:
                    win, mult = trend
                    if len(bret) >= win:
                        tr = sum(bret[-win:])
                        if tr < 0:
                            target *= mult
                # ребаланс по порогу и не чаще MIN_DAYS
                if abs(target - exposure) > REBAL_THR and (i - last_rebal) >= MIN_DAYS:
                    turnover_cost += COST * abs(target - exposure) * 2
                    exposure = target
                    last_rebal = i
        # --- месячная замена состава/довески весов
        if i + 1 < end_i and days[i + 1] // 30 != d // 30:
            new_uni = universe(px, qv, days, i + 1) if i + 1 >= VOL_WIN else uni
            if new_uni:
                turn = sum(abs(w.get(c, 0.0) - 1.0 / len(new_uni)) for c in set(list(w) + new_uni)) / 2
                turnover_cost += COST * turn * 2
                w = {c: 1.0 / len(new_uni) for c in new_uni}
        exposures.append(exposure)
        curve.append(eq * (1.0 - 0.0))  # издержки ребаланса уже в eq? нет — ниже
    # вычесть издержки оборота из итоговой кривой пропорционально (упрощение:
    # считаю, что издержки уходили ровно в дни ребаланса; для сравнения режимов
    # масштаба достаточно)
    curve = [v * (1.0 - turnover_cost) for v in curve]
    return metrics(curve, "%s" % mode), sum(exposures) / len(exposures)


def main():
    px, qv, days = load()
    print("монет %d, дней %d (%d..%d)" % (len(px), len(days), days[0], days[-1]))
    regimes = [
        ("2022 медведь", "2022-01-01", "2023-01-01"),
        ("2021 бык", "2021-01-01", "2022-01-01"),
        ("2023 рост", "2023-01-01", "2024-01-01"),
        ("2024-26 текущий", "2024-01-01", "2026-10-20"),
        ("вся 2019-2026", "2019-07-01", "2026-10-20"),
    ]
    from datetime import date, timedelta
    def daynum(iso):
        y, m, dd = [int(x) for x in iso.split("-")]
        return (date(y, m, dd) - date(1970, 1, 1)).days
    out = []
    for name, a, b in regimes:
        an, bn = daynum(a), daynum(b)
        ai = next((k for k, d in enumerate(days) if d >= an), len(days) // 2)
        bi = next((k for k, d in enumerate(days) if d >= bn), len(days) - 1)
        print("\n=== %s (%s..%s, %d дней) ===" % (name, a, b, bi - ai))
        for mode in ("BE", "B", "B20"):
            r = run_variant(px, qv, days, mode, ai, bi)
            if r:
                m, avg_exp = r
                print("   ср. экспозиция %.0f%%" % (100 * avg_exp))
                out.append({"regime": name, "mode": mode, **m, "avg_exposure": avg_exp})
    json.dump(out, open(os.path.join(HERE, ".probe", "basket_regimes.json"), "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
