"""Self-check for the Telegram alert channel and its panel row.

Run: PYTHONPATH=.devlibs python3 test_alerts.py

Offline: the HTTP layer is replaced by a fake, nothing is sent anywhere.

Why this file exists: the registry used to say "Telegram not configured" from a
hardcoded string even after the token was set, and the sender ignored the HTTP
response, so a revoked token looked exactly like success. These checks pin the
behaviour that made the panel lie.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import utils  # noqa: E402

REAL_REQUESTS = utils.requests

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ((" :: " + str(detail)) if detail else ""))
    if not cond:
        failures.append(name)


class FakeResponse(object):
    def __init__(self, ok, status_code):
        self.ok = ok
        self.status_code = status_code


class FakeRequests(object):
    """Stands in for utils.requests: records calls, returns a scripted reply."""

    def __init__(self, ok=True, status_code=200, raises=None):
        self.ok = ok
        self.status_code = status_code
        self.raises = raises
        self.calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self.raises:
            raise self.raises
        return FakeResponse(self.ok, self.status_code)


saved = (utils.TELEGRAM_TOKEN, utils.TELEGRAM_CHAT_ID, utils.requests,
         dict(utils.TELEGRAM_STATE))


def restore():
    utils.TELEGRAM_TOKEN, utils.TELEGRAM_CHAT_ID, utils.requests, state = saved
    utils.TELEGRAM_STATE.update(state)
    utils.TELEGRAM_STATE.update({"last_error": "", "sent": 0, "failed": 0,
                                 "last_ok_ts": 0.0})


try:
    # ---- not configured: nothing is sent, the reason is recorded
    utils.TELEGRAM_TOKEN, utils.TELEGRAM_CHAT_ID = "", ""
    utils.TELEGRAM_STATE.update({"sent": 0, "failed": 0, "last_error": ""})
    fake = FakeRequests()
    utils.requests = fake
    check("no token means no send", utils.send_telegram("x") is False)
    check("no token means no HTTP call", fake.calls == [], fake.calls)
    check("missing configuration is reported",
          "токен" in (utils.telegram_status()["last_error"] or ""),
          utils.telegram_status()["last_error"])

    # ---- configured: the reply decides, not the exception handler
    utils.TELEGRAM_TOKEN, utils.TELEGRAM_CHAT_ID = "123:abc", "42"
    utils.TELEGRAM_STATE.update({"sent": 0, "failed": 0, "last_error": ""})
    fake = FakeRequests(ok=True, status_code=200)
    utils.requests = fake
    check("accepted message returns True", utils.send_telegram("hello") is True)
    st = utils.telegram_status()
    check("accepted message is counted", st["sent"] == 1, st["sent"])
    check("accepted message clears the old error", st["last_error"] == "", st["last_error"])
    check("accepted message stamps the delivery time", st["last_ok_ts"] > 0, st["last_ok_ts"])
    check("message goes to the configured chat",
          (fake.calls[0]["json"] or {}).get("chat_id") == "42", fake.calls[0]["json"])
    check("the token never appears in the status dict",
          "123:abc" not in str(utils.telegram_status()), utils.telegram_status())

    # ---- rejected message: HTTP error surfaces instead of looking fine
    utils.TELEGRAM_STATE.update({"sent": 0, "failed": 0, "last_error": ""})
    utils.requests = FakeRequests(ok=False, status_code=403)
    check("rejected message returns False", utils.send_telegram("x") is False)
    st = utils.telegram_status()
    check("rejected message is counted as a failure", st["failed"] == 1, st["failed"])
    check("rejected message records the HTTP code",
          st["last_error"] == "HTTP 403", st["last_error"])
    check("rejected message does not count as delivered", st["sent"] == 0, st["sent"])

    # ---- network error: counted, not swallowed silently
    utils.TELEGRAM_STATE.update({"sent": 0, "failed": 0, "last_error": ""})
    utils.requests = FakeRequests(raises=REAL_REQUESTS.RequestException("boom"))
    check("network error returns False", utils.send_telegram("x") is False)
    st = utils.telegram_status()
    check("network error is counted as a failure", st["failed"] == 1, st["failed"])
    check("network error names the exception",
          st["last_error"] == "RequestException", st["last_error"])
finally:
    restore()

print()
if failures:
    print("FAILED:", ", ".join(failures))
    sys.exit(1)
print("ALL CHECKS PASSED")
