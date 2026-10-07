"""Walk-forward backtest for the HL sniper strategy.

It does NOT re-implement the strategy. It drives a real Bot instance with a
simulated clock and a client that serves historical bars, so the entries are
produced by the same sniper.py / levels.py / indicators.py code that runs
live. Sizing, costs, partials and closes go through the real Bot methods.

Modelling decisions, all documented because they change the numbers:

  * Entry happens at the close of the bar that produced the signal, which is
    what the live bot does (it evaluates on candle close and crosses).
  * Stops and targets are checked against each bar's high/low, and when both
    could have been hit in the same bar the STOP is assumed first. That is the
    pessimistic reading, which matters a lot at 1.35xATR stops.
  * There is no historical L2 feed, so the order book is synthesised as
    balanced at the bar close. The order-book signal therefore contributes its
    neutral "disagrees" value instead of a real imbalance.
  * Funding comes from the exchange's hourly funding history. This is not
    optional: on the reference project funding was 89.5% of all costs.

Usage:
    python3 backtest.py fetch --coins 12            # download + cache
    python3 backtest.py run --stops 1.35,2.0,2.5    # sweep
"""

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# The backtest must never read or write the live state volume, and it must not
# trigger a universe scan.
os.environ["STATE_DIR"] = tempfile.mkdtemp(prefix="hl_backtest_")
os.environ["UNIVERSE_ENABLED"] = "0"

import config  # noqa: E402

CACHE_DIR = os.path.join(HERE, "bt_cache")
INFO_URL = "https://api.hyperliquid.xyz/info"


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

def post(payload, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                INFO_URL, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            time.sleep(1 + attempt)
    return None


def fetch_candles(coin, bars=5000, interval="1h", delay=3.0):
    """Up to `bars` hourly candles, oldest first, walking backwards."""
    out = []
    end = int(time.time() * 1000)
    step = 5000
    while len(out) < bars:
        data = post({"type": "candleSnapshot", "req": {
            "coin": coin, "interval": interval,
            "startTime": end - step * 3600 * 1000, "endTime": end}})
        if not data:
            break
        chunk = []
        for c in data:
            try:
                chunk.append({"t": int(c["t"]), "o": float(c["o"]), "h": float(c["h"]),
                              "l": float(c["l"]), "c": float(c["c"]), "v": float(c["v"])})
            except (KeyError, TypeError, ValueError):
                continue
        if not chunk:
            break
        out = chunk + out
        if len(chunk) < 2:
            break
        end = chunk[0]["t"] - 1
        if delay:
            time.sleep(delay)
    out.sort(key=lambda x: x["t"])
    return out[-bars:]


def fetch_funding(coin, days=220, delay=1.0):
    """Hourly funding history as {bar_open_ms: rate}."""
    out = {}
    end = int(time.time() * 1000)
    start = end - days * 86400 * 1000
    cursor = start
    while cursor < end:
        data = post({"type": "fundingHistory", "coin": coin,
                     "startTime": cursor, "endTime": end})
        if not data:
            break
        got = 0
        for rec in data:
            try:
                out[int(rec["time"])] = float(rec["fundingRate"])
                got += 1
            except (KeyError, TypeError, ValueError):
                continue
        if got == 0:
            break
        cursor = max(int(r["time"]) for r in data) + 1
        if delay:
            time.sleep(delay)
        if got < 500:
            break
    return out


def cache_path(coin):
    return os.path.join(CACHE_DIR, "%s.json" % coin)


def fetch_all(coins, bars, funding_days, delay_candles, delay_funding, log=print):
    os.makedirs(CACHE_DIR, exist_ok=True)
    for coin in coins:
        path = cache_path(coin)
        if os.path.exists(path):
            log("  %-10s cache hit" % coin)
            continue
        candles = fetch_candles(coin, bars=bars, delay=delay_candles)
        funding = fetch_funding(coin, days=funding_days, delay=delay_funding)
        if not candles:
            log("  %-10s no candles, skipped" % coin)
            continue
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"coin": coin, "candles": candles, "funding": funding}, fh)
        log("  %-10s %d bars, %d funding points" % (coin, len(candles), len(funding)))


def load_cached(coins, log=print):
    data = {}
    for coin in coins:
        path = cache_path(coin)
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as fh:
            blob = json.load(fh)
        if blob.get("candles"):
            data[coin] = {"candles": blob["candles"],
                          "funding": {int(k): v for k, v in (blob.get("funding") or {}).items()}}
    log("  loaded %d coins from cache" % len(data))
    return data


# --------------------------------------------------------------------------
# replay plumbing
# --------------------------------------------------------------------------

class SimClock:
    """Stands in for the `time` module so every timestamp is simulated."""

    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def strftime(self, fmt, *args):
        return time.strftime(fmt, *args)

    def gmtime(self, *args):
        return time.gmtime(*args)

    def sleep(self, *args):
        return None


