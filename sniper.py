import time
from config import (CONVERGENCE_THRESHOLD, CONVERGENCE_THRESHOLD_STRONG,
                    W_TREND, W_VOLUME, W_ORDERBOOK, W_FUNDING)

SIGNAL_TTL_SEC = 3600


def _clamp(x):
    return max(0.0, min(1.0, x))


def generate_trend_signal(direction, adx_value, adx_min):
    if adx_value < adx_min:
        return None
    return _clamp(0.4 + min((adx_value - adx_min) / 50.0, 0.6))


def generate_volume_signal(volume_ratio, min_ratio):
    if volume_ratio < min_ratio:
        return None
    return _clamp(0.4 + 0.3 * min(volume_ratio / min_ratio, 2.0))


def generate_orderbook_signal(direction, bid_depth, ask_depth):
    total = bid_depth + ask_depth
    if total <= 0:
        return None
    if direction == "long":
        return _clamp(bid_depth / total)
    return _clamp(ask_depth / total)


def generate_funding_signal(direction, funding):
    # hourly funding on hyperliquid; enter against the crowd skew
    if direction == "long":
        return _clamp(0.5 - funding / 0.0002)
    return _clamp(0.5 + funding / 0.0002)


class ConvergenceEngine:
    def __init__(self):
        self.signals = {}

    def add_signal(self, symbol, name, direction, score):
        self.signals.setdefault(symbol, []).append({
            "name": name, "direction": direction,
            "score": score, "time": time.time()})

    def evaluate(self, symbol):
        now = time.time()
        raw = [s for s in self.signals.get(symbol, [])
               if now - s["time"] <= SIGNAL_TTL_SEC]
        self.signals[symbol] = raw
        if not raw:
            return None
        dirs = {}
        for s in raw:
            d = dirs.setdefault(s["direction"], [])
            d.append(s)
        best_dir, best_score, best_signals = None, 0.0, []
        for direction, sigs in dirs.items():
            weights = {"trend": W_TREND, "volume": W_VOLUME,
                       "orderbook": W_ORDERBOOK, "funding": W_FUNDING}
            used = [s for s in sigs if s["name"] in weights]
            if not used:
                continue
            wsum = sum(weights[s["name"]] for s in used)
            score = sum(weights[s["name"]] * s["score"] for s in used) / wsum
            if score > best_score:
                best_dir, best_score, best_signals = direction, score, used
        if best_dir is None or best_score < CONVERGENCE_THRESHOLD:
            return None
        return {
            "direction": best_dir,
            "score": best_score,
            "strength": "strong" if best_score >= CONVERGENCE_THRESHOLD_STRONG else "normal",
            "signals": best_signals}


class SniperModule:
    def __init__(self):
        self.convergence = ConvergenceEngine()
        self.total_snipes = 0
        self.rejected = 0
        self.strong_count = 0

    def execute_snipe(self, symbol, direction, size, convergence):
        self.total_snipes += 1
        if convergence["strength"] == "strong":
            self.strong_count += 1
        return {
            "status": "approved",
            "execution_type": "taker",
            "slippage_est": 0.05,
            "convergence_score": convergence["score"],
            "reason": "convergence ok"}

    def get_stats(self):
        return {
            "total_snipes": self.total_snipes,
            "strong_convergence_pct": (100.0 * self.strong_count / self.total_snipes) if self.total_snipes else 0.0}
