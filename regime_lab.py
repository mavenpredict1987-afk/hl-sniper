"""Paper-only regime laboratory.

Pure instrumentation, in the spirit of the reference bot's paper_lab, and the
only source of new evidence we have left: hourly history on this venue stops at
about 210 days (RESEARCH_LOG.md #13), so no out-of-sample window exists and every
further backtest would reuse the same data.

It opens no orders, holds no money, and cannot change cash, equity or a real
position. State lives in its own paper_lab.json so state.json keeps its shape.

Three arms run at once on the same hour:
  cell      - MA100 only when BTC is in trend+turbulence, capped at MAX_OPEN
  control   - the same MA100 signal with no regime filter, same cap
  cell_all  - the same cell signal with no cap, for signal-level statistics

The control is the point: a cell that does not beat no-filter is decoration,
which is exactly what our backtest could not settle (t 1.50 on the best of four
cells, difference to control t 1.33).
"""

import json
import math
import os
import statistics
import time

import config
import state_store

ER_WIN = 20
ER_TREND = 0.25
ATR_WIN = 14
PCT_WIN = 720            # 30 days of hours for the ATR percentile
PCT_CUT = 60.0
EMA_LEN = 100
EMA_FEED = 110
STOP_ATR = 2.0
TARGET_ATR = 3.0
HOLD_H = 72
MAX_OPEN = 2
COST_PCT = 0.19          # round trip, percent, our standard
CELL_TREND = "trend"
CELL_VOL = "turb"
ARMS = ("cell", "control", "cell_all")
STATE_FILE = "paper_lab.json"
REGIME_HISTORY = 300
CLOSED_CAP = 400


# --------------------------------------------------------------- pure helpers

def ema_last(closes, length=EMA_LEN, feed=EMA_FEED):
    """Re-seeded from the last `feed` closes, the way the reference bot does it."""
    if len(closes) < feed:
        return None
    k = 2.0 / (length + 1)
    e = closes[-feed]
    for v in closes[-feed + 1:]:
        e = v * k + e * (1.0 - k)
    return e


def _true_ranges(bars):
    trs = [None] * len(bars)
    for i in range(1, len(bars)):
        high, low, prev = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs[i] = max(high - low, abs(high - prev), abs(low - prev))
    return trs


def atr_series(bars, win=ATR_WIN):
    trs = _true_ranges(bars)
    out = [None] * len(bars)
    for i in range(win, len(bars)):
        window = trs[i - win + 1:i + 1]
        if all(v is not None for v in window):
            out[i] = sum(window) / win
    return out


def atr_last(bars, win=ATR_WIN):
    for value in reversed(atr_series(bars, win)):
        if value is not None:
            return value
    return None


def er_last(closes, win=ER_WIN):
    if len(closes) < win + 2:
        return None
    net = abs(closes[-1] - closes[-1 - win])
    path = sum(abs(closes[j] - closes[j - 1])
               for j in range(len(closes) - win, len(closes)))
    return net / path if path > 0 else 0.0


def regime_from(btc_bars):
    """Cell from BTC hourly candles, using the reference bot's definitions."""
    if not btc_bars or len(btc_bars) < PCT_WIN + ATR_WIN + 2:
        return None
    closes = [b["c"] for b in btc_bars]
    atr = atr_series(btc_bars)
    atrp = [None if atr[i] is None else atr[i] / closes[i] * 100.0
            for i in range(len(closes))]
    current = atrp[-1]
    hist = [x for x in atrp[-(PCT_WIN + 1):-1] if x is not None]
    if current is None or not hist:
        return None
    pct = 100.0 * sum(1 for x in hist if x <= current) / len(hist)
    er = er_last(closes)
    if er is None:
        return None
    return {
        "trend": CELL_TREND if er >= ER_TREND else "range",
        "vol": CELL_VOL if pct >= PCT_CUT else "calm",
        "er": round(er, 3),
        "atr_pct": round(current, 3),
        "pct": round(pct, 0),
    }


def in_cell(regime):
    return bool(regime) and regime.get("trend") == CELL_TREND and regime.get("vol") == CELL_VOL


def exit_for(position, bars, cost_pct=COST_PCT):
    """Walk the bars after entry; stop is checked before target inside an hour."""
    sig_ts = position["sig_ts"]
    deadline = sig_ts + position["max_h"] * 3600000
    for bar in bars:
        if bar["t"] <= sig_ts:
            continue
        if bar["l"] <= position["stop"]:
            return position["stop"], bar["t"], "stop"
        if bar["h"] >= position["target"]:
            return position["target"], bar["t"], "target"
        if bar["t"] >= deadline:
            return bar["c"], bar["t"], "time"
    return None


