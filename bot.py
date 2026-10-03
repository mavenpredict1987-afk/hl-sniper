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


@dataclass
class Position:
    symbol: str
    direction: str
    entry: float
    size: float
    stop: float
    target: float
    atr_at_entry: float
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
        self.reentries = {}
        self.client = HLClient()
        self.sniper = SniperModule()
        self.status = {"status": "initialized", "mode": self.mode,
                       "started": time.time()}
        self._lock = threading.Lock()
        setup_logging()
        self._event("bot initialized mode=%s capital=%s" % (self.mode, money(self.capital)))

    def _day_stamp(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

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

    def position_size(self, entry, stop, conv_score):
        risk_pct = self.kelly_risk()
        dd = (self.peak - self.equity) / self.peak
        if dd >= 0.10:
            risk_pct *= config.DD_BREAKER_10_PCT
        elif dd >= 0.05:
            risk_pct *= config.DD_BREAKER_5_PCT
        if conv_score >= config.CONVERGENCE_THRESHOLD_STRONG:
            risk_pct *= config.SNIPER_SIZE_MULT
        stop_dist = abs(entry - stop)
        if stop_dist <= 0:
            return 0.0
        size = (self.equity * risk_pct) / stop_dist
        max_notional = self.equity * config.MAX_MARGIN_UTILIZATION * config.LEVERAGE
        if size * entry > max_notional:
            size = max_notional / entry
        if size * entry < config.MIN_NOTIONAL:
            return 0.0
        return size

    def open_position(self, symbol, direction, entry, atr_val, conv):
        if self.halted or self.day_halt:
            return False
        if len(self.positions) >= config.MAX_POSITIONS:
            return False
        if symbol in self.positions:
            return False
        stop = entry - config.ATR_STOP_MULT * atr_val if direction == "long" else entry + config.ATR_STOP_MULT * atr_val
        target = entry + config.ATR_TARGET_MULT * atr_val if direction == "long" else entry - config.ATR_TARGET_MULT * atr_val
        size = self.position_size(entry, stop, conv["score"])
        if size <= 0:
            return False
        open_risk = sum(abs(p.entry - p.stop) * p.size for p in self.positions.values())
        if open_risk + abs(entry - stop) * size > self.equity * config.MAX_TOTAL_RISK_PCT:
            return False
        fill = entry * (1 + config.SLIPPAGE) if direction == "long" else entry * (1 - config.SLIPPAGE)
        fee = size * fill * config.TAKER_FEE
        self.cash -= fee
        pos = Position(symbol, direction, fill, size, stop, target, atr_val)
        self.positions[symbol] = pos
        self.reentries[symbol] = self.reentries.get(symbol, 0)
        self._event("OPEN %s %s @ %s size=%.4f stop=%s target=%s score=%.2f %s" % (
            direction.upper(), symbol, money(fill), size, money(stop), money(target),
            conv["score"], conv["strength"]))
        send_telegram("OPEN %s %s @ %s score=%.2f" % (direction.upper(), symbol, money(fill), conv["score"]))
        return True

    def close_position(self, symbol, price, reason):
        pos = self.positions.pop(symbol, None)
        if not pos:
            return
        fill = price * (1 - config.SLIPPAGE) if pos.direction == "long" else price * (1 + config.SLIPPAGE)
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
        self.trades.append({"symbol": symbol, "direction": pos.direction, "entry": pos.entry,
                            "exit": fill, "pnl": net, "reason": reason, "time": time.time()})
        self._event("CLOSE %s @ %s pnl=%s (%s)" % (symbol, money(fill), money(net), reason))
        send_telegram("CLOSE %s pnl=%s (%s)" % (symbol, money(net), reason))

    def manage_positions(self):
        for symbol, pos in list(self.positions.items()):
            px = self.last_prices.get(symbol)
            if not px:
                continue
            if pos.direction == "long":
                if px <= pos.stop:
                    self.close_position(symbol, px, "stop")
                    continue
                if not pos.partial_done and px >= pos.entry + config.ATR_PARTIAL_MULT * pos.atr_at_entry:
                    half = pos.size / 2
                    pos.size -= half
                    pos.stop = pos.entry
                    pos.partial_done = True
                    self._event("PARTIAL %s closed 50%% @ %s" % (symbol, money(px)))
                elif pos.partial_done and px > pos.entry + 1.5 * pos.atr_at_entry:
                    pos.stop = max(pos.stop, pos.entry + 0.5 * pos.atr_at_entry)
                if px >= pos.target:
                    self.close_position(symbol, px, "target")
            else:
                if px >= pos.stop:
                    self.close_position(symbol, px, "stop")
                    continue
                if not pos.partial_done and px <= pos.entry - config.ATR_PARTIAL_MULT * pos.atr_at_entry:
                    half = pos.size / 2
                    pos.size -= half
                    pos.stop = pos.entry
                    pos.partial_done = True
                    self._event("PARTIAL %s closed 50%% @ %s" % (symbol, money(px)))
                elif pos.partial_done and px < pos.entry - 1.5 * pos.atr_at_entry:
                    pos.stop = min(pos.stop, pos.entry - 0.5 * pos.atr_at_entry)
                if px <= pos.target:
                    self.close_position(symbol, px, "target")

    def analyze_symbol(self, symbol, ctxs):
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - config.CANDLE_COUNT * 3600 * 1000
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
        e_fast = ema(closes[-60:], 20)
        e_slow = ema(closes[-120:], 50)
        direction = "long" if e_fast > e_slow else "short"
        ax = adx(highs, lows, closes)
        v_ratio = vols[-2] / sma(vols[-21:-1], 20) if sma(vols[-21:-1], 20) > 0 else 0.0
        levels = find_levels(highs[:-1], lows[:-1], vols[:-1], a)
        prev = candles[-2]
        if not breakout_confirmed(levels, direction, prev["l"], prev["h"], last_close, a):
            return
        s_trend = generate_trend_signal(direction, ax, config.ADX_MIN)
        if s_trend is not None:
            self.sniper.convergence.add_signal(symbol, "trend", direction, s_trend)
        s_vol = generate_volume_signal(v_ratio, config.VOLUME_RATIO_MIN)
        if s_vol is not None:
            self.sniper.convergence.add_signal(symbol, "volume", direction, s_vol)
        book = self.client.l2_book(symbol)
        if book:
            s_ob = generate_orderbook_signal(direction, book["bid_depth"], book["ask_depth"])
            if s_ob is not None:
                self.sniper.convergence.add_signal(symbol, "orderbook", direction, s_ob)
        ctx = ctxs.get(symbol) or {}
        s_fund = generate_funding_signal(direction, ctx.get("funding", 0.0))
        self.sniper.convergence.add_signal(symbol, "funding", direction, s_fund)
        conv = self.sniper.convergence.evaluate(symbol)
        if not conv:
            return
        if conv["direction"] != direction:
            return
        self._event("CONVERGENCE %s %s score=%.2f %s" % (symbol, direction.upper(), conv["score"], conv["strength"]))
        self.open_position(symbol, direction, last_close, a, conv)

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
            self._event("DAILY STOP hit: %.1f%% - entries blocked until tomorrow" % (config.DAILY_STOP_PCT * 100))
            send_telegram("DAILY STOP - entries blocked until tomorrow UTC")

    def update_status(self):
        self._recalc_equity()
        self.check_halts()
        self.status.update({
            "status": "running" if self.running else "stopped",
            "mode": self.mode, "equity": round(self.equity, 2),
            "cash": round(self.cash, 2), "peak": round(self.peak, 2),
            "drawdown_pct": round((self.peak - self.equity) / self.peak * 100, 2),
            "positions": [p.__dict__ for p in self.positions.values()],
            "trades_total": len(self.trades),
            "winrate": round(100.0 * self.win_count / len(self.trades), 1) if self.trades else 0.0,
            "positions_open": len(self.positions),
            "halted": self.halted, "day_halt": self.day_halt,
            "symbols": config.SYMBOLS,
            "prices": self.last_prices,
            "sniper_stats": self.sniper.get_stats(),
            "events": list(reversed(self.events[:20]))})
        HealthHandler.bot_status = self.status

    def run(self):
        self.running = True
        self.status["status"] = "running"
        threading.Thread(target=start_health_server, kwargs={"port": config.PORT}, daemon=True).start()
        self._event("bot started, polling every %ds" % config.PRICE_POLL_SEC)
        send_telegram("HL sniper bot started mode=%s capital=%s" % (self.mode, money(self.capital)))
        while self.running:
            try:
                mids = self.client.all_mids()
                for sym in config.SYMBOLS:
                    if sym in mids:
                        self.last_prices[sym] = mids[sym]
                self.manage_positions()
                ctxs = self.client.meta_and_ctxs()
                for sym in config.SYMBOLS:
                    if sym not in self.last_prices:
                        continue
                    now_ms = int(time.time() * 1000)
                    candles = self.client.candles(sym, config.TIMEFRAME,
                                                  now_ms - 2 * 3600 * 1000, now_ms)
                    if len(candles) < 2:
                        continue
                    closed_ts = candles[-2]["t"]
                    if self.last_candle_ts.get(sym) != closed_ts:
                        self.last_candle_ts[sym] = closed_ts
                        self.analyze_symbol(sym, ctxs)
                self.update_status()
                time.sleep(config.PRICE_POLL_SEC)
            except Exception as exc:
                self._event("loop error: %s" % exc)
                time.sleep(15)

    def stop(self):
        self.running = False
        self.update_status()
        self._event("bot stopped")


if __name__ == "__main__":
    bot = Bot()
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.stop()
