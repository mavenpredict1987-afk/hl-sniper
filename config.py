import os

PORT = int(os.getenv("PORT", "8080"))
TRADING_MODE = os.getenv("TRADING_MODE", "paper")
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "10000"))
SYMBOLS = [s.strip() for s in os.getenv("SYMBOLS", "BTC,ETH,SOL").split(",") if s.strip()]

# Persistence. STATE_DIR should point at a mounted volume (for example /data)
# so paper statistics survive a redeploy, not just a process restart.
STATE_DIR = os.getenv("STATE_DIR", "")
STATE_SAVE_SEC = int(os.getenv("STATE_SAVE_SEC", "30"))

HL_INFO_URL = os.getenv("HL_INFO_URL", "https://api.hyperliquid.xyz/info")
API_TIMEOUT = 10
PRICE_POLL_SEC = 5
CTX_CACHE_SEC = 60
CANDLE_COUNT = 300
TIMEFRAME = "1h"

TAKER_FEE = 0.00045
MAKER_FEE = 0.00015
SLIPPAGE = 0.0005

# Cost model: every trade must pay fees, slippage and funding out of its own
# risk budget, otherwise real risk per trade silently exceeds risk_pct.
# A snipe with a target that cannot clear these costs several times over is
# not worth taking regardless of how good the signal looks.
ENTRY_FEE_RATE = float(os.getenv("ENTRY_FEE_RATE", str(TAKER_FEE)))
ASSUMED_HOLDING_HOURS = float(os.getenv("ASSUMED_HOLDING_HOURS", "12"))
MIN_RR_AFTER_COSTS = float(os.getenv("MIN_RR_AFTER_COSTS", "1.5"))

# Universe scanner. Trading three majors is the single biggest brake on this
# bot: 50 hours produced three signals. The scanner ranks every Hyperliquid
# perp by noise and liquidity and keeps the tradable ones. SYMBOLS above stays
# as the fallback seed when no scan is available yet.
UNIVERSE_ENABLED = os.getenv("UNIVERSE_ENABLED", "1").strip().lower() not in ("0", "false", "no")
UNIVERSE_SIZE = int(os.getenv("UNIVERSE_SIZE", "25"))
UNIVERSE_REFRESH_HOURS = float(os.getenv("UNIVERSE_REFRESH_HOURS", "6"))
UNIVERSE_BARS = int(os.getenv("UNIVERSE_BARS", "168"))
UNIVERSE_DELAY_SEC = float(os.getenv("UNIVERSE_DELAY_SEC", "3.0"))
UNIVERSE_MIN_VOLUME_USD = float(os.getenv("UNIVERSE_MIN_VOLUME_USD", "2000000"))
UNIVERSE_MAX_SPREAD_BPS = float(os.getenv("UNIVERSE_MAX_SPREAD_BPS", "40"))

# HIP-3 / builder-deployed perps (namespaced with a colon) and 1000x unit
# tickers carry a different fee schedule than the 0.015/0.045% this cost model
# assumes, so they are skipped rather than mispriced.
UNIVERSE_EXCLUDE = [s.strip() for s in os.getenv(
    "UNIVERSE_EXCLUDE", "kPEPE,kFLOKI,kNEIRO,2Z").split(",") if s.strip()]

# Noise score weights (0..100, higher = noisier), calibrated on the
# Hyperliquid perp universe. Metric definitions live in universe.py.
NOISE_SCORE_WEIGHTS = {
    "atr": 25.0, "atr_ref": 4.0,
    "wick": 20.0, "wick_ref": 6.0,
    "er": 20.0,
    "impact": 20.0, "impact_ref": 40.0,
    "thin_vlm": 15.0, "vlm_floor": 500000.0,
}

