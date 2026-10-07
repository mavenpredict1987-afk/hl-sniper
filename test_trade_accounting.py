"""Self-check for trade accounting.

Regression guard for a real defect: profit booked by a partial was left out of
the trade record, so winrate, avg R and profit factor could contradict the
equity curve (trades read as losers while the account was up).

Run: PYTHONPATH=.devlibs python3 test_trade_accounting.py
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

os.environ["STATE_DIR"] = tempfile.mkdtemp(prefix="hl_trades_")
os.environ["RESET_STATE"] = "1"

import config                      # noqa: E402
from bot import Bot, Position      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ((" :: " + str(detail)) if detail else ""))
    if not cond:
        failures.append(name)


def fresh():
    bot = Bot()
    bot.cash = bot.equity = bot.peak = 10000.0
    bot.trades, bot.positions = [], {}
    bot.win_count = bot.loss_count = 0
    bot.win_sum = bot.loss_sum = bot.r_sum = 0.0
    return bot


# --- a trade that takes a partial then closes at target -------------------
bot = fresh()
pos = Position("BTC", "long", 100.0, 10.0, 90.0, 200.0, 10.0, 5.0)
pos.initial_size = 10.0
pos.initial_risk_usd = 100.0
bot.positions["BTC"] = pos
cash_before = bot.cash

bot.book_partial(pos, "BTC", 120.0)
partial = pos.realized_pnl
check("partial books a profit", partial > 0, round(partial, 2))
check("partial halves the position", abs(pos.size - 5.0) < 1e-9, pos.size)
check("partial raises cash", bot.cash > cash_before, round(bot.cash - cash_before, 2))

bot.close_position("BTC", 130.0, "target")
trade = bot.trades[-1]

closing_leg = (130.0 * (1 - config.SLIPPAGE / 4) - 100.0) * 5.0
check("trade pnl is the closing leg plus the partial",
      abs(trade["pnl"] - (closing_leg + partial)) < 0.5,
      "%.2f vs %.2f" % (trade["pnl"], closing_leg + partial))
check("trade pnl exceeds the closing leg alone", trade["pnl"] > closing_leg + 1.0,
      "%.2f" % trade["pnl"])
check("partial is reported separately",
      abs(trade["partial_pnl"] - round(partial, 2)) < 0.01, trade["partial_pnl"])
check("R multiple uses the combined pnl", trade["r"] > 2.0, trade["r"])
check("win counted once", bot.win_count == 1 and bot.loss_count == 0,
      (bot.win_count, bot.loss_count))

# equity must equal starting cash plus everything booked
check("cash matches the booked pnl",
      abs(bot.cash - (10000.0 + trade["pnl"])) < 0.5,
      "%.2f vs %.2f" % (bot.cash, 10000.0 + trade["pnl"]))

# --- a plain trade without a partial is unaffected ------------------------
bot2 = fresh()
pos2 = Position("ETH", "long", 100.0, 10.0, 90.0, 200.0, 10.0, 5.0)
pos2.initial_size = 10.0
pos2.initial_risk_usd = 100.0
bot2.positions["ETH"] = pos2
bot2.close_position("ETH", 110.0, "target")
t2 = bot2.trades[-1]
check("no partial means no partial pnl", t2["partial_pnl"] == 0.0, t2["partial_pnl"])
expected = ((110.0 * (1 - config.SLIPPAGE / 4)) - 100.0) * 10.0 - \
           10.0 * (110.0 * (1 - config.SLIPPAGE / 4)) * config.TAKER_FEE
check("plain trade pnl is unchanged", abs(t2["pnl"] - expected) < 0.5,
      "%.2f vs %.2f" % (t2["pnl"], expected))
check("R multiple is about 1", 0.9 < t2["r"] < 1.1, t2["r"])

# --- a losing partial still counts once -----------------------------------
bot3 = fresh()
pos3 = Position("SOL", "long", 100.0, 10.0, 90.0, 200.0, 10.0, 5.0)
pos3.initial_size = 10.0
pos3.initial_risk_usd = 100.0
bot3.positions["SOL"] = pos3
bot3.book_partial(pos3, "SOL", 105.0)
bot3.close_position("SOL", 95.0, "stop")
t3 = bot3.trades[-1]
check("small partial then a stop is still one trade", len(bot3.trades) == 1)
check("loss counted once", bot3.loss_count == 1 and bot3.win_count == 0,
      (bot3.win_count, bot3.loss_count))

print()
if failures:
    print("FAILED:", ", ".join(failures))
    sys.exit(1)
print("ALL CHECKS PASSED")
