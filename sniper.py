import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import config
from config import (CONVERGENCE_THRESHOLD, CONVERGENCE_THRESHOLD_STRONG,
                    MIN_SIGNALS, SIGNAL_TTL_SEC, W_TREND, W_VOLUME,
                    W_ORDERBOOK, W_FUNDING)

logger = logging.getLogger("sniper")


class SignalType:
    TREND = "trend"
    VOLUME = "volume"
    ORDERBOOK = "orderbook"
    FUNDING = "funding"


@dataclass
class Signal:
    type: str
    direction: str
    weight: float
    confidence: float
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


@dataclass
class OrderBookSnapshot:
    bids: List[Tuple[float, float]]
    asks: List[Tuple[float, float]]
    timestamp: float = field(default_factory=time.time)

    def mid(self):
        if not self.bids or not self.asks:
            return 0.0
        return (self.bids[0][0] + self.asks[0][0]) / 2

    def spread_pct(self):
        mid = self.mid()
        if not self.bids or not self.asks or mid <= 0:
            return 999.0
        return (self.asks[0][0] - self.bids[0][0]) / mid * 100

    def estimate_slippage_pct(self, size: float, direction: str):
        """Walk the book with our size. Returns slippage vs mid, percent."""
        book = self.asks if direction == "long" else self.bids
        mid = self.mid()
        if not book or mid <= 0:
            return 999.0
        remaining, cost, filled = size, 0.0, 0.0
        for price, avail in book:
            if remaining <= 0:
                break
            fill = min(remaining, avail)
            cost += fill * price
            filled += fill
            remaining -= fill
        if remaining > 0 or filled <= 0:
            return 999.0
        avg = cost / filled
        slip = (avg - mid) / mid * 100 if direction == "long" else (mid - avg) / mid * 100
        return max(slip, 0.0)

    def depth(self, levels: int = 10):
        bd = sum(s for _, s in self.bids[:levels])
        ad = sum(s for _, s in self.asks[:levels])
        ratio = bd / ad if ad > 0 else 999.0
        return {"bid": bd, "ask": ad, "ratio": ratio}


class ConvergenceEngine:
    WEIGHTS = {SignalType.TREND: W_TREND, SignalType.VOLUME: W_VOLUME,
               SignalType.ORDERBOOK: W_ORDERBOOK, SignalType.FUNDING: W_FUNDING}

    def __init__(self):
        self.signals: Dict[str, List[Signal]] = {}

    def add_signal(self, symbol: str, signal: Signal):
        if signal is None:
            return
        now = time.time()
        kept = [s for s in self.signals.get(symbol, []) if now - s.timestamp <= SIGNAL_TTL_SEC]
        kept = [s for s in kept if s.type != signal.type]
        kept.append(signal)
        self.signals[symbol] = kept

    def evaluate(self, symbol: str):
        now = time.time()
        signals = [s for s in self.signals.get(symbol, []) if now - s.timestamp <= SIGNAL_TTL_SEC]
        if len(signals) < MIN_SIGNALS:
            return None
        result = None
        for direction in ("long", "short"):
            ds = [s for s in signals if s.direction == direction]
            if len(ds) < MIN_SIGNALS:
                continue
            wsum = sum(self.WEIGHTS[s.type] for s in ds)
            if wsum <= 0:
                continue
            raw = sum(self.WEIGHTS[s.type] * s.weight * s.confidence for s in ds)
            score = min(raw / wsum, 1.0)
            if score >= CONVERGENCE_THRESHOLD and (result is None or score > result["score"]):
                result = {"symbol": symbol, "direction": direction, "score": score,
                          "strength": "strong" if score >= CONVERGENCE_THRESHOLD_STRONG else "normal",
                          "size_multiplier": config.SNIPER_SIZE_MULT if score >= CONVERGENCE_THRESHOLD_STRONG else 1.0,
                          "signals": len(ds)}
        return result


def generate_trend_signal(direction, adx_value, adx_min):
    if adx_value < adx_min:
        return None
    return Signal(SignalType.TREND, direction,
                  weight=0.9 if adx_value > 30 else 0.6,
                  confidence=min(adx_value / 50.0, 1.0),
                  metadata={"adx": round(adx_value, 1)})


def generate_volume_signal(direction, volume_ratio, min_ratio):
    if volume_ratio < min_ratio:
        return None
    return Signal(SignalType.VOLUME, direction,
                  weight=0.8 if volume_ratio > 2.0 else 0.5,
                  confidence=min(volume_ratio / 3.0, 1.0),
                  metadata={"volume_ratio": round(volume_ratio, 2)})


