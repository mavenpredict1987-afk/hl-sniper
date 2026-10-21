"""The status endpoint is what the dashboard polls every five seconds from a
phone, so its size is a real cost. These checks pin three things: that the JSON
survives being compressed, that a client which does not ask for gzip still gets
plain JSON (a broken proxy must not lock the page out), and that the dashboard
file is actually served.

Run: PYTHONPATH=.devlibs python3 test_health_server.py
"""

import gzip
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import health_server    # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ((" :: " + str(detail)) if detail else ""))
    if not cond:
        failures.append(name)


payload = {"equity": 10110.21,
           "coins": [{"sym": "COIN%02d" % i, "price": 1.0 + i, "note": "x" * 40}
                     for i in range(40)],
           "strategies": {"total": 33}}
health_server.HealthHandler.bot_status = payload

server = ThreadingHTTPServer(("127.0.0.1", 0), health_server.HealthHandler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % port


def get(path, encoding=None):
    req = urllib.request.Request(base + path)
    if encoding:
        req.add_header("Accept-Encoding", encoding)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


code, headers, body = get("/status", "gzip, deflate")
check("gz reader gets 200", code == 200, code)
check("gz reader is told gzip", headers.get("Content-Encoding") == "gzip",
      headers.get("Content-Encoding"))
check("gzip response varies by encoding", headers.get("Vary") == "Accept-Encoding",
      headers.get("Vary"))
check("gzip body round-trips", json.loads(gzip.decompress(body).decode()) == payload)
check("gzip actually shrinks the payload", len(body) < 8000, len(body))

code, headers, body = get("/status")
check("plain reader gets 200", code == 200, code)
check("plain reader gets no encoding", headers.get("Content-Encoding") is None,
      headers.get("Content-Encoding"))
check("plain body is the same JSON", json.loads(body.decode()) == payload)

code, headers, body = get("/health")
check("health stays tiny and plain", code == 200 and json.loads(body.decode()) == {"ok": True})

code, headers, body = get("/")
check("dashboard is served", code == 200 and b"<html" in body.lower(), len(body))
check("dashboard is html", "text/html" in (headers.get("Content-Type") or ""),
      headers.get("Content-Type"))
gcode, gheaders, gbody = get("/", "gzip")
check("dashboard is compressed for browsers",
      gcode == 200 and gheaders.get("Content-Encoding") == "gzip"
      and b"<html" in gzip.decompress(gbody).lower(),
      (gcode, gheaders.get("Content-Encoding"), len(gbody)))

# the registry panel is a tabbed, collapsed list: pick a category, tap a row only
# if you want the measurement. A regression to the old wall-of-cards scroll is a
# UI regression the server test can still catch, because it ships the page.
page = open(os.path.join(HERE, "dashboard.html"), encoding="utf-8").read()
check("registry has category tabs", 'id="strat-tabs"' in page)
check("registry rows are collapsed by default", ".strat-body{display:none" in page)
check("registry rows expand on tap",
      "function toggleStrat" in page and "stOpen[id]=!stOpen[id]" in page)
check("registry tab survives the 5s refresh",
      "var stTab='strategy'" in page and "stLast=sm" in page)
check("old always-expanded card wall is gone",
      'class="sec-cap" style="margin:14px 0 8px"' not in page)
# Four short tabs fit on a phone, so they wrap instead of scrolling: a scrolling
# row got scrolled when the last tab was tapped and clipped the first one.
tabs_css = ""
if "\n.tabs{" in page:
    tabs_css = page.split("\n.tabs{", 1)[1].split("}", 1)[0]
check("registry tabs wrap instead of scrolling sideways",
      "flex-wrap:wrap" in tabs_css and "overflow-x" not in tabs_css, tabs_css)
# Gray "no" was read as "I do not own this feature"; it means "not built yet".
check("gray status is worded as not built, not as absence of a feature",
      "missing:['не сделано','off']" in page and "pchip('не сделано '" in page)

code, headers, body = get("/nope")
check("unknown path is 404", code == 404, code)

server.shutdown()

print()
if failures:
    print("FAILED:", ", ".join(failures))
    sys.exit(1)
print("ALL CHECKS PASSED")
