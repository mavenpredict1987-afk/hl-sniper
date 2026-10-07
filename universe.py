"""Which Hyperliquid perps this bot may trade, and how risky each one is.

Trading three majors produced three signals in fifty hours, which is the
single biggest brake on the bot. This scanner ranks every perp by
volatility, candle noise, liquidity and execution cost, sorts them into
calibrated noise classes, and decides what is tradable at all.

The metric maths is ported from a reference adaptive-trading pack, minus two
defects found in it:
  * rows with no candle data are flagged untradable instead of being forced
    into the "toxic" class, which in the original also handed dead tickers a
    trend-following recommendation;
  * the class always drives the parameters, never the other way round.

candleSnapshot is heavy (roughly 20 requests/minute), so a scan is
rate-limited and cached on the state volume; a restart reuses the cache.
"""

import argparse
import json
import os
import time

import config
import state_store

# Bump when the scanner's rules change, so an existing cache is treated as
# stale and the next start rescans instead of serving results from the old
# logic for up to UNIVERSE_REFRESH_HOURS.
SCANNER_VERSION = 2


def atr_pct(candles, period=14):
    """ATR(period) as a percentage of the last close."""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["h"], candles[i]["l"], candles[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[-period:]) / period
    last = candles[-1]["c"]
    return atr / last * 100 if last > 0 else None


