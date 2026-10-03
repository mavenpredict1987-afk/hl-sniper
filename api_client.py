import time
import requests
from config import HL_INFO_URL, API_TIMEOUT


class HLClient:
    def __init__(self):
        self.session = requests.Session()
        self.ctx_cache = {"time": 0, "data": None}

    def _post(self, payload):
        for attempt in range(3):
            try:
                r = self.session.post(HL_INFO_URL, json=payload, timeout=API_TIMEOUT)
                if r.status_code == 200:
                    return r.json()
            except requests.RequestException:
                pass
            time.sleep(1 + attempt)
        return None

    def all_mids(self):
        data = self._post({"type": "allMids"})
        if not data or "mids" not in data:
            return {}
        out = {}
        for coin, px in data["mids"].items():
            try:
                out[coin] = float(px)
            except (TypeError, ValueError):
                continue
        return out

    def candles(self, coin, interval, start_ms, end_ms):
        data = self._post({"type": "candleSnapshot", "req": {
            "coin": coin, "interval": interval,
            "startTime": start_ms, "endTime": end_ms}})
        if not isinstance(data, list):
            return []
        out = []
        for c in data:
            try:
                out.append({"t": int(c["t"]), "o": float(c["o"]), "h": float(c["h"]),
                            "l": float(c["l"]), "c": float(c["c"]), "v": float(c["v"])})
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def l2_book(self, coin):
        data = self._post({"type": "l2Book", "coin": coin})
        if not data or "levels" not in data or not data["levels"]:
            return None
        bids, asks = data["levels"][0], data["levels"][1]
        bid_depth = sum(float(b["sz"]) * float(b["px"]) for b in bids[:10] if b)
        ask_depth = sum(float(a["sz"]) * float(a["px"]) for a in asks[:10] if a)
        return {"bid_depth": bid_depth, "ask_depth": ask_depth,
                "bid_px": float(bids[0]["px"]) if bids else 0.0,
                "ask_px": float(asks[0]["px"]) if asks else 0.0}

    def meta_and_ctxs(self, cache_sec=60):
        now = time.time()
        if self.ctx_cache["data"] and now - self.ctx_cache["time"] < cache_sec:
            return self.ctx_cache["data"]
        data = self._post({"type": "metaAndAssetCtxs"})
        if not data or len(data) < 2:
            return self.ctx_cache["data"]
        meta, ctxs = data[0], data[1]
        out = {}
        for i, asset in enumerate(meta.get("universe", [])):
            if i >= len(ctxs):
                break
            name = asset.get("name")
            if not name:
                continue
            try:
                out[name] = {
                    "funding": float(ctxs[i].get("funding", 0)),
                    "mark": float(ctxs[i].get("markPx", 0)),
                    "max_leverage": float(asset.get("maxLeverage", 5)),
                    "sz_decimals": int(asset.get("szDecimals", 3))}
            except (TypeError, ValueError):
                continue
        self.ctx_cache = {"time": now, "data": out}
        return out