def closed_record(position, exit_px, exit_ts, why):
    gross = (exit_px / position["entry"] - 1.0) * 100.0
    record = {k: position.get(k) for k in
              ("arm", "coin", "entry", "stop", "target", "sig_ts", "max_h", "phase")}
    record.update({"exit": exit_px, "exit_ts": exit_ts, "why": why,
                   "gross_pct": round(gross, 4),
                   "res_pct": round(gross - COST_PCT, 4)})
    return record


def arm_stats(closed):
    n = len(closed)
    if not n:
        return {"n": 0, "mean_pct": 0.0, "wins_pct": 0.0, "pf": None, "t": 0.0}
    values = [c["res_pct"] for c in closed]
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v <= 0]
    mean = sum(values) / n
    sd = statistics.stdev(values) if n > 1 else 0.0
    gross_loss = abs(sum(losses))
    return {
        "n": n,
        "mean_pct": round(mean, 4),
        "total_pct": round(sum(values), 2),
        "wins_pct": round(100.0 * len(wins) / n, 1),
        "pf": round(sum(wins) / gross_loss, 3) if gross_loss > 0 else None,
        "t": round(mean / (sd / math.sqrt(n)), 3) if sd > 0 and n > 1 else 0.0,
        "reasons": {w: sum(1 for c in closed if c.get("why") == w)
                    for w in sorted({c.get("why") for c in closed})},
    }


# ---------------------------------------------------------------------- module

