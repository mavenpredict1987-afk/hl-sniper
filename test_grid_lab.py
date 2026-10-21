"""Self-check for grid_lab.py - the paper-only grid arm.

Run: PYTHONPATH=.devlibs python3 test_grid_lab.py
Offline: candles come from a fake fetcher, the clock from a fake clock.

What matters here is not that the strategy is good, it is that the module is
safe and honest: it must never raise into the trading loop, it must not be able
to place an order, it must not assume an exit inside the entry bar, it must
survive a restart without losing or duplicating lots, and its equity must be
marked to market instead of being computed from closed trades only.
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import config                                            # noqa: E402
import grid_lab                                          # noqa: E402

failures = []
HOUR = 3600
BASE_TS = 1790000000


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ((" :: " + str(detail)) if detail else ""))
    if not cond:
        failures.append(name)


class FakeJournal:
    def __init__(self):
        self.rows = []

    def add(self, module, action, reason, key=None, **data):
        self.rows.append({"module": module, "action": action, "reason": reason, "data": data})


class FakeClient:
    """Deliberately has no order methods: if grid_lab ever tried to trade, the
    test would fail with AttributeError instead of quietly working."""

    def __init__(self):
        self.candles = {}
        self.funding = {}
        self.calls = []

    def candles_recent(self, coin, interval, bars):
        self.calls.append((coin, interval, bars))
        return list(self.candles.get(coin, []))[-bars:]

    def meta_and_ctxs(self, cache_sec=60):
        return {c: {"funding": f} for c, f in self.funding.items()}


class FakeBot:
    def __init__(self):
        self.client = FakeClient()
        self.journal = FakeJournal()


class Clock:
    def __init__(self, now=BASE_TS):
        self.now = now

    def __call__(self):
        return self.now


def mk(ts, close, high=None, low=None):
    high = close * 1.004 if high is None else high
    low = close * 0.996 if low is None else low
    return {"t": ts, "o": close, "h": high, "l": low, "c": close, "v": 100.0}


def flat(n=30, close=100.0, start_ts=BASE_TS):
    return [mk(start_ts + i * HOUR, close) for i in range(n)]


def lab_for(bot, clock, universe=("AAA",), name="s1"):
    config.STATE_DIR = os.path.join(tmpdir, name)
    os.makedirs(config.STATE_DIR, exist_ok=True)
    config.GRID_LAB_ENABLED = True
    config.GRID_LAB_UNIVERSE = list(universe)
    config.GRID_LAB_SEC = 3600
    return grid_lab.GridLab(bot, clock=clock)


tmpdir = tempfile.mkdtemp(prefix="gridlab_")

# ---------------------------------------------------------------- pure pieces
check("lot size is 2.5% of equity", grid_lab.lot_usd(1000.0) == 25.0)
check("lot size never goes to zero", grid_lab.lot_usd(10.0) == 5.0)
check("stop distance is 20%", grid_lab.STOP_FROM_ENTRY == 0.20)
check("cheap gate is 1.5%", grid_lab.STEP_PCT == 0.015)

closed = [{"usd": 0.24}, {"usd": 0.24}, {"usd": -5.0}]
m = grid_lab.metrics(closed, [1000.0, 1010.0, 980.0, 1000.0])
check("metrics count lots", m["lots_closed"] == 3)
check("metrics winrate", abs(m["winrate_pct"] - 66.7) < 0.1, m["winrate_pct"])
check("metrics profit factor", abs(m["profit_factor"] - 0.096) < 0.01, m["profit_factor"])
check("metrics expectancy is per lot", abs(m["expectancy_pct_per_lot"] - (100.0 * (-4.52) / 75.0)) < 1e-4,
      m["expectancy_pct_per_lot"])
check("metrics realized sum", m["realized_usd"] == -4.52)
check("metrics mtm drawdown from the curve", abs(m["mtm_max_dd_pct"] - 2.97) < 0.05, m["mtm_max_dd_pct"])
check("metrics time below start", abs(m["pct_time_below_start"] - 20.0) < 0.1, m["pct_time_below_start"])
check("metrics without losses has no pf", grid_lab.metrics([{"usd": 1.0}], [1000.0])["profit_factor"] is None)

# ---------------------------------------------------------------- entry logic
bot, clock = FakeBot(), Clock()
bot.client.candles["AAA"] = flat(30)
lab = lab_for(bot, clock)
entry_bar_ts = BASE_TS + 30 * HOUR
bot.client.candles["AAA"].append(mk(entry_bar_ts, 99.0, high=200.0, low=98.5))
lab.refresh(clock())
centre = max(b["h"] for b in bot.client.candles["AAA"][-25:-1])
check("centre is the 24h high", abs(centre - 100.4) < 1e-9, centre)
check("one lot opened on the first level", len(lab.state["open"]) == 1, len(lab.state["open"]))
pos = lab.state["open"][0]
check("entry price is the level", abs(pos["entry"] - 100.4 * 0.985) < 1e-6, pos["entry"])
check("stop is 20% below the entry", abs(pos["stop"] - pos["entry"] * 0.8) < 1e-9)
check("entry fee is charged once", abs(lab.state["entry_fees"] - 25.0 * grid_lab.MAKER_FEE) < 1e-9,
      lab.state["entry_fees"])
check("open journal row written", any(r["action"] == "open" for r in bot.journal.rows))

# no exit inside the bar that filled the entry, even though that bar traded
# through the target: assuming a V-reversal in the entry hour is what inflated
# the first version of the backtest
lab.refresh(clock())
check("no exit on the entry bar", len(lab.state["open"]) == 1 and not lab.state["closed"],
      (len(lab.state["open"]), len(lab.state["closed"])))

# next bar: target and partial are taken together, stop is not touched
bot.client.candles["AAA"].append(mk(entry_bar_ts + HOUR, 99.6, high=1e6, low=99.5))
lab.refresh(clock())
check("target closes the lot", len(lab.state["closed"]) == 1, len(lab.state["closed"]))
rec = lab.state["closed"][0]
check("target recorded as target", rec["why"] == "target", rec["why"])
expected = (25.0 * 0.5 * 0.005 - 12.5 * grid_lab.TAKER_FEE) + (12.5 * 0.015 - 12.5 * grid_lab.TAKER_FEE)
check("target cash = partial + rest", abs(rec["usd"] - expected) < 1e-4, (rec["usd"], expected))
check("partial journal row written", any(r["action"] == "partial" for r in bot.journal.rows))
check("no lots left open", not lab.state["open"])

# ---------------------------------------------------------------- stop logic
bot2, clock2 = FakeBot(), Clock(BASE_TS + 100 * HOUR)
bot2.client.candles["AAA"] = flat(30, start_ts=BASE_TS + 40 * HOUR)
lab2 = lab_for(bot2, clock2, name="s2")
bar_ts = BASE_TS + 70 * HOUR
bot2.client.candles["AAA"].append(mk(bar_ts, 99.0, high=200.0, low=98.5))
lab2.refresh(clock2())
p2 = lab2.state["open"][0]
bot2.client.candles["AAA"].append(mk(bar_ts + HOUR, p2["entry"] * 0.5, high=p2["entry"] * 1.2,
                                     low=p2["entry"] * 0.5))
lab2.state["open"] = [p2]                       # keep the scenario to one lot
lab2.refresh(clock2())
check("stop closes the lot", len(lab2.state["closed"]) == 1)
check("stop wins over a target in the same bar", lab2.state["closed"][0]["why"] == "stop",
      lab2.state["closed"][0]["why"])
loss = lab2.state["closed"][0]["usd"]
check("stop loss is about -20% of the lot", -5.2 < loss < -4.9, loss)

# ---------------------------------------------------------------- caps
bot3, clock3 = FakeBot(), Clock(BASE_TS + 200 * HOUR)
bot3.client.candles["AAA"] = flat(30, start_ts=BASE_TS + 150 * HOUR)
bot3.client.candles["BBB"] = flat(30, start_ts=BASE_TS + 150 * HOUR)
lab3 = lab_for(bot3, clock3, universe=("AAA", "BBB"), name="s3")
for coin in ("AAA", "BBB"):
    bot3.client.candles[coin].append(mk(BASE_TS + 180 * HOUR, 99.0, high=99.2, low=97.0))
lab3.refresh(clock3())
per_coin = {}
for p in lab3.state["open"]:
    per_coin[p["coin"]] = per_coin.get(p["coin"], 0) + 1
check("two levels per coin fill", per_coin == {"AAA": 2, "BBB": 2}, per_coin)
for coin in ("AAA", "BBB"):
    bot3.client.candles[coin].append(mk(BASE_TS + 181 * HOUR, 99.0, high=99.2, low=90.0))
lab3.refresh(clock3())
per_coin = {}
for p in lab3.state["open"]:
    per_coin[p["coin"]] = per_coin.get(p["coin"], 0) + 1
check("per-coin cap holds", max(per_coin.values()) <= grid_lab.MAX_PER_COIN, per_coin)
check("total cap holds", len(lab3.state["open"]) <= grid_lab.MAX_LOTS, len(lab3.state["open"]))

# ---------------------------------------------------------------- funding
bot4, clock4 = FakeBot(), Clock(BASE_TS + 300 * HOUR)
bot4.client.candles["AAA"] = flat(30, start_ts=BASE_TS + 250 * HOUR)
bot4.client.funding["AAA"] = 0.0001
lab4 = lab_for(bot4, clock4, name="s4")
bot4.client.candles["AAA"].append(mk(BASE_TS + 280 * HOUR, 99.0, high=99.2, low=98.5))
lab4.refresh(clock4())
one_hour = 25.0 * 0.0001
check("funding is not charged for the fill hour itself", abs(lab4.state["funding"]) < 1e-12,
      lab4.state["funding"])
lab4.refresh(clock4() + HOUR)
check("funding charged on the open notional", abs(lab4.state["funding"] - one_hour) < 1e-12,
      lab4.state["funding"])
lab4.refresh(clock4() + 2 * HOUR)
check("funding accumulates hourly", abs(lab4.state["funding"] - 2 * one_hour) < 1e-12, lab4.state["funding"])
bot4.client.funding["AAA"] = -0.0001
lab4.refresh(clock4() + 3 * HOUR)
check("negative funding is a credit", abs(lab4.state["funding"] - one_hour) < 1e-12, lab4.state["funding"])

# ---------------------------------------------------------------- equity identity
s = lab4.state
manual = grid_lab.START_EQUITY - s["entry_fees"] - s["funding"]
manual += sum(c["usd"] for c in s["closed"])
lot = grid_lab.lot_usd(grid_lab.START_EQUITY)
for p in s["open"]:
    last = s["last_close"].get(p["coin"], p["entry"])
    manual += p["cash"] + lot * p["left"] * (last / p["entry"] - 1.0)
check("equity is cash + fees + funding + open mark", abs(lab4.equity() - manual) < 1e-6,
      (lab4.equity(), manual))
st = lab4.status()
check("equity is exposed in status", abs(st["equity"] - lab4.equity()) < 0.02, (st["equity"], lab4.equity()))
check("status shows open risk", st["open_risk_usd"] > 0 and st["open_risk_pct"] > 0)
check("open risk is 0.5% per lot", abs(st["open_risk_pct"] - 100.0 * st["open_risk_usd"] / st["equity"]) < 0.01)

# ---------------------------------------------------------------- restart
config.STATE_DIR = os.path.join(tmpdir, "s4")
lab5 = grid_lab.GridLab(bot4, clock=clock4)
check("restart restores the arm", lab5.restored is True)
check("restart keeps open lots", len(lab5.state["open"]) == len(lab4.state["open"]))
check("restart keeps the curve", len(lab5.state["curve"]) == len(lab4.state["curve"]))
check("restart keeps costs", abs(lab5.state["funding"] - s["funding"]) < 1e-12)
check("restart keeps the equity", abs(lab5.equity() - lab4.equity()) < 1e-6)
lab5.refresh(clock4() + 4 * HOUR)
check("after restart the lot is not duplicated", len(lab5.state["open"]) == len(lab4.state["open"]),
      (len(lab5.state["open"]), len(lab4.state["open"])))

# ---------------------------------------------------------------- resilience
bot6, clock6 = FakeBot(), Clock(BASE_TS + 400 * HOUR)
bot6.client.candles["AAA"] = []


def exploding_fetch(coin, bars):
    raise RuntimeError("api is down")


config.STATE_DIR = os.path.join(tmpdir, "s6")
os.makedirs(config.STATE_DIR, exist_ok=True)
lab6 = grid_lab.GridLab(bot6, clock=clock6, fetch=exploding_fetch, funding_fn=lambda: {})
lab6.on_tick()
check("a failing fetch does not raise", True)
check("a failing fetch is reported", "api is down" in lab6.last_error, lab6.last_error)
check("a failing fetch does not open anything", not lab6.state["open"])
lab6.on_tick(force=True)
check("the second failure still does not raise", "api is down" in lab6.last_error)

bot7, clock7 = FakeBot(), Clock(BASE_TS + 500 * HOUR)
bot7.client.candles["AAA"] = flat(30, start_ts=BASE_TS + 450 * HOUR)
lab7 = lab_for(bot7, clock7, name="s7")
lab7.enabled = False
lab7.on_tick(force=True)
check("disabled arm does nothing", lab7.state["hours"] == 0)

config.GRID_LAB_ENABLED = True
lab8 = lab_for(bot7, clock7, name="s7b")
lab8.on_tick()
hours1 = lab8.state["hours"]
lab8.on_tick()
check("hourly gate prevents a second run", lab8.state["hours"] == hours1, lab8.state["hours"])

# corrupt state must not crash the bot
config.STATE_DIR = os.path.join(tmpdir, "s8")
os.makedirs(config.STATE_DIR, exist_ok=True)
with open(os.path.join(config.STATE_DIR, grid_lab.STATE_FILE), "w") as fh:
    fh.write("{not json")
lab9 = grid_lab.GridLab(bot7, clock=clock7)
check("corrupt state is survivable", lab9.restored is False and lab9.state["open"] == [])
check("corrupt state reports the error", bool(lab9.last_error))

# the module cannot reach the exchange
check("fake client has no order methods", not any("order" in a for a in dir(FakeClient())))

# status contract used by the dashboard and by the pre-registration
required = {"enabled", "restored", "last_run", "hours", "lot_usd", "open_count", "open_notional_usd",
            "open_risk_usd", "open_risk_pct", "open", "by_coin", "lots_closed", "winrate_pct",
            "profit_factor", "expectancy_pct_per_lot", "realized_usd", "costs_usd",
            "mtm_max_dd_pct", "pct_time_below_start", "equity", "last_error"}
missing = required - set(lab4.status())
check("status exposes the pre-registered metrics", not missing, missing)

# documented constants must stay in the range that the pre-registration froze
check("max lots is 6", grid_lab.MAX_LOTS == 6)
check("lot fraction keeps the risk at half a percent", abs(grid_lab.LOT_FRAC / grid_lab.STOP_FROM_ENTRY - 0.125) < 1e-9)
check("universe has at least 20 coins", len(grid_lab.DEFAULT_UNIVERSE) >= 20, len(grid_lab.DEFAULT_UNIVERSE))

shutil.rmtree(tmpdir, ignore_errors=True)

print()
if failures:
    print("FAILED: %d" % len(failures))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