# Class decisions. risk_pct_cap is a ceiling applied on top of Kelly, so a
# noisy coin can never take more risk than a calm one. stop_atr_mult is
# recorded here for the backtest that will decide it; this build still uses
# the global ATR_STOP_MULT so the change stays measurable in isolation.
NOISE_CLASSES = {
    "calm":   {"max_score": 25, "risk_pct_cap": 1.5, "stop_atr_mult": 2.0,
               "min_rr": 2.0, "max_leverage": 10, "holding_hours": 48,
               "strategy": "trend_follow", "tradable": True},
    "normal": {"max_score": 45, "risk_pct_cap": 1.0, "stop_atr_mult": 2.5,
               "min_rr": 2.5, "max_leverage": 5, "holding_hours": 36,
               "strategy": "trend_or_breakout", "tradable": True},
    "noisy":  {"max_score": 70, "risk_pct_cap": 0.5, "stop_atr_mult": 3.5,
               "min_rr": 3.0, "max_leverage": 3, "holding_hours": 24,
               "strategy": "mean_reversion_wide", "tradable": True},
    "toxic":  {"max_score": 999, "risk_pct_cap": 0.25, "stop_atr_mult": 4.0,
               "min_rr": 4.0, "max_leverage": 1, "holding_hours": 12,
               "strategy": "no_trade", "tradable": False},
}

BASE_RISK_PER_TRADE = 0.01
MIN_RISK_PCT = 0.005
MAX_RISK_PCT = 0.02
KELLY_FRACTION = 0.25
MAX_POSITIONS = 4
MAX_TOTAL_RISK_PCT = 0.06
MAX_MARGIN_UTILIZATION = 0.5
LEVERAGE = 5
DD_BREAKER_5_PCT = 0.5
DD_BREAKER_10_PCT = 0.25
HALT_DD_PCT = 0.15
DAILY_STOP_PCT = 0.10

# Thresholds matched to the shipped spec of a bot that actually trades
# (0.55 / 0.70). The previous 0.60 / 0.75 were stricter than that bot, which
# is a large part of why this one almost never fires.
CONVERGENCE_THRESHOLD = float(os.getenv("CONVERGENCE_THRESHOLD", "0.55"))
CONVERGENCE_THRESHOLD_STRONG = float(os.getenv("CONVERGENCE_THRESHOLD_STRONG", "0.70"))
MIN_SIGNALS = 2
SNIPER_SIZE_MULT = 1.3
SIGNAL_TTL_SEC = 3600

ADX_MIN = 25.0
VOLUME_RATIO_MIN = 1.5
MIN_VOLUME_USD = 1000000.0
PREMIUM_MAX_PCT = 0.015

ATR_STOP_MULT = 1.35
ATR_TARGET_MULT = 4.8
PARTIAL_AT_R = 2.0
PARTIAL_RATIO = 0.5

REGIME_LOW = 0.8
REGIME_HIGH = 1.25
REGIME_SIZE_MULT = {"low": 1.2, "normal": 1.0, "high": 0.5}

SPREAD_MAX_PCT = 0.10
SLIPPAGE_MAX_PCT = 0.50
LIQUIDITY_MIN_USD = 5000.0
# A book older than a couple of seconds is stale: the live bot this spec came
# from refuses to snipe on anything older than 2s. 120s was ~60x too loose.
BOOK_MAX_AGE_SEC = int(os.getenv("BOOK_MAX_AGE_SEC", "3"))
SLIPPAGE_LIMIT_EXEC = 0.15
SLIPPAGE_MARKET_EXEC = 0.30

MAX_REENTRIES = 2
LEVEL_TOUCH_MIN = 2
LEVEL_CLUSTER_ATR = 0.5
BREAKOUT_CONFIRM_ATR = 0.1

TIME_STOP_HOURS = 36.0
COOLDOWN_AFTER_STOP_H = 6.0
MIN_TARGET_PCT = 0.003

CLUSTERS = {"majors": ["BTC", "ETH", "SOL"]}
MAX_CLUSTER_RISK_PCT = 0.03

W_TREND = 0.35
W_VOLUME = 0.25
W_ORDERBOOK = 0.25
W_FUNDING = 0.15

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "")
MIN_NOTIONAL = 10.0
