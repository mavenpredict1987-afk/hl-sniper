import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest

COINS = open("bt_coins_all.txt").read().strip().split(",")
DATA = backtest.load_cached(COINS)
print("loaded %d coins" % len(DATA), flush=True)
OUT = "bt_big.json"
res = json.load(open(OUT)) if os.path.exists(OUT) else []

def run(label, fn):
    if any(r.get("label") == label for r in res):
        print("skip", label, flush=True); return
    t0 = time.time()
    m = fn(); m["label"] = label; res.append(m)
    json.dump(res, open(OUT, "w"), indent=1)
    print("%-30s return %+7.2f%% trades %4d WR %4.1f%% PF %-5s DD %5.1f%% (%.0fs)" % (
        label, m["return_pct"], m["trades"], m["winrate"], m["profit_factor"],
        m["max_dd_pct"], time.time()-t0), flush=True)

# the configuration the ablation picked, on 52 coins instead of 19
run("signal longs-only", lambda: backtest.run_once(DATA, stop_mult=1.35, shorts=False))
for seed in (7, 11, 23, 42, 99):
    run("random longs-only s=%d" % seed,
        lambda s=seed: backtest.run_random_control(DATA, stop_mult=1.35, seed=s, shorts=False))
run("signal both-ways", lambda: backtest.run_once(DATA, stop_mult=1.35, shorts=True))