def generate_orderbook_signal(direction, depth_ratio):
    """Strong when book confirms direction; weak same-direction signal
    when book disagrees - it dilutes the score instead of vetoing."""
    if direction == "long" and depth_ratio > 1.2:
        return Signal(SignalType.ORDERBOOK, direction, 0.8, min(depth_ratio / 3.0, 1.0),
                      metadata={"depth_ratio": round(depth_ratio, 2)})
    if direction == "short" and depth_ratio < 0.8:
        return Signal(SignalType.ORDERBOOK, direction, 0.8, min((1 / depth_ratio) / 3.0, 1.0),
                      metadata={"depth_ratio": round(depth_ratio, 2)})
    return Signal(SignalType.ORDERBOOK, direction, 0.3, 0.3,
                  metadata={"depth_ratio": round(depth_ratio, 2)})


def generate_funding_signal(direction, funding_hourly):
    """Hyperliquid funding is hourly (typ +/-0.0001). Enter against skew."""
    if direction == "long" and funding_hourly < -0.0001:
        return Signal(SignalType.FUNDING, direction, 0.7, 0.8, metadata={"funding": funding_hourly})
    if direction == "short" and funding_hourly > 0.0001:
        return Signal(SignalType.FUNDING, direction, 0.7, 0.8, metadata={"funding": funding_hourly})
    return Signal(SignalType.FUNDING, direction, 0.4, 0.5, metadata={"funding": funding_hourly})


class SniperModule:
    def __init__(self):
        self.convergence = ConvergenceEngine()
        self.order_books: Dict[str, OrderBookSnapshot] = {}
        self.execution_history: List[dict] = []
        self.rejected = 0

    def update_order_book(self, symbol, bids, asks):
        self.order_books[symbol] = OrderBookSnapshot(bids, asks)

    def analyze_entry(self, symbol, direction, size):
        ob = self.order_books.get(symbol)
        if not ob:
            return {"can_enter": False, "reason": "no order book"}
        mid = ob.mid()
        spread = ob.spread_pct()
        slippage = ob.estimate_slippage_pct(size, direction)
        d = ob.depth()
        side_usd = (d["bid"] if direction == "short" else d["ask"]) * mid
        checks = {
            "spread_ok": spread < config.SPREAD_MAX_PCT,
            "slippage_ok": slippage < config.SLIPPAGE_MAX_PCT,
            "liquidity_ok": side_usd > config.LIQUIDITY_MIN_USD,
            "book_fresh": (time.time() - ob.timestamp) < config.BOOK_MAX_AGE_SEC,
        }
        if not all(checks.values()):
            self.rejected += 1
            failed = ",".join(k for k, v in checks.items() if not v)
            return {"can_enter": False, "reason": "checks failed: %s" % failed,
                    "checks": checks, "slippage_pct": slippage, "spread_pct": spread}
        if slippage < config.SLIPPAGE_LIMIT_EXEC:
            execution = "limit"
        elif slippage < config.SLIPPAGE_MARKET_EXEC:
            execution = "taker"
        else:
            self.rejected += 1
            return {"can_enter": False, "reason": "slippage too high %.3f" % slippage,
                    "slippage_pct": slippage, "spread_pct": spread}
        return {"can_enter": True, "execution": execution,
                "slippage_pct": slippage, "spread_pct": spread,
                "price": ob.asks[0][0] if direction == "long" else ob.bids[0][0]}

    def execute_snipe(self, symbol, direction, size, convergence):
        if convergence["score"] < CONVERGENCE_THRESHOLD:
            self.rejected += 1
            return {"status": "rejected", "reason": "no convergence"}
        entry = self.analyze_entry(symbol, direction, size)
        if not entry["can_enter"]:
            return {"status": "rejected", "reason": entry["reason"], "entry": entry}
        final_size = size * convergence.get("size_multiplier", 1.0)
        decision = {"time": time.time(), "symbol": symbol, "direction": direction,
                    "size": final_size, "score": convergence["score"],
                    "strength": convergence["strength"],
                    "execution": entry["execution"], "slippage_pct": entry["slippage_pct"]}
        self.execution_history.append(decision)
        return {"status": "executed", "decision": decision}

    def get_stats(self):
        if not self.execution_history:
            return {"total_snipes": 0, "rejected": self.rejected}
        total = len(self.execution_history)
        strong = sum(1 for e in self.execution_history if e["strength"] == "strong")
        avg_slip = sum(e["slippage_pct"] for e in self.execution_history) / total
        return {"total_snipes": total, "rejected": self.rejected,
                "strong_pct": round(100.0 * strong / total, 1),
                "avg_slippage_pct": round(avg_slip, 3)}
