import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest

COINS = open("bt_coins.txt").read().strip().split(",")
DATA = backtest.load_cached(COINS)
OUT = "bt_control.json"
res = []
if os.path.exists(OUT):
    res = json.load(open(OUT))

def run(label, fn):
    if any(r["label"] == label for r in res):
        print("skip", label); return
    t0 = time.time()
    metrics = fn()
    metrics["label"] = label
    res.append(metrics)
    json.dump(res, open(OUT, "w"), indent=1)
    print("%-26s return %+6.2f%% trades %3d WR %4.1f%% PF %-5s (%.0fs)" % (
        label, metrics["return_pct"], metrics["trades"], metrics["winrate"],
        metrics["profit_factor"], time.time() - t0))

STOP = float(os.environ.get("CTRL_STOP", "1.35"))
run("signal stop=%.2f" % STOP, lambda: backtest.run_once(DATA, stop_mult=STOP, shorts=True))
for seed in (7, 11, 23):
    run("random seed=%d stop=%.2f" % (seed, STOP),
        lambda s=seed: backtest.run_random_control(DATA, stop_mult=STOP, seed=s, shorts=True))
