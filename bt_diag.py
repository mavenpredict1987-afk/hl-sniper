import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest

COINS = ["BTC", "ETH", "DOGE"]
DATA = backtest.load_cached(COINS)

captured = {}
orig = backtest.summarize
def spy(engine, data, capital):
    captured['engine'] = engine
    captured['capital'] = capital
    return orig(engine, data, capital)
backtest.summarize = spy

m = backtest.run_once(DATA, stop_mult=1.35, shorts=True)
eng = captured['engine']
capital = captured['capital']
print("итог: доход %+.2f%% | сделок %d | avgR %+.2f | PF %s" % (
    m['return_pct'], m['trades'], m['avg_r'], m['profit_factor']))
print()
print("%-6s %-5s %9s %9s %10s %8s" % ("монета","напр","pnl","risk_usd","r","risk/cap"))
tot_pnl = 0.0
tot_r = 0.0
for t in eng.trades:
    tot_pnl += t['pnl']; tot_r += t['r']
    print("%-6s %-5s %+9.2f %9s %+10.2f" % (
        t['symbol'], t['direction'], t['pnl'], '-', t['r']))
print()
print("сумма pnl по сделкам : %+.2f" % tot_pnl)
print("equity - capital     : %+.2f" % (eng.equity - capital))
print("расхождение          : %+.2f" % (eng.equity - capital - tot_pnl))
print("сумма R              : %+.2f" % tot_r)
print("фандинг уплачен      : %+.2f" % eng.funding_paid)
print("открытых на конец    : %d" % len(eng.positions))
for sym, p in eng.positions.items():
    print("   %s %s size=%.4f entry=%.4f stop=%.4f" % (sym, p.direction, p.size, p.entry, p.stop))
