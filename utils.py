import logging
import requests
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger("hl_sniper")


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.StreamHandler()])


def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            "https://api.telegram.org/bot%s/sendMessage" % TELEGRAM_TOKEN,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=8)
    except requests.RequestException:
        logger.warning("telegram alert failed")


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
