"""Paper-only grid arm: long grid on dips, replicating the design of the
reference bot with the sizing and the exit rules frozen in
`research/GRID_PREREG.md`.

Why it exists: the design backtested positive (+0.15..+0.40% per lot, PF 1.7)
but it spends a third of its life underwater and its drawdown scales with the
lot size. His live account runs $45 lots on a $110 balance (41% of equity per
position, up to 2x gross, 48% of equity at open risk) and is 10% below its own
start. We want the same edge at 0.5% risk per lot, which is 80x less risk.

Safety contract, enforced by tests:

* it opens no orders and touches no exchange state - paper only;
* it has its own state file, its own journal prefix and its own capital, so it
  cannot bias the pairs measurement or the regime laboratory;
* an exception inside it can never propagate into the trading loop;
* it never assumes an exit inside the bar that filled the entry (the
  optimistic assumption that inflated the first version of the backtest).

Rules (frozen):
    entry      limit buy at centre * (1 - 0.015*k), k in 1..2, when bar low
               touches the level and the previous close is above it
    centre     highest high of the previous 24 hourly bars
    target     +1.5% from the entry price, 50% of the lot closed at +0.5%
    stop       -20% from the entry price, filled with 0.03% slippage
    caps       6 lots total, 2 per coin
    lot size   2.5% of the arm equity (= 0.5% risk at a 20% stop)
    costs      maker 0.015% in, taker 0.045% out, hourly funding on the notional
"""

import os
import time

import config
import state_store

STEP_PCT = 0.015
LEVELS = (1, 2)
TARGET_PCT = 0.015
PARTIAL_PCT = 0.005
PARTIAL_SHARE = 0.5
STOP_FROM_ENTRY = 0.20
CENTRE_WIN = 24
MAX_LOTS = 6
MAX_PER_COIN = 2
LOT_FRAC = 0.025
MAKER_FEE = 0.00015
TAKER_FEE = 0.00045
SLIP_STOP = 0.0003
START_EQUITY = 1000.0
STATE_FILE = "grid_lab.json"
CLOSED_CAP = 600
CURVE_CAP = 6000

DEFAULT_UNIVERSE = (
    "PUMP", "ENA", "FARTCOIN", "NEAR", "ZRO", "HYPE", "XRP", "ADA", "AVAX",
    "kPEPE", "ARB", "MON", "ZEC", "UNI", "VVV", "ICP", "DOGE", "XPL", "ONDO",
    "GRASS", "SUI", "XMR", "SOL", "LINK", "AAVE", "LIT", "PYTH",
)


def lot_usd(equity):
    """Notional of one lot: 2.5% of equity, i.e. 0.5% of equity at a 20% stop."""
    return round(max(5.0, float(equity) * LOT_FRAC), 2)


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def metrics(closed, curve, entry_fees=0.0, funding=0.0):
    """Statistics of the arm. `curve` is the mark-to-market equity series, and
    the drawdown is computed from it, not from closed trades: a grid looks
    profitable on closed trades while it is deep under water."""
    lots = len(closed)
    wins = [c for c in closed if _num(c.get("usd")) > 0]
    losses = [c for c in closed if _num(c.get("usd")) <= 0]
    gain = sum(_num(c.get("usd")) for c in wins)
    loss = -sum(_num(c.get("usd")) for c in losses)
    values = [START_EQUITY]
    for item in curve:
        # the curve is stored as [timestamp, equity] pairs so the dashboard can
        # draw it without guessing spacing
        values.append(_num(item[-1]) if isinstance(item, (list, tuple)) and item else _num(item))
    peak = values[0]
    max_dd = 0.0
    below = 0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, 100.0 * (1.0 - v / peak))
        if v < START_EQUITY:
            below += 1
    total = sum(_num(c.get("usd")) for c in closed)
    lot = lot_usd(START_EQUITY)
    return {
        "lots_closed": lots,
        "winrate_pct": round(100.0 * len(wins) / lots, 1) if lots else 0.0,
        "profit_factor": round(gain / loss, 3) if loss > 0 else None,
        "avg_win_usd": round(gain / len(wins), 2) if wins else 0.0,
        "avg_loss_usd": round(-loss / len(losses), 2) if losses else 0.0,
        "expectancy_pct_per_lot": round(100.0 * total / (lots * lot), 4) if lots else 0.0,
        "realized_usd": round(total, 2),
        "entry_fees_usd": round(entry_fees, 2),
        "costs_usd": round(entry_fees + funding, 2),
        "funding_usd": round(funding, 2),
        "mtm_max_dd_pct": round(max_dd, 2),
        "pct_time_below_start": round(100.0 * below / max(1, len(values)), 1),
        "min_equity": round(min(values), 2),
        "equity": round(values[-1], 2),
    }


