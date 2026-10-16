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
# Exits are checked once per poll, so this IS the exit latency. The reference
# bot reacts to every WebSocket book update; a shorter poll is the cheap
# improvement until that layer exists. allMids is a light endpoint.
PRICE_POLL_SEC = float(os.getenv("PRICE_POLL_SEC", "2"))
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

# --- Guards and exit management taken from the reference bot ---------------
# Source values and rationale: research/REFERENCE_FRIEND_BOT.md

# Shorts lost money in both bots: theirs 15 days at WR 0-22%, ours 19 trades at
# WR 32% for -$84 against -$22 on longs. Kept switchable so the backtest can
# compare both settings before the default is changed.
SHORTS_ENABLED = os.getenv("SHORTS_ENABLED", "1").strip().lower() not in ("0", "false", "no")

# Continuous ATR trailing once a partial has been booked. 0 keeps the old
# single ratchet to +0.5R, which let winners hand everything back.
ATR_TRAIL_MULT = float(os.getenv("ATR_TRAIL_MULT", "0"))

# What to risk when the measured Kelly edge is negative. 1.0 reproduces the old
# behaviour (risk MIN_RISK_PCT anyway); 0 stops taking new positions.
KELLY_NEGATIVE_MULT = float(os.getenv("KELLY_NEGATIVE_MULT", "1.0"))

# Pause new entries after N consecutive losses (0 disables).
CONSEC_LOSS_PAUSE = int(os.getenv("CONSEC_LOSS_PAUSE", "0"))
CONSEC_LOSS_PAUSE_SEC = float(os.getenv("CONSEC_LOSS_PAUSE_SEC", str(4 * 3600)))

# Drop a coin's signals when its position closes, so a stale set cannot fire an
# instant re-entry.
RESET_SIGNALS_ON_CLOSE = os.getenv("RESET_SIGNALS_ON_CLOSE", "1").strip().lower() not in (
    "0", "false", "no")

# Auto-blacklist, as the reference bot runs it: a coin that stops out twice
# inside thirty days is not unlucky, it is mistimed, so it is benched for a
# fortnight. This replaces benching coins by hand after the fact.
AUTO_BLACKLIST_STOPS = int(os.getenv("AUTO_BLACKLIST_STOPS", "2"))
AUTO_BLACKLIST_WINDOW_DAYS = float(os.getenv("AUTO_BLACKLIST_WINDOW_DAYS", "30"))
AUTO_BLACKLIST_PAUSE_DAYS = float(os.getenv("AUTO_BLACKLIST_PAUSE_DAYS", "14"))

# Raise the entry threshold when volatility is high, instead of only shrinking
# the position. Deliberately mild: the normalised score ceiling is about 0.80,
# so 0.55 * 1.5 = 0.83 blocked EVERY entry in a high-vol regime - observed live
# as "skip ETH: score 0.59 below regime bar 0.83 (high)". MAX_ENTRY_THRESHOLD
# is a hard cap that keeps any future value below the achievable ceiling.
REGIME_FILTER_MULT = {"low": 1.0, "normal": 1.0, "high": 1.1}
MAX_ENTRY_THRESHOLD = float(os.getenv("MAX_ENTRY_THRESHOLD", "0.72"))

# Symbols benched by live statistics rather than by intuition.
BLACKLIST = {s.strip() for s in os.getenv("BLACKLIST", "").split(",") if s.strip()}
WHITELIST = {s.strip() for s in os.getenv("WHITELIST", "").split(",") if s.strip()}
WHITELIST_SIZE_BONUS = float(os.getenv("WHITELIST_SIZE_BONUS", "1.0"))
WHITELIST_SCORE_BONUS = float(os.getenv("WHITELIST_SCORE_BONUS", "0.0"))

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
# The reference bot uses 2.5%; ours is far looser. Env-configurable so the
# difference can be measured before the default moves.
DAILY_STOP_PCT = float(os.getenv("DAILY_STOP_PCT", "0.10"))

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
# The reference bot cut this from 50% to 30% deliberately: "fix the R:R
# asymmetry - trim 30%, let the winner run".
PARTIAL_RATIO = float(os.getenv("PARTIAL_RATIO", "0.5"))

REGIME_LOW = 0.8
REGIME_HIGH = 1.25
REGIME_SIZE_MULT = {"low": 1.2, "normal": 1.0, "high": 0.5}

SPREAD_MAX_PCT = 0.10
SLIPPAGE_MAX_PCT = 0.50
# The REST l2Book returns a fixed 20 price levels whatever their spacing, so on
# a fine-tick coin like HYPE the top ten levels hold only a few hundred dollars
# while the real book is deep. A flat $5,000 floor therefore rejected HYPE, LIT,
# UNI and ICP as "illiquid" while BTC sat at $800k. The floor is now small and
# the real test is relative: our order may not be more than LIQUIDITY_MAX_SHARE
# of the resting notional on the side we would trade.
LIQUIDITY_MIN_USD = float(os.getenv("LIQUIDITY_MIN_USD", "1000"))
LIQUIDITY_MAX_SHARE = float(os.getenv("LIQUIDITY_MAX_SHARE", "0.25"))
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

# Correlation clusters. Without them the bot opened three shorts (DOGE, ZEC,
# UNI) inside twelve seconds and all three stopped out together for -$37.77.
# Every coin the live universe can hold now belongs to a group.
CLUSTERS = {
    "majors": ["BTC", "ETH", "SOL"],
    "l1": ["BNB", "ADA", "AVAX", "NEAR", "SUI", "XRP", "LTC", "ASTER"],
    "defi": ["UNI", "AAVE", "CRV", "AERO", "LINK", "ONDO"],
    "ai": ["TAO", "RNDR", "FET", "ARKM"],
    "l2": ["ARB", "OP", "STRK", "MANTA"],
    "memes": ["DOGE", "TRUMP", "HYPE"],
    "privacy": ["XMR", "ZEC"],
    "metals": ["PAXG"],
}
MAX_CLUSTER_RISK_PCT = 0.03
# Crypto beta dominates every other correlation, so cap one direction outright.
# The three stopped shorts were 3.75% of equity in one direction, under the 6%
# total cap. This is the limit that would have stopped the second and third.
MAX_DIRECTION_RISK_PCT = float(os.getenv("MAX_DIRECTION_RISK_PCT", "0.02"))

W_TREND = 0.35
W_VOLUME = 0.25
W_ORDERBOOK = 0.25
W_FUNDING = 0.15

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "")
MIN_NOTIONAL = 10.0
