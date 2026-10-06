"""Self-check for the durable state layer.

Run:  python3 test_state_store.py
Exits non-zero when any check fails. No network calls are made.
"""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TMP = tempfile.mkdtemp(prefix="hl_state_")
os.environ["STATE_DIR"] = TMP
os.environ.pop("RESET_STATE", None)

import config          # noqa: E402  (reads STATE_DIR at import time)
import state_store     # noqa: E402
from bot import Bot    # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ((" :: " + str(detail)) if detail else ""))
    if not cond:
        failures.append(name)


print("STATE_DIR =", TMP)
print()

# 1. seed is valid and complete
seed, src = state_store.load()
check("seed loads", seed is not None, src)
check("source is state_seed.json", src == state_store.seed_path(), src)
check("no live state yet", not os.path.exists(state_store.state_path()))
check("seed equity 9758.88", abs(seed["equity"] - 9758.88) < 1e-9, seed.get("equity"))
check("seed has 2 trades", len(seed["trades"]) == 2, len(seed.get("trades", [])))
check("seed has 1440 equity points", len(seed["equity_history"]) == 1440)
check("seed has 3 snipes", len(seed["sniper"]["execution_history"]) == 3)

# 2. bot restores from seed
bot = Bot()
check("bot equity restored", abs(bot.equity - 9758.88) < 1e-9, bot.equity)
check("bot peak restored", abs(bot.peak - 10000.0) < 1e-9, bot.peak)
check("bot trades restored", len(bot.trades) == 2, len(bot.trades))
check("bot losses restored", bot.loss_count == 2, bot.loss_count)
check("bot r_sum restored", abs(bot.r_sum + 2.26) < 1e-9, bot.r_sum)
check("bot equity history restored", len(bot.equity_history) == 1440)
check("bot sniper stats restored", bot.sniper.get_stats().get("total_snipes") == 3,
      bot.sniper.get_stats())
check("restart counted", bot.restarts == 1, bot.restarts)
check("resume event logged", any("resumed from" in e for e in bot.events), bot.events[-1:])

# 3. save writes a snapshot and the next start prefers it over the seed
bot.update_status()
check("state file written", os.path.exists(state_store.state_path()), state_store.state_path())
again, src2 = state_store.load()
check("live state wins over seed", src2 == state_store.state_path(), src2)
check("live state keeps equity", abs(again["equity"] - 9758.88) < 1e-9, again.get("equity"))

# 4. arbitrary payload round trip
state_store.save({"equity": 123.45, "trades": [{"x": 1}], "custom": {"a": [1, 2]}})
back, _ = state_store.load()
check("round trip equity", back["equity"] == 123.45, back.get("equity"))
check("round trip nested", back["custom"] == {"a": [1, 2]})
check("schema stamped", back["schema"] == state_store.SCHEMA_VERSION)

# 5. corrupt snapshot falls back to the seed instead of crashing
with open(state_store.state_path(), "w", encoding="utf-8") as fh:
    fh.write("{not json")
data, src3 = state_store.load()
check("corrupt state falls back to seed", src3 == state_store.seed_path(), src3)
check("fallback still has bot data", data is not None and len(data["trades"]) == 2)

# 6. clear() removes the live snapshot only
state_store.save({"equity": 1.0})
state_store.clear()
check("clear removes live file", not os.path.exists(state_store.state_path()))
check("clear keeps seed", os.path.exists(state_store.seed_path()))

shutil.rmtree(TMP, ignore_errors=True)
print()
if failures:
    print("FAILED:", ", ".join(failures))
    sys.exit(1)
print("ALL CHECKS PASSED")
