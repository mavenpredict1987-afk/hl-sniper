"""Self-check for the cost model and the recalibrated thresholds.

Run: PYTHONPATH=.devlibs python3 test_costs.py
Offline: no network calls are made.
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

os.environ["STATE_DIR"] = tempfile.mkdtemp(prefix="hl_costs_")
os.environ["RESET_STATE"] = "1"

import config          # noqa: E402
from bot import Bot    # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ((" :: " + str(detail)) if detail else ""))
    if not cond:
        failures.append(name)


print("thresholds: entry=%.2f strong=%.2f book_max_age=%ds holding=%.0fh min_rr_after_costs=%.1f" % (
    config.CONVERGENCE_THRESHOLD, config.CONVERGENCE_THRESHOLD_STRONG,
    config.BOOK_MAX_AGE_SEC, config.ASSUMED_HOLDING_HOURS, config.MIN_RR_AFTER_COSTS))
print()

# 1. thresholds now match the reference bot's shipped spec
check("entry threshold 0.55", abs(config.CONVERGENCE_THRESHOLD - 0.55) < 1e-9,
      config.CONVERGENCE_THRESHOLD)
check("strong threshold 0.70", abs(config.CONVERGENCE_THRESHOLD_STRONG - 0.70) < 1e-9,
      config.CONVERGENCE_THRESHOLD_STRONG)
check("book freshness 3s", config.BOOK_MAX_AGE_SEC == 3, config.BOOK_MAX_AGE_SEC)

bot = Bot()
check("fresh capital", abs(bot.equity - 10000.0) < 1e-6, bot.equity)

# 2. cost arithmetic
base = bot.cost_pct(0.0, "long")
expect_base = config.ENTRY_FEE_RATE + config.TAKER_FEE + 2 * (config.SLIPPAGE / 4.0)
check("cost = entry fee + exit fee + both legs slippage",
      abs(base - expect_base) < 1e-12, "%.6f vs %.6f" % (base, expect_base))

f = 0.0001
held = config.ASSUMED_HOLDING_HOURS
check("long pays positive funding",
      abs(bot.cost_pct(f, "long") - (expect_base + f * held)) < 1e-12,
      "%.6f" % bot.cost_pct(f, "long"))
check("long is not charged negative funding",
      abs(bot.cost_pct(-f, "long") - expect_base) < 1e-12)
check("short pays negative funding",
      abs(bot.cost_pct(-f, "short") - (expect_base + f * held)) < 1e-12)
check("short is not charged positive funding",
      abs(bot.cost_pct(f, "short") - expect_base) < 1e-12)

# 3. sizing keeps realised risk inside the budget
entry, stop = 1000.0, 900.0
size = bot.position_size(entry, stop, 0.60, "normal")
budget = bot.equity * bot.kelly_risk()
loss_at_stop = size * (entry - stop) + size * entry * base
check("realised loss at stop <= risk budget",
      loss_at_stop <= budget + 1e-6, "%.6f vs %.6f" % (loss_at_stop, budget))
check("sizing is smaller than the cost-blind formula",
      size < budget / (entry - stop), "%.6f vs %.6f" % (size, budget / (entry - stop)))

# 4. adverse funding shrinks the position further
size_f = bot.position_size(entry, stop, 0.60, "normal", 0.0005, "long")
check("adverse funding shrinks size", size_f < size, "%.6f vs %.6f" % (size_f, size))

# 5. viability rule: the target must clear costs by MIN_RR_AFTER_COSTS
check("target barely above costs is rejected",
      base * 1.1 < base * config.MIN_RR_AFTER_COSTS)
btc_atr_pct = 0.0057
target_pct = config.ATR_TARGET_MULT * btc_atr_pct
check("4.8xATR target clears costs",
      target_pct >= base * config.MIN_RR_AFTER_COSTS,
      "%.5f vs %.5f" % (target_pct, base * config.MIN_RR_AFTER_COSTS))

print()
if failures:
    print("FAILED:", ", ".join(failures))
    sys.exit(1)
print("ALL CHECKS PASSED")
