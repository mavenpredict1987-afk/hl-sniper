"""Checks for the ALO fill model in bt_alo.py.

The interesting part of that measurement is not the data, it is the rule that
decides what counts as a fill: a touch is not a fill, and a missed limit costs a
taker fallback. Those two rules decide the whole verdict, so they get their own
test instead of hiding inside a 200-line loop.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bt_alo                                           # noqa: E402

failures = []


def check(name, cond, detail=""):
    if not cond:
        failures.append("%s%s" % (name, (": " + detail) if detail else ""))
        print("FAIL %s%s" % (name, (" :: " + detail) if detail else ""))


def candles(rows):
    """rows = [(o, h, l, c)] -> the dict shape bt_alo uses."""
    return {"t": [i * 3600000 for i in range(len(rows))],
            "o": [r[0] for r in rows], "h": [r[1] for r in rows],
            "l": [r[2] for r in rows], "c": [r[3] for r in rows]}


# --- fills ---------------------------------------------------------------
# A long limit at 100 needs the low to reach 99.95 (0.05% through).
cd = candles([(100, 100.5, 99.99, 100.1),     # 0: the decision candle
              (100, 100.2, 99.96, 100.0),      # 1: touches 99.96, not enough
              (100, 100.1, 99.94, 99.95)])     # 2: trades through, fills
check("long fills only after trading through the limit",
      bt_alo.maker_fill(cd, 0, "long", 100.0, 1) is None,
      "a 0.04% touch was counted as a fill")
check("long fills on the candle that goes through",
      bt_alo.maker_fill(cd, 0, "long", 100.0, 3) == 2,
      "expected index 2, got %r" % (bt_alo.maker_fill(cd, 0, "long", 100.0, 3),))

# Short mirror: needs the high to reach 100.05.
cd = candles([(100, 100.0, 99.5, 100.0),
              (100, 100.04, 99.9, 100.0),      # 0.04% up, not a fill
              (100, 100.06, 99.9, 100.0)])     # 0.06% up, fills
check("short fills only after trading through the limit",
      bt_alo.maker_fill(cd, 0, "short", 100.0, 1) is None)
check("short fills on the candle that goes through",
      bt_alo.maker_fill(cd, 0, "short", 100.0, 3) == 2,
      "expected index 2, got %r" % (bt_alo.maker_fill(cd, 0, "short", 100.0, 3),))

# Wrong direction never fills: a long limit above the market is a market order.
check("a long limit above the price never fills as maker",
      bt_alo.maker_fill(cd, 0, "long", 50.0, 3) is None)
check("a short limit below the price never fills as maker",
      bt_alo.maker_fill(cd, 0, "short", 200.0, 3) is None)

# Running out of candles is not a fill, and must not raise.
short_cd = candles([(100, 100, 100, 100), (100, 100, 100, 100)])
check("data ending before the window is not a fill",
      bt_alo.maker_fill(short_cd, 0, "long", 100.0, 5) is None)
check("window longer than data does not raise",
      bt_alo.maker_fill(short_cd, 1, "short", 100.0, 10) is None)

# --- benefit -------------------------------------------------------------
# Same price, maker vs taker: exactly the fee difference, both sides.
fee_delta = bt_alo.TAKER_PCT - bt_alo.MAKER_PCT
check("maker at the same price saves one fee delta (long)",
      abs(bt_alo.benefit_pct(100.0, 100.0, bt_alo.MAKER_PCT, "long") - fee_delta) < 1e-9,
      "got %r" % bt_alo.benefit_pct(100.0, 100.0, bt_alo.MAKER_PCT, "long"))
check("maker at the same price saves one fee delta (short)",
      abs(bt_alo.benefit_pct(100.0, 100.0, bt_alo.MAKER_PCT, "short") - fee_delta) < 1e-9)

# Price improvement is signed so that positive always means "we did better".
check("buying 0.5% lower is a gain",
      abs(bt_alo.benefit_pct(100.0, 99.5, bt_alo.MAKER_PCT, "long")
          - (0.5 + fee_delta)) < 1e-9,
      "got %r" % bt_alo.benefit_pct(100.0, 99.5, bt_alo.MAKER_PCT, "long"))
check("selling 0.5% higher is a gain",
      abs(bt_alo.benefit_pct(100.0, 100.5, bt_alo.MAKER_PCT, "short")
          - (0.5 + fee_delta)) < 1e-9,
      "got %r" % bt_alo.benefit_pct(100.0, 100.5, bt_alo.MAKER_PCT, "short"))

# The fallback is exactly the case the fee saving must beat: a missed limit
# entered at market later, at a worse price.
check("a missed long limit with a worse fallback is negative",
      bt_alo.benefit_pct(100.0, 100.3, bt_alo.TAKER_PCT, "long") < -0.29)
check("a missed short limit with a worse fallback is negative",
      bt_alo.benefit_pct(100.0, 99.7, bt_alo.TAKER_PCT, "short") < -0.29)

# --- costs ---------------------------------------------------------------
check("baseline round trip is four taker fills",
      abs(bt_alo.BASELINE_COST_PCT - 4 * bt_alo.TAKER_PCT) < 1e-9)
check("taker fill is the 0.0575% the pairs module charges",
      abs(bt_alo.TAKER_PCT - 0.0575) < 1e-9, "got %r" % bt_alo.TAKER_PCT)
check("maker fill is 0.015%", abs(bt_alo.MAKER_PCT - 0.015) < 1e-9)
check("the buffer is the 0.05% through-trade the prereg fixed",
      abs(bt_alo.BUFFER - 0.0005) < 1e-12)

if failures:
    print("FAILED: %d" % len(failures))
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL CHECKS PASSED")
