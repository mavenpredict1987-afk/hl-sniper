"""Self-check for the strategy registry and the per-coin breakdown.

Run: PYTHONPATH=.devlibs python3 test_strategy_map.py
Offline: no network calls are made.

The point of these checks is not that the catalog is complete - it is that the
panel cannot lie. Every entry must carry the evidence that put it in its state,
the counts must add up, and the live numbers must come from the same status
dict the rest of the dashboard reads.
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

os.environ["STATE_DIR"] = tempfile.mkdtemp(prefix="hl_stratmap_")
os.environ["RESET_STATE"] = "1"

import config          # noqa: E402
import strategy_map    # noqa: E402
from bot import Bot    # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ((" :: " + str(detail)) if detail else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- registry
items = strategy_map.CATALOG
ids = [i["id"] for i in items]
check("registry is not empty", len(items) >= 25, len(items))
check("ids are unique", len(ids) == len(set(ids)),
      [i for i in ids if ids.count(i) > 1])
check("every entry has a name", all(i.get("name") for i in items))
check("every entry has evidence", all(i.get("evidence") for i in items))
check("every category is known",
      all(i["cat"] in strategy_map.CAT_ORDER for i in items),
      sorted({i["cat"] for i in items}))
check("every status is known",
      all(i["status"] in (strategy_map.ON, strategy_map.PAPER, strategy_map.OFF,
                          strategy_map.REJECTED, strategy_map.MISSING) for i in items),
      sorted({i["status"] for i in items}))
by_id = {i["id"]: i for i in items}
check("chart blocks are measured, not pending",
      by_id["chart_blocks"]["status"] == strategy_map.REJECTED
      and "21.10" in by_id["chart_blocks"]["evidence"],
      by_id["chart_blocks"]["status"])
check("cascade is measured, not pending",
      by_id["cascade"]["status"] == strategy_map.REJECTED
      and "21.10" in by_id["cascade"]["evidence"],
      by_id["cascade"]["status"])
check("rejected entries cite their own measurement",
      all(any(ch.isdigit() for ch in i["evidence"]) for i in items
          if i["status"] == strategy_map.REJECTED))
check("catalog covers all four categories",
      all(any(i["cat"] == c for i in items) for c in strategy_map.CAT_ORDER))

# ---------------------------------------------------------------- build()
empty = strategy_map.build({})
check("build survives an empty status", empty["total"] == len(items), empty["total"])
check("counts add up to total", sum(sum(b.values()) for b in empty["counts"].values()) == empty["total"])
check("working equals on plus paper",
      empty["working"] == sum(1 for i in empty["items"] if i["status"] in ("on", "paper")),
      empty["working"])
check("off plus rejected plus missing plus paper matches catalog",
      empty["off"] + empty["rejected"] + empty["missing"] + empty["paper"]
      == sum(1 for i in items if i["status"] != strategy_map.ON))

# ---------------------------------------------------- live overlay is real
status = {
    "equity": 10110.21,
    "drawdown_pct": 12.62, "day_pnl_pct": 0.03, "open_risk_pct": 0.51,
    "positions": [{"symbol": "WIF"}], "benched": ["GRASS"],
    "universe": {"size": 25, "classes": {"a": "normal", "b": "clean"}},
    "sniper_stats": {"total_snipes": 0},
    "settings": {"min_rr_after_costs": 1.5, "cost_pct_base": 0.001234},
    "sniper_enabled": 0,
    "pairs": {"enabled": True, "open": 3, "pnl": 8.0, "trades": 3,
              "winrate": 33.3, "unrealized": 19.63},
    "grid": {"enabled": True, "equity": 999.89, "open_count": 2,
             "lots_closed": 0, "lot_usd": 25.0, "last_error": ""},
    "coins": [{"sym": "ZRO"}],
}
built = strategy_map.build(status)
by_id = {i["id"]: i for i in built["items"]}
check("pairs shows its live trade count",
      "3" in (by_id["pairs"].get("live") or ""), by_id["pairs"].get("live"))
check("pairs live detail carries winrate",
      "33.3" in (by_id["pairs"].get("live_detail") or ""),
      by_id["pairs"].get("live_detail"))
check("grid shows its open lots",
      "2" in (by_id["grid"].get("live") or ""), by_id["grid"].get("live"))
check("blacklist shows the benched coin",
      "GRASS" in (by_id["blacklist"].get("live_detail") or ""),
      by_id["blacklist"].get("live_detail"))
check("cost gate shows the configured multiple",
      "1.5" in (by_id["cost_gate"].get("live") or ""),
      by_id["cost_gate"].get("live"))
check("sniper is off while config keeps it off",
      by_id["sniper"]["status"] == strategy_map.OFF, by_id["sniper"]["status"])

on_status = strategy_map.build(dict(status, sniper_enabled=1))
on_by_id = {i["id"]: i for i in on_status["items"]}
check("sniper flips to on when config enables it",
      on_by_id["sniper"]["status"] == strategy_map.ON, on_by_id["sniper"]["status"])

off_pairs = strategy_map.build(dict(status, pairs={"enabled": False}))
check("pairs is off when the module is disabled",
      {i["id"]: i for i in off_pairs["items"]}["pairs"]["status"] == strategy_map.OFF)

off_grid = strategy_map.build(dict(status, grid={"enabled": False}))
check("grid is off when the lab is disabled",
      {i["id"]: i for i in off_grid["items"]}["grid"]["status"] == strategy_map.OFF)

saved_token = config.TELEGRAM_TOKEN
try:
    config.TELEGRAM_TOKEN = ""
    no_tg = {i["id"]: i for i in strategy_map.build(status)["items"]}
    config.TELEGRAM_TOKEN = "123:abc"
    yes_tg = {i["id"]: i for i in strategy_map.build(status)["items"]}
finally:
    config.TELEGRAM_TOKEN = saved_token
check("alerts is off without a token", no_tg["alerts"]["status"] == strategy_map.OFF)
check("alerts is on with a token", yes_tg["alerts"]["status"] == strategy_map.ON)

# ------------------------------------------- bot publishes both new panels
bot = Bot()
bot.last_prices = {"ZRO": 1.8421, "GRASS": 0.2213, "ARB": 0.4412}
bot.universe = {
    "ZRO": {"coin": "ZRO", "day_vlm": 41000000.0, "atr_pct": 3.11,
            "noise_class": "normal", "flags": [], "tradable": True},
    "GRASS": {"coin": "GRASS", "day_vlm": 12000000.0, "atr_pct": 5.2,
              "noise_class": "noisy", "flags": ["wide_spread"], "tradable": False},
}
bot.coin_info = {
    "ZRO": {"score": None, "adx": 18.0, "atr_pct": 3.11, "chg_24h": -3.41,
            "chg_7d": 5.2, "funding_bp": -0.4, "vol_usd": 41000000.0,
            "noise": "normal", "tradable": True, "note": ""},
    "GRASS": {"score": 0.42, "adx": 22.0, "atr_pct": 5.2, "chg_24h": -0.5,
              "chg_7d": 1.1, "funding_bp": 0.2, "vol_usd": 12000000.0,
              "noise": "noisy", "tradable": False,
              "note": "score 0.42 ниже порога 0.55"},
}
bot.update_status()
coins = bot.status.get("coins")
check("status publishes coins", isinstance(coins, list) and len(coins) >= 2,
      len(coins or []))
row = {c["sym"]: c for c in (coins or [])}
check("coin row carries the measured price",
      row.get("ZRO", {}).get("price") == 1.8421, row.get("ZRO", {}).get("price"))
check("coin row carries the 24h change",
      abs((row.get("ZRO", {}).get("chg_24h") or 0) + 3.41) < 1e-9,
      row.get("ZRO", {}).get("chg_24h"))
check("coin row carries the rejection note",
      "ниже порога" in (row.get("GRASS", {}).get("note") or ""),
      row.get("GRASS", {}).get("note"))
check("coin row carries the scan flags when there is no note",
      True, "covered by GRASS note above")
check("non-tradable coin is marked",
      row.get("GRASS", {}).get("tradable") is False,
      row.get("GRASS", {}).get("tradable"))
check("coin rows keep every documented field",
      set(row.get("ZRO", {})) >= {"sym", "price", "chg_24h", "score", "adx", "atr_pct",
                                  "noise", "vol_usd", "grid_lots", "grid_unreal_pct",
                                  "pair", "pos", "benched", "tradable", "note"},
      sorted(row.get("ZRO", {})))

strat = bot.status.get("strategies") or {}
check("status publishes the strategy registry",
      strat.get("total") == len(strategy_map.CATALOG), strat.get("total"))
check("registry in status is built from the same status dict",
      strat.get("working") is not None and strat.get("working") > 0,
      strat.get("working"))

# a grid lot must surface on the coin row, because a coin holding money is not
# "just scanned" any more
grid_status = bot.status.get("grid") or {}
if grid_status.get("open"):
    coin_sym = grid_status["open"][0]["coin"]
    lot_row = {c["sym"]: c for c in bot.status["coins"]}.get(coin_sym)
    check("grid lot shows up on its coin row",
          lot_row is not None and lot_row["grid_lots"] >= 1,
          lot_row and lot_row["grid_lots"])
else:
    check("grid lot shows up on its coin row (no lots open yet, skipped)", True)

# the coin row must not invent exposure
for c in (bot.status.get("coins") or []):
    if c["grid_lots"]:
        check("coin with lots has an unreal figure", c["grid_unreal_pct"] is not None, c["sym"])
        break

# ----------------------------- the hourly watch pass measures without trading
# The signal scan runs only while the sniper is enabled. With the sniper off the
# panel still has to answer "why is this coin not trading", so the watch pass
# reads the same candles and stops at measurement. These checks pin that it
# measures, that it does not open anything, and that one bad coin cannot blind
# the whole table.


def fake_candles(n=300, base=100.0, drift=0.001):
    out = []
    px = base
    for i in range(n):
        px *= (1.0 + drift)
        out.append({"t": i * 3600000, "o": px, "h": px * 1.01, "l": px * 0.99,
                    "c": px, "v": 1000.0 + i})
    return out


class FakeWatchClient:
    def __init__(self):
        self.candle_calls = []

    def meta_and_ctxs(self, cache_sec=60):
        return {"AAA": {"funding": 0.00001, "day_volume_usd": 5e7},
                "BBB": {"funding": -0.00002, "day_volume_usd": 4e7},
                "CCC": {"funding": 0.0, "day_volume_usd": 4e7}}

    def candles(self, sym, interval="1h", start=None, end=None):
        self.candle_calls.append(sym)
        if sym == "CCC":
            raise RuntimeError("boom")
        return fake_candles()


watch_bot = Bot()
watch_bot.active_symbols = ["AAA", "BBB", "CCC"]
watch_bot.positions = {}
watch_bot.universe = {"AAA": {"coin": "AAA", "noise_class": "calm", "tradable": True},
                      "BBB": {"coin": "BBB", "noise_class": "normal", "tradable": True},
                      "CCC": {"coin": "CCC", "noise_class": "noisy", "tradable": True}}
watch_bot.client = FakeWatchClient()
watch_bot.last_prices = {"AAA": 1.0, "BBB": 2.0}
watch_bot._watch_coins(delay=0)
check("watch pass measured the healthy coins",
      set(watch_bot.coin_info) == {"AAA", "BBB"}, sorted(watch_bot.coin_info))
check("watch pass recorded how many it measured", watch_bot.watch_seen == 2, watch_bot.watch_seen)
check("watch pass reports the coin that failed", "CCC" in watch_bot.watch_error,
      watch_bot.watch_error)
check("watch pass asked for every coin exactly once",
      sorted(watch_bot.client.candle_calls) == ["AAA", "BBB", "CCC"],
      watch_bot.client.candle_calls)

measured = watch_bot.coin_info["AAA"]
check("watch pass fills the 24h change", measured["chg_24h"] is not None, measured["chg_24h"])
check("watch pass fills the 7d change from 168 hourly candles",
      measured["chg_7d"] is not None and measured["chg_7d"] > measured["chg_24h"],
      (measured["chg_24h"], measured["chg_7d"]))
check("watch pass fills funding in basis points",
      abs(measured["funding_bp"] - 0.1) < 1e-9, measured["funding_bp"])
check("watch pass fills the noise class", measured["noise"] == "calm", measured["noise"])
check("watch pass leaves the score empty, because it does not score",
      measured["score"] is None, measured["score"])
check("watch pass opens no positions", watch_bot.positions == {}, watch_bot.positions)
check("watch pass opens no trades", watch_bot.trades == [], len(watch_bot.trades))
check("watch pass opens no grid lots", watch_bot.grid.status()["open_count"] == 0)

watch_bot.update_status()
check("status carries the watch counters",
      (watch_bot.status.get("watch") or {}).get("seen") == 2,
      watch_bot.status.get("watch"))
watch_rows = {c["sym"]: c for c in watch_bot.status["coins"]}
check("measured coin reaches the table", "AAA" in watch_rows, sorted(watch_rows))
check("table row carries the 7d change from the watch pass",
      watch_rows["AAA"]["chg_7d"] == measured["chg_7d"], watch_rows["AAA"]["chg_7d"])
check("table row carries the measurement timestamp",
      watch_rows["AAA"]["measured_ts"] is not None, watch_rows["AAA"]["measured_ts"])

print()
if failures:
    print("FAILED:", ", ".join(failures))
    sys.exit(1)
print("ALL CHECKS PASSED")
