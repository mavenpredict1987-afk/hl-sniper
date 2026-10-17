"""pairs.py — market-neutral pairs module.

The only strategy in this project that survived an out-of-sample split. Buying
the weakest coins and shorting the strongest, equal notional, held a couple of
days:

    full 208 days   +17.9%  PF 1.22  DD 5.4%  corr to market -0.146
    first half      +10.5%  PF 1.30  DD 4.5%
    second half      +7.8%  PF 1.19  DD 5.4%

Eight further variants (2/3/5/8 pairs, 20%/40% exposure) were all positive.

Why it is built this way:

* Both legs carry the same notional, so the market cancels out. Every
  directional strategy tested here lost for the same reason - it was a bet on
  direction. This one trades the spread between two coins instead.
* The direction is MEAN REVERSION: long the loser, short the winner. The
  opposite (long the winner) lost 3-25%, so the sign matters.
* Shorter lookbacks (24h) did not survive the half split. Seven days did.
* Funding is close to neutral by construction: the long pays what the short
  receives, since both legs sit on the same venue.

Paper first. This module sizes and books positions internally and places no
orders; switching it to live needs the same care as any other real-money code.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import List

import config

logger = logging.getLogger(__name__)


@dataclass
class PairPosition:
    long_sym: str
    short_sym: str
    long_entry: float
    short_entry: float
    notional: float          # per leg, equal on both sides
    opened: float
    due: float
    long_size: float = 0.0
    short_size: float = 0.0


class PairsModule:
    def __init__(self, bot):
        self.bot = bot
        self.positions: List[PairPosition] = []
        self.closed: List[dict] = []
        self.stats = {"trades": 0, "wins": 0, "pnl": 0.0, "legs": 0}
        self.last_rebalance = 0.0
        self.last_ranking: List[dict] = []
        self.history: List[float] = []
        self.last_error = ""

    # ---------- data ----------
    def _lookback_return(self, symbol, hours):
        """Return over `hours` of hourly candles, or None if unavailable."""
        try:
            candles = self.bot.client.candles_recent(symbol, "1h", hours + 2)
        except Exception as exc:                     # network trouble is normal
            self.last_error = "%s: %s" % (symbol, exc)
            return None
        if not candles or len(candles) < hours:
            return None
        try:
            first = float(candles[-hours]["c"])
            last = float(candles[-1]["c"])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        if first <= 0:
            return None
        return last / first - 1.0

    def rank(self):
        """Coins ordered by their lookback return, weakest first."""
        rows = []
        for symbol in self.bot.active_symbols:
            r = self._lookback_return(symbol, config.PAIRS_LOOKBACK_H)
            if r is None:
                continue
            rows.append({"symbol": symbol, "ret": r})
        rows.sort(key=lambda row: row["ret"])
        return rows

    # ---------- cycle ----------
    def on_cycle(self):
        if not config.PAIRS_ENABLED:
            return
        now = time.time()
        self._close_due(now)
        if now - self.last_rebalance >= config.PAIRS_HOLD_H * 3600:
            self._rebalance(now)

    def _close_due(self, now):
        keep = []
        for pos in self.positions:
            if now < pos.due:
                keep.append(pos)
                continue
            self._close(pos, now)
        self.positions = keep

    def _rebalance(self, now):
        rows = self.rank()
        if len(rows) < 2 * config.PAIRS_N:
            self.last_error = "not enough coins ranked (%d)" % len(rows)
            return
        self.last_ranking = rows
        self.last_rebalance = now

        n = config.PAIRS_N
        per_leg = self.bot.equity * config.PAIRS_EXPOSURE / (2.0 * n)
        if per_leg <= 0:
            return

        # weakest first. Long the bottom n, short the top n.
        weakest = rows[:n]
        strongest = rows[-n:]

        for weak, strong in zip(weakest, reversed(strongest)):
            lp = self.bot.last_prices.get(weak["symbol"])
            sp = self.bot.last_prices.get(strong["symbol"])
            if not lp or not sp or lp <= 0 or sp <= 0:
                continue
            self.positions.append(PairPosition(
                long_sym=weak["symbol"], short_sym=strong["symbol"],
                long_entry=lp, short_entry=sp, notional=per_leg,
                opened=now, due=now + config.PAIRS_HOLD_H * 3600,
                long_size=per_leg / lp, short_size=per_leg / sp))
            self.bot._event("PAIRS open: long %s / short %s ($%.0f per leg)" % (
                weak["symbol"], strong["symbol"], per_leg))
        logger.info("PAIRS rebalance: %d pair(s) opened, %d ranked" % (
            len(self.positions), len(rows)))
        self.bot._save_state(force=True)

    def _close(self, pos, now):
        lp = self.bot.last_prices.get(pos.long_sym)
        sp = self.bot.last_prices.get(pos.short_sym)
        if not lp or not sp or lp <= 0 or sp <= 0:
            return
        cost = config.TAKER_FEE + config.SLIPPAGE / 4.0

        long_ret = lp / pos.long_entry - 1.0
        short_ret = 1.0 - sp / pos.short_entry
        gross = pos.notional * (long_ret + short_ret)
        fees = pos.notional * cost * (1.0 + lp / pos.long_entry
                                      + 1.0 + sp / pos.short_entry)
        net = gross - fees

        self.stats["trades"] += 1
        self.stats["legs"] += 2
        self.stats["pnl"] += net
        if net > 0:
            self.stats["wins"] += 1
        self.closed.append({
            "long": pos.long_sym, "short": pos.short_sym,
            "long_ret": round(long_ret * 100, 2), "short_ret": round(short_ret * 100, 2),
            "pnl": round(net, 2), "opened": pos.opened, "closed": now,
            "hours": round((now - pos.opened) / 3600.0, 1)})
        if len(self.closed) > 200:
            self.closed = self.closed[-200:]
        self.bot._event("PAIRS close: %s/%s pnl $%+.2f" % (
            pos.long_sym, pos.short_sym, net))
        logger.info("PAIRS close %s/%s: $%+.2f", pos.long_sym, pos.short_sym, net)

    # ---------- reporting ----------
    def unrealized(self):
        total = 0.0
        for pos in self.positions:
            lp = self.bot.last_prices.get(pos.long_sym)
            sp = self.bot.last_prices.get(pos.short_sym)
            if not lp or not sp:
                continue
            total += pos.notional * ((lp / pos.long_entry - 1.0)
                                     + (1.0 - sp / pos.short_entry))
        return total

    def next_rebalance_hours(self):
        if not self.last_rebalance:
            return 0.0
        left = self.last_rebalance + config.PAIRS_HOLD_H * 3600 - time.time()
        return max(0.0, round(left / 3600.0, 1))

    def get_stats(self):
        trades = self.stats["trades"]
        return {
            "enabled": config.PAIRS_ENABLED,
            "trades": trades,
            "wins": self.stats["wins"],
            "winrate": round(100.0 * self.stats["wins"] / trades, 1) if trades else 0.0,
            "pnl": round(self.stats["pnl"], 2),
            "unrealized": round(self.unrealized(), 2),
            "open": len(self.positions),
            "lookback_h": config.PAIRS_LOOKBACK_H,
            "hold_h": config.PAIRS_HOLD_H,
            "n_pairs": config.PAIRS_N,
            "exposure": config.PAIRS_EXPOSURE,
            "next_rebalance_h": self.next_rebalance_hours(),
            "last_error": self.last_error,
            "ranking": [{"symbol": r["symbol"], "ret_pct": round(r["ret"] * 100, 2)}
                        for r in self.last_ranking[:5]] +
                       [{"symbol": r["symbol"], "ret_pct": round(r["ret"] * 100, 2)}
                        for r in self.last_ranking[-3:]],
            "open_pairs": [{"long": p.long_sym, "short": p.short_sym,
                            "notional": round(p.notional, 2),
                            "hours": round((time.time() - p.opened) / 3600.0, 1)}
                           for p in self.positions],
            "recent": list(reversed(self.closed[-10:])),
        }
