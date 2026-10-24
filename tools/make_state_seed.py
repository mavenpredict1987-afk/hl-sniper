"""Rebuild a boot seed for the bot from a /export snapshot of the running one.

Why this exists: the Railway volume from the trial workspace is not reachable
from outside - there is no shell and no volume download on that plan. The only
thing we can still read out of the old bot is its HTTP surface, and /export
serves the dashboard payload (what the page shows), not the internal
state.json. This script turns that payload into a state_seed.json that
state_store.load() accepts, so a deployment on a fresh workspace resumes the
paper run instead of starting from 10 000 again.

What is exact, because /export carries it verbatim:
  cash, equity, peak, last prices, the 3 open pairs (legs, entries, notional,
  opened/due derived from the "hours" column), the 9 closed pairs with their
  contexts, equity history, events, journal entries, halted/day flags,
  consec_losses, win/loss counts, sniper counters.
What is an estimate, with the reason:
  win_sum/loss_sum - the internal counters cover all 76 trades, /export only
  the last 30, so the payoff ratio (average win / average loss) is preserved
  and the absolute scale is not. r_sum - average R is known, count is not, so
  r_sum = avg_r * len(trades). cooldowns/symbol_stops - short-lived anyway and
  not exported. sniper.execution_history - not exported; sniper is switched
  off. day_start_equity/day_stamp - the day rolls over at the next midnight.

Usage:
    python3 tools/make_state_seed.py snapshot.json [--out state_seed.json]

The output is written to <repo>/state_seed.json by default, which is where
state_store looks when the volume holds no state.json yet.
"""
import argparse
import json
import os
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

SCHEMA_VERSION = 1


def _num(x, default=0.0):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v


def _pair_positions(payload, hold_h, now):
    """Convert the display form of open pairs into PairPosition dicts."""
    out = []
    for raw in payload.get("open_pairs") or []:
        try:
            long_sym = str(raw["long"])
            short_sym = str(raw["short"])
            long_entry = _num(raw["long_entry"])
            short_entry = _num(raw["short_entry"])
            notional = _num(raw["notional"])
        except (KeyError, TypeError):
            continue
        if long_entry <= 0 or short_entry <= 0 or notional <= 0:
            continue
        hours = _num(raw.get("hours"), 0.0)
        opened = now - hours * 3600.0
        out.append({
            "long_sym": long_sym,
            "short_sym": short_sym,
            "long_entry": long_entry,
            "short_entry": short_entry,
            "notional": notional,
            "opened": opened,
            "due": opened + hold_h * 3600.0,
            "long_size": notional / long_entry,
            "short_size": notional / short_entry,
            "context": raw.get("context") or {},
        })
    return out


def _payoff_sums(trades, win_count, loss_count):
    """Scale the last-30 payoff ratio up to the full win/loss counts."""
    wins = [_num(t.get("r")) for t in trades if _num(t.get("r")) > 0]
    losses = [abs(_num(t.get("r"))) for t in trades if _num(t.get("r")) < 0]
    mean_win = sum(wins) / len(wins) if wins else 1.0
    mean_loss = sum(losses) / len(losses) if losses else 1.0
    return win_count * mean_win, loss_count * mean_loss


