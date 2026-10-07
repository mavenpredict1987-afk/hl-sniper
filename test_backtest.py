"""Self-check for the backtest data plumbing.

The load-bearing check is funding alignment. The exchange stamps funding a few
milliseconds after the hour, and an exact-timestamp join matched only 69 of
5000 bars - which silently zeroed almost all funding in the first honest run.

Run: PYTHONPATH=.devlibs python3 test_backtest.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import backtest  # noqa: E402  (sets its own STATE_DIR on import)

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ((" :: " + str(detail)) if detail else ""))
    if not cond:
        failures.append(name)


HOUR = 3600000
base = 1791338400000  # an exact hour boundary

# Candles sit exactly on the hour; funding records lag by a few milliseconds,
# which is what the exchange actually returns.
candles = [{"t": base + i * HOUR, "c": 100.0} for i in range(10)]
funding = {base + i * HOUR + 83: 0.00001 * (i + 1) for i in range(10)}

aligned = backtest.align_funding(candles, funding)
check("one rate per candle", len(aligned) == len(candles), len(aligned))
check("millisecond offsets still match", all(r > 0 for r in aligned), aligned)
check("rates land on the right bar", abs(aligned[4] - 0.00005) < 1e-12, aligned[4])

# A gap in funding must read as zero, not shift the series.
sparse = {base + 2 * HOUR: 0.0002}
sparse_aligned = backtest.align_funding(candles, sparse)
check("gaps become zero", sparse_aligned[0] == 0.0 and sparse_aligned[9] == 0.0,
      sparse_aligned)
check("the known hour is filled", abs(sparse_aligned[2] - 0.0002) < 1e-12, sparse_aligned[2])
check("count of non-zero matches the input", sum(1 for r in sparse_aligned if r) == 1)

# No funding data at all must not crash or invent anything.
check("empty funding yields zeros", backtest.align_funding(candles, {}) == [0.0] * 10)

# A funding rate that lands one hour early must not smear across bars.
early = {base - HOUR: 0.0007, base + HOUR: 0.0009}
early_aligned = backtest.align_funding(candles, early)
check("early hour is not smeared", early_aligned[0] == 0.0, early_aligned[0])
check("the following hour is exact", abs(early_aligned[1] - 0.0009) < 1e-12, early_aligned[1])

print()
if failures:
    print("FAILED:", ", ".join(failures))
    sys.exit(1)
print("ALL CHECKS PASSED")
