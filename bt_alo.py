"""ALO / post-only entry measurement on the cached candles.

Run: python3 bt_alo.py            (reads bt_cache/, writes .probe/alo.json)

The question from research/ALO_PREREG.md: if entries were posted as post-only
limits at the decision price instead of taken at market, how often do they fill,
does the saving survive the adverse selection, and what is left of the pair's
round-trip cost. Pessimistic on purpose: a fill needs the price to trade 0.05%
through the limit, and anything unfilled is taken at market after the window,
so the number of trades never shrinks just because a limit missed.
"""

import glob
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "bt_cache")
OUT = os.path.join(HERE, ".probe", "alo.json")

LOOKBACK_H = 168          # 7 day ranking, as in pairs.py
HOLD_H = 48               # cycle length
N_LEGS = 3                # pairs per cycle
BUFFER = 0.0005           # price must trade 0.05% through the limit
WINDOWS = (1, 4, 24)      # hours to wait for the maker fill
OFFSETS = (0.0, 0.001, 0.002, 0.005)   # stage 2: post this far in our favour
OFFSET_WINDOW_H = 4       # stage 2 wait window
SPLIT_TS = None           # set in main(): 2026-08-01, in-sample / out-of-sample
TAKER_PCT = (0.00045 + 0.0005 / 4.0) * 100.0   # 0.0575% per taker fill
MAKER_PCT = 0.00015 * 100.0                    # 0.015% per maker fill
BASELINE_COST_PCT = 4.0 * TAKER_PCT            # 0.23% per pair round trip


def maker_fill(cd, i, side, limit, window_h, buffer=BUFFER):
    """Index of the candle that filled a post-only limit, or None.

    A touch is not a fill: the price has to trade `buffer` through the limit,
    which is the only honest way to model an order we cannot see in the queue.
    """
    for k in range(1, window_h + 1):
        j = i + k
        if j >= len(cd["c"]):
            return None
        if side == "long" and cd["l"][j] <= limit * (1.0 - buffer):
            return j
        if side == "short" and cd["h"][j] >= limit * (1.0 + buffer):
            return j
    return None


def benefit_pct(limit, entry, cost_pct, side):
    """Value of an entry versus taking it at the decision close, in %% of notional.

    Price improvement is signed so that positive always means "we did better":
    paying less for a long, receiving more for a short. The fee delta is added on
    top, so a maker fill at the same price is worth `TAKER_PCT - MAKER_PCT`.
    """
    gain = ((limit - entry) / limit * 100.0) if side == "long" else \
           ((entry - limit) / limit * 100.0)
    return gain + (TAKER_PCT - cost_pct)


def load():
    coins = {}
    for path in sorted(glob.glob(os.path.join(CACHE, "*.json"))):
        d = json.load(open(path))
        cs = d["candles"]
        coins[d["coin"]] = {
            "t": [c["t"] for c in cs],
            "o": [c["o"] for c in cs],
            "h": [c["h"] for c in cs],
            "l": [c["l"] for c in cs],
            "c": [c["c"] for c in cs],
        }
    return coins


def idx_map(times):
    return {t: i for i, t in enumerate(times)}


