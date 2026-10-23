"""Lead-lag: «BTC резко двинулся -> отставший альт догоняет».

Предрегистрация: research/LEADLAG_PREREG.md (04:40, ДО этого файла).
Триггер: z = часовой доход BTC / σ часовых доходов BTC за 168ч; |z| >= 2.
Кандидаты: 3 монеты с наименьшим часовым доходом из топ-20 по медианному
часовому USD-объёму за 30 дней до входа (BTC/ETH - только триггер, не кандидаты).
Вход по закрытию бара t, выход по закрытию t+h, h = 1, 3, 6.
Издержки: 0.115% на монету за круг (0.045% тейкер + 0.0125% слиппедж * 2 стороны);
стресс 2x обязателен (N5).

Запуск: PYTHONPATH=.devlibs python3 bt_leadlag.py
"""
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from bt_pairs_filter import load_cache_vol

SIGMA_WIN = 168
LIQ_WIN = 720
UNIVERSE_TOP = 20
CANDIDATES = 3
Z_TRIG = 2.0
COST_ROUND_PCT = 0.115     # % на монету за круг, база
LEADERS = ("BTC", "ETH")


def rolling_sigma(x, win):
    """σ по окну win, только прошлое (на индексе i - по x[i-win:i])."""
    out = [None] * len(x)
    s = s2 = 0.0
    for i, v in enumerate(x):
        s += v
        s2 += v * v
        if i >= win:
            old = x[i - win]
            s -= old
            s2 -= old * old
            mean = s / win
            var = s2 / win - mean * mean
            out[i] = math.sqrt(var) if var > 0 else 0.0
    return out


def med_liq(usdvol, coin, t):
    lo = max(0, t - LIQ_WIN)
    seg = usdvol[coin][lo:t]
    return statistics.median(seg) if seg else 0.0


def run(series, usdvol, timeline, horizon, direction, mult=1.0):
    coins = [c for c in sorted(series) if c in LEADERS or len(series[c]) == len(timeline)]
    n = len(timeline)
    btc = series["BTC"]
    rets_btc = [0.0] + [btc[i] / btc[i - 1] - 1.0 for i in range(1, n)]
    sig = rolling_sigma(rets_btc, SIGMA_WIN)
    cost = COST_ROUND_PCT / 100.0 * mult

    trades = []
    warm = max(SIGMA_WIN, LIQ_WIN) + 1
    for t in range(warm, n - horizon):
        sg = sig[t]
        if not sg:
            continue
        z = rets_btc[t] / sg
        if direction == "up" and z < Z_TRIG:
            continue
        if direction == "down" and z > -Z_TRIG:
            continue
        # универсум и часовые доходы
        liq = []
        r1 = {}
        for c in coins:
            px = series[c]
            if px[t] <= 0 or px[t - 1] <= 0:
                continue
            r1[c] = px[t] / px[t - 1] - 1.0
            liq.append((med_liq(usdvol, c, t), c))
        liq.sort(reverse=True)
        top = set(c for _, c in liq[:UNIVERSE_TOP])
        cand = sorted([c for c in top if c not in LEADERS and c in r1], key=lambda c: r1[c])
        for c in cand[:CANDIDATES]:
            e, x = series[c][t], series[c][t + horizon]
            gross = x / e - 1.0
            if direction == "down":
                gross = -gross
            trades.append({"t": timeline[t], "coin": c, "z": round(z, 2),
                           "gross": gross, "net": gross - cost, "mult": mult})
    return trades


def stats(trades, capital_per_trade=1000.0):
    if not trades:
        return {"n": 0}
    nets = [t["net"] for t in trades]
    n = len(nets)
    mean = sum(nets) / n
    var = sum((x - mean) ** 2 for x in nets) / (n - 1) if n > 1 else 0.0
    se = math.sqrt(var / n) if n > 1 else float("inf")
    tstat = mean / se if se > 0 else 0.0
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")
    half = n // 2
    srt = sorted(nets, reverse=True)
    return {
        "n": n,
        "mean_pct": round(100.0 * mean, 3),
        "mean_usd": round(mean * capital_per_trade, 2),
        "t": round(tstat, 2),
        "pf": round(pf, 3) if pf != float("inf") else None,
        "total_usd": round(sum(nets) * capital_per_trade, 2),
        "h1_net_pct": round(100.0 * sum(nets[:half]) / max(1, half), 3),
        "h2_net_pct": round(100.0 * sum(nets[half:]) / max(1, n - half), 3),
        "winrate_pct": round(100.0 * len(wins) / n, 1),
        "drop5_mean_pct": round(100.0 * (sum(nets) - sum(srt[:5])) / max(1, n - 5), 3) if n > 5 else None,
    }


def main():
    cache = os.path.join(HERE, "bt_cache")
    series, usdvol, timeline = load_cache_vol(cache)
    if not series:
        print("нет данных в bt_cache")
        return 1
    print("монет: %d, часов: %d, окно %d..%d" % (len(series), len(timeline),
                                                 timeline[0], timeline[-1]), flush=True)
    summary = {}
    for direction in ("up", "down"):
        for h in (1, 3, 6):
            base = run(series, usdvol, timeline, h, direction, 1.0)
            stress = run(series, usdvol, timeline, h, direction, 2.0)
            st, st2 = stats(base), stats(stress)
            summary["%s_h%d" % (direction, h)] = {"base": st, "x2": st2}
            print("%-4s h=%d | сделок %5d | средний %+7.3f%% (%+7.2f на $1000) | t %+5.2f | "
                  "PF %.2f | обе половины %+.3f%% / %+.3f%% | без 5 лучших %+.3f%% | "
                  "2x: средний %+7.3f%% t %+5.2f" % (
                      direction, h, st.get("n", 0), st.get("mean_pct", 0),
                      st.get("mean_usd", 0), st.get("t", 0), st.get("pf") or 0,
                      st.get("h1_net_pct", 0), st.get("h2_net_pct", 0),
                      st.get("drop5_mean_pct") or 0,
                      st2.get("mean_pct", 0), st2.get("t", 0)), flush=True)
            json.dump({"base": base, "x2": stress},
                      open(os.path.join(HERE, ".probe", "leadlag_%s_h%d.json" % (direction, h)), "w"))

    print("\nкритерии (направление up, h=3 основной):", flush=True)
    s = summary["up_h3"]["base"]
    s2 = summary["up_h3"]["x2"]
    checks = [
        ("N1 средний нетто > 0 (1x)", s.get("mean_pct", 0) > 0, s.get("mean_pct")),
        ("N2 t >= 2.0", (s.get("t") or 0) >= 2.0, s.get("t")),
        ("N3 PF >= 1.15", (s.get("pf") or 0) >= 1.15, s.get("pf")),
        ("N4 обе половины > 0", (s.get("h1_net_pct", 0) > 0 and s.get("h2_net_pct", 0) > 0),
         (s.get("h1_net_pct"), s.get("h2_net_pct"))),
        ("N5 при 2x средний > 0", s2.get("mean_pct", 0) > 0, s2.get("mean_pct")),
        ("N6 без 5 лучших > 0", (s.get("drop5_mean_pct") or -1) > 0, s.get("drop5_mean_pct")),
    ]
    for name, ok, val in checks:
        print("  %-28s %s  %s" % (name, "OK" if ok else "FAIL", val))
    summary["criteria_up_h3"] = {name: bool(ok) for name, ok, _ in checks}
    json.dump(summary, open(os.path.join(HERE, ".probe", "leadlag_summary.json"), "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