class ReplayClient:
    """Serves historical slices through the interface Bot.analyze_symbol uses."""

    def __init__(self, data):
        self.data = data
        self.cursor = 0

    def all_mids(self):
        out = {}
        for coin, blob in self.data.items():
            bars = blob["candles"]
            if self.cursor < len(bars):
                out[coin] = bars[self.cursor]["c"]
        return out

    def candles(self, coin, interval, start_ms, end_ms):
        blob = self.data.get(coin)
        if not blob:
            return []
        bars = blob["candles"]
        end = self.cursor + 1
        start = max(0, end - 300)
        return bars[start:end]

    def l2_book(self, coin, depth=10):
        # No historical L2 exists. A balanced book at the bar close keeps the
        # execution guards meaningful without inventing an imbalance.
        blob = self.data.get(coin)
        if not blob:
            return None
        px = blob["candles"][self.cursor]["c"]
        half = px * 0.0001
        bids = [(px - half - i * half, 5.0) for i in range(depth)]
        asks = [(px + half + i * half, 5.0) for i in range(depth)]
        return {"bids": bids, "asks": asks, "bid_px": bids[0][0], "ask_px": asks[0][0]}

    def meta_and_ctxs(self, cache_sec=60):
        out = {}
        for coin, blob in self.data.items():
            bars = blob["candles"]
            if self.cursor >= len(bars):
                continue
            bar = bars[self.cursor]
            lo = max(0, self.cursor - 23)
            notional = sum(b["v"] * b["c"] for b in bars[lo:self.cursor + 1])
            out[coin] = {
                "funding": blob["funding"].get(bar["t"], 0.0),
                "mark": bar["c"],
                "premium": 0.0,
                "day_volume_usd": notional,
                "open_interest": 0.0,
                "impact_pxs": None,
                "max_leverage": 20.0,
                "sz_decimals": 3,
            }
        return out


def manage_bar(bot, coin, bar, clock):
    """Intrabar stop/target/partial handling. Stops are assumed first."""
    pos = bot.positions.get(coin)
    if not pos:
        return
    dist = pos.risk_dist
    long = pos.direction == "long"

    if long:
        if bar["l"] <= pos.stop:
            bot.close_position(coin, pos.stop, "stop")
            bot.cooldowns[coin] = clock.now + config.COOLDOWN_AFTER_STOP_H * 3600
            return
        if not pos.partial_done and bar["h"] >= pos.entry + config.PARTIAL_AT_R * dist:
            bot.book_partial(pos, coin, pos.entry + config.PARTIAL_AT_R * dist)
            pos.stop, pos.partial_done = pos.entry, True
        if bar["h"] >= pos.target:
            bot.close_position(coin, pos.target, "target")
            return
    else:
        if bar["h"] >= pos.stop:
            bot.close_position(coin, pos.stop, "stop")
            bot.cooldowns[coin] = clock.now + config.COOLDOWN_AFTER_STOP_H * 3600
            return
        if not pos.partial_done and bar["l"] <= pos.entry - config.PARTIAL_AT_R * dist:
            bot.book_partial(pos, coin, pos.entry - config.PARTIAL_AT_R * dist)
            pos.stop, pos.partial_done = pos.entry, True
        if bar["l"] <= pos.target:
            bot.close_position(coin, pos.target, "target")
            return

    if clock.now - pos.entry_time > config.TIME_STOP_HOURS * 3600:
        bot.close_position(coin, bar["c"], "time_stop")
        bot.cooldowns[coin] = clock.now + config.COOLDOWN_AFTER_STOP_H * 3600


def snapshot_equity(bot, clock):
    bot._recalc_equity()
    bot.equity_history.append([round(clock.now, 1), round(bot.equity, 2)])


