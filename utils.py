import logging
import time

import requests
from requests import RequestException
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger("hl_sniper")


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.StreamHandler()])


# Delivery counters, so the panel can show whether alerts are actually going
# out instead of repeating a hardcoded "not configured" line from the day the
# token was still missing.
TELEGRAM_STATE = {"sent": 0, "failed": 0, "last_error": "", "last_ok_ts": 0.0}


def telegram_status():
    """What the alert channel looks like right now, without leaking the token."""
    return {
        "configured": bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID),
        "token_set": bool(TELEGRAM_TOKEN),
        "chat_set": bool(TELEGRAM_CHAT_ID),
        "sent": TELEGRAM_STATE["sent"],
        "failed": TELEGRAM_STATE["failed"],
        "last_error": TELEGRAM_STATE["last_error"],
        "last_ok_ts": TELEGRAM_STATE["last_ok_ts"],
    }


def send_telegram(text):
    """Send one alert. Returns True only when Telegram accepted the message.

    The old version ignored the HTTP response, so a revoked token or a wrong
    chat id looked exactly like success and the panel had no way to tell.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        TELEGRAM_STATE["last_error"] = "нет токена или чата"
        return False
    try:
        r = requests.post(
            "https://api.telegram.org/bot%s/sendMessage" % TELEGRAM_TOKEN,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=8)
        ok = bool(getattr(r, "ok", False))
        if not ok:
            TELEGRAM_STATE["last_error"] = "HTTP %s" % getattr(r, "status_code", "?")
    except RequestException as exc:
        ok = False
        TELEGRAM_STATE["last_error"] = type(exc).__name__
    if ok:
        TELEGRAM_STATE["sent"] += 1
        TELEGRAM_STATE["last_error"] = ""
        TELEGRAM_STATE["last_ok_ts"] = time.time()
    else:
        TELEGRAM_STATE["failed"] += 1
        logger.warning("telegram alert failed: %s", TELEGRAM_STATE["last_error"])
    return ok


def money(x):
    return "%.2f" % x


def price(x):
    """Format a price with enough significant digits to stay readable.

    money() rounds to two decimals, which printed DOGE entry, stop and target
    all as "0.08" and made the log useless for cheap coins.
    """
    if x is None:
        return "-"
    a = abs(x)
    if a >= 1000:
        return "%.2f" % x
    if a >= 1:
        return "%.4f" % x
    if a >= 0.01:
        return "%.5f" % x
    return "%.8f" % x


COVERAGE_OK = 10.0
COVERAGE_THIN = 3.0


def coverage_x(expected_pct, cost_pct):
    """How many times over the round trip pays for its own costs.

    The number his dashboard prints as "cost coverage 20.0x", and the most
    useful single ratio in the whole project. A strategy with a 0.15% edge and a
    0.20% round trip loses money no matter how good the signal is, and the
    backtest that showed +17% never says so out loud.

    Both arguments are fractions (0.0015, not 1.5). Returns None when the cost
    is unknown or zero, because "infinite coverage" would read as excellent when
    it actually means "not measured".
    """
    try:
        expected = float(expected_pct)
        cost = float(cost_pct)
    except (TypeError, ValueError):
        return None
    if cost <= 0 or expected is None:
        return None
    return expected / cost


def coverage_grade(x):
    """ok / thin / below_cost / unknown, for a display that cannot mislead."""
    if x is None:
        return "unknown"
    if x >= COVERAGE_OK:
        return "ok"
    if x >= COVERAGE_THIN:
        return "thin"
    if x >= 1.0:
        return "thin"
    return "below_cost"
