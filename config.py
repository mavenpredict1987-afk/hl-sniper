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
