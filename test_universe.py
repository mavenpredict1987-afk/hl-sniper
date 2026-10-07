"""Self-check for universe.py. Offline: uses a fake exchange client.

Run: PYTHONPATH=.devlibs python3 test_universe.py
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

os.environ["STATE_DIR"] = tempfile.mkdtemp(prefix="hl_universe_")

import config        # noqa: E402
import universe      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ((" :: " + str(detail)) if detail else ""))
    if not cond:
        failures.append(name)


def make_candles(closes, wick=0.002, pad=0.001):
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        hi = max(o, c) * (1 + wick)
        lo = min(o, c) * (1 - wick)
        out.append({"t": i * 3600000, "o": o, "h": hi, "l": lo, "c": c, "v": 1.0})
    return out


# --- 1. metric maths -------------------------------------------------------
steady = make_candles([100.0 + i * 0.5 for i in range(60)], wick=0.001)
choppy = make_candles([100.0 + (5 if i % 2 else 0) for i in range(60)], wick=0.02)

a_steady = universe.atr_pct(steady)
a_choppy = universe.atr_pct(choppy)
check("atr_pct returns a number", a_steady is not None and a_steady > 0, a_steady)
check("choppy atr > steady atr", a_choppy > a_steady, "%.3f vs %.3f" % (a_choppy, a_steady))

w_steady, w_choppy = universe.wickiness(steady), universe.wickiness(choppy)
check("steady wickiness < choppy", w_steady < w_choppy, "%.2f vs %.2f" % (w_steady, w_choppy))

er_trend = universe.efficiency_ratio(steady)
er_chop = universe.efficiency_ratio(choppy)
check("trend efficiency > chop efficiency", er_trend > er_chop,
      "%.3f vs %.3f" % (er_trend, er_chop))
check("perfect trend is near 1.0", er_trend > 0.95, er_trend)

check("impact_bps computes", universe.impact_bps(
    {"impact_pxs": ["100.0", "100.2"], "mark": "100.1"}) is not None)
check("impact missing is None", universe.impact_bps({"mark": "100.0"}) is None)
check("atr_pct short series is None", universe.atr_pct(steady[:5]) is None)

# --- 2. scoring and classification ----------------------------------------
calm_metrics = {"atr_pct": 0.5, "wickiness": 1.5, "efficiency": 0.6,
                "impact_bps": 1.0, "day_vlm": 5e8}
wild_metrics = {"atr_pct": 6.0, "wickiness": 8.0, "efficiency": 0.05,
                "impact_bps": 90.0, "day_vlm": 1e6}
s_calm = universe.noise_score(calm_metrics)
s_wild = universe.noise_score(wild_metrics)
check("calm scores low", s_calm <= config.NOISE_CLASSES["calm"]["max_score"], s_calm)
check("wild scores high", s_wild >= config.NOISE_CLASSES["noisy"]["max_score"], s_wild)
check("score is capped at 100", universe.noise_score(
    {"atr_pct": 99, "wickiness": 99, "efficiency": 0.0, "impact_bps": 999,
     "day_vlm": 0}) <= 100.0)
check("classify calm", universe.classify(s_calm) == "calm", universe.classify(s_calm))
check("classify toxic at the top", universe.classify(100.0) == "toxic")

# --- 3. scan() over a fake exchange, including a dead ticker ---------------
cold = make_candles([10.0 + i * 0.01 for i in range(60)], wick=0.001)
hot = make_candles([10.0 + (3 if i % 2 else 0) for i in range(60)], wick=0.03)

CTXS = {
    "STEADY": {"mark": 100.0, "day_volume_usd": 5e8, "open_interest": 1e6,
               "funding": 0.00001, "impact_pxs": ["99.9", "100.1"],
               "max_leverage": 20},
    "CHOPPY": {"mark": 10.0, "day_volume_usd": 1e8, "open_interest": 1e6,
               "funding": 0.00005, "impact_pxs": ["9.99", "10.01"],
               "max_leverage": 10},
    "WILD": {"mark": 10.0, "day_volume_usd": 5e7, "open_interest": 1e6,
             "funding": 0.0002, "impact_pxs": ["9.9", "10.1"], "max_leverage": 5},
    "DEAD": {"mark": 1.0, "day_volume_usd": 3e7, "open_interest": 1e6,
             "funding": 0.0, "impact_pxs": ["0.99", "1.01"], "max_leverage": 5},
    "TINY": {"mark": 1.0, "day_volume_usd": 1000.0, "open_interest": 1.0,
             "funding": 0.0, "impact_pxs": None, "max_leverage": 3},
    "kPEPE": {"mark": 0.01, "day_volume_usd": 1e8, "open_interest": 1e6,
              "funding": 0.0, "impact_pxs": ["0.0099", "0.0101"], "max_leverage": 10},
    "xyz:NVDA": {"mark": 100.0, "day_volume_usd": 1e8, "open_interest": 1e6,
                 "funding": 0.0, "impact_pxs": ["99.9", "100.1"], "max_leverage": 10},
}
CNDLS = {"STEADY": steady, "CHOPPY": cold, "WILD": hot, "DEAD": []}


class FakeClient:
    def meta_and_ctxs(self, cache_sec=60):
        return CTXS

    def candles_recent(self, coin, interval="1h", n_bars=168):
        return CNDLS.get(coin, [])


tradable, pool = universe.scan(FakeClient(), size=4, bars=60, delay=0.0)
by_coin = {r["coin"]: r for r in pool}
names = [r["coin"] for r in tradable]

check("scan excluded the illiquid coin", "TINY" not in by_coin, list(by_coin))
check("scan excluded the 1000x builder ticker", "kPEPE" not in by_coin, list(by_coin))
check("scan excluded the HIP-3 namespaced market", "xyz:NVDA" not in by_coin, list(by_coin))
check("dead ticker is not tradable", by_coin["DEAD"]["tradable"] is False)
check("dead ticker is flagged", "no_candles" in by_coin["DEAD"]["flags"],
      by_coin["DEAD"]["flags"])
# The defect in the reference pack: no-data rows were forced into "toxic",
# which also handed them a trend-following recommendation. Class must stay
# honest and tradability must come from the flags.
check("dead ticker keeps an honest class, not a forced toxic",
      by_coin["DEAD"]["noise_class"] != "toxic", by_coin["DEAD"]["noise_class"])
check("dead ticker is out of the tradable list", "DEAD" not in names, names)
check("tradable list respects size", len(tradable) <= 4, len(tradable))
check("every tradable coin has a class", all(r.get("noise_class") for r in tradable))
check("every tradable coin has a risk cap", all(r.get("risk_pct_cap") for r in tradable))
check("every tradable coin has a leverage cap",
      all(r.get("class_max_leverage") for r in tradable))
check("tradable is sorted by noise then depth",
      [r["noise_score"] for r in tradable] == sorted(r["noise_score"] for r in tradable),
      [r["noise_score"] for r in tradable])
check("wild coin is riskier class than steady",
      config.NOISE_CLASSES[by_coin["WILD"]["noise_class"]]["max_score"] >=
      config.NOISE_CLASSES[by_coin["STEADY"]["noise_class"]]["max_score"],
      (by_coin["STEADY"]["noise_class"], by_coin["WILD"]["noise_class"]))

# --- 4. cache round trip and seed fallback --------------------------------
check("no cache yet", universe.load() is None)
seeded = universe.load_or_seed()
check("seed falls back to configured majors",
      [r["coin"] for r in seeded] == list(config.SYMBOLS), [r["coin"] for r in seeded])

universe.save(tradable)
loaded = universe.load()
check("cache survives a round trip", loaded is not None and len(loaded) == len(tradable),
      None if loaded is None else len(loaded))
check("cache keeps names", [r["coin"] for r in loaded] == names)

print()
if failures:
    print("FAILED:", ", ".join(failures))
    sys.exit(1)
print("ALL CHECKS PASSED")