class RegimeLab:
    def __init__(self, bot=None, fetch=None, clock=None):
        self.bot = bot
        self.enabled = bool(getattr(config, "PAPER_LAB_ENABLED", True))
        self.interval = float(getattr(config, "PAPER_LAB_SEC", 3600))
        self.universe = list(getattr(config, "PAPER_LAB_UNIVERSE") or [])
        self.regime_coin = getattr(config, "PAPER_LAB_REGIME_COIN", "BTC")
        self.clock = clock or time.time
        self.fetch = fetch or self._fetch_candles
        self.last_error = ""
        self.state = self._blank()
        self.load()

    # ---- persistence

    @staticmethod
    def _blank():
        return {"last_run": 0.0, "phase": None, "regimes": [], "errors": [],
                "arms": {arm: {"open": [], "closed": []} for arm in ARMS}}

    def path(self):
        try:
            return os.path.join(state_store.state_dir(), STATE_FILE)
        except Exception:
            return STATE_FILE

    def load(self):
        try:
            data = state_store.load(self.path())
        except Exception as exc:
            self.last_error = "load: %s" % exc
            return
        if not isinstance(data, dict):
            return
        self.state["last_run"] = float(data.get("last_run") or 0.0)
        phase = data.get("phase")
        self.state["phase"] = phase if isinstance(phase, dict) else None
        regimes = data.get("regimes")
        self.state["regimes"] = regimes[-REGIME_HISTORY:] if isinstance(regimes, list) else []
        arms = data.get("arms")
        if not isinstance(arms, dict):
            return
        for arm in ARMS:
            raw = arms.get(arm)
            if not isinstance(raw, dict):
                continue
            live = [p for p in (raw.get("open") or []) if isinstance(p, dict) and "sig_ts" in p]
            done = [c for c in (raw.get("closed") or []) if isinstance(c, dict) and "res_pct" in c]
            self.state["arms"][arm]["open"] = live
            self.state["arms"][arm]["closed"] = done[-CLOSED_CAP:]

    def save(self):
        try:
            state_store.save(self.state, self.path())
        except Exception as exc:
            self.last_error = "save: %s" % exc

    # ---- data

    def _fetch_candles(self, coin, bars):
        try:
            return self.bot.client.candles_recent(coin, "1h", bars) or []
        except Exception as exc:
            self.last_error = "candles %s: %s" % (coin, exc)
            return []

    def _journal(self, action, reason, **data):
        """One entry per arm and coin: the journal dedupes on the key, so a bare
        action would collapse three simultaneous opens into one."""
        journal = getattr(self.bot, "journal", None)
        if journal is None:
            return
        key = "%s:%s:%s" % (action, data.get("arm", "-"), data.get("coin", "-"))
        try:
            journal.add("lab", action, reason, key=key, **data)
        except Exception:
            pass

    def _note_error(self, where, exc):
        self.last_error = "%s: %s" % (where, exc)
        try:
            self.state["errors"] = (self.state["errors"] + [
                {"ts": self.clock(), "where": where, "error": str(exc)[:200]}])[-20:]
        except Exception:
            pass

    # ---- hourly work

    def on_tick(self, force=False):
        """Cheap gate called from the price loop; real work happens once an hour."""
        if not self.enabled:
            return
        try:
            now = self.clock()
            if not force and now - float(self.state.get("last_run") or 0.0) < self.interval:
                return
            self.refresh(now)
        except Exception as exc:
            self._note_error("on_tick", exc)

    def refresh(self, now=None):
        now = self.clock() if now is None else now
        btc = self.fetch(self.regime_coin, PCT_WIN + ATR_WIN + 40)
        regime = regime_from(btc)
        if regime is None:
            self.state["last_run"] = now - self.interval + 600.0   # retry in ~10 min
            self.save()
            return
        self.state["phase"] = regime
        self.state["regimes"] = (self.state["regimes"] +
                                 [dict(regime, ts=int(now))])[-REGIME_HISTORY:]

        book = {}
        for coin in self.universe:
            bars = self.fetch(coin, EMA_FEED + ATR_WIN + HOLD_H + 20)
            if bars:
                book[coin] = bars

        self._track_open(book, now)
        self._scan(book, regime, now)
        self.state["last_run"] = now
        self.save()

    def _track_open(self, book, now):
        for arm in ARMS:
            still_open = []
            for position in self.state["arms"][arm]["open"]:
                try:
                    bars = book.get(position["coin"])
                    if not bars:
                        still_open.append(position)
                        continue
                    done = exit_for(position, bars)
                    if done is None:
                        still_open.append(position)
                        continue
                    exit_px, exit_ts, why = done
                    record = closed_record(position, exit_px, exit_ts, why)
                    closed = self.state["arms"][arm]["closed"] + [record]
                    self.state["arms"][arm]["closed"] = closed[-CLOSED_CAP:]
                    self._journal("close", "%s %s %s %+.2f%%" % (
                        arm, position["coin"], why, record["res_pct"]),
                        arm=arm, coin=position["coin"], res_pct=record["res_pct"])
                except Exception as exc:
                    self._note_error("track %s" % arm, exc)
                    still_open.append(position)
            self.state["arms"][arm]["open"] = still_open

    def _scan(self, book, regime, now):
        cell_open = in_cell(regime)
        phase = "%s/%s" % (regime["trend"], regime["vol"])
        for coin, bars in book.items():
            try:
                closes = [b["c"] for b in bars]
                ema = ema_last(closes)
                atr = atr_last(bars)
                if ema is None or atr is None or closes[-1] <= ema:
                    continue
                entry = closes[-1]
                for arm in ARMS:
                    if arm != "control" and not cell_open:
                        continue
                    if self._has_slot(arm, coin):
                        self._open(arm, coin, entry, atr, bars, regime, phase, now)
            except Exception as exc:
                self._note_error("scan %s" % coin, exc)

    def _has_slot(self, arm, coin):
        state = self.state["arms"][arm]
        if any(p["coin"] == coin for p in state["open"]):
            return False
        if arm == "cell_all":
            return True
        return len(state["open"]) < MAX_OPEN

    def _open(self, arm, coin, entry, atr, bars, regime, phase, now):
        position = {
            "arm": arm, "coin": coin, "entry": entry,
            "stop": entry - STOP_ATR * atr, "target": entry + TARGET_ATR * atr,
            "max_h": HOLD_H, "sig_ts": bars[-1]["t"], "phase": phase,
            "er": regime["er"], "atr_pct": regime["atr_pct"],
            "opened_at": int(now),
        }
        self.state["arms"][arm]["open"].append(position)
        self._journal("open", "%s %s @ %.6g (%s)" % (arm, coin, entry, phase),
                      arm=arm, coin=coin)

    # ---- reporting

    def status(self):
        arms = {arm: arm_stats(self.state["arms"][arm]["closed"]) for arm in ARMS}
        return {
            "enabled": self.enabled,
            "phase": self.state.get("phase"),
            "interval_sec": self.interval,
            "last_run": self.state.get("last_run"),
            "age_sec": round(max(0.0, self.clock() - float(self.state.get("last_run") or 0.0)), 0)
            if self.state.get("last_run") else None,
            "hours_seen": len(self.state.get("regimes") or []),
            "open": {arm: [{"coin": p["coin"], "phase": p.get("phase")}
                           for p in self.state["arms"][arm]["open"]] for arm in ARMS},
            "arms": arms,
            "penalty": "paper only: no orders, no cash, no equity",
            "last_error": self.last_error,
        }

    def to_state(self):
        return self.state