def run_once(data, capital=10000.0, stop_mult=None, target_mult=None,
             threshold=None, strong=None, fee_taker=None, log=None):
    """Replay the whole history once. Returns a metrics dict."""
    import bot as bot_module
    import config as config_module
    import sniper as sniper_module

    if stop_mult is not None:
        config_module.ATR_STOP_MULT = stop_mult
    if target_mult is not None:
        config_module.ATR_TARGET_MULT = target_mult
    if threshold is not None:
        config_module.CONVERGENCE_THRESHOLD = threshold
    if strong is not None:
        config_module.CONVERGENCE_THRESHOLD_STRONG = strong
    if fee_taker is not None:
        config_module.TAKER_FEE = fee_taker

    clock = SimClock()
    bot_module.time = clock
    sniper_module.time = clock

    replay = ReplayClient(data)
    engine = bot_module.Bot()
    engine.client = replay
    engine.capital = capital
    engine.cash = capital
    engine.equity = capital
    engine.peak = capital
    engine.day_start_equity = capital
    engine.trades = []
    engine.events = []
    engine.equity_history = []
    engine.positions = {}
    engine.win_count = engine.loss_count = 0
    engine.win_sum = engine.loss_sum = engine.r_sum = 0.0
    engine._day_stamp = lambda: time.strftime("%Y-%m-%d", time.gmtime(clock.now))
    engine._save_state = lambda *a, **k: None
    engine._event = lambda msg: None
    engine.universe = {c: {"tradable": True, "risk_pct_cap": 100.0,
                           "class_max_leverage": 100} for c in data}
    engine.active_symbols = list(data.keys())

    length = min(len(blob["candles"]) for blob in data.values())
    warmup = 120
    for i in range(warmup, length):
        clock.now = data[engine.active_symbols[0]]["candles"][i]["t"] / 1000.0
        replay.cursor = i
        engine.last_prices = replay.all_mids()
        for coin, blob in data.items():
            manage_bar(engine, coin, blob["candles"][i], clock)
        ctxs = replay.meta_and_ctxs()
        for coin in engine.active_symbols:
            engine.analyze_symbol(coin, ctxs)
        snapshot_equity(engine, clock)
        if log and i % 500 == 0:
            log("    bar %d/%d equity=%.2f trades=%d" % (i, length, engine.equity, len(engine.trades)))

    return summarize(engine, data, capital)


def summarize(engine, data, capital):
    trades = engine.trades
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    peak = capital
    max_dd = 0.0
    for _, eq in engine.equity_history:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
    rs = [t["r"] for t in trades]
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    span_days = 0.0
    if engine.equity_history:
        span_days = (engine.equity_history[-1][0] - engine.equity_history[0][0]) / 86400.0
    return {
        "final_equity": round(engine.equity, 2),
        "return_pct": round((engine.equity / capital - 1) * 100, 2),
        "trades": len(trades),
        "winrate": round(100.0 * len(wins) / len(trades), 1) if trades else 0.0,
        "avg_r": round(sum(rs) / len(rs), 2) if rs else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "max_dd_pct": round(max_dd * 100, 2),
        "best_r": round(max(rs), 2) if rs else 0.0,
        "worst_r": round(min(rs), 2) if rs else 0.0,
        "exit_reasons": reasons,
        "days": round(span_days, 1),
        "trades_per_day": round(len(trades) / span_days, 2) if span_days else 0.0,
    }


def cmd_fetch(args):
    coins = [c.strip() for c in args.coins.split(",") if c.strip()]
    print("fetching %d coins: %s" % (len(coins), ", ".join(coins)))
    fetch_all(coins, args.bars, args.funding_days, args.delay, args.funding_delay)


def cmd_run(args):
    coins = [c.strip() for c in args.coins.split(",") if c.strip()]
    data = load_cached(coins)
    if not data:
        raise SystemExit("no cached data; run: backtest.py fetch --coins ...")
    stops = [float(s) for s in args.stops.split(",")]
    results = []
    for stop in stops:
        print("\n=== stop %.2f x ATR ===" % stop)
        started = time.time()
        metrics = run_once(data, capital=args.capital, stop_mult=stop,
                           log=lambda m: print(m))
        metrics["stop_mult"] = stop
        results.append(metrics)
        print("  %s  (%.0fs)" % (json.dumps(metrics["exit_reasons"]), time.time() - started))
    print("\n%-8s %9s %7s %7s %7s %7s %7s %6s" % (
        "stop", "equity", "return", "trades", "winrate", "avgR", "PF", "maxDD"))
    for r in results:
        print("%-8.2f %9.2f %6.1f%% %7d %6.1f%% %7.2f %7s %5.1f%%" % (
            r["stop_mult"], r["final_equity"], r["return_pct"], r["trades"],
            r["winrate"], r["avg_r"], r["profit_factor"], r["max_dd_pct"]))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=1)
        print("\nwritten to %s" % args.out)


DEFAULT_COINS = "BTC,ETH,SOL,BNB,XRP,DOGE,ADA,AVAX,LINK,LTC,SUI,ARB"


def main():
    ap = argparse.ArgumentParser(description="HL sniper walk-forward backtest")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download and cache history")
    f.add_argument("--coins", default=DEFAULT_COINS)
    f.add_argument("--bars", type=int, default=5000)
    f.add_argument("--funding-days", type=int, default=220)
    f.add_argument("--delay", type=float, default=3.0)
    f.add_argument("--funding-delay", type=float, default=1.0)
    f.set_defaults(func=cmd_fetch)

    r = sub.add_parser("run", help="replay the cached history")
    r.add_argument("--coins", default=DEFAULT_COINS)
    r.add_argument("--stops", default="1.35,2.0,2.5,3.5")
    r.add_argument("--capital", type=float, default=10000.0)
    r.add_argument("--out")
    r.set_defaults(func=cmd_run)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