def main():
    coins = load()
    if not coins:
        sys.exit("no cache: run the fetcher first")
    names = sorted(coins)
    maps = {n: idx_map(coins[n]["t"]) for n in names}

    # Rebalance anchor: the first common candle time, stepped by HOLD_H.
    starts = [coins[n]["t"][0] for n in names]
    ends = [coins[n]["t"][-1] for n in names]
    t0 = max(starts)
    t_end = min(ends)
    hour = 3600 * 1000

    # Stage 2 splits in-sample (choose the offset) from out-of-sample
    # (check that the choice was not fitted to noise).
    global SPLIT_TS
    SPLIT_TS = int(time.mktime(time.strptime("2026-08-01", "%Y-%m-%d"))) * 1000
    cycles = []
    t = t0 + LOOKBACK_H * hour          # need a full lookback behind us
    while t + HOLD_H * hour <= t_end:
        cycles.append(t)
        t += HOLD_H * hour

    window_stats = {w: {"tried": 0, "filled": 0, "delays": [], "drifts": []}
                    for w in WINDOWS}
    # Stage 2: a limit posted below the market (long) or above it (short) earns
    # price improvement instead of paying adverse selection. The benefit is per
    # leg, in percent of the leg notional, relative to taking at the decision
    # close with a taker fee.
    off_stats = {o: {"all": [], "in": [], "out": [],
                     "filled": [0, 0], "in_filled": [0, 0], "out_filled": [0, 0]}
                 for o in OFFSETS}
    # Baseline vs hybrid, aggregated over all cycles.
    base_gross = []          # decision-close -> exit-close, in % of leg notional
    hybrid_gross = []        # actual entry price (maker or fallback) -> exit
    hybrid_cost = []         # per pair round trip, %
    pair_details = []

    for T in cycles:
        rows = []
        for n in names:
            i = maps[n].get(T)
            if i is None or i < LOOKBACK_H:
                continue
            first = coins[n]["c"][i - LOOKBACK_H]
            last = coins[n]["c"][i]
            if first <= 0:
                continue
            rows.append({"sym": n, "ret": last / first - 1.0, "i": i,
                         "px": last})
        if len(rows) < 2 * N_LEGS:
            continue
        rows.sort(key=lambda r: r["ret"])
        weakest, strongest = rows[:N_LEGS], rows[-N_LEGS:][::-1]
        for weak, strong in zip(weakest, strongest):
            legs = [(weak, "long"), (strong, "short")]
            exit_i_leg = []
            ok = True
            for row, side in legs:
                i = row["i"]
                if i + HOLD_H >= len(coins[row["sym"]]["c"]):
                    ok = False
                    break
                exit_i_leg.append(i + HOLD_H)
            if not ok:
                continue

            pair = {"t": T, "long": weak["sym"], "short": strong["sym"],
                    "entry_px": {}, "fill": {}, "delay": {}}
            for (row, side) in legs:
                sym, i, limit = row["sym"], row["i"], row["px"]
                cd = coins[sym]
                exit_c = cd["c"][i + HOLD_H]
                # baseline: enter at the decision close
                base_ret = (exit_c / limit - 1.0) if side == "long" else \
                           (1.0 - exit_c / limit)
                pair.setdefault("base_ret", [])
                pair["base_ret"].append(base_ret * 100.0)
                pair["entry_px"][side] = limit

                for w in WINDOWS:
                    st = window_stats[w]
                    st["tried"] += 1
                    fill_i = maker_fill(cd, i, side, limit, w)
                    if fill_i is None:
                        continue
                    st["filled"] += 1
                    st["delays"].append((fill_i - i) * 1.0)
                    # Signed drift over the hour after the fill: negative means
                    # the price kept moving against the position we just opened.
                    j2 = min(fill_i + 1, len(cd["c"]) - 1)
                    after = cd["c"][j2]
                    drift = (after / limit - 1.0) if side == "long" else \
                            (1.0 - after / limit)
                    st["drifts"].append(drift * 100.0)
                    if w == WINDOWS[-1]:
                        pair["fill"][side] = True
                        pair["delay"][side] = fill_i - i
                # ---- stage 2: limit with an offset in our favour
                for off in OFFSETS:
                    limit2 = (limit * (1.0 - off)) if side == "long" else \
                             (limit * (1.0 + off))
                    fill_j = maker_fill(cd, i, side, limit2, OFFSET_WINDOW_H)
                    if fill_j is None:
                        jf = min(i + OFFSET_WINDOW_H, len(cd["c"]) - 1)
                        entry, cost = cd["c"][jf], TAKER_PCT
                    else:
                        entry, cost = limit2, MAKER_PCT
                    benefit = benefit_pct(limit, entry, cost, side)
                    bucket = off_stats[off]
                    bucket["all"].append(benefit)
                    bucket["filled"][0] += 1 if fill_j is not None else 0
                    bucket["filled"][1] += 1
                    if T > SPLIT_TS:
                        bucket["out"].append(benefit)
                        bucket["out_filled"][0] += 1 if fill_j is not None else 0
                        bucket["out_filled"][1] += 1
                    else:
                        bucket["in"].append(benefit)
                        bucket["in_filled"][0] += 1 if fill_j is not None else 0
                        bucket["in_filled"][1] += 1

                if side not in pair["fill"]:
                    pair["fill"][side] = False

            # Hybrid: maker on filled legs (limit price = decision close),
            # taker on the rest at close(T+W).
            w = WINDOWS[-1]
            pair_cost = 2 * (TAKER_PCT)      # exits are always taker
            for (row, side) in legs:
                if pair["fill"].get(side):
                    pair_cost += MAKER_PCT
                else:
                    pair_cost += TAKER_PCT
                    j = min(row["i"] + w, len(coins[row["sym"]]["c"]) - 1)
                    pair["entry_px"][side] = coins[row["sym"]]["c"][j]
                # hybrid return uses the real entry price, same exit
                i = row["i"]
                exit_c = coins[row["sym"]]["c"][i + HOLD_H]
                px = pair["entry_px"][side]
                r = (exit_c / px - 1.0) if side == "long" else (1.0 - exit_c / px)
                pair.setdefault("hyb_ret", [])
                pair["hyb_ret"].append(r * 100.0)

            base_gross.append(sum(pair["base_ret"]) / 2.0)
            hybrid_gross.append(sum(pair["hyb_ret"]) / 2.0)
            hybrid_cost.append(pair_cost)
            pair_details.append({
                "long": pair["long"], "short": pair["short"],
                "fill": {k: bool(v) for k, v in pair["fill"].items()},
                "delay": pair["delay"],
                "base_pct": round(sum(pair["base_ret"]) / 2.0, 4),
                "hyb_pct": round(sum(pair["hyb_ret"]) / 2.0, 4),
                "cost_pct": round(pair_cost, 4),
            })

    report = {
        "data": {"coins": len(names), "cycles": len(cycles),
                 "pairs": len(pair_details),
                 "from": time.strftime("%Y-%m-%d", time.gmtime(t0 / 1000)),
                 "to": time.strftime("%Y-%m-%d", time.gmtime(t_end / 1000))},
        "windows": {},
        "baseline_cost_pct": round(BASELINE_COST_PCT, 4),
        "base_gross_avg_pct": round(statistics.mean(base_gross), 4) if base_gross else None,
        "hybrid_gross_avg_pct": round(statistics.mean(hybrid_gross), 4) if hybrid_gross else None,
        "hybrid_cost_avg_pct": round(statistics.mean(hybrid_cost), 4) if hybrid_cost else None,
        "pairs": pair_details,
    }
    for w in WINDOWS:
        st = window_stats[w]
        report["windows"]["%dh" % w] = {
            "tried": st["tried"],
            "fill_rate_pct": round(100.0 * st["filled"] / st["tried"], 2)
                             if st["tried"] else None,
            "mean_delay_h": (round(statistics.mean(st["delays"]), 2)
                             if st["delays"] else None),
            "mean_drift_pct": (round(statistics.mean(st["drifts"]), 4)
                               if st["drifts"] else None),
            "share_adverse_pct": (round(100.0 * sum(1 for d in st["drifts"] if d < 0)
                                        / len(st["drifts"]), 1)
                                  if st["drifts"] else None),
        }

    stage2 = {}
    for off in OFFSETS:
        b = off_stats[off]
        stage2["%.1f%%" % (off * 100.0)] = {
            "n": len(b["all"]),
            "fill_rate_pct": round(100.0 * b["filled"][0] / b["filled"][1], 2)
                             if b["filled"][1] else None,
            "benefit_mean_pct": round(statistics.mean(b["all"]), 4) if b["all"] else None,
            "benefit_share_pos_pct": (round(100.0 * sum(1 for x in b["all"] if x > 0)
                                            / len(b["all"]), 1) if b["all"] else None),
            "in_n": len(b["in"]),
            "in_benefit_pct": round(statistics.mean(b["in"]), 4) if b["in"] else None,
            "in_fill_pct": (round(100.0 * b["in_filled"][0] / b["in_filled"][1], 2)
                            if b["in_filled"][1] else None),
            "out_n": len(b["out"]),
            "out_benefit_pct": round(statistics.mean(b["out"]), 4) if b["out"] else None,
            "out_fill_pct": (round(100.0 * b["out_filled"][0] / b["out_filled"][1], 2)
                             if b["out_filled"][1] else None),
        }
    report["stage2_offsets"] = stage2
    report["split"] = time.strftime("%Y-%m-%d", time.gmtime(SPLIT_TS / 1000))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(report, open(OUT, "w"), indent=1)
    print(json.dumps({k: v for k, v in report.items() if k != "pairs"},
                     indent=1, ensure_ascii=False))
    print("pairs:", len(report["pairs"]), "->", OUT)


if __name__ == "__main__":
    main()
