"""Out-of-sample check: does the chosen configuration hold up on unseen bars?

The long-only / stop 1.35 configuration was picked by looking at this very
history, so scoring it on the same history flatters it. Here the history is cut
in half: fit nothing on the first half, then see whether the same frozen
configuration still works on the second.
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest

COINS = open("bt_coins_all.txt").read().strip().split(",")
DATA = backtest.load_cached(COINS)
print("loaded %d coins" % len(DATA), flush=True)


def slice_data(data, lo, hi):
    out = {}
    for coin, blob in data.items():
        c = blob.get("candles") or []
        n = len(c)
        a, b = int(n * lo), int(n * hi)
        out[coin] = {
            "candles": c[a:b],
            "funding_by_bar": (blob.get("funding_by_bar") or [])[a:b],
            "funding": blob.get("funding") or {},
        }
    return out


HALVES = {
    "first half  ": slice_data(DATA, 0.0, 0.5),
    "second half ": slice_data(DATA, 0.5, 1.0),
}
OUT = "bt_oos.json"
res = json.load(open(OUT)) if os.path.exists(OUT) else []

def run(label, fn):
    if any(r.get("label") == label for r in res):
        print("skip", label, flush=True); return
    t0 = time.time()
    m = fn(); m["label"] = label; res.append(m)
    json.dump(res, open(OUT, "w"), indent=1)
    print("%-32s return %+7.2f%% trades %4d WR %4.1f%% PF %-5s DD %5.1f%% (%.0fs)" % (
        label, m["return_pct"], m["trades"], m["winrate"], m["profit_factor"],
        m["max_dd_pct"], time.time()-t0), flush=True)

for name, part in HALVES.items():
    run("longs-only " + name, lambda p=part: backtest.run_once(p, stop_mult=1.35, shorts=False))
