"""Checks that a /export snapshot turns into a bootable state seed.

The migration off the trial workspace depends on this: the old volume is not
reachable, so /export is the only surviving copy of the paper run. If this
mapper silently drops the open pairs or mangles the equity, the new deployment
starts from zero and nobody notices until the A/B window opens on 01.11.
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))

os.environ["STATE_DIR"] = tempfile.mkdtemp(prefix="hl_seed_")

import make_state_seed  # noqa: E402

failures = []


def check(name, cond, detail=""):
    if cond:
        print("ok   %s" % name)
    else:
        print("FAIL %s %s" % (name, detail))
        failures.append(name)


SNAPSHOT = {
    "mode": "paper",
    "cash": 10008.5,
    "equity": 10094.19,
    "peak": 11569.82,
    "wins": 25,
    "losses": 51,
    "avg_r": -0.1,
    "day_pnl": -3.5,
    "day_pnl_pct": -0.03,
    "halted": False,
    "day_halt": False,
    "consec_losses": 1,
    "loss_pause_until": 0.0,
    "started": 1000.0,
    "restarts": 64,
    "prices": {"BTC": 61000.0, "XMR": 578.0, "ZRO": 1.69},
    "universe": {"size": 2, "classes": {"BTC": "calm", "XMR": "normal"}},
    "sniper_stats": {"total_snipes": 517, "rejected": 139, "strong_snipes": 2},
    "trades": [
        {"symbol": "UNI", "pnl": -12.77, "r": -1.07},
        {"symbol": "ADA", "pnl": 8.0, "r": 0.8},
        {"symbol": "XMR", "pnl": 4.0, "r": 0.4},
    ],
    "events": [{"ts": 1792810434.6, "msg": "watch: measured 25 coins"}],
    "equity_history": [[1792810357.7, 10097.07], [1792810360.2, 10097.07]],
    "journal": [{"seq": 214, "ts": 1792772036.8, "module": "grid",
                 "action": "open", "k": "grid:open:XRP"}],
    "journal_dropped": 0,
    "pairs": {
        "enabled": True,
        "trades": 9,
        "wins": 4,
        "winrate": 44.4,
        "pnl": 26.05,
        "hold_h": 48,
        "next_rebalance_h": 24.7,
        "open_pairs": [
            {"long": "ZRO", "short": "XMR", "notional": 337.03,
             "long_entry": 1.77805, "short_entry": 607.795,
             "long_px": 1.69095, "short_px": 578.835, "pnl": -0.45,
             "hours": 23.3, "context": {"long_rank": 1}},
            {"long": "OP", "short": "BNB", "notional": 337.03,
             "long_entry": 0.4, "short_entry": 600.0, "hours": 2.0},
        ],
        "recent": [
            {"long": "JUP", "short": "XMR", "pnl": -15.61,
             "opened": 1792557084.9, "closed": 1792729886.9, "hours": 48.0},
        ],
        "costs": {"cost_pct": 0.23, "gross_avg_pct": 1.089, "net_avg_pct": 0.859},
    },
}


def main():
    seed = make_state_seed.build(SNAPSHOT, hold_h=48.0, capital=10000.0, now=2_000_000_000.0)

    check("cash/equity/peak carried", seed["cash"] == 10008.5
          and seed["equity"] == 10094.19 and seed["peak"] == 11569.82)
    check("both open pairs kept", len(seed["pairs"]["positions"]) == 2,
          str(seed["pairs"]["positions"]))
    first = seed["pairs"]["positions"][0]
    check("open pair fields map", first["long_sym"] == "ZRO" and first["short_sym"] == "XMR"
          and first["long_entry"] == 1.77805 and first["notional"] == 337.03)
    check("sizes derive from notional/entry",
          abs(first["long_size"] - 337.03 / 1.77805) < 1e-9)
    check("opened from hours, due = opened + hold",
          abs(first["opened"] - (2_000_000_000.0 - 23.3 * 3600)) < 1e-6
          and abs(first["due"] - first["opened"] - 48 * 3600) < 1e-6)
    check("closed pairs carried", len(seed["pairs"]["closed"]) == 1)
    check("pairs stats", seed["pairs"]["stats"]["trades"] == 9
          and seed["pairs"]["stats"]["pnl"] == 26.05
          and seed["pairs"]["stats"]["legs"] == 18)
    check("credited equals realised pairs pnl", seed["pairs"]["credited"] == 26.05)
    check("rebalance schedule keeps hours left",
          abs(seed["pairs"]["last_rebalance"] - (2_000_000_000.0 - 23.3 * 3600)) < 1e-6)
    check("win/loss counts exact", seed["win_count"] == 25 and seed["loss_count"] == 51)
    check("payoff sums positive", seed["win_sum"] > 0 and seed["loss_sum"] > 0)
    check("r_sum = avg_r * trades", seed["r_sum"] == -0.3, str(seed["r_sum"]))
    check("journal wrapped for from_state",
          isinstance(seed["journal"], dict) and len(seed["journal"]["entries"]) == 1
          and seed["journal"]["seq"] == 214)
    check("equity history kept", seed["equity_history"] == SNAPSHOT["equity_history"])
    check("active symbols from universe classes",
          seed["active_symbols"] == ["BTC", "XMR"], str(seed["active_symbols"]))
    check("restarts not double counted", seed["restarts"] == 63, str(seed["restarts"]))
    check("day start equity backs out day pnl",
          abs(seed["day_start_equity"] - (10094.19 + 3.5)) < 1e-9)
    check("sniper counters carried", seed["sniper"]["total_snipes"] == 517
          and seed["sniper"]["rejected"] == 139)
    check("schema and meta present", seed["schema"] == 1
          and seed["seed_meta"]["source"] == "snapshot /export")

    # The seed has to survive what state_store.load() does to it: a JSON round
    # trip, nothing more.
    path = os.path.join(tempfile.mkdtemp(prefix="hl_seed_json_"), "seed.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(seed, fh)
    with open(path, encoding="utf-8") as fh:
        again = json.load(fh)
    check("json round trip", again["equity"] == seed["equity"]
          and len(again["pairs"]["positions"]) == 2)

    # A snapshot with no open pairs must not invent any.
    empty = json.loads(json.dumps(SNAPSHOT))
    empty["pairs"]["open_pairs"] = []
    empty["pairs"]["recent"] = []
    seed2 = make_state_seed.build(empty, now=2_000_000_000.0)
    check("empty pairs stay empty", seed2["pairs"]["positions"] == []
          and seed2["pairs"]["closed"] == [])

    if failures:
        print("FAILED: %s" % ", ".join(failures))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
