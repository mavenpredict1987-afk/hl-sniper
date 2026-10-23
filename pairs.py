"""pairs.py — market-neutral pairs module.

STATUS (2026-10-23, RESEARCH_LOG #35): NOT VALIDATED. The +17.9% over 208 days
quoted below came from ONE 48-hour grid alignment. Shifting the grid by 1..47
hours gives a median of -444 USD over 48 alignments, only 9 of 48 positive, and
the per-cycle spread return at the original alignment has t = 0.63 - i.e. the
edge is not distinguishable from zero, and the average over all alignments is
negative BEFORE costs. The live paper module keeps running until the freeze
ends (2026-10-31) purely as a forward measurement, not as a validated edge.

Historical numbers, from the single alignment (kept so the record of how we
fooled ourselves survives):
    full 208 days   +17.9%  PF 1.22  DD 5.4%  corr to market -0.146
    first half      +10.5%  PF 1.30  DD 4.5%
    second half      +7.8%  PF 1.19  DD 5.4%

Eight further variants (2/3/5/8 pairs, 20%/40% exposure) were all positive -
all measured on the same unlucky-for-us... actually lucky-for-us alignment,
which is why they agreed.

Why it is built this way:

* Both legs carry the same notional, so the market cancels out. Every
  directional strategy tested here lost for the same reason - it was a bet on
  direction. This one trades the spread between two coins instead.
* The direction is MEAN REVERSION: long the loser, short the winner. The
  opposite (long the winner) lost 3-25%, so the sign matters.
* Shorter lookbacks (24h) did not survive the half split. Seven days did.
* Funding is NOT neutral by construction, this was an error in the original
  note: every leg is charged its own hourly rate, so the pair nets
  funding_short - funding_long. Measured on 208 days (RESEARCH_LOG #33) that
  difference was POSITIVE, +27.30 USD on top of +138.81 unrealised before it,
  because the short legs (strong coins) usually sit in higher positive funding
  than the long legs (weak coins). The backtests before #33 omitted it.

Paper first. This module sizes and books positions internally and places no
orders; switching it to live needs the same care as any other real-money code.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import List

import config
from utils import coverage_grade, coverage_x

logger = logging.getLogger(__name__)

# How many executions to remember for the cost measurement. Bounded because it
# rides in the state file on the Railway volume: 400 samples is a couple of
# hundred rounds of opens and closes, enough for a stable median, small enough
# that the JSON stays under a few dozen KB.
COST_SAMPLES_CAP = 400


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
    # The numbers that were on the screen when the pair was opened: the return
    # that ranked each leg, its volatility and its funding. Without them a
    # closed pair is a bare PnL figure and the only way to explain it is to go
    # and look at the chart again, which is how you fool yourself.
    context: dict = field(default_factory=dict)


def _n(x, digits=2):
    """Format a number for a journal reason string, "n/a" when not measured."""
    if x is None:
        return "n/a"
    try:
        return ("%." + str(digits) + "f") % float(x)
    except (TypeError, ValueError):
        return "n/a"


def _atr_pct(candles, period=14):
    """Average true range as a fraction of the last close, or None.

    None rather than 0.0 when the candles carry no high/low: a zero volatility
    reading would silently rank as the safest coin in the universe.
    """
    if not candles or len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        try:
            high = float(candles[i]["h"])
            low = float(candles[i]["l"])
            prev = float(candles[i - 1]["c"])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        trs.append(max(high - low, abs(high - prev), abs(low - prev)))
    if not trs:
        return None
    window = trs[-period:]
    try:
        last = float(candles[-1]["c"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if last <= 0:
        return None
    return (sum(window) / len(window)) / last


class PairsModule:
    def __init__(self, bot):
        self.bot = bot
        self.positions: List[PairPosition] = []
        self.closed: List[dict] = []
        self.stats = {"trades": 0, "wins": 0, "pnl": 0.0, "legs": 0}
        # How much of stats["pnl"] has actually been added to the bot's cash.
        # Kept apart from stats["pnl"] so a restart can credit whatever was
        # booked while the bot was down, and so a state file written by an older
        # build (no "credited" key at all) is reconciled instead of lost.
        self.credited = 0.0
        self.last_rebalance = 0.0
        self.last_ranking: List[dict] = []
        self.history: List[float] = []
        self.last_error = ""
        # Measured execution costs: what crossing the visible book would have
        # cost at the moment we decided. Never used to change a fill - paper
        # trading fills at the mid, and this experiment is frozen - only to
        # check the 0.23% model against the venue.
        self.cost_samples: List[dict] = []

    # ---------- instrumentation ----------
    def _journal(self, action, reason, symbol=None, **data):
        """Record a decision with the numbers behind it, if the bot keeps one."""
        journal = getattr(self.bot, "journal", None)
        if journal is None:
            return None
        try:
            return journal.add("pairs", action, reason, symbol=symbol,
                               key=action, **data)
        except Exception as exc:                      # never block trading
            logger.warning("journal failed: %s", exc)
            return None

    def round_trip_cost_pct(self):
        """Cost of one pair round trip as a percentage of one leg's notional.

        Four fills - open and close on both legs - each paying the taker fee and
        the modelled slippage. This is the number the strategy has to beat, and
        printing it next to the measured edge is the whole point: a 0.15% edge
        against a 0.23% cost is a losing strategy that a backtest can present as
        profitable if the cost line is left out.
        """
        return 4.0 * (config.TAKER_FEE + config.SLIPPAGE / 4.0) * 100.0

    def coverage_ratio(self):
        """Measured gross edge per closed pair divided by the round-trip cost.

        None until there is at least one closed pair: an invented prior would
        make the dashboard look confident about a number nobody measured.
        """
        gross = [float(c.get("long_ret") or 0.0) + float(c.get("short_ret") or 0.0)
                 for c in self.closed]
        if not gross:
            return None
        return coverage_x(sum(gross) / len(gross), self.round_trip_cost_pct())

    # ---------- execution cost measurement ----------
    def _book_cost(self, symbol, notional):
        """What a taker would actually pay to cross the book right now, in %.

        Paper trading fills at the last mid, so this never moves a fill. It
        records the venue's own prices: half-spread to cross plus the slippage
        of walking `notional` through the visible levels, separately for the buy
        and the sell side, taking the worse of the two. The per-fill cost is
        then fee + that number, and four such fills are one pair round trip -
        directly comparable with round_trip_cost_pct().
        """
        try:
            book = self.bot.client.l2_book(symbol)
        except Exception as exc:                     # venue slow is not fatal
            self.last_error = "book %s: %s" % (symbol, exc)
            return None
        if not book:
            return None
        bid, ask = book.get("bid_px"), book.get("ask_px")
        if not bid or not ask or bid <= 0 or ask <= 0 or ask < bid:
            return None
        mid = (bid + ask) / 2.0
        spread_pct = (ask - bid) / mid * 100.0

        def walk(levels):
            """VWAP and filled notional (USD) when eating `levels` from the top."""
            left, spent, qty = notional, 0.0, 0.0
            for px, sz in levels:
                if px <= 0 or sz <= 0:
                    continue
                use = min(left, px * sz)          # USD taken from this level
                if use <= 0:
                    continue
                spent += use
                qty += use / px                   # units bought/sold
                left -= use
                if left <= 1e-9:
                    break
            filled = notional - left
            return (spent / qty if qty > 0 else None), filled

        vwap_buy, fill_buy = walk(book.get("asks") or [])
        vwap_sell, fill_sell = walk(book.get("bids") or [])
        if not vwap_buy or not vwap_sell:
            return None
        slip_buy = (vwap_buy - mid) / mid * 100.0
        slip_sell = (mid - vwap_sell) / mid * 100.0
        slip_pct = max(slip_buy, slip_sell)
        fee_pct = config.TAKER_FEE * 100.0
        return {
            "symbol": symbol,
            "mid": mid,
            "spread_pct": round(spread_pct, 4),
            "half_spread_pct": round(spread_pct / 2.0, 4),
            "slip_pct": round(slip_pct, 4),
            "fill_pct": round(100.0 * min(fill_buy, fill_sell) / notional, 1)
            if notional > 0 else 0.0,
            "per_fill_pct": round(fee_pct + slip_pct, 4),
            "notional": round(notional, 2),
        }

    def _record_cost(self, symbol, stage, notional):
        """Fetch and remember one execution sample, never raising."""
        row = self._book_cost(symbol, notional)
        if row is None:
            return None
        row["stage"] = stage
        row["t"] = round(time.time(), 0)
        self.cost_samples.append(row)
        if len(self.cost_samples) > COST_SAMPLES_CAP:
            self.cost_samples = self.cost_samples[-COST_SAMPLES_CAP:]
        return row

    def _median(self, values):
        vals = sorted(v for v in values if isinstance(v, (int, float)))
        if not vals:
            return None
        n = len(vals)
        return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0

    def execution_stats(self):
        """Measured cost vs the model, in the same units as round_trip_cost_pct."""
        model = self.round_trip_cost_pct()
        rows = [r for r in self.cost_samples if isinstance(r.get("per_fill_pct"), (int, float))]
        if not rows:
            return {"samples": 0, "model_round_trip_pct": round(model, 3),
                    "measured_round_trip_pct": None,
                    "note": "no executions measured yet"}
        per_fill = self._median([r["per_fill_pct"] for r in rows])
        spread = self._median([r.get("spread_pct") for r in rows])
        slip = self._median([r.get("slip_pct") for r in rows])
        measured_rt = 4.0 * per_fill
        worst_rt = 4.0 * max(r["per_fill_pct"] for r in rows)
        full = [r for r in rows if (r.get("fill_pct") or 0) >= 99.0]
        return {
            "samples": len(rows),
            "model_round_trip_pct": round(model, 3),
            "measured_round_trip_pct": round(measured_rt, 3),
            "measured_worst_round_trip_pct": round(worst_rt, 3),
            "median_spread_pct": round(spread, 4) if spread is not None else None,
            "median_walk_slip_pct": round(slip, 4) if slip is not None else None,
            "median_per_fill_pct": round(per_fill, 4),
            "model_per_fill_pct": round(model / 4.0, 4),
            "ratio_vs_model": round(measured_rt / model, 2) if model else None,
            "depth_full_pct": round(100.0 * len(full) / len(rows), 1),
            "by_stage": {stage: sum(1 for r in rows if r.get("stage") == stage)
                         for stage in ("open_long", "open_short",
                                       "close_long", "close_short")},
        }

    # ---------- data ----------
    def _leg_stats(self, symbol, hours):
        """Return over `hours` plus the volatility that produced it.

        Both come from one candle fetch. Ranking on return alone hides the fact
        that the weakest coin is often the most violent one, and a pair of two
        violent legs is not market-neutral in any useful sense.
        """
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
        return {"symbol": symbol, "ret": last / first - 1.0,
                "atr_pct": _atr_pct(candles),
                "px": last}

    def _market_meta(self):
        """Funding and turnover per coin, or nothing if the venue is slow."""
        try:
            return self.bot.client.meta_and_ctxs() or {}
        except Exception as exc:
            self.last_error = "meta: %s" % exc
            return {}

    def rank(self):
        """Coins ordered by their lookback return, weakest first."""
        rows = []
        for symbol in self.bot.active_symbols:
            row = self._leg_stats(symbol, config.PAIRS_LOOKBACK_H)
            if row is None:
                continue
            rows.append(row)
        rows.sort(key=lambda row: row["ret"])
        return rows

    def _leg_context(self, row, meta):
        """The handful of numbers worth keeping about one leg."""
        m = meta.get(row["symbol"]) or {}
        funding = m.get("funding")
        return {
            "ret_pct": round(row["ret"] * 100.0, 2),
            "atr_pct": (round(row["atr_pct"] * 100.0, 2)
                        if row.get("atr_pct") is not None else None),
            "funding_bp_h": (round(float(funding) * 10000.0, 3)
                             if isinstance(funding, (int, float)) else None),
            "vol_usd": (round(float(m["day_volume_usd"]), 0)
                        if isinstance(m.get("day_volume_usd"), (int, float)) else None),
        }

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
            # A pair whose coins left the active universe has no last_price.
            # Dropping it here erased the trade from the statistics entirely,
            # so it stays open until a price can be found.
            if not self._close(pos, now):
                keep.append(pos)
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
        meta = self._market_meta()
        ranks = {row["symbol"]: i + 1 for i, row in enumerate(rows)}

        for weak, strong in zip(weakest, reversed(strongest)):
            lp = self.bot.last_prices.get(weak["symbol"])
            sp = self.bot.last_prices.get(strong["symbol"])
            if not lp or not sp or lp <= 0 or sp <= 0:
                continue
            long_ctx = self._leg_context(weak, meta)
            short_ctx = self._leg_context(strong, meta)
            context = {
                "long": long_ctx,
                "short": short_ctx,
                "long_rank": ranks.get(weak["symbol"]),
                "short_rank": ranks.get(strong["symbol"]),
                "universe": len(rows),
                "horizon_h": config.PAIRS_LOOKBACK_H,
                "cycle_h": config.PAIRS_HOLD_H,
            }
            self.positions.append(PairPosition(
                long_sym=weak["symbol"], short_sym=strong["symbol"],
                long_entry=lp, short_entry=sp, notional=per_leg,
                opened=now, due=now + config.PAIRS_HOLD_H * 3600,
                long_size=per_leg / lp, short_size=per_leg / sp,
                context=context))
            self._record_cost(weak["symbol"], "open_long", per_leg)
            self._record_cost(strong["symbol"], "open_short", per_leg)
            self.bot._event("PAIRS open: long %s / short %s ($%.0f per leg)" % (
                weak["symbol"], strong["symbol"], per_leg))
            self._journal(
                "open",
                "long %s %+.1f%% (rank %d) / short %s %+.1f%% (rank %d) of %d; "
                "atr %s/%s pct; funding %s/%s bp/h; $%.0f per leg" % (
                    weak["symbol"], long_ctx["ret_pct"], context["long_rank"],
                    strong["symbol"], short_ctx["ret_pct"], context["short_rank"],
                    len(rows), _n(long_ctx["atr_pct"]), _n(short_ctx["atr_pct"]),
                    _n(long_ctx["funding_bp_h"]), _n(short_ctx["funding_bp_h"]),
                    per_leg),
                symbol="%s/%s" % (weak["symbol"], strong["symbol"]),
                coverage_x=self.coverage_ratio(), notional=per_leg)
        logger.info("PAIRS rebalance: %d pair(s) opened, %d ranked" % (
            len(self.positions), len(rows)))
        self.bot._save_state(force=True)

    def _price(self, symbol):
        """Last price, falling back to the exchange for a coin that has left
        the active universe."""
        px = self.bot.last_prices.get(symbol)
        if px and px > 0:
            return px
        try:
            mids = self.bot.client.all_mids()
        except Exception:
            return None
        px = (mids or {}).get(symbol)
        return px if px and px > 0 else None

    def _close(self, pos, now):
        lp = self._price(pos.long_sym)
        sp = self._price(pos.short_sym)
        if not lp or not sp or lp <= 0 or sp <= 0:
            self.last_error = "cannot price %s/%s yet" % (pos.long_sym, pos.short_sym)
            return False
        cost = config.TAKER_FEE + config.SLIPPAGE / 4.0
        self._record_cost(pos.long_sym, "close_long", pos.notional)
        self._record_cost(pos.short_sym, "close_short", pos.notional)

        long_ret = lp / pos.long_entry - 1.0
        short_ret = 1.0 - sp / pos.short_entry
        gross = pos.notional * (long_ret + short_ret)
        fees = pos.notional * cost * (1.0 + lp / pos.long_entry
                                      + 1.0 + sp / pos.short_entry)
        net = gross - fees

        self.stats["trades"] += 1
        self.stats["legs"] += 2
        self.stats["pnl"] += net
        # A pair never reserved cash when it opened, so the whole round trip -
        # fees included - is booked here. Until this line existed the pairs
        # module kept its own scoreboard and the bot's equity never moved, which
        # made the frozen experiment impossible to measure against its 5% draw-
        # down hypothesis: the strategy could win or lose and the headline
        # capital stayed exactly where it was.
        self.bot.cash += net
        self.credited += net
        if net > 0:
            self.stats["wins"] += 1
        self.closed.append({
            "long": pos.long_sym, "short": pos.short_sym,
            "long_ret": round(long_ret * 100, 2), "short_ret": round(short_ret * 100, 2),
            "pnl": round(net, 2), "opened": pos.opened, "closed": now,
            "hours": round((now - pos.opened) / 3600.0, 1),
            "context": pos.context or {}})
        if len(self.closed) > 200:
            self.closed = self.closed[-200:]
        self.bot._event("PAIRS close: %s/%s pnl $%+.2f" % (
            pos.long_sym, pos.short_sym, net))
        gross_pct = (long_ret + short_ret) * 100.0
        self._journal(
            "close",
            "long %s %+.2f%% / short %s %+.2f%% = %+.2f%% gross, cost %.2f%%, "
            "net $%+.2f after %.0fh" % (
                pos.long_sym, long_ret * 100.0, pos.short_sym, short_ret * 100.0,
                gross_pct, self.round_trip_cost_pct(), net,
                (now - pos.opened) / 3600.0),
            symbol="%s/%s" % (pos.long_sym, pos.short_sym),
            pnl=net, gross_pct=gross_pct, coverage_x=self.coverage_ratio())
        logger.info("PAIRS close %s/%s: $%+.2f", pos.long_sym, pos.short_sym, net)
        return True

    def reconcile_cash(self):
        """Credit any realised pair PnL the cash never received.

        Runs after a restore. A state file written before pairs booked into cash
        carries a non-zero stats["pnl"] with no "credited" key, so the whole
        balance is credited once; afterwards the two agree and this is a no-op.
        """
        gap = self.stats["pnl"] - self.credited
        if abs(gap) > 1e-9:
            self.bot.cash += gap
            self.credited = self.stats["pnl"]
            logger.info("PAIRS reconciled $%+.2f of realised PnL into cash", gap)
            return gap
        return 0.0

    # ---------- reporting ----------
    def _pair_pnl(self, pos):
        """Unrealised PnL of one pair, nil if either price is missing."""
        lp = self.bot.last_prices.get(pos.long_sym)
        sp = self.bot.last_prices.get(pos.short_sym)
        if not lp or not sp:
            return 0.0
        return pos.notional * ((lp / pos.long_entry - 1.0)
                               + (1.0 - sp / pos.short_entry))

    def unrealized(self):
        total = 0.0
        return sum(self._pair_pnl(pos) for pos in self.positions)

    def next_rebalance_hours(self):
        if not self.last_rebalance:
            return 0.0
        left = self.last_rebalance + config.PAIRS_HOLD_H * 3600 - time.time()
        return max(0.0, round(left / 3600.0, 1))

    def get_stats(self):
        trades = self.stats["trades"]
        cost_pct = self.round_trip_cost_pct()
        gross = [float(c.get("long_ret") or 0.0) + float(c.get("short_ret") or 0.0)
                 for c in self.closed]
        gross_avg = (sum(gross) / len(gross)) if gross else None
        ratio = self.coverage_ratio()
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
            # Does the measured gross edge pay for the round trip, and by how
            # much? Reported, never acted on: changing behaviour mid-experiment
            # would invalidate the frozen comparison.
            "costs": {
                "cost_pct": round(cost_pct, 3),
                "gross_avg_pct": round(gross_avg, 3) if gross_avg is not None else None,
                "net_avg_pct": (round(gross_avg - cost_pct, 3)
                                if gross_avg is not None else None),
                "coverage_x": round(ratio, 2) if ratio is not None else None,
                "grade": coverage_grade(ratio),
                "samples": len(gross)},
            "execution": self.execution_stats(),
            "ranking": [{"symbol": r["symbol"], "ret_pct": round(r["ret"] * 100, 2)}
                        for r in self.last_ranking[:5]] +
                       [{"symbol": r["symbol"], "ret_pct": round(r["ret"] * 100, 2)}
                        for r in self.last_ranking[-3:]],
            "open_pairs": [{"long": p.long_sym, "short": p.short_sym,
                            "notional": round(p.notional, 2),
                            "long_entry": round(p.long_entry, 6),
                            "short_entry": round(p.short_entry, 6),
                            "long_px": round(self.bot.last_prices.get(p.long_sym) or 0, 6),
                            "short_px": round(self.bot.last_prices.get(p.short_sym) or 0, 6),
                            "pnl": round(self._pair_pnl(p), 2),
                            "hours": round((time.time() - p.opened) / 3600.0, 1),
                            "context": p.context or {}}
                           for p in self.positions],
            "recent": list(reversed(self.closed[-10:])),
        }
