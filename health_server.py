import gzip
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The dashboard polls /status every five seconds, and the payload has grown to
# ~90 KB (equity curve, trades, the per-coin table). The page is viewed on a
# phone, so it is compressed: the same JSON is ~10 KB gzipped. Below this size
# the framing overhead is not worth it.
GZIP_MIN_BYTES = 1024


class HealthHandler(BaseHTTPRequestHandler):
    bot_status = {}

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        accepts = "gzip" in (self.headers.get("Accept-Encoding") or "")
        if accepts and len(data) >= GZIP_MIN_BYTES:
            data = gzip.compress(data, compresslevel=5)
            encoding = "gzip"
        else:
            encoding = None
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if encoding:
            self.send_header("Content-Encoding", encoding)
            self.send_header("Vary", "Accept-Encoding")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, json.dumps({"ok": True}))
        elif self.path == "/status":
            self._send(200, json.dumps(HealthHandler.bot_status))
        elif self.path == "/export":
            data = json.dumps(HealthHandler.bot_status, indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition",
                             'attachment; filename="hl-sniper-state.json"')
            self.end_headers()
            self.wfile.write(data)
        elif self.path in ("/", "/index.html"):
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
            try:
                with open(path, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except OSError:
                self._send(404, "dashboard not found", "text/plain")
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def log_message(self, fmt, *args):
        pass


def start_health_server(port):
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