class GridLab:
    def __init__(self, bot=None, fetch=None, clock=None, funding_fn=None):
        self.bot = bot
        self.enabled = bool(getattr(config, "GRID_LAB_ENABLED", True))
        self.interval = float(getattr(config, "GRID_LAB_SEC", 3600))
        self.universe = list(getattr(config, "GRID_LAB_UNIVERSE", None) or DEFAULT_UNIVERSE)
        self.clock = clock or time.time
        self.fetch = fetch or self._fetch_candles
        self.funding_fn = funding_fn or self._fetch_funding
        self.last_error = ""
        self.restored = False
        self.state = self._blank()
        self.load()

    # ---- persistence

    @staticmethod
    def _blank():
        return {"last_run": 0.0, "curve": [], "open": [], "closed": [],
                "errors": [], "entry_fees": 0.0, "funding": 0.0,
                "last_close": {}, "last_bar": {}, "hours": 0, "funding_hours": 0}

    def path(self):
        try:
            return os.path.join(state_store.state_dir(), STATE_FILE)
        except Exception:
            return STATE_FILE

    def load(self):
        try:
            data, _ = state_store.load(self.path())
        except Exception as exc:
            self.last_error = "load: %s" % exc
            return
        if not isinstance(data, dict):
            # state_store.load swallows an unreadable file and returns nothing;
            # report it instead of silently starting from zero
            if os.path.exists(self.path()):
                self.last_error = "load: unreadable state file"
            return
        self.restored = True
        self.state["last_run"] = _num(data.get("last_run"))
        for key in ("entry_fees", "funding"):
            self.state[key] = _num(data.get(key))
        self.state["hours"] = int(_num(data.get("hours")))
        self.state["funding_hours"] = int(_num(data.get("funding_hours")))
        curve = data.get("curve")
        self.state["curve"] = [[int(_num(p[0])), round(_num(p[1]), 2)]
                               for p in curve if isinstance(p, list) and len(p) == 2][-CURVE_CAP:] \
            if isinstance(curve, list) else []
        errors = data.get("errors")
        self.state["errors"] = errors[-20:] if isinstance(errors, list) else []
        last_close = data.get("last_close")
        self.state["last_close"] = ({k: _num(v) for k, v in last_close.items()}
                                    if isinstance(last_close, dict) else {})
        last_bar = data.get("last_bar")
        self.state["last_bar"] = ({k: int(_num(v)) for k, v in last_bar.items()}
                                  if isinstance(last_bar, dict) else {})
        opened = data.get("open")
        self.state["open"] = [p for p in opened if isinstance(p, dict) and "entry" in p] \
            if isinstance(opened, list) else []
        closed = data.get("closed")
        self.state["closed"] = [c for c in closed if isinstance(c, dict) and "usd" in c][-CLOSED_CAP:] \
            if isinstance(closed, list) else []

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

    def _fetch_funding(self):
        """Hourly funding rate per coin, in fractions of notional."""
        try:
            ctxs = self.bot.client.meta_and_ctxs() or {}
        except Exception as exc:
            self.last_error = "funding: %s" % exc
            return {}
        out = {}
        for coin, ctx in ctxs.items():
            if isinstance(ctx, dict):
                out[coin] = _num(ctx.get("funding"))
        return out

    def _journal(self, action, reason, **data):
        journal = getattr(self.bot, "journal", None)
        if journal is None:
            return
        key = "grid:%s:%s" % (action, data.get("coin", "-"))
        try:
            journal.add("grid", action, reason, key=key, **data)
        except Exception:
            pass

    def _note_error(self, where, exc):
        self.last_error = "%s: %s" % (where, exc)
        try:
            self.state["errors"] = (self.state["errors"] + [
                {"ts": self.clock(), "where": where, "error": str(exc)[:200]}])[-20:]
        except Exception:
            pass

    # ---- equity

    def equity(self):
        """Mark to market. A grid that only reports closed trades looks
        profitable while it is under water, so every open lot is valued at the
        last close and the entry fees and funding are subtracted here, not only
        from the trade statistics."""
        lot = lot_usd(START_EQUITY)
        value = START_EQUITY - _num(self.state["entry_fees"]) - _num(self.state["funding"])
        value += sum(_num(c.get("usd")) for c in self.state["closed"])
        for pos in self.state["open"]:
            entry = _num(pos.get("entry"))
            last = _num(self.state["last_close"].get(pos["coin"]), entry)
            left = _num(pos.get("left"), 1.0)
            if entry > 0:
                value += _num(pos.get("cash")) + lot * left * (last / entry - 1.0)
        return value

    def _book_funding(self, rates):
        """Charge one hour of funding on every open lot. A long pays when the
        rate is positive, receives when it is negative."""
        if not rates:
            return
        paid = 0.0
        for pos in self.state["open"]:
            rate = _num(rates.get(pos["coin"]))
            if rate:
                paid += lot_usd(START_EQUITY) * _num(pos.get("left"), 1.0) * rate
        if paid:
            self.state["funding"] = _num(self.state["funding"]) + paid
        self.state["funding_hours"] = int(self.state["funding_hours"]) + 1

    # ---- hourly work

    def on_tick(self, force=False):
        if not self.enabled:
            return
        try:
            now = self.clock()
            if not force and now - _num(self.state.get("last_run")) < self.interval:
                return
            self.refresh(now)
        except Exception as exc:                     # never raise into the loop
            self._note_error("on_tick", exc)

    def refresh(self, now=None):
        now = self.clock() if now is None else now
        self._book_funding(self.funding_fn())
        book, closes = {}, {}
        for coin in self.universe:
            bars = [b for b in (self.fetch(coin, CENTRE_WIN + 3) or [])
                    if isinstance(b, dict) and _num(b.get("h")) > 0]
            if len(bars) < CENTRE_WIN + 2:
                continue
            book[coin] = bars
            closes[coin] = _num(bars[-1].get("c"))
        if not book:
            self.state["last_run"] = now - self.interval + 600.0     # retry in ~10 min
            self.save()
            return
        self.state["last_close"].update(closes)
        self._track_open(book, now)
        self._open_new(book, closes, now)
        self.state["hours"] = int(self.state["hours"]) + 1
        curve = self.state["curve"]
        curve.append([int(now), round(self.equity(), 2)])
        self.state["curve"] = curve[-CURVE_CAP:]
        self.state["last_run"] = now
        self.save()

    def _close(self, pos, usd, why, now):
        self.state["closed"] = (self.state["closed"] + [{
            "coin": pos["coin"], "usd": round(usd, 4), "why": why,
            "entry": _num(pos["entry"]), "lot": lot_usd(START_EQUITY),
            "opened_ts": int(_num(pos.get("opened_ts"), now)),
            "closed_ts": int(now),
            "hold_h": round(max(0.0, now - _num(pos.get("opened_ts"), now)) / 3600.0, 1)}])[-CLOSED_CAP:]
        self._journal("close", why, coin=pos["coin"], usd=round(usd, 4),
                      entry=_num(pos["entry"]), hold_h=round(max(0.0, now - _num(pos.get("opened_ts"), now)) / 3600.0, 1))

    def _track_open(self, book, now):
        keep = []
        for pos in self.state["open"]:
            bars = book.get(pos["coin"])
            entry = _num(pos.get("entry"))
            if not bars or entry <= 0:
                keep.append(pos)
                continue
            bar = bars[-1]
            same_bar = int(_num(bar.get("t"))) == int(_num(pos.get("entry_bar_ts"), -1))
            if same_bar:                                   # no exit on the entry bar
                keep.append(pos)
                continue
            high, low, close = _num(bar.get("h")), _num(bar.get("l")), _num(bar.get("c"))
            left = _num(pos.get("left"), 1.0)
            lot = lot_usd(START_EQUITY)
            stop = _num(pos.get("stop"), entry * (1.0 - STOP_FROM_ENTRY))
            if low <= stop:                                # stop first: pessimistic
                px = stop * (1.0 - SLIP_STOP)
                size = lot * left
                cash = size * (px / entry - 1.0) - size * TAKER_FEE
                self._close(pos, _num(pos.get("cash")) + cash, "stop", now)
                continue
            if abs(left - 1.0) < 1e-9 and high >= entry * (1.0 + PARTIAL_PCT):
                part = lot * PARTIAL_SHARE
                cash = part * PARTIAL_PCT - part * TAKER_FEE
                pos["cash"] = _num(pos.get("cash")) + cash
                pos["left"] = round(left - PARTIAL_SHARE, 6)
                left = pos["left"]
                self._journal("partial", "partial 50% at +0.5%", coin=pos["coin"],
                              usd=round(cash, 4))
            if high >= entry * (1.0 + TARGET_PCT):
                size = lot * left
                cash = size * TARGET_PCT - size * TAKER_FEE
                self._close(pos, _num(pos.get("cash")) + cash, "target", now)
                continue
            keep.append(pos)
        self.state["open"] = keep

    def _open_new(self, book, closes, now):
        total = len(self.state["open"])
        if total >= MAX_LOTS:
            return
        equity = self.equity()
        for coin in self.universe:
            bars = book.get(coin)
            if not bars:
                continue
            per_coin = sum(1 for p in self.state["open"] if p["coin"] == coin)
            prev_close = _num(bars[-2].get("c"))
            bar = bars[-1]
            bar_ts = int(_num(bar.get("t")))
            if bar_ts and self.state["last_bar"].get(coin) == bar_ts:
                # the same closed bar was already evaluated (a restart inside the
                # same hour used to open a second lot at a level already filled)
                continue
            centre = max(_num(b.get("h")) for b in bars[-1 - CENTRE_WIN:-1])
            if centre <= 0 or prev_close <= 0:
                continue
            for k in LEVELS:
                if total >= MAX_LOTS:
                    break
                if per_coin >= MAX_PER_COIN:
                    break
                lvl = centre * (1.0 - STEP_PCT * k)
                if not (_num(bar.get("l")) <= lvl < prev_close):
                    continue
                pos = {"coin": coin, "entry": round(lvl, 10), "left": 1.0, "cash": 0.0,
                       "stop": round(lvl * (1.0 - STOP_FROM_ENTRY), 10),
                       "opened_ts": int(now), "entry_bar_ts": int(_num(bar.get("t")))}
                self.state["open"].append(pos)
                total += 1
                per_coin += 1
                fee = lot_usd(START_EQUITY) * MAKER_FEE
                self.state["entry_fees"] = _num(self.state["entry_fees"]) + fee
                self._journal("open", "limit filled at level %d" % k, coin=coin,
                              entry=pos["entry"], stop=pos["stop"], level=k)
            if bar_ts:
                self.state["last_bar"][coin] = bar_ts
            if coin in closes:
                self.state["last_close"][coin] = closes[coin]

    # ---- reporting

    def by_coin(self):
        out = {}
        for c in self.state["closed"]:
            row = out.setdefault(c["coin"], {"lots": 0, "usd": 0.0})
            row["lots"] += 1
            row["usd"] = round(row["usd"] + _num(c.get("usd")), 2)
        return [dict(coin=k, **v) for k, v in sorted(out.items(), key=lambda kv: -kv[1]["usd"])]

    def status(self):
        try:
            agg = metrics(self.state["closed"], self.state["curve"],
                          _num(self.state["entry_fees"]), _num(self.state["funding"]))
            open_rows = []
            for pos in self.state["open"]:
                entry = _num(pos.get("entry"))
                last = _num(self.state["last_close"].get(pos["coin"]), entry)
                open_rows.append({
                    "coin": pos["coin"],
                    "entry": round(entry, 6),
                    "last": round(last, 6),
                    "stop": round(_num(pos.get("stop")), 6),
                    "left": _num(pos.get("left"), 1.0),
                    "age_h": round(max(0.0, self.clock() - _num(pos.get("opened_ts"))) / 3600.0, 1),
                    "unreal_pct": round(100.0 * (last / entry - 1.0), 2) if entry else 0.0,
                    "to_target_pct": round(100.0 * (entry * (1.0 + TARGET_PCT) / last - 1.0), 2) if last else 0.0,
                    "to_stop_pct": round(100.0 * (_num(pos.get("stop")) / last - 1.0), 2) if last else 0.0,
                })
            lot = lot_usd(agg["equity"])
            risk = sum(lot * _num(p.get("left"), 1.0) * STOP_FROM_ENTRY for p in self.state["open"])
            return {
                "enabled": self.enabled,
                "restored": self.restored,
                "last_run": round(_num(self.state.get("last_run")), 1),
                "hours": int(self.state.get("hours")),
                "funding_hours": int(self.state.get("funding_hours")),
                "lot_usd": lot,
                "start_equity": START_EQUITY,
                "open_count": len(self.state["open"]),
                "open_notional_usd": round(lot * sum(_num(p.get("left"), 1.0) for p in self.state["open"]), 2),
                "open_risk_usd": round(risk, 2),
                "open_risk_pct": round(100.0 * risk / agg["equity"], 2) if agg["equity"] else 0.0,
                "open": open_rows,
                "by_coin": self.by_coin()[:8],
                "last_error": self.last_error,
                "errors": self.state["errors"][-5:],
                **agg,
            }
        except Exception as exc:
            self._note_error("status", exc)
            return {"enabled": self.enabled, "restored": self.restored,
                    "last_error": self.last_error, "lots_closed": 0, "open_count": 0}
