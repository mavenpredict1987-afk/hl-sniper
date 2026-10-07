import os
import sys
import time
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from api_client import HLClient
from indicators import ema, sma, atr, adx
from levels import find_levels, breakout_confirmed
from sniper import (SniperModule, generate_trend_signal, generate_volume_signal,
                    generate_orderbook_signal, generate_funding_signal)
from health_server import start_health_server, HealthHandler
from utils import setup_logging, send_telegram, money, logger
import state_store
import universe


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
        self.r_sum = 0.0
        self.equity_history = []
        self.client = HLClient()
        self.sniper = SniperModule()
        self.status = {"status": "initialized", "mode": self.mode, "started": time.time()}
        self.restarts = 0
        self.last_save_ts = 0.0
        self.state_source = None
        self.universe = {}
        self.active_symbols = list(config.SYMBOLS)
        self.last_hour_bucket = 0
        setup_logging()
        self._load_universe()
        self._restore_state()

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
            "last_prices": dict(self.last_prices),
            "last_candle_ts": dict(self.last_candle_ts),
            "equity_history": list(self.equity_history),
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
        self.events = list(data.get("events") or [])
        self.win_count = int(data.get("win_count", 0))
        self.loss_count = int(data.get("loss_count", 0))
        self.win_sum = float(data.get("win_sum", 0.0))
        self.loss_sum = float(data.get("loss_sum", 0.0))
        self.r_sum = float(data.get("r_sum", 0.0))
        self.cooldowns = {k: float(v) for k, v in (data.get("cooldowns") or {}).items()}
        self.last_prices = {k: float(v) for k, v in (data.get("last_prices") or {}).items()}
        self.last_candle_ts = dict(data.get("last_candle_ts") or {})
        self.equity_history = [list(x) for x in (data.get("equity_history") or [])]
        self.halted = bool(data.get("halted", False))
        self.day_halt = bool(data.get("day_halt", False))
        self.day_stamp = data.get("day_stamp") or self._day_stamp()
        self.day_start_equity = float(data.get("day_start_equity", self.equity))
        sniper = data.get("sniper") or {}
        self.sniper.execution_history = list(sniper.get("execution_history") or [])
        self.sniper.rejected = int(sniper.get("rejected", 0))
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
        line = "%s | %s" % (time.strftime("%H:%M:%S"), msg)
        self.events.append(line)
        if len(self.events) > 50:
            self.events = self.events[-50:]
        logger.info(msg)

    def _recalc_equity(self):
        unreal = 0.0
        for p in self.positions.values():
            px = self.last_prices.get(p.symbol, p.entry)
            unreal += (px - p.entry) * p.size if p.direction == "long" else (p.entry - px) * p.size
        self.equity = self.cash + unreal
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
        k = max(0.05, min(wr - (1 - wr) / (aw / al), 0.06))
        return max(config.MIN_RISK_PCT, min(k * config.KELLY_FRACTION, config.MAX_RISK_PCT))

    def cluster_of(self, symbol):
        for name, members in config.CLUSTERS.items():
            if symbol in members:
                return name
        return symbol

    def cluster_risk(self, exclude=None):
        risk = 0.0
        for sym, p in self.positions.items():
            if exclude and sym == exclude:
                continue
            risk += p.risk_dist * p.size
        return risk

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
                      funding_hourly=0.0):
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
        regime = "normal"
        size = self.position_size(entry, stop, conv["score"], regime, funding_hourly,
                                  direction, symbol)
        if size <= 0:
            return False
        open_risk = sum(p.risk_dist * p.size for p in self.positions.values())
        if open_risk + risk_dist * size > self.equity * config.MAX_TOTAL_RISK_PCT:
            return False
        if self.cluster_risk() + risk_dist * size > self.equity * config.MAX_CLUSTER_RISK_PCT and self.cluster_of(symbol) in config.CLUSTERS:
            return False
        slip = max(book_slippage_pct / 100.0, config.SLIPPAGE / 4)
        fill = entry * (1 + slip) if direction == "long" else entry * (1 - slip)
        fee = size * fill * config.TAKER_FEE
        self.cash -= fee
        pos = Position(symbol, direction, fill, size, stop, target, risk_dist, atr_val)
        pos.initial_size = size
        pos.initial_risk_usd = risk_dist * size
        self.positions[symbol] = pos
        self._event("OPEN %s %s @ %s size=%.4f stop=%s target=%s score=%.2f %s slip=%.3f%%" % (
            direction.upper(), symbol, money(fill), size, money(stop), money(target),
            conv["score"], conv["strength"], book_slippage_pct))
        send_telegram("OPEN %s %s @ %s score=%.2f" % (direction.upper(), symbol, money(fill), conv["score"]))
        self._save_state(force=True)
        return True

    def close_position(self, symbol, price, reason):
        pos = self.positions.pop(symbol, None)
        if not pos:
            return
        slip = config.SLIPPAGE / 4
        fill = price * (1 - slip) if pos.direction == "long" else price * (1 + slip)
        pnl = (fill - pos.entry) * pos.size if pos.direction == "long" else (pos.entry - fill) * pos.size
        fee = pos.size * fill * config.TAKER_FEE
        net = pnl - fee
        self.cash += net
        ret_pct = net / (pos.size * pos.entry)
        if net > 0:
            self.win_count += 1
            self.win_sum += ret_pct
        else:
            self.loss_count += 1
            self.loss_sum += abs(ret_pct)
        risk_usd = pos.initial_risk_usd or (pos.risk_dist * pos.size)
        r_mult = net / risk_usd if risk_usd > 0 else 0.0
        self.r_sum += r_mult
        self.trades.append({"symbol": symbol, "direction": pos.direction, "entry": pos.entry,
                            "exit": fill, "pnl": net, "r": round(r_mult, 2),
                            "reason": reason, "time": time.time()})
        self._event("CLOSE %s @ %s pnl=%s (%s)" % (symbol, money(fill), money(net), reason))
        send_telegram("CLOSE %s pnl=%s (%s)" % (symbol, money(net), reason))
        self._save_state(force=True)

    def book_partial(self, pos, symbol, px):
        closed = pos.size * config.PARTIAL_RATIO
        fill = px * (1 - config.SLIPPAGE / 4) if pos.direction == "long" else px * (1 + config.SLIPPAGE / 4)
        pnl = (fill - pos.entry) * closed if pos.direction == "long" else (pos.entry - fill) * closed
        fee = closed * fill * config.TAKER_FEE
        self.cash += pnl - fee
        pos.size -= closed
        self._event("PARTIAL %s closed %.0f%% @ %s booked pnl=%s" % (
            symbol, config.PARTIAL_RATIO * 100, money(fill), money(pnl - fee)))
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
                if pos.partial_done and px > pos.entry + (config.PARTIAL_AT_R + 1.0) * r:
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
                if pos.partial_done and px < pos.entry - (config.PARTIAL_AT_R + 1.0) * r:
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

    def analyze_symbol(self, symbol, ctxs):
        info = self.universe.get(symbol)
        if info is not None and not info.get("tradable", True):
            return
        if time.time() < self.cooldowns.get(symbol, 0):
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
        highs = [c["h"] for c in candles]
        lows = [c["l"] for c in candles]
        closes = [c["c"] for c in candles]
        vols = [c["v"] for c in candles]
        last_close = closes[-1]
        a = atr(highs, lows, closes)
        if a <= 0:
            return
        atrs = []
        for i in range(30, len(candles)):
            atrs.append(atr(highs[:i], lows[:i], closes[:i]))
        atr_slow = sma(atrs, 50)
        regime = self.regime_of(a, atr_slow)
        e_fast = ema(closes[-60:], 20)
        e_slow = ema(closes[-120:], 50)
        direction = "long" if e_fast > e_slow else "short"
        ax = adx(highs, lows, closes)
        base_vol = sma(vols[-21:-1], 20)
        v_ratio = vols[-2] / base_vol if base_vol > 0 else 0.0
        levels = find_levels(highs[:-1], lows[:-1], vols[:-1], a)
        prev = candles[-2]
        if not breakout_confirmed(levels, direction, prev["l"], prev["h"], last_close, a):
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
        self.open_position(symbol, direction, last_close, a, conv, slip_pct, funding_hourly)

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
            },
            "events": list(reversed(self.events[-20:]))})
        HealthHandler.bot_status = self.status
        self._save_state()

    def run(self):
        self.running = True
        self.status["status"] = "running"
        threading.Thread(target=start_health_server, kwargs={"port": config.PORT}, daemon=True).start()
        threading.Thread(target=self._universe_loop, daemon=True).start()
        self._event("bot v1.1 started, polling every %ds" % config.PRICE_POLL_SEC)
        send_telegram("HL sniper bot v1.1 started mode=%s capital=%s" % (self.mode, money(self.capital)))
        while self.running:
            try:
                mids = self.client.all_mids()
                for sym in self.active_symbols:
                    if sym in mids:
                        self.last_prices[sym] = mids[sym]
                self.manage_positions()
                if self._candle_window_open():
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
