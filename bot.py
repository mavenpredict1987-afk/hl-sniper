import os
import sys
import time
import json
import hashlib
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from api_client import HLClient
from indicators import ema, sma, atr, adx, atr_series
from levels import find_levels, breakout_confirmed
from sniper import (SniperModule, generate_trend_signal, generate_volume_signal,
                    generate_orderbook_signal, generate_funding_signal)
from health_server import start_health_server, HealthHandler
from utils import (setup_logging, send_telegram, telegram_status, money, price,
                   logger, coverage_x)
import state_store
import universe
import journal
import pairs as pairs_module
import grid_lab
import regime_lab
import strategy_map


@dataclass
class Position:
    symbol: str
    direction: str
    entry: float
    size: float
    stop: float
    target: float
    risk_dist: float
    atr_at_entry: float
    initial_size: float = 0.0
    initial_risk_usd: float = 0.0
    partial_done: bool = False
    realized_pnl: float = 0.0
    entry_fee: float = 0.0
    # What the screen said when this was opened. Kept on the position so a trade
    # can be explained later without re-reading a chart that has since moved.
    context: dict = field(default_factory=dict)
    entry_time: float = field(default_factory=time.time)


class Bot:
    def __init__(self):
        self.mode = config.TRADING_MODE
        self.capital = config.INITIAL_CAPITAL
        self.cash = self.capital
        self.equity = self.capital
        self.peak = self.capital
        self.positions = {}
        self.trades = []
        self.events = []
        self.running = False
        self.halted = False
        self.day_halt = False
        self.day_stamp = self._day_stamp()
        self.day_start_equity = self.capital
        self.win_count = 0
        self.loss_count = 0
        self.win_sum = 0.0
        self.loss_sum = 0.0
        self.last_prices = {}
        self.last_candle_ts = {}
        self.cooldowns = {}
        self.consec_losses = 0
        self.loss_pause_until = 0.0
        self.symbol_stops = {}
        self.r_sum = 0.0
        self.equity_history = []
        self.client = HLClient()
        self.sniper = SniperModule()
        self.journal = journal.Journal()
        self.pairs = pairs_module.PairsModule(self)
        # Paper-only regime lab: no orders, no cash, no equity. Its whole job is
        # to accrue forward evidence for a rule the backtest could not settle.
        self.lab = regime_lab.RegimeLab(self)
        # Paper-only grid arm. It has its own capital and state file; like the
        # laboratory it can never place an order.
        self.grid = grid_lab.GridLab(self)
        self.status = {"status": "initialized", "mode": self.mode, "started": time.time()}
        self.restarts = 0
        self.last_save_ts = 0.0
        self.state_source = None
        self.resumed = False
        self.universe = {}
        # Last measured picture of each coin, refreshed once an hour with the
        # scan. It outlives nothing (a restart clears it) but it is what lets
        # the dashboard answer "why is this coin not trading" without asking
        # the API again.
        self.coin_info = {}
        self.active_symbols = list(config.SYMBOLS)
        self.last_hour_bucket = 0
        self.watch_seen = 0
        self.watch_error = ""
        setup_logging()
        self._load_universe()
        self._restore_state()
        self.config_snapshot = self._config_snapshot()
        self._record_config()

    # Parameters that decide how the bot behaves, and therefore what a result
    # means. They live in the Railway environment, which is mutable and leaves no
    # trace, so a week of trades cannot otherwise be attributed to the settings
    # that produced it. The pairs experiment runs until 2026-10-31; if a number
    # changes mid-flight, this is how the two halves stay separable.
    CONFIG_KEYS = (
        "PAIRS_ENABLED", "PAIRS_LOOKBACK_H", "PAIRS_HOLD_H", "PAIRS_N",
        "PAIRS_EXPOSURE", "SNIPER_ENABLED", "SHORTS_ENABLED", "PARTIAL_RATIO",
        "PAPER_LAB_ENABLED", "PAPER_LAB_SEC",
        "TAKER_FEE", "SLIPPAGE", "MAX_ENTRY_THRESHOLD", "CONVERGENCE_THRESHOLD",
        "MAX_POSITIONS", "MAX_TOTAL_RISK_PCT", "MAX_DIRECTION_RISK_PCT",
        "MAX_RISK_PER_TRADE", "KELLY_FRACTION", "ATR_STOP_MULT", "ATR_TARGET_MULT",
        "HALT_DD_PCT", "DAILY_STOP_PCT", "AUTO_BLACKLIST_STOPS",
        "LIQUIDITY_MIN_USD", "LIQUIDITY_MAX_SHARE", "MIN_RR_AFTER_COSTS",
    )

    def _config_snapshot(self):
        values = {}
        for key in self.CONFIG_KEYS:
            if hasattr(config, key):
                val = getattr(config, key)
                if isinstance(val, (int, float, str, bool)) or val is None:
                    values[key] = val
        blob = json.dumps(values, sort_keys=True, separators=(",", ":"))
        return {"hash": hashlib.sha1(blob.encode()).hexdigest()[:8], "values": values}

    def _record_config(self):
        snap = self.config_snapshot
        self.journal.add(
            "config", "startup",
            "%s %s; %d params; %s" % (
                "resumed" if self.resumed else "cold start", snap["hash"],
                len(snap["values"]),
                ", ".join("%s=%s" % (k, snap["values"][k])
                          for k in ("PAIRS_ENABLED", "PAIRS_N", "PAIRS_HOLD_H",
                                    "PAIRS_EXPOSURE", "SNIPER_ENABLED",
                                    "SHORTS_ENABLED", "TAKER_FEE", "SLIPPAGE")
                          if k in snap["values"])),
            key=snap["hash"], config_hash=snap["hash"], resumed=self.resumed)
        self._event("config %s (%d params) %s" % (
            snap["hash"], len(snap["values"]),
            "resumed" if self.resumed else "cold start"))

    def _day_stamp(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load_universe(self):
        """Coins we may trade: from the scan cache, else the configured majors."""
        rows = universe.load_or_seed()
        self.universe = {r["coin"]: r for r in rows}
        self.active_symbols = [r["coin"] for r in rows]
        return rows

    def _universe_age_sec(self):
        data, _ = state_store.load(path=universe.cache_path())
        if not data or data.get("kind") != "universe":
            return None
        if data.get("scanner_version") != universe.SCANNER_VERSION:
            return None
        return time.time() - float(data.get("scanned_at") or 0)

    def refresh_universe(self):
        """Rescan the exchange in the background. A failure keeps the old list."""
        if not config.UNIVERSE_ENABLED:
            return False
        try:
            rows, pool = universe.scan(HLClient(), log=self._event)
        except Exception as exc:  # noqa: BLE001 - never kill the trading loop
            self._event("universe: refresh failed (%s)" % exc)
            return False
        if not rows:
            self._event("universe: nothing tradable, keeping %d symbols" % len(self.active_symbols))
            return False
        universe.save(rows)
        self._load_universe()
        self._event("universe: %d tradable of %d scanned" % (len(rows), len(pool)))
        return True

    def _universe_loop(self):
        ttl = max(config.UNIVERSE_REFRESH_HOURS, 0.25) * 3600
        while self.running:
            age = self._universe_age_sec()
            if age is not None and age < ttl:
                time.sleep(min(ttl - age, 600))
                continue
            self.refresh_universe()
            time.sleep(60)

    def _candle_window_open(self):
        """True once per hour, a few seconds after the hourly candle closes.

        Candle requests are the expensive part: 25 symbols polled every five
        seconds would be ~18k candleSnapshot calls an hour against a ~1.2k/h
        budget. The signal only ever changes on a candle close, so the scan
        runs once an hour and prices keep streaming in the meantime.
        """
        bucket = int(time.time() // 3600)
        if bucket == self.last_hour_bucket:
            return False
        if time.time() % 3600 < 5:
            return False
        self.last_hour_bucket = bucket
        return True

    def _scan_candles(self, ctxs):
        now_ms = int(time.time() * 1000)
        for sym in list(self.active_symbols):
            if sym not in self.last_prices:
                continue
            candles = self.client.candles(sym, config.TIMEFRAME,
                                          now_ms - 2 * 3600 * 1000, now_ms)
            if len(candles) < 2:
                continue
            closed_ts = candles[-2]["t"]
            if self.last_candle_ts.get(sym) == closed_ts:
                continue
            self.last_candle_ts[sym] = closed_ts
            self.analyze_symbol(sym, ctxs)

    def _state_snapshot(self):
        return {
            "mode": self.mode,
            "capital": self.capital,
            "cash": self.cash,
            "equity": self.equity,
            "peak": self.peak,
            "positions": {sym: p.__dict__ for sym, p in self.positions.items()},
            "trades": list(self.trades),
            "events": list(self.events),
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "win_sum": self.win_sum,
            "loss_sum": self.loss_sum,
            "r_sum": self.r_sum,
            "cooldowns": dict(self.cooldowns),
            "symbol_stops": {k: list(v) for k, v in self.symbol_stops.items()},
            "pairs": {
                "positions": [p.__dict__ for p in self.pairs.positions],
                "closed": list(self.pairs.closed),
                "stats": dict(self.pairs.stats),
                "credited": self.pairs.credited,
                "last_rebalance": self.pairs.last_rebalance,
            },
            "consec_losses": self.consec_losses,
            "loss_pause_until": self.loss_pause_until,
            "last_prices": dict(self.last_prices),
            "last_candle_ts": dict(self.last_candle_ts),
            "equity_history": list(self.equity_history),
            "journal": self.journal.to_state(),
            "halted": self.halted,
            "day_halt": self.day_halt,
            "day_stamp": self.day_stamp,
            "day_start_equity": self.day_start_equity,
            "started": self.status.get("started", time.time()),
            "restarts": self.restarts,
            "active_symbols": list(self.active_symbols),
            "sniper": {
                "execution_history": list(self.sniper.execution_history),
                "rejected": self.sniper.rejected,
                "total_snipes": self.sniper.total_snipes,
                "strong_snipes": self.sniper.strong_snipes,
                "slippage_sum": self.sniper.slippage_sum,
                "slippage_n": self.sniper.slippage_n,
            },
        }

    def _restore_state(self):
        if os.getenv("RESET_STATE", "").strip().lower() in ("1", "true", "yes"):
            state_store.clear()
            self._event("RESET_STATE set: starting from a clean state")
            self._event("bot v1.1 initialized mode=%s capital=%s" % (self.mode, money(self.capital)))
            return
        data, source = state_store.load()
        if not data:
            self._event("bot v1.1 initialized mode=%s capital=%s" % (self.mode, money(self.capital)))
            return
        self.state_source = source
        self.resumed = True
        self.capital = float(data.get("capital", self.capital))
        self.cash = float(data.get("cash", self.cash))
        self.equity = float(data.get("equity", self.equity))
        self.peak = float(data.get("peak", self.peak))
        self.positions = {}
        for sym, raw in (data.get("positions") or {}).items():
            try:
                self.positions[sym] = Position(**raw)
            except TypeError:
                continue
        self.trades = list(data.get("trades") or [])
        # Old snapshots hold "HH:MM:SS | text" strings; new ones hold
        # {"ts", "msg"}. Keeping both forms means an upgrade never drops events.
        self.events = list(data.get("events") or [])
        self.win_count = int(data.get("win_count", 0))
        self.loss_count = int(data.get("loss_count", 0))
        self.win_sum = float(data.get("win_sum", 0.0))
        self.loss_sum = float(data.get("loss_sum", 0.0))
        self.r_sum = float(data.get("r_sum", 0.0))
        self.cooldowns = {k: float(v) for k, v in (data.get("cooldowns") or {}).items()}
        # Keep only stops still inside the window, so the map cannot grow forever.
        cutoff = time.time() - config.AUTO_BLACKLIST_WINDOW_DAYS * 86400.0
        self.symbol_stops = {
            k: [float(t) for t in v if float(t) > cutoff]
            for k, v in (data.get("symbol_stops") or {}).items()}
        self.symbol_stops = {k: v for k, v in self.symbol_stops.items() if v}
        self.consec_losses = int(data.get("consec_losses", 0))
        self.loss_pause_until = float(data.get("loss_pause_until", 0.0))
        self.last_prices = {k: float(v) for k, v in (data.get("last_prices") or {}).items()}
        self.last_candle_ts = dict(data.get("last_candle_ts") or {})
        pair_state = data.get("pairs") or {}
        self.pairs.positions = []
        for raw in pair_state.get("positions") or []:
            try:
                self.pairs.positions.append(pairs_module.PairPosition(**raw))
            except TypeError:
                continue
        self.pairs.closed = list(pair_state.get("closed") or [])
        self.pairs.stats.update(pair_state.get("stats") or {})
        self.pairs.credited = float(pair_state.get("credited", 0.0))
        self.pairs.reconcile_cash()
        self.pairs.last_rebalance = float(pair_state.get("last_rebalance", 0.0))
        self.equity_history = [list(x) for x in (data.get("equity_history") or [])]
        self.journal.from_state(data.get("journal"))
        self.halted = bool(data.get("halted", False))
        self.day_halt = bool(data.get("day_halt", False))
        self.day_stamp = data.get("day_stamp") or self._day_stamp()
        self.day_start_equity = float(data.get("day_start_equity", self.equity))
        sniper = data.get("sniper") or {}
        self.sniper.execution_history = list(sniper.get("execution_history") or [])
        self.sniper.rejected = int(sniper.get("rejected", 0))
        # Older snapshots carried no counters; fall back to the window length.
        self.sniper.total_snipes = int(sniper.get("total_snipes", len(self.sniper.execution_history)))
        # Snapshots written before the counters existed carry no strong count;
        # seed it from the retained window rather than showing a hard zero.
        window_strong = sum(1 for e in self.sniper.execution_history
                            if e.get("strength") == "strong")
        self.sniper.strong_snipes = int(sniper.get("strong_snipes", window_strong))
        self.sniper.slippage_sum = float(sniper.get("slippage_sum", 0.0))
        self.sniper.slippage_n = int(sniper.get("slippage_n", 0))
        if len(self.sniper.execution_history) > self.sniper.HISTORY_MAX:
            del self.sniper.execution_history[:-self.sniper.HISTORY_MAX]
        stored_symbols = data.get("active_symbols")
        if stored_symbols and not universe.load():
            self.active_symbols = list(stored_symbols)
        self.status["started"] = float(data.get("started", self.status["started"]))
        self.restarts = int(data.get("restarts", 0)) + 1
        self._event("bot v1.2 resumed from %s: trades=%d equity=%s open=%d" % (
            os.path.basename(source), len(self.trades), money(self.equity), len(self.positions)))

    def _save_state(self, force=False):
        now = time.time()
        if not force and now - self.last_save_ts < config.STATE_SAVE_SEC:
            return
        try:
            state_store.save(self._state_snapshot())
            self.last_save_ts = now
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("state save failed: %s", exc)

    def _event(self, msg):
        # Stored with an epoch, not a pre-rendered clock: the server runs in UTC
        # while the phone is on MSK, and a UTC "02:45" next to a local "06:05"
        # reads like a three-hour gap that never happened. Old entries are plain
        # strings and are left alone, so a restored state never loses history.
        self.events.append({"ts": round(time.time(), 1), "msg": msg})
        if len(self.events) > 50:
            self.events = self.events[-50:]
        logger.info(msg)

    def _recalc_equity(self):
        unreal = 0.0
        for p in self.positions.values():
            px = self.last_prices.get(p.symbol, p.entry)
            unreal += (px - p.entry) * p.size if p.direction == "long" else (p.entry - px) * p.size
        # Pairs are open exposure too, so their swing belongs in equity and in
        # the drawdown that gates the bot. Leaving it out understated both.
        pairs_unreal = self.pairs.unrealized() if getattr(self, "pairs", None) else 0.0
        self.equity = self.cash + unreal + pairs_unreal
        self.peak = max(self.peak, self.equity)

    def kelly_risk(self):
        total = self.win_count + self.loss_count
        if total < 20:
            return config.BASE_RISK_PER_TRADE
        wr = self.win_count / total
        aw = self.win_sum / self.win_count if self.win_count else 0.02
        al = self.loss_sum / self.loss_count if self.loss_count else 0.01
        if al <= 0:
            return config.BASE_RISK_PER_TRADE
        k = min(wr - (1 - wr) / (aw / al), 0.06)
        # No floor. The old max(0.05, ...) pinned the risk at a constant 1.25%
        # whatever the estimate said, so the bot never de-risked while losing.
        # Kelly exists to shrink, or stop, when the measured edge is negative.
        if k <= 0:
            return config.MIN_RISK_PCT * config.KELLY_NEGATIVE_MULT
        return max(config.MIN_RISK_PCT, min(k * config.KELLY_FRACTION, config.MAX_RISK_PCT))

    def cluster_of(self, symbol):
        for name, members in config.CLUSTERS.items():
            if symbol in members:
                return name
        return symbol

    def cluster_risk(self, symbol, exclude=None):
        """Open risk held inside the same correlation cluster as `symbol`.

        This used to sum every open position regardless of cluster, so the 3%
        cluster cap was really a 3% cap on total risk - stricter than the 6%
        MAX_TOTAL_RISK_PCT. With one cluster covering only BTC/ETH/SOL that was
        mostly invisible; expanding the clusters to the whole universe silently
        halved the trade count.
        """
        members = config.CLUSTERS.get(self.cluster_of(symbol), [symbol])
        risk = 0.0
        for sym, p in self.positions.items():
            if exclude and sym == exclude:
                continue
            if sym in members:
                risk += p.risk_dist * p.size
        return risk

    def direction_risk(self, direction, exclude=None):
        """Open risk held in one direction. Crypto beta dominates everything
        else, so this is what actually stops a pile-up of same-way positions."""
        risk = 0.0
        for sym, p in self.positions.items():
            if exclude and sym == exclude:
                continue
            if p.direction == direction:
                risk += p.risk_dist * p.size
        return risk

    def symbol_benched(self, symbol):
        """True while a coin is serving an auto-blacklist pause.

        The reference bot benches a coin after two stops in thirty days and
        keeps it out for a fortnight. Doing it by hand means acting after the
        losses; doing it here means the coin leaves the pool on its own.
        """
        if config.AUTO_BLACKLIST_STOPS <= 0:
            return False
        now = time.time()
        window = config.AUTO_BLACKLIST_WINDOW_DAYS * 86400.0
        stops = [t for t in self.symbol_stops.get(symbol, []) if now - t < window]
        if len(stops) < config.AUTO_BLACKLIST_STOPS:
            return False
        return now < stops[-1] + config.AUTO_BLACKLIST_PAUSE_DAYS * 86400.0

    def benched_symbols(self):
        return sorted(s for s in self.symbol_stops if self.symbol_benched(s))

    def cost_pct(self, funding_hourly=0.0, direction="long", hours=None):
        """Round-trip cost as a fraction of notional: fees + slippage + funding.

        This build crosses the book on both legs, so fees and slippage are
        charged twice. Hyperliquid funding is HOURLY and is only counted when
        it works against the position.
        """
        held = config.ASSUMED_HOLDING_HOURS if hours is None else hours
        fees = config.ENTRY_FEE_RATE + config.TAKER_FEE
        slippage = 2.0 * (config.SLIPPAGE / 4.0)
        adverse = 0.0
        if direction == "long" and funding_hourly > 0:
            adverse = funding_hourly * held
        elif direction == "short" and funding_hourly < 0:
            adverse = -funding_hourly * held
        return fees + slippage + adverse

    def position_size(self, entry, stop, conv_score, regime, funding_hourly=0.0,
                      direction="long", symbol=None):
        risk_pct = self.kelly_risk()
        risk_pct *= config.REGIME_SIZE_MULT.get(regime, 1.0)
        dd = (self.peak - self.equity) / self.peak
        if dd >= 0.10:
            risk_pct *= config.DD_BREAKER_10_PCT
        elif dd >= 0.05:
            risk_pct *= config.DD_BREAKER_5_PCT
        if conv_score >= config.CONVERGENCE_THRESHOLD_STRONG:
            risk_pct *= config.SNIPER_SIZE_MULT
        # A coin's noise class caps how much it may ever risk and how much
        # leverage it may use, so a noisy coin never out-risks a calm one.
        info = (self.universe.get(symbol) or {}) if symbol else {}
        cap = info.get("risk_pct_cap")
        if cap:
            risk_pct = min(risk_pct, float(cap) / 100.0)
        if symbol and symbol in config.WHITELIST:
            risk_pct *= config.WHITELIST_SIZE_BONUS
        leverage = config.LEVERAGE
        class_lev = info.get("class_max_leverage")
        if class_lev:
            leverage = min(leverage, float(class_lev))
        stop_dist = abs(entry - stop)
        if stop_dist <= 0:
            return 0.0
        # Costs are paid out of the same risk budget as the stop distance.
        # Ignoring them made the real risk per trade exceed risk_pct.
        denom = stop_dist + entry * self.cost_pct(funding_hourly, direction)
        if denom <= 0:
            return 0.0
        size = (self.equity * risk_pct) / denom
        max_notional = self.equity * config.MAX_MARGIN_UTILIZATION * leverage
        if size * entry > max_notional:
            size = max_notional / entry
        if size * entry < config.MIN_NOTIONAL:
            return 0.0
        return size

    def open_position(self, symbol, direction, entry, atr_val, conv, book_slippage_pct,
                      funding_hourly=0.0, market_regime=None):
        if self.halted or self.day_halt:
            return False
        if len(self.positions) >= config.MAX_POSITIONS:
            return False
        if symbol in self.positions:
            return False
        cd = self.cooldowns.get(symbol, 0)
        if time.time() < cd:
            return False
        stop = entry - config.ATR_STOP_MULT * atr_val if direction == "long" else entry + config.ATR_STOP_MULT * atr_val
        target = entry + config.ATR_TARGET_MULT * atr_val if direction == "long" else entry - config.ATR_TARGET_MULT * atr_val
        risk_dist = abs(entry - stop)
        # Sizing has to see the regime the signal was found in. Passing the
        # literal "normal" made config.REGIME_SIZE_MULT dead code: a high
        # volatility entry was risked exactly like a calm one. Unknown names
        # fall back instead of silently losing the multiplier.
        regime = market_regime if market_regime in config.REGIME_SIZE_MULT else "normal"
        size = self.position_size(entry, stop, conv["score"], regime, funding_hourly,
                                  direction, symbol)
        if size <= 0:
            return False
        open_risk = sum(p.risk_dist * p.size for p in self.positions.values())
        if open_risk + risk_dist * size > self.equity * config.MAX_TOTAL_RISK_PCT:
            return False
        if self.cluster_of(symbol) in config.CLUSTERS:
            if (self.cluster_risk(symbol) + risk_dist * size
                    > self.equity * config.MAX_CLUSTER_RISK_PCT):
                self._event("skip %s: %s cluster risk would exceed %.1f%%" % (
                    symbol, self.cluster_of(symbol), 100 * config.MAX_CLUSTER_RISK_PCT))
                return False
        # One direction at a time. Three shorts opened inside twelve seconds and
        # all three stopped out together; this is the check that stops the
        # second and third from being taken at all.
        if (self.direction_risk(direction) + risk_dist * size
                > self.equity * config.MAX_DIRECTION_RISK_PCT):
            self._event("skip %s: %s risk would exceed the %.1f%% directional cap" % (
                symbol, direction, 100 * config.MAX_DIRECTION_RISK_PCT))
            return False
        slip = max(book_slippage_pct / 100.0, config.SLIPPAGE / 4)
        fill = entry * (1 + slip) if direction == "long" else entry * (1 - slip)
        fee = size * fill * config.TAKER_FEE
        self.cash -= fee
        pos = Position(symbol, direction, fill, size, stop, target, risk_dist, atr_val)
        pos.initial_size = size
        pos.entry_fee = fee
        pos.initial_risk_usd = risk_dist * size
        self.positions[symbol] = pos
        edge_pct = abs(target - fill) / fill * 100.0
        cost_pct = self.cost_pct(funding_hourly, direction) * 100.0
        cov = coverage_x(edge_pct, cost_pct)
        pos.context = {
            "score": round(conv["score"], 3),
            "strength": conv.get("strength"),
            "regime": market_regime,
            # What sizing actually used. Before bug 50 these always differed:
            # open_position passed the literal "normal" into position_size, so
            # config.REGIME_SIZE_MULT never applied. Recording both keeps the
            # journal able to prove which build produced the trade.
            "sizing_regime": regime,
            "atr_pct": round(atr_val / entry * 100.0, 3) if entry else None,
            "funding_bp_h": round(funding_hourly * 10000.0, 3),
            "slip_pct": round(book_slippage_pct, 3),
            "edge_pct": round(edge_pct, 3),
            "cost_pct": round(cost_pct, 3),
            "coverage_x": round(cov, 2) if cov is not None else None,
            "cluster": self.cluster_of(symbol),
            "risk_usd": round(pos.initial_risk_usd, 2),
            "risk_pct_equity": (round(100.0 * pos.initial_risk_usd / self.equity, 2)
                                if self.equity else None),
        }
        self.journal.add(
            "sniper", "open",
            "%s %s score %.2f %s regime %s; target %.2f%% away, cost %.3f%%, "
            "coverage %s; risk $%.2f (%.1f%% of equity); slip %.3f%%; "
            "funding %+.3f bp/h; atr %.2f%%; cluster %s" % (
                direction.upper(), symbol, conv["score"], conv.get("strength", ""),
                market_regime or "-",
                edge_pct, cost_pct,
                ("%.1fx" % cov) if cov is not None else "n/a",
                pos.initial_risk_usd, pos.context["risk_pct_equity"] or 0.0,
                book_slippage_pct, funding_hourly * 10000.0,
                pos.context["atr_pct"] or 0.0, pos.context["cluster"]),
            symbol=symbol, key=direction, score=round(conv["score"], 3),
            edge_pct=round(edge_pct, 3), cost_pct=round(cost_pct, 3))
        self._event("OPEN %s %s @ %s size=%.4f stop=%s target=%s score=%.2f %s slip=%.3f%%" % (
            direction.upper(), symbol, price(fill), size, price(stop), price(target),
            conv["score"], conv["strength"], book_slippage_pct))
        send_telegram("OPEN %s %s @ %s score=%.2f" % (direction.upper(), symbol, money(fill), conv["score"]))
        self._save_state(force=True)
        return True

    def close_position(self, symbol, price, reason):
        pos = self.positions.pop(symbol, None)
        if not pos:
            return
        # Drop this coin's signals so a stale set cannot trigger an instant
        # re-entry on the very next scan.
        if config.RESET_SIGNALS_ON_CLOSE:
            self.sniper.convergence.signals.pop(symbol, None)
        if reason == "stop":
            self.symbol_stops.setdefault(symbol, []).append(time.time())
        slip = config.SLIPPAGE / 4
        fill = price * (1 - slip) if pos.direction == "long" else price * (1 + slip)
        pnl = (fill - pos.entry) * pos.size if pos.direction == "long" else (pos.entry - fill) * pos.size
        fee = pos.size * fill * config.TAKER_FEE
        net = pnl - fee
        self.cash += net
        # The whole round trip, so the trade record matches the cash movement.
        # The entry fee was already taken from cash at open but was left out of
        # the record, which overstated every trade's PnL, its R, the winrate and
        # the profit factor - and produced the impossible combination of a
        # positive average R on a losing account.
        total = net + pos.realized_pnl - pos.entry_fee
        base = (pos.initial_size or pos.size) * pos.entry
        ret_pct = total / base if base > 0 else 0.0
        if total > 0:
            self.win_count += 1
            self.win_sum += ret_pct
            self.consec_losses = 0
        else:
            self.loss_count += 1
            self.loss_sum += abs(ret_pct)
            self.consec_losses += 1
            if (config.CONSEC_LOSS_PAUSE > 0
                    and self.consec_losses >= config.CONSEC_LOSS_PAUSE):
                self.loss_pause_until = time.time() + config.CONSEC_LOSS_PAUSE_SEC
                self._event("PAUSE: %d losses in a row, entries blocked for %.1fh" % (
                    self.consec_losses, config.CONSEC_LOSS_PAUSE_SEC / 3600.0))
                send_telegram("PAUSE: %d losses in a row - new entries blocked" % self.consec_losses)
        risk_usd = pos.initial_risk_usd or (pos.risk_dist * pos.size)
        r_mult = total / risk_usd if risk_usd > 0 else 0.0
        self.r_sum += r_mult
        self.trades.append({"symbol": symbol, "direction": pos.direction, "entry": pos.entry,
                            "exit": fill, "pnl": total, "r": round(r_mult, 2),
                            "partial_pnl": round(pos.realized_pnl, 2),
                            "reason": reason, "time": time.time()})
        self._event("CLOSE %s @ %s pnl=%s (%s)" % (symbol, money(fill), money(net), reason))
        ctx = pos.context or {}
        self.journal.add(
            "sniper", "close",
            "%s %s %s at %s after %.1fh: net $%+.2f (%+.2f%%), %.2fR; "
            "opened at score %s, target %.2f%% away vs cost %.3f%% => coverage %s" % (
                pos.direction.upper(), symbol, reason, money(fill),
                (time.time() - pos.entry_time) / 3600.0, net,
                100.0 * total / (pos.entry * (pos.initial_size or pos.size) or 1.0),
                r_mult, ctx.get("score", "n/a"), ctx.get("edge_pct") or 0.0,
                ctx.get("cost_pct") or 0.0,
                ("%.1fx" % ctx["coverage_x"]) if ctx.get("coverage_x") is not None else "n/a"),
            symbol=symbol, key=reason, pnl=round(net, 2), r=round(r_mult, 2))
        send_telegram("CLOSE %s pnl=%s (%s)" % (symbol, money(net), reason))
        self._save_state(force=True)

    def book_partial(self, pos, symbol, px):
        closed = pos.size * config.PARTIAL_RATIO
        fill = px * (1 - config.SLIPPAGE / 4) if pos.direction == "long" else px * (1 + config.SLIPPAGE / 4)
        pnl = (fill - pos.entry) * closed if pos.direction == "long" else (pos.entry - fill) * closed
        fee = closed * fill * config.TAKER_FEE
        booked = pnl - fee
        self.cash += booked
        pos.realized_pnl += booked
        pos.size -= closed
        self._event("PARTIAL %s closed %.0f%% @ %s booked pnl=%s" % (
            symbol, config.PARTIAL_RATIO * 100, money(fill), money(booked)))
        self._save_state(force=True)

    def manage_positions(self):
        for symbol, pos in list(self.positions.items()):
            px = self.last_prices.get(symbol)
            if not px:
                continue
            if time.time() - pos.entry_time > config.TIME_STOP_HOURS * 3600:
                self.close_position(symbol, px, "time_stop")
                self.cooldowns[symbol] = time.time() + config.COOLDOWN_AFTER_STOP_H * 3600
                continue
            r = pos.risk_dist
            if pos.direction == "long":
                if px <= pos.stop:
                    self.close_position(symbol, px, "stop")
                    self.cooldowns[symbol] = time.time() + config.COOLDOWN_AFTER_STOP_H * 3600
                    continue
                if not pos.partial_done and px >= pos.entry + config.PARTIAL_AT_R * r:
                    self.book_partial(pos, symbol, px)
                    pos.stop, pos.partial_done = pos.entry, True
                if pos.partial_done:
                    if config.ATR_TRAIL_MULT > 0:
                        trail = px - pos.atr_at_entry * config.ATR_TRAIL_MULT
                        if trail > pos.stop:
                            pos.stop = trail
                    elif px > pos.entry + (config.PARTIAL_AT_R + 1.0) * r:
                        pos.stop = max(pos.stop, pos.entry + 0.5 * r)
                if px >= pos.target:
                    self.close_position(symbol, px, "target")
            else:
                if px >= pos.stop:
                    self.close_position(symbol, px, "stop")
                    self.cooldowns[symbol] = time.time() + config.COOLDOWN_AFTER_STOP_H * 3600
                    continue
                if not pos.partial_done and px <= pos.entry - config.PARTIAL_AT_R * r:
                    self.book_partial(pos, symbol, px)
                    pos.stop, pos.partial_done = pos.entry, True
                if pos.partial_done:
                    if config.ATR_TRAIL_MULT > 0:
                        trail = px + pos.atr_at_entry * config.ATR_TRAIL_MULT
                        if trail < pos.stop:
                            pos.stop = trail
                    elif px < pos.entry - (config.PARTIAL_AT_R + 1.0) * r:
                        pos.stop = min(pos.stop, pos.entry - 0.5 * r)
                if px <= pos.target:
                    self.close_position(symbol, px, "target")

    def regime_of(self, atr_now, atr_slow):
        if atr_slow <= 0:
            return "normal"
        ratio = atr_now / atr_slow
        if ratio < config.REGIME_LOW:
            return "low"
        if ratio > config.REGIME_HIGH:
            return "high"
        return "normal"

    def _watch_coins(self, delay=None):
        """Measure the active universe for the dashboard; trade nothing.

        The signal scan only runs while the sniper is enabled, so with it off
        the panel would have no measurements and the bot would look asleep.
        This pass fetches the same candles the scan would fetch and stops at
        measure_coin: it cannot open a position, and it writes nothing but
        self.coin_info.
        """
        ctxs = {}
        # A clean start, so a pass that fully succeeds clears the last failure
        # instead of leaving a stale error on the panel.
        self.watch_error = ""
        try:
            ctxs = self.client.meta_and_ctxs() or {}
        except Exception as exc:  # noqa: BLE001 - the panel is not worth a loop error
            self.watch_error = "ctxs: %s" % exc
        # candleSnapshot allows roughly 20 requests a minute, so the pass is
        # spaced out like the universe scan. It runs on its own thread, once an
        # hour: nobody is waiting for it.
        delay = config.UNIVERSE_DELAY_SEC if delay is None else delay
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - config.CANDLE_COUNT * 3600 * 1000
        symbols = list(self.active_symbols)
        seen = 0
        for i, sym in enumerate(symbols):
            if i and delay > 0:
                time.sleep(delay)
            try:
                candles = self.client.candles(sym, config.TIMEFRAME, start_ms, now_ms)
                if len(candles) < 60:
                    continue
                measured = self.measure_coin(sym, ctxs.get(sym) or {}, candles)
                if measured is None:
                    continue
                self.coin_info[sym] = measured[0]
                seen += 1
            except Exception as exc:  # noqa: BLE001 - one coin must not stop the pass
                self.watch_error = "%s: %s" % (sym, exc)
        self.watch_seen = seen
        self._event("watch: measured %d coins" % seen)

    def _watch_loop(self):
        """Hourly, on its own thread: the dashboard must not wait for the scan."""
        while self.running:
            try:
                self._watch_coins()
            except Exception as exc:  # noqa: BLE001 - never kill the thread
                self.watch_error = "loop: %s" % exc
            time.sleep(3600)

    def measure_coin(self, symbol, ctx, candles):
        """Everything we know about one coin from its candles, nothing else.

        Pure measurement: no orders, no sniper state, no journal. The trading
        scan calls it and then decides whether the numbers justify a trade; the
        hourly watch pass calls it only to fill the dashboard. One
        implementation means the panel cannot report different numbers than the
        signal that traded the coin.
        """
        info = self.universe.get(symbol) or {}
        highs = [c["h"] for c in candles]
        lows = [c["l"] for c in candles]
        closes = [c["c"] for c in candles]
        vols = [c["v"] for c in candles]
        last_close = closes[-1]
        a = atr(highs, lows, closes)
        if a <= 0:
            return None
        atrs = atr_series(highs, lows, closes, from_len=30)
        atr_slow = sma(atrs, 50)
        regime = self.regime_of(a, atr_slow)
        e_fast = ema(closes[-60:], 20)
        e_slow = ema(closes[-120:], 50)
        direction = "long" if e_fast > e_slow else "short"
        ax = adx(highs, lows, closes)
        base_vol = sma(vols[-21:-1], 20)
        v_ratio = vols[-2] / base_vol if base_vol > 0 else 0.0
        row = {
            "ts": time.time(),
            "adx": round(ax, 1),
            "atr_pct": round(100.0 * a / last_close, 2) if last_close else None,
            # 168 hourly candles is the seven-day window; 24 for the day.
            "chg_24h": round(100.0 * (last_close / closes[-25] - 1.0), 2) if len(closes) >= 25 else None,
            "chg_7d": round(100.0 * (last_close / closes[-169] - 1.0), 2) if len(closes) >= 169 else None,
            "funding_bp": round(1e4 * (ctx.get("funding") or 0.0), 2),
            "vol_usd": ctx.get("day_volume_usd"),
            "regime": regime,
            "direction": direction,
            "noise": info.get("noise_class"),
            "tradable": info.get("tradable", True),
            "score": None,
            "note": "",
        }
        locals_ = {"atr": a, "adx": ax, "direction": direction, "regime": regime,
                   "last_close": last_close, "vol_ratio": v_ratio,
                   "highs": highs, "lows": lows, "closes": closes, "vols": vols}
        return row, locals_

    def analyze_symbol(self, symbol, ctxs):
        if symbol in config.BLACKLIST:
            return
        info = self.universe.get(symbol)
        if info is not None and not info.get("tradable", True):
            return
        if time.time() < self.cooldowns.get(symbol, 0):
            return
        if time.time() < self.loss_pause_until:
            return
        if self.symbol_benched(symbol):
            return
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - config.CANDLE_COUNT * 3600 * 1000
        ctx = ctxs.get(symbol) or {}
        if ctx.get("day_volume_usd", 0) < config.MIN_VOLUME_USD:
            return
        if abs(ctx.get("premium", 0)) > config.PREMIUM_MAX_PCT:
            self._event("skip %s: premium %.2f%%" % (symbol, ctx.get("premium", 0) * 100))
            return
        candles = self.client.candles(symbol, config.TIMEFRAME, start_ms, now_ms)
        if len(candles) < 60:
            self._event("skip %s: not enough candles" % symbol)
            return
        measured = self.measure_coin(symbol, ctx, candles)
        if measured is None:
            return
        row, m = measured
        self.coin_info[symbol] = row
        highs, lows, vols = m["highs"], m["lows"], m["vols"]
        last_close = m["last_close"]
        a = m["atr"]
        ax = m["adx"]
        direction = m["direction"]
        regime = m["regime"]
        v_ratio = m["vol_ratio"]
        levels = find_levels(highs[:-1], lows[:-1], vols[:-1], a)
        prev = candles[-2]
        if not breakout_confirmed(levels, direction, prev["l"], prev["h"], last_close, a):
            self.coin_info[symbol]["note"] = "нет слома структуры - вход не по правилам"
            return
        self.sniper.convergence.add_signal(symbol, generate_trend_signal(direction, ax, config.ADX_MIN))
        self.sniper.convergence.add_signal(symbol, generate_volume_signal(direction, v_ratio, config.VOLUME_RATIO_MIN))
        book = self.client.l2_book(symbol)
        if not book:
            return
        self.sniper.update_order_book(symbol, book["bids"], book["asks"])
        d = self.sniper.order_books[symbol].depth()
        self.sniper.convergence.add_signal(symbol, generate_orderbook_signal(direction, d["ratio"]))
        self.sniper.convergence.add_signal(symbol, generate_funding_signal(direction, ctx.get("funding", 0.0)))
        conv = self.sniper.convergence.evaluate(symbol)
        if not conv or conv["direction"] != direction:
            self.coin_info[symbol]["note"] = "сигналы не сошлись в сторону"
            return
        self.coin_info[symbol]["score"] = round(conv["score"], 3)
        if conv["direction"] == "short" and not config.SHORTS_ENABLED:
            self._event("skip %s: shorts disabled" % symbol)
            return
        # A high-volatility regime must clear a higher bar, not merely take a
        # smaller position. Whitelisted coins get a small discount.
        threshold = config.CONVERGENCE_THRESHOLD * config.REGIME_FILTER_MULT.get(regime, 1.0)
        if symbol in config.WHITELIST:
            threshold -= config.WHITELIST_SCORE_BONUS
        # Never let the bar climb above what the score can actually reach.
        threshold = min(threshold, config.MAX_ENTRY_THRESHOLD)
        if conv["score"] < threshold:
            self._event("skip %s: score %.2f below regime bar %.2f (%s)" % (
                symbol, conv["score"], threshold, regime))
            self.coin_info[symbol]["note"] = "score %.2f ниже порога %.2f" % (
                conv["score"], threshold)
            return
        stop = last_close - config.ATR_STOP_MULT * a if direction == "long" else last_close + config.ATR_STOP_MULT * a
        target_dist = config.ATR_TARGET_MULT * a
        if target_dist / last_close < config.MIN_TARGET_PCT:
            self._event("skip %s: target %.2f%% below viability floor" % (symbol, 100 * target_dist / last_close))
            return
        funding_hourly = ctx.get("funding", 0.0) or 0.0
        costs = self.cost_pct(funding_hourly, direction)
        if target_dist / last_close < costs * config.MIN_RR_AFTER_COSTS:
            self._event("skip %s: reward %.2f%% vs costs %.2f%% x%.1f" % (
                symbol, 100 * target_dist / last_close, 100 * costs,
                config.MIN_RR_AFTER_COSTS))
            return
        size = self.position_size(last_close, stop, conv["score"], regime,
                                  funding_hourly, direction, symbol)
        if size <= 0:
            return
        result = self.sniper.execute_snipe(symbol, direction, size, conv)
        if result["status"] != "executed":
            self._event("SNIPER REJECT %s: %s" % (symbol, result.get("reason", "?")))
            return
        slip_pct = result["decision"]["slippage_pct"]
        self.open_position(symbol, direction, last_close, a, conv, slip_pct,
                           funding_hourly, market_regime=regime)

    def check_halts(self):
        dd = (self.peak - self.equity) / self.peak
        if dd >= config.HALT_DD_PCT and not self.halted:
            self.halted = True
            self._event("HALT: drawdown %.1f%%" % (dd * 100))
            send_telegram("HALT: drawdown %.1f%% - new entries blocked" % (dd * 100))
        stamp = self._day_stamp()
        if stamp != self.day_stamp:
            self.day_stamp = stamp
            self.day_start_equity = self.equity
            self.day_halt = False
        if not self.day_halt and self.equity < self.day_start_equity * (1 - config.DAILY_STOP_PCT):
            self.day_halt = True
            self._event("DAILY STOP: entries blocked until tomorrow UTC")
            send_telegram("DAILY STOP - entries blocked until tomorrow UTC")

    def _coins_breakdown(self, limit=40):
        """One row per coin the bot currently has an opinion about.

        The dashboard used to show only open positions, so a coin that was
        scanned, scored and rejected was invisible - which looks identical to a
        bot that is asleep. Now every coin carries its measurement and the
        reason it is not trading, and coins with exposure sort to the top.
        """
        grid_open = {}
        for row in ((self.status.get("grid") or {}).get("open") or []):
            grid_open.setdefault(row["coin"], []).append(row)
        pair_side = {}
        for pp in getattr(self.pairs, "positions", []):
            pair_side[pp.long_sym] = "L"
            pair_side[pp.short_sym] = "S"
        held = {sym: pos.direction for sym, pos in self.positions.items()}
        universe = self.universe or {}
        names = (set(universe) | set(grid_open) | set(pair_side) | set(held)
                 | set(self.last_prices))
        rows = []
        for sym in names:
            info = universe.get(sym) or {}
            ci = self.coin_info.get(sym) or {}
            px = self.last_prices.get(sym)
            lots = grid_open.get(sym) or []
            if px is None and lots:
                # A grid lot can outlive its coin's membership in the active
                # universe, so the price poll stops updating it. The lab keeps
                # the last close it saw; showing that beats showing a dash on a
                # coin that is holding money.
                px = lots[0].get("last")
            unreal = None
            if lots and px:
                avg = sum(lot["entry"] for lot in lots) / len(lots)
                if avg:
                    unreal = round(100.0 * (px / avg - 1.0), 2)
            flags = info.get("flags") or []
            note = ci.get("note") or ""
            if not note and flags:
                note = "скан: " + ", ".join(flags)
            rows.append({
                "sym": sym,
                "price": px,
                "chg_24h": ci.get("chg_24h"),
                "chg_7d": ci.get("chg_7d"),
                "score": ci.get("score"),
                "adx": ci.get("adx"),
                "atr_pct": ci.get("atr_pct"),
                "noise": info.get("noise_class") or ci.get("noise"),
                "vol_usd": info.get("day_vlm"),
                "funding_bp": ci.get("funding_bp"),
                "grid_lots": len(lots),
                "grid_unreal_pct": unreal,
                "pair": pair_side.get(sym),
                "pos": held.get(sym),
                "benched": self.symbol_benched(sym),
                "tradable": info.get("tradable", True),
                "note": note,
            "measured_ts": ci.get("ts"),
            })
        rows.sort(key=lambda r: (bool(r["pos"] or r["grid_lots"] or r["pair"]),
                                 r["score"] or 0.0, r["vol_usd"] or 0.0),
                  reverse=True)
        return rows[:limit]

    def update_status(self):
        self._recalc_equity()
        self.check_halts()
        self.equity_history.append([round(time.time(), 1), round(self.equity, 2)])
        if len(self.equity_history) > 1440:
            self.equity_history = self.equity_history[-1440:]
        open_risk = 0.0
        for p in self.positions.values():
            open_risk += p.risk_dist * p.size
        day_pnl = self.equity - self.day_start_equity
        self.status.update({
            "status": "running" if self.running else "stopped",
            "mode": self.mode, "equity": round(self.equity, 2),
            "cash": round(self.cash, 2), "peak": round(self.peak, 2),
            "drawdown_pct": round((self.peak - self.equity) / self.peak * 100, 2),
            "positions": [p.__dict__ for p in self.positions.values()],
            "trades_total": len(self.trades),
            "winrate": round(100.0 * self.win_count / len(self.trades), 1) if self.trades else 0.0,
            "avg_r": round(self.r_sum / len(self.trades), 2) if self.trades else 0.0,
            "positions_open": len(self.positions),
            "halted": self.halted, "day_halt": self.day_halt,
            "symbols": list(self.active_symbols), "prices": self.last_prices,
            "universe": {
                "size": len(self.active_symbols),
                "enabled": config.UNIVERSE_ENABLED,
                "classes": {c: (self.universe.get(c) or {}).get("noise_class")
                            for c in self.active_symbols},
            },
            "sniper_stats": self.sniper.get_stats(),
            "equity_history": list(self.equity_history),
            "trades": list(reversed(self.trades[-30:])),
            "day_pnl": round(day_pnl, 2),
            "day_pnl_pct": round(day_pnl / self.day_start_equity * 100, 2) if self.day_start_equity else 0.0,
            "kelly_risk_pct": round(self.kelly_risk() * 100, 2),
            "open_risk": round(open_risk, 2),
            "open_risk_pct": round(open_risk / self.equity * 100, 2) if self.equity else 0.0,
            "wins": self.win_count, "losses": self.loss_count,
            "uptime_sec": round(time.time() - self.status.get("started", time.time()), 1),
            "state_path": state_store.state_path(),
            "restarts": self.restarts,
            "settings": {
                "convergence": config.CONVERGENCE_THRESHOLD,
                "convergence_strong": config.CONVERGENCE_THRESHOLD_STRONG,
                "atr_stop_mult": config.ATR_STOP_MULT,
                "atr_target_mult": config.ATR_TARGET_MULT,
                "book_max_age_sec": config.BOOK_MAX_AGE_SEC,
                "cost_pct_base": round(self.cost_pct(0.0, "long"), 6),
                "entry_fee_rate": config.ENTRY_FEE_RATE,
                "holding_hours": config.ASSUMED_HOLDING_HOURS,
                "min_rr_after_costs": config.MIN_RR_AFTER_COSTS,
                "symbols": list(config.SYMBOLS),
                "shorts_enabled": config.SHORTS_ENABLED,
                "atr_trail_mult": config.ATR_TRAIL_MULT,
                "partial_ratio": config.PARTIAL_RATIO,
                "partial_at_r": config.PARTIAL_AT_R,
                "kelly_fraction": config.KELLY_FRACTION,
                "kelly_negative_mult": config.KELLY_NEGATIVE_MULT,
                "consec_loss_pause": config.CONSEC_LOSS_PAUSE,
                "daily_stop_pct": config.DAILY_STOP_PCT,
                "price_poll_sec": config.PRICE_POLL_SEC,
                "blacklist": sorted(config.BLACKLIST),
                "whitelist": sorted(config.WHITELIST),
                "clusters": len(config.CLUSTERS),
                "max_direction_risk_pct": config.MAX_DIRECTION_RISK_PCT,
                "auto_blacklist_stops": config.AUTO_BLACKLIST_STOPS,
                "auto_blacklist_pause_days": config.AUTO_BLACKLIST_PAUSE_DAYS,
            },
            "consec_losses": self.consec_losses,
            "loss_pause_until": self.loss_pause_until,
            "benched": self.benched_symbols(),
            "sniper_enabled": config.SNIPER_ENABLED,
            "pairs": self.pairs.get_stats(),
            "lab": self.lab.status(),
            "grid": self.grid.status(),
            # Read-only hourly measurement pass, so the panel can say how fresh
            # its per-coin numbers are and whether the pass is failing.
            "watch": {"seen": self.watch_seen, "error": self.watch_error},
            # Whether alerts are really being delivered, so the registry can
            # show it instead of a hardcoded line.
            "telegram": telegram_status(),
            "config": getattr(self, "config_snapshot", None),
            "journal": self.journal.recent(30),
            "journal_counts": self.journal.counts(),
            "journal_dropped": self.journal.dropped,
            "events": list(reversed(self.events[-20:]))})
        # The strategy registry and the per-coin table read the status dict that
        # was just built, so they cannot drift from what the bot is doing.
        self.status["coins"] = self._coins_breakdown()
        self.status["strategies"] = strategy_map.build(self.status)
        HealthHandler.bot_status = self.status
        self._save_state()

    def run(self):
        self.running = True
        self.status["status"] = "running"
        threading.Thread(target=start_health_server, kwargs={"port": config.PORT}, daemon=True).start()
        threading.Thread(target=self._universe_loop, daemon=True).start()
        threading.Thread(target=self._watch_loop, daemon=True).start()
        self._event("bot v1.2 started, polling every %ds" % config.PRICE_POLL_SEC)
        send_telegram("HL sniper v1.2 %s | %s | equity=$%s trades=%d universe=%d restarts=%d" % (
            "resumed" if self.resumed else "cold start", self.mode, money(self.equity),
            len(self.trades), len(self.active_symbols), self.restarts))
        while self.running:
            try:
                mids = self.client.all_mids()
                # Prices must keep updating for anything we are HOLDING, not
                # just the active universe. A coin that rotates out while a
                # pair is open otherwise freezes at its old price and the pair
                # is reported at a PnL that has nothing to do with reality.
                watched = set(self.active_symbols) | set(self.positions)
                for pos in self.pairs.positions:
                    watched.add(pos.long_sym)
                    watched.add(pos.short_sym)
                for sym in watched:
                    if sym in mids:
                        self.last_prices[sym] = mids[sym]
                self.manage_positions()
                self.pairs.on_cycle()
                self.lab.on_tick()
                self.grid.on_tick()
                if config.SNIPER_ENABLED and self._candle_window_open():
                    self._scan_candles(self.client.meta_and_ctxs())
                self.update_status()
                time.sleep(config.PRICE_POLL_SEC)
            except Exception as exc:
                self._event("loop error: %s" % exc)
                time.sleep(15)

    def stop(self):
        self.running = False
        self.update_status()
        self._event("bot stopped")
        self._save_state(force=True)


if __name__ == "__main__":
    bot = Bot()
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.stop()
