"""Self-check for regime_lab.py - the paper-only regime laboratory.

Run: PYTHONPATH=.devlibs python3 test_regime_lab.py
Offline: candles come from a fake fetcher, the clock from a fake clock.

The properties that matter are the ones that would make this module dangerous:
it must never raise into the trading loop, it must never be able to touch money,
it must survive a restart without losing or duplicating its arms, and the control
arm must actually be a control (no regime filter) rather than a copy of the cell.
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import regime_lab                                        # noqa: E402
import state_store                                       # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ((" :: " + str(detail)) if detail else ""))
    if not cond:
        failures.append(name)


def bar(ts, close, spread_pct=0.01):
    half = close * spread_pct / 2.0
    return {"t": ts, "o": close, "h": close + half, "l": close - half,
            "c": close, "v": 1000.0}


HOUR = 3600000
BASE_TS = 1790000000000


def trending_bars(n=780, start=100.0, step=0.02, spread_pct=0.01, turb_after=None,
                  turb_spread=0.03):
    """Monotonic rise, optionally with widening spreads in the tail."""
    bars = []
    close = start
    for i in range(n):
        spread = spread_pct
        if turb_after is not None and i >= n - turb_after:
            spread = turb_spread
        bars.append(bar(BASE_TS + i * HOUR, close, spread))
        close *= (1.0 + step / 100.0)
    return bars


def calm_flat_bars(n=780):
    """Flat price, wide spreads early, narrow at the end -> range and low ATR pct."""
    bars = []
    for i in range(n):
        spread = 0.05 if i < n - 60 else 0.002
        bars.append(bar(BASE_TS + i * HOUR, 100.0, spread))
    return bars


def fake_state_dir():
    tmp = tempfile.mkdtemp(prefix="labs-")
    return tmp


print("stop=%.1fxATR target=%.1fxATR hold=%dh max_open=%d cost=%.2f%%"
      % (regime_lab.STOP_ATR, regime_lab.TARGET_ATR, regime_lab.HOLD_H,
         regime_lab.MAX_OPEN, regime_lab.COST_PCT))
print()

# 1. pure helpers
closes = [100.0] * 120
check("ema of a flat series is the price", abs(regime_lab.ema_last(closes) - 100.0) < 1e-9)
check("ema needs enough feed", regime_lab.ema_last([100.0] * 50) is None)

bars = trending_bars(60)
atr = regime_lab.atr_last(bars)
check("atr_last returns a number", isinstance(atr, float) and atr > 0, atr)
check("atr_last needs history", regime_lab.atr_last(bars[:5]) is None)

up = [100.0 + i for i in range(30)]
check("ER of a straight line is 1.0", abs(regime_lab.er_last(up) - 1.0) < 1e-9,
      regime_lab.er_last(up))
zig = [100.0, 101.0] * 15
check("ER of a zigzag is near zero", regime_lab.er_last(zig) < 0.1,
      regime_lab.er_last(zig))
check("ER needs history", regime_lab.er_last([100.0, 101.0]) is None)

# 2. regime cells
trend_turb = regime_lab.regime_from(trending_bars(turb_after=30))
check("rising price with widening range -> trend", trend_turb["trend"] == "trend", trend_turb)
check("widening range -> turb", trend_turb["vol"] == "turb", trend_turb)
check("cell predicate true", regime_lab.in_cell(trend_turb) is True)

calm = regime_lab.regime_from(calm_flat_bars())
check("flat price -> range", calm["trend"] == "range", calm)
check("narrowing range -> calm", calm["vol"] == "calm", calm)
check("cell predicate false for range/calm", regime_lab.in_cell(calm) is False)
check("in_cell tolerates None", regime_lab.in_cell(None) is False)
check("regime needs 30 days of history", regime_lab.regime_from(trending_bars(100)) is None)
check("regime tolerates empty input", regime_lab.regime_from([]) is None)

# 3. exits
pos = {"sig_ts": BASE_TS, "entry": 100.0, "stop": 96.0, "target": 106.0, "max_h": 72}
after = [bar(BASE_TS + HOUR, 100.0)] + [bar(BASE_TS + (2 + i) * HOUR, 100.0) for i in range(3)]
check("no exit while price sits between the barriers", regime_lab.exit_for(pos, after) is None)

stop_bars = after + [bar(BASE_TS + 6 * HOUR, 95.0, 0.20)]
res = regime_lab.exit_for(pos, stop_bars)
check("stop hit is reported as stop", res and res[2] == "stop", res)
check("stop fills at the stop price", res and abs(res[0] - 96.0) < 1e-9, res)

target_bars = after + [bar(BASE_TS + 6 * HOUR, 107.0, 0.20)]
res_t = regime_lab.exit_for(pos, target_bars)
check("target hit is reported as target", res_t and res_t[2] == "target", res_t)

both = after + [bar(BASE_TS + 6 * HOUR, 100.0, 0.30)]     # low 85, high 115
res_b = regime_lab.exit_for(pos, both)
check("stop wins when one bar touches both", res_b and res_b[2] == "stop", res_b)

late = after + [bar(BASE_TS + 100 * HOUR, 100.0)]
res_time = regime_lab.exit_for(pos, late)
check("time stop closes at the close", res_time and res_time[2] == "time", res_time)

# 4. record and statistics
record = regime_lab.closed_record(pos, 106.0, BASE_TS, "target")
check("gross percent computed", abs(record["gross_pct"] - 6.0) < 1e-6, record["gross_pct"])
check("cost subtracted", abs(record["res_pct"] - (6.0 - regime_lab.COST_PCT)) < 1e-6,
      record["res_pct"])

check("stats of nothing are zero", regime_lab.arm_stats([])["n"] == 0)
sample = [{"res_pct": 2.0, "why": "target"}, {"res_pct": -4.19, "why": "stop"},
          {"res_pct": 1.0, "why": "time"}]
s = regime_lab.arm_stats(sample)
check("stats count trades", s["n"] == 3)
check("stats mean is right", abs(s["mean_pct"] - (-0.3967)) < 1e-3, s["mean_pct"])
check("stats profit factor is right", abs(s["pf"] - (3.0 / 4.19)) < 1e-2, s["pf"])
check("stats t is negative here", s["t"] < 0, s["t"])
check("stats reasons counted", s["reasons"].get("stop") == 1, s["reasons"])

# 5. the module, offline
tmpdir = fake_state_dir()
state_store.state_dir = lambda: tmpdir
state_store._atomic_write = state_store._atomic_write
state_store.save = lambda data, path=None: _save(data, path)
state_store.load = lambda path=None: _load(path)
_MEMORY = {}


def _save(data, path=None):
    with open(path or os.path.join(tmpdir, "x.json"), "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def _load(path=None):
    try:
        with open(path or os.path.join(tmpdir, "x.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


regime_bars = trending_bars(turb_after=400)      # trend + turbulence, persistent
book = {"regime": regime_bars}
for coin in ("ETH", "SOL", "AVAX"):
    book[coin] = trending_bars(n=220, start=50.0)


def fake_fetch(coin, bars):
    if coin == "BTC":
        return book["regime"][-bars:] if bars < len(book["regime"]) else book["regime"]
    return book.get(coin, [])


now = [BASE_TS + 800 * HOUR]
lab = regime_lab.RegimeLab(bot=None, fetch=fake_fetch, clock=lambda: now[0])
lab.universe = ["ETH", "SOL", "AVAX"]
lab.interval = 3600.0
lab.refresh()
check("phase recorded", lab.state["phase"] and lab.state["phase"]["trend"] == "trend",
      lab.state["phase"])
check("cell arm only runs in the cell", regime_lab.in_cell(lab.state["phase"]))
check("cell arm capped at MAX_OPEN",
      len(lab.state["arms"]["cell"]["open"]) == regime_lab.MAX_OPEN,
      len(lab.state["arms"]["cell"]["open"]))
check("cell_all arm is uncapped by positions but one per coin",
      len(lab.state["arms"]["cell_all"]["open"]) == 3,
      len(lab.state["arms"]["cell_all"]["open"]))
check("control opens the same coins as cell here",
      len(lab.state["arms"]["control"]["open"]) == regime_lab.MAX_OPEN)
check("one position per coin per arm", all(
    len({p["coin"] for p in lab.state["arms"][a]["open"]}) == len(lab.state["arms"][a]["open"])
    for a in regime_lab.ARMS))

# no money is touched
check("no cash attribute", not hasattr(lab, "cash"))
check("no order method", not hasattr(lab, "open_position"))

# 6. a second refresh in the same minute does not duplicate
before = sum(len(lab.state["arms"][a]["open"]) for a in regime_lab.ARMS)
snapshot = [dict(p) for p in lab.state["arms"]["cell_all"]["open"]]
lab.on_tick()
check("on_tick respects the interval",
      sum(len(lab.state["arms"][a]["open"]) for a in regime_lab.ARMS) == before)
check("last_run unchanged inside the interval",
      lab.state["last_run"] == BASE_TS + 800 * HOUR, lab.state["last_run"])

# 7. tracking closes a position when the stop is crossed
now[0] += 2 * HOUR
for coin in book:
    if coin == "regime":
        continue
    bars = book[coin]
    crash = bar(bars[-1]["t"] + HOUR, bars[-1]["c"] * 0.90, 0.01)
    bars.append(crash)
lab.state["last_run"] = 0.0
lab.refresh()
closed_cell = lab.state["arms"]["cell"]["closed"]
check("crash closes cell positions", len(closed_cell) >= 1, len(closed_cell))
check("closed record carries a reason", closed_cell and closed_cell[0]["why"] in
      ("stop", "target", "time"), closed_cell[:1])
check("closed record is net of costs",
      closed_cell and closed_cell[0]["res_pct"] < closed_cell[0]["gross_pct"])

# 8. state round trip
lab.save()
restored = regime_lab.RegimeLab(bot=None, fetch=fake_fetch, clock=lambda: now[0])
restored.universe = lab.universe
check("closed trades survive a restart",
      len(restored.state["arms"]["cell"]["closed"]) == len(lab.state["arms"]["cell"]["closed"]),
      (len(restored.state["arms"]["cell"]["closed"]),
       len(lab.state["arms"]["cell"]["closed"])))
check("last_run survives a restart", restored.state["last_run"] == lab.state["last_run"])
check("phase survives a restart", restored.state["phase"] == lab.state["phase"])

# 9. garbage state and failing fetches must not raise
with open(os.path.join(tmpdir, regime_lab.STATE_FILE), "w", encoding="utf-8") as fh:
    fh.write("not json at all")
try:
    broken = regime_lab.RegimeLab(bot=None, fetch=fake_fetch, clock=lambda: now[0])
    check("garbage state file is tolerated", broken.state["arms"]["cell"]["closed"] == [])
except Exception as exc:
    check("garbage state file is tolerated", False, exc)

with open(os.path.join(tmpdir, regime_lab.STATE_FILE), "w", encoding="utf-8") as fh:
    json.dump({"arms": {"cell": {"open": [{"coin": "ETH"}], "closed": [{"x": 1}]}},
               "regimes": "nonsense", "phase": "trash"}, fh)
try:
    partial = regime_lab.RegimeLab(bot=None, fetch=fake_fetch, clock=lambda: now[0])
    check("positions without sig_ts are dropped",
          partial.state["arms"]["cell"]["open"] == [], partial.state["arms"]["cell"]["open"])
    check("records without res_pct are dropped",
          partial.state["arms"]["cell"]["closed"] == [])
    check("non-dict phase is dropped", partial.state["phase"] is None)
except Exception as exc:
    check("malformed arms tolerated", False, exc)


def exploding_fetch(coin, bars):
    raise RuntimeError("api down")


lab2 = regime_lab.RegimeLab(bot=None, fetch=exploding_fetch, clock=lambda: now[0])
lab2.universe = ["ETH"]
lab2.interval = 3600.0
try:
    lab2.on_tick(force=True)
    check("failing fetch does not raise", True)
except Exception as exc:
    check("failing fetch does not raise", False, exc)
check("failing fetch is recorded", "api down" in (lab2.last_error or ""), lab2.last_error)
check("failing fetch does not advance the hour",
      (lab2.state.get("last_run") or 0.0) <= now[0] - lab2.interval + 601,
      lab2.state.get("last_run"))


def empty_fetch(coin, bars):
    return []


lab3 = regime_lab.RegimeLab(bot=None, fetch=empty_fetch, clock=lambda: now[0])
try:
    lab3.on_tick(force=True)
    check("empty candles do not raise", True)
except Exception as exc:
    check("empty candles do not raise", False, exc)
check("no phase without candles", lab3.state["phase"] is None)

status = lab.status()
check("status says paper only", "no orders" in status["penalty"], status["penalty"])
check("status exposes all three arms", set(status["arms"]) == set(regime_lab.ARMS))
check("status exposes the phase", status["phase"] is not None)

shutil.rmtree(tmpdir, ignore_errors=True)

print()
if failures:
    print("FAILED: %d" % len(failures))
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