def wickiness(candles, lookback=50, cap=20.0):
    """Median ratio of candle range to body. High means the tape is all wicks."""
    sample = candles[-lookback:]
    ratios = []
    for c in sample:
        body = abs(c["c"] - c["o"])
        rng = c["h"] - c["l"]
        if rng > 0:
            ratios.append(min(rng / max(body, rng * 1e-6), cap))
    ratios.sort()
    return ratios[len(ratios) // 2] if ratios else None


def efficiency_ratio(candles, lookback=20):
    """Kaufman efficiency ratio: net move over total path. Low means choppy."""
    if len(candles) < lookback + 1:
        return None
    closes = [c["c"] for c in candles[-(lookback + 1):]]
    net = abs(closes[-1] - closes[0])
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    return net / path if path > 0 else 0.0


def impact_bps(ctx):
    """Impact bid/ask spread in basis points, used as a slippage proxy."""
    pxs = ctx.get("impact_pxs")
    mid = ctx.get("mark")
    if not pxs or not mid:
        return None
    try:
        bid, ask = float(pxs[0]), float(pxs[1])
        mid = float(mid)
    except (TypeError, ValueError, IndexError):
        return None
    return (ask - bid) / mid * 10000 if mid > 0 else None


def noise_score(metrics, weights=None):
    """0..100, higher is noisier. Weighting comes from config."""
    w = weights or config.NOISE_SCORE_WEIGHTS
    score = 0.0
    if metrics.get("atr_pct") is not None:
        score += w["atr"] * min(metrics["atr_pct"] / w["atr_ref"], 1.0)
    if metrics.get("wickiness") is not None:
        span = max(w["wick_ref"] - 1.0, 1e-9)
        score += w["wick"] * min(max(metrics["wickiness"] - 1.0, 0.0) / span, 1.0)
    if metrics.get("efficiency") is not None:
        score += w["er"] * (1.0 - metrics["efficiency"])
    if metrics.get("impact_bps") is not None:
        score += w["impact"] * min(metrics["impact_bps"] / w["impact_ref"], 1.0)
    if (metrics.get("day_vlm") or 0) < w["vlm_floor"]:
        score += w["thin_vlm"]
    return round(min(score, 100.0), 1)


def classify(score, classes=None):
    classes = classes or config.NOISE_CLASSES
    for name in ("calm", "normal", "noisy", "toxic"):
        if score <= classes[name]["max_score"]:
            return name
    return "toxic"


def cache_path():
    return os.path.join(state_store.state_dir(), "universe.json")


def save(rows, path=None):
    return state_store.save({"kind": "universe", "scanner_version": SCANNER_VERSION,
                             "scanned_at": time.time(),
                             "count": len(rows), "universe": list(rows)},
                            path=path or cache_path())


def load(path=None):
    data, _ = state_store.load(path=path or cache_path())
    if not data or data.get("kind") != "universe":
        return None
    if data.get("scanner_version") != SCANNER_VERSION:
        return None
    rows = data.get("universe")
    return rows if isinstance(rows, list) and rows else None


def load_or_seed():
    """Tradable coins from the cache, or the configured majors as a fallback."""
    rows = load()
    if rows:
        return rows
    return [{"coin": coin, "noise_class": "normal",
             "risk_pct_cap": config.NOISE_CLASSES["normal"]["risk_pct_cap"],
             "stop_atr_mult": config.ATR_STOP_MULT, "day_vlm": 0.0,
             "tradable": True, "flags": ["seed"]} for coin in config.SYMBOLS]


def scan(client, size=None, bars=None, delay=None, log=None):
    """Rank the perp universe; return (tradable, full_pool)."""
    size = config.UNIVERSE_SIZE if size is None else size
    bars = config.UNIVERSE_BARS if bars is None else bars
    delay = config.UNIVERSE_DELAY_SEC if delay is None else delay

    ctxs = client.meta_and_ctxs(cache_sec=0) or {}
    if not ctxs:
        return [], []
    rows = []
    for coin, ctx in ctxs.items():
        if ":" in coin or coin in config.UNIVERSE_EXCLUDE:
            continue
        mark = float(ctx.get("mark") or 0)
        rows.append({
            "coin": coin,
            "mark_px": mark,
            "day_vlm": float(ctx.get("day_volume_usd") or 0),
            "oi_usdc": float(ctx.get("open_interest") or 0) * mark,
            "funding_ann": float(ctx.get("funding") or 0) * 8760 * 100,
            "impact_bps": impact_bps(ctx),
            "max_leverage": float(ctx.get("max_leverage") or 1),
        })
    rows.sort(key=lambda r: r["day_vlm"], reverse=True)

    # Only the liquid part of the universe justifies the candle requests.
    pool = [r for r in rows if r["day_vlm"] >= config.UNIVERSE_MIN_VOLUME_USD]
    pool = pool[:max(size * 2, size)]

    for i, row in enumerate(pool):
        row.update({"atr_pct": None, "wickiness": None, "efficiency": None})
        try:
            candles = client.candles_recent(row["coin"], config.TIMEFRAME, bars)
            row["atr_pct"] = atr_pct(candles)
            row["wickiness"] = wickiness(candles)
            row["efficiency"] = efficiency_ratio(candles)
        except Exception as exc:  # one bad coin must not abort the scan
            if log:
                log("universe: %s candles failed (%s)" % (row["coin"], exc))
        if i < len(pool) - 1 and delay > 0:
            time.sleep(delay)

    for row in pool:
        row["noise_score"] = noise_score(row)
        row["noise_class"] = classify(row["noise_score"])
        flags = []
        if row["atr_pct"] is None:
            flags.append("no_candles")
        if row["day_vlm"] <= 0:
            flags.append("no_volume")
        if row["impact_bps"] is not None and row["impact_bps"] > config.UNIVERSE_MAX_SPREAD_BPS:
            flags.append("wide_spread")
        klass = config.NOISE_CLASSES[row["noise_class"]]
        row["flags"] = flags
        row["tradable"] = (not flags) and bool(klass["tradable"])
        row["risk_pct_cap"] = klass["risk_pct_cap"]
        row["stop_atr_mult"] = klass["stop_atr_mult"]
        row["class_max_leverage"] = klass["max_leverage"]
        row["strategy"] = klass["strategy"]

    tradable = [r for r in pool if r["tradable"]]
    tradable.sort(key=lambda r: (r["noise_score"], -r["day_vlm"]))
    return tradable[:size], pool


def main():
    from api_client import HLClient

    ap = argparse.ArgumentParser(description="Hyperliquid universe scanner")
    ap.add_argument("--size", type=int, default=config.UNIVERSE_SIZE)
    ap.add_argument("--bars", type=int, default=config.UNIVERSE_BARS)
    ap.add_argument("--delay", type=float, default=config.UNIVERSE_DELAY_SEC)
    ap.add_argument("--json", dest="json_path")
    ap.add_argument("--save", action="store_true", help="write the state cache")
    args = ap.parse_args()

    client = HLClient()
    started = time.time()
    tradable, pool = scan(client, args.size, args.bars, args.delay,
                          log=lambda m: print("# " + m))

    header = "%-9s %-7s %6s %6s %5s %5s %7s %9s %5s  %s" % (
        "coin", "class", "score", "atr%", "wick", "er", "imp_bp", "vlm$M", "lev", "strategy")
    print(header)
    print("-" * len(header))
    for r in tradable:
        print("%-9s %-7s %6.1f %6s %5s %5s %7s %9.1f %5.0f  %s" % (
            r["coin"], r["noise_class"], r["noise_score"],
            "-" if r["atr_pct"] is None else "%.2f" % r["atr_pct"],
            "-" if r["wickiness"] is None else "%.2f" % r["wickiness"],
            "-" if r["efficiency"] is None else "%.2f" % r["efficiency"],
            "-" if r["impact_bps"] is None else "%.1f" % r["impact_bps"],
            r["day_vlm"] / 1e6, r["max_leverage"], r["strategy"]))
    print("# tradable %d of %d scanned, %.0fs" % (len(tradable), len(pool), time.time() - started))

    if args.save:
        print("# cached to %s" % save(tradable))
    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as fh:
            json.dump({"universe": tradable, "pool": pool}, fh, indent=1)
        print("# json: %s" % args.json_path)


if __name__ == "__main__":
    main()
