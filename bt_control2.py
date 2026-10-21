import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest

COINS = open("bt_coins.txt").read().strip().split(",")
DATA = backtest.load_cached(COINS)
OUT = "bt_control2.json"
res = json.load(open(OUT)) if os.path.exists(OUT) else []

def run(label, fn):
    if any(r.get("label") == label for r in res):
        print("skip", label); return
    t0 = time.time()
    m = fn(); m["label"] = label; res.append(m)
    json.dump(res, open(OUT, "w"), indent=1)
    print("%-28s return %+6.2f%% trades %3d WR %4.1f%% PF %-5s (%.0fs)" % (
        label, m["return_pct"], m["trades"], m["winrate"], m["profit_factor"], time.time()-t0))

# shorts OFF everywhere: the configuration the ablation picked
run("signal longs-only", lambda: backtest.run_once(DATA, stop_mult=1.35, shorts=False))
for seed in (7, 11, 23):
    run("random longs-only s=%d" % seed,
        lambda s=seed: backtest.run_random_control(DATA, stop_mult=1.35, seed=s, shorts=False))