def build(snapshot, hold_h=48.0, capital=10000.0, now=None):
    now = now or time.time()
    pairs_payload = snapshot.get("pairs") or {}
    trades = list(snapshot.get("trades") or [])
    win_count = int(_num(snapshot.get("wins"), 0))
    loss_count = int(_num(snapshot.get("losses"), 0))
    win_sum, loss_sum = _payoff_sums(trades, win_count, loss_count)

    cost = pairs_payload.get("costs") or {}
    wins_pairs = int(_num(pairs_payload.get("wins"), 0))
    trades_pairs = int(_num(pairs_payload.get("trades"), 0))
    pnl_pairs = _num(pairs_payload.get("pnl"))
    hold = _num(pairs_payload.get("hold_h"), hold_h)
    hours_to_rebalance = _num(pairs_payload.get("next_rebalance_h"), 0.0)

    journal_entries = snapshot.get("journal") or []
    if isinstance(journal_entries, dict):
        journal_entries = journal_entries.get("entries") or []

    universe = snapshot.get("universe") or {}
    classes = universe.get("classes") or {}
    active = list(classes.keys()) or list((snapshot.get("prices") or {}).keys())

    sniper_stats = snapshot.get("sniper_stats") or {}
    equity = _num(snapshot.get("equity"), capital)
    day_pnl_pct = _num(snapshot.get("day_pnl_pct"), 0.0)
    day_start = equity - _num(snapshot.get("day_pnl"), 0.0)

    seed = {
        "schema": SCHEMA_VERSION,
        "saved_at": now,
        "mode": snapshot.get("mode") or "paper",
        "capital": capital,
        "cash": _num(snapshot.get("cash"), capital),
        "equity": equity,
        "peak": _num(snapshot.get("peak"), equity),
        "positions": {},
        "trades": trades,
        "events": list(snapshot.get("events") or []),
        "win_count": win_count,
        "loss_count": loss_count,
        "win_sum": round(win_sum, 4),
        "loss_sum": round(loss_sum, 4),
        "r_sum": round(_num(snapshot.get("avg_r")) * len(trades), 4),
        "cooldowns": {},
        "symbol_stops": {},
        "last_prices": dict(snapshot.get("prices") or {}),
        "last_candle_ts": {},
        "pairs": {
            "positions": _pair_positions(pairs_payload, hold, now),
            "closed": list(pairs_payload.get("recent") or []),
            "stats": {
                "trades": trades_pairs,
                "legs": trades_pairs * 2,
                "pnl": pnl_pairs,
                "wins": wins_pairs,
                "cost_pct": _num(cost.get("cost_pct")),
                "gross_avg_pct": _num(cost.get("gross_avg_pct")),
                "net_avg_pct": _num(cost.get("net_avg_pct")),
            },
            "credited": pnl_pairs,
            "last_rebalance": now - (hold - hours_to_rebalance) * 3600.0,
            "cost_samples": [],
        },
        "consec_losses": int(_num(snapshot.get("consec_losses"), 0)),
        "loss_pause_until": _num(snapshot.get("loss_pause_until"), 0.0),
        "equity_history": list(snapshot.get("equity_history") or []),
        "journal": {
            "entries": journal_entries,
            "seq": max([int(_num(e.get("seq"), 0)) for e in journal_entries] or [0]),
            "dropped": int(_num(snapshot.get("journal_dropped"), 0)),
        },
        "halted": bool(snapshot.get("halted", False)),
        "day_halt": bool(snapshot.get("day_halt", False)),
        "day_stamp": snapshot.get("day_stamp") or time.strftime("%Y-%m-%d"),
        "day_start_equity": day_start,
        "started": _num(snapshot.get("started"), now),
        "restarts": max(int(_num(snapshot.get("restarts"), 1)) - 1, 0),
        "active_symbols": active,
        "sniper": {
            "execution_history": [],
            "rejected": int(_num(sniper_stats.get("rejected"), 0)),
            "total_snipes": int(_num(sniper_stats.get("total_snipes"), 0)),
            "strong_snipes": int(_num(sniper_stats.get("strong_snipes"), 0)),
            "slippage_sum": 0.0,
            "slippage_n": 0,
        },
        "seed_meta": {
            "source": "snapshot /export",
            "generated_at": now,
            "generated_on": socket.gethostname(),
            "day_pnl_pct_at_export": day_pnl_pct,
            "exact": ["cash", "equity", "peak", "prices", "open pairs",
                      "closed pairs", "equity_history", "events", "journal",
                      "halted", "win/loss counts", "sniper counters"],
            "estimated": ["win_sum/loss_sum (payoff ratio kept, scale not)",
                          "r_sum (avg_r x len(trades))",
                          "cooldowns/symbol_stops (not exported)",
                          "sniper.execution_history (not exported, sniper off)"],
        },
    }
    return seed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshot")
    ap.add_argument("--out", default=os.path.join(REPO, "state_seed.json"))
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--hold-h", type=float, default=48.0)
    args = ap.parse_args(argv)

    with open(args.snapshot, encoding="utf-8") as fh:
        snapshot = json.load(fh)
    seed = build(snapshot, hold_h=args.hold_h, capital=args.capital)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(seed, fh, ensure_ascii=False, indent=1)
    pairs = seed["pairs"]
    print("seed -> %s" % args.out)
    print("  equity %.2f cash %.2f peak %.2f" % (seed["equity"], seed["cash"], seed["peak"]))
    print("  pairs: %d open, %d closed, pnl %.2f" % (
        len(pairs["positions"]), len(pairs["closed"]), pairs["stats"]["pnl"]))
    print("  trades %d, journal %d, equity points %d, coins %d" % (
        len(seed["trades"]), len(seed["journal"]["entries"]),
        len(seed["equity_history"]), len(seed["active_symbols"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
