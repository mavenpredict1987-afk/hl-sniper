"""Grid v2: do the entry blocks cut the tail?

The simulation is a bar-by-bar port of `grid_lab.py` -- the arm that is actually
running in paper -- and that port is the point. The first attempt at this
question backtested an ATR ladder from `bt_grid2.py` instead; it returned -90%,
and the result was worthless because the live arm does not work that way. Every
rule below is copied from the live code, and the only thing that changes between
runs is the block on new entries.

Frozen before the run: research/GRID_BLOCKS_PREREG.md
Run: PYTHONPATH=.devlibs python3 bt_grid_blocks.py
"""

import json
import os
import sys
from collections import deque

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import grid_lab                     # noqa: E402  (the live rules live there)
from indicators import atr_series   # noqa: E402

CACHE = os.path.join(HERE, "bt_cache")
UNIVERSE = [c for c in grid_lab.DEFAULT_UNIVERSE
            if os.path.exists(os.path.join(CACHE, c + ".json"))]

START = grid_lab.START_EQUITY
LOT = grid_lab.lot_usd(START)


def load():
    out = {}
    for coin in UNIVERSE:
        out[coin] = json.load(open(os.path.join(CACHE, coin + ".json")))
    return out


def rolling_max(values, period):
    out = [None] * len(values)
    dq = deque()
    for i, v in enumerate(values):
        while dq and values[dq[-1]] <= v:
            dq.pop()
        dq.append(i)
        while dq[0] <= i - period:
            dq.popleft()
        out[i] = values[dq[0]] if i >= period - 1 else None
    return out


def funding_series(blob):
    """Funding rate per bar, aligned to the candle hour.

    The ticks carry millisecond noise ("...5600013" against a candle at
    "...5600000"), so an exact match silently drops ~98% of the bars and the
    run looks cheaper than it is. Round to the hour.
    """
    hour = {}
    for k, v in (blob.get("funding") or {}).items():
        try:
            hour[int(k) // 3_600_000 * 3_600_000] = float(v)
        except (TypeError, ValueError):
            continue
    return [hour.get(int(b["t"]) // 3_600_000 * 3_600_000, 0.0) for b in blob["candles"]]


def build():
    data = load()
    st = {}
    for coin, blob in data.items():
        c = [b["c"] for b in blob["candles"]]
        h = [b["h"] for b in blob["candles"]]
        l = [b["l"] for b in blob["candles"]]
        atr = atr_series(h, l, c)
        off = len(c) - len(atr)
        st[coin] = {"c": c, "h": h, "l": l,
                    "atr": [None] * off + list(atr),
                    "hi20": rolling_max(h, 20),
                    "fund": funding_series(blob)}
    return st


def run(block=None, b1_atr=1.5, b1_3atr=2.5, b2_atr=0.75, per_coin=False,
        start=40):
    st = build()
    length = min(len(s["c"]) for s in st.values())

    equity = START
    peak = START
    max_dd = 0.0
    below = 0
    bars_seen = 0
    closed = []
    open_pos = []
    entry_fees = 0.0
    funding_paid = 0.0
    coins = {c: {"usd": 0.0, "lots": 0, "stops": 0, "worst": 0.0} for c in st}

    def blocked(coin, i):
        """Block decision for an entry filled on bar i (the last closed bar)."""
        if block is None:
            return False
        s = st[coin]
        a = s["atr"][i]
        if not a or a <= 0:
            return False
        if block in ("B1", "B1+B2"):
            if (s["c"][i - 1] - s["c"][i]) / a <= -b1_atr:
                return True
            if i >= 3 and (s["c"][i - 3] - s["c"][i]) / a <= -b1_3atr:
                return True
        if block in ("B2", "B1+B2"):
            hi = s["hi20"][i]
            if hi and (hi - s["c"][i]) / a < b2_atr:
                return True
        return False

    for i in range(start, length):
        for pos in open_pos:
            rate = st[pos["coin"]]["fund"][i]
            if rate:
                funding_paid += LOT * pos["left"] * rate

        keep = []
        for pos in open_pos:
            s = st[pos["coin"]]
            if pos["bar"] >= i:
                keep.append(pos)
                continue
            entry = pos["entry"]
            high, low = s["h"][i], s["l"][i]
            if low <= pos["stop"]:
                px = pos["stop"] * (1.0 - grid_lab.SLIP_STOP)
                size = LOT * pos["left"]
                cash = size * (px / entry - 1.0) - size * grid_lab.TAKER_FEE
                usd = pos.get("cash", 0.0) + cash
                closed.append({"coin": pos["coin"], "usd": usd, "why": "stop"})
                coins[pos["coin"]]["usd"] += usd
                coins[pos["coin"]]["lots"] += 1
                coins[pos["coin"]]["stops"] += 1
                coins[pos["coin"]]["worst"] = min(coins[pos["coin"]]["worst"], usd)
                continue
            if abs(pos["left"] - 1.0) < 1e-9 and high >= entry * (1.0 + grid_lab.PARTIAL_PCT):
                part = LOT * grid_lab.PARTIAL_SHARE
                pos["cash"] = pos.get("cash", 0.0) + part * grid_lab.PARTIAL_PCT \
                    - part * grid_lab.TAKER_FEE
                pos["left"] = round(pos["left"] - grid_lab.PARTIAL_SHARE, 6)
            if high >= entry * (1.0 + grid_lab.TARGET_PCT):
                size = LOT * pos["left"]
                cash = size * grid_lab.TARGET_PCT - size * grid_lab.TAKER_FEE
                closed.append({"coin": pos["coin"],
                               "usd": pos.get("cash", 0.0) + cash, "why": "target"})
                coins[pos["coin"]]["usd"] += pos.get("cash", 0.0) + cash
                coins[pos["coin"]]["lots"] += 1
                continue
            keep.append(pos)
        open_pos = keep

        for coin in UNIVERSE:
            if len(open_pos) >= grid_lab.MAX_LOTS:
                break
            s = st[coin]
            per_coin = sum(1 for p in open_pos if p["coin"] == coin)
            if per_coin >= grid_lab.MAX_PER_COIN:
                continue
            if i < 1 + grid_lab.CENTRE_WIN:
                continue
            prev_close = s["c"][i - 1]
            centre = max(s["h"][i - 1 - grid_lab.CENTRE_WIN:i - 1])
            if centre <= 0 or prev_close <= 0:
                continue
            if blocked(coin, i):
                continue
            for k in grid_lab.LEVELS:
                if len(open_pos) >= grid_lab.MAX_LOTS or per_coin >= grid_lab.MAX_PER_COIN:
                    break
                lvl = centre * (1.0 - grid_lab.STEP_PCT * k)
                if not (s["l"][i] <= lvl < prev_close):
                    continue
                open_pos.append({"coin": coin, "entry": lvl, "left": 1.0, "cash": 0.0,
                                 "stop": lvl * (1.0 - grid_lab.STOP_FROM_ENTRY),
                                 "bar": i})
                per_coin += 1
                entry_fees += LOT * grid_lab.MAKER_FEE

        equity = START - entry_fees - funding_paid
        equity += sum(c["usd"] for c in closed)
        for pos in open_pos:
            last = st[pos["coin"]]["c"][i]
            equity += pos.get("cash", 0.0) + LOT * pos["left"] * (last / pos["entry"] - 1.0)
        peak = max(peak, equity)
        max_dd = max(max_dd, 1.0 - equity / peak if peak > 0 else 0.0)
        below += 1 if equity < START else 0
        bars_seen += 1

    wins = [c for c in closed if c["usd"] > 0]
    losses = [c for c in closed if c["usd"] <= 0]
    gain = sum(c["usd"] for c in wins)
    loss = -sum(c["usd"] for c in losses)
    total = sum(c["usd"] for c in closed)
    out = {"block": block or "base",
           "return_pct": round((equity / START - 1.0) * 100, 2),
           "lots": len(closed),
           "winrate": round(100.0 * len(wins) / len(closed), 1) if closed else 0.0,
           "profit_factor": round(gain / loss, 3) if loss > 0 else None,
           "max_dd_pct": round(100.0 * max_dd, 2),
           "underwater_pct": round(100.0 * below / max(1, bars_seen), 1),
           "avg_win": round(gain / len(wins), 2) if wins else 0.0,
           "avg_loss": round(-loss / len(losses), 2) if losses else 0.0,
           "worst_lot": round(min([c["usd"] for c in closed], default=0.0), 2),
           "stops": sum(1 for c in closed if c["why"] == "stop"),
           "expectancy_pct_per_lot": round(100.0 * total / (len(closed) * LOT), 3) if closed else 0.0,
           "open_at_end": len(open_pos)}
    if per_coin:
        out["coins"] = {c: {"usd": round(v["usd"], 2), "lots": v["lots"],
                            "stops": v["stops"], "worst": round(v["worst"], 2)}
                        for c, v in coins.items() if v["lots"]}
    return out


CONFIGS = [
    ("base", {}),
    ("B1", {"block": "B1"}),
    ("B2", {"block": "B2"}),
    ("B1+B2", {"block": "B1+B2"}),
    ("B1 wide", {"block": "B1", "b1_atr": 2.0, "b1_3atr": 3.0}),
    ("B2 wide", {"block": "B2", "b2_atr": 1.25}),
]

if __name__ == "__main__":
    print("conins: %d, bars up to 5000" % len(UNIVERSE))
    print("%-9s %10s %6s %6s %7s %8s %8s %9s %9s %6s" % (
        "block", "return", "lots", "WR", "PF", "DD", "avgwin", "worst",
        "underw", "stops"))
    rows = []
    base = None
    for name, kw in CONFIGS:
        m = run(**kw, per_coin=True)
        m["name"] = name
        rows.append(m)
        if base is None:
            base = m
        print("%-9s %+9.2f%% %6d %5.1f%% %7s %7.2f%% %8.2f %8.2f %8.1f%% %6d" % (
            name, m["return_pct"], m["lots"], m["winrate"], m["profit_factor"],
            m["max_dd_pct"], m["avg_win"], m["worst_lot"], m["underwater_pct"],
            m["stops"]), flush=True)

    print()
    print("base: PF %s DD %.2f%% return %+.2f%% worst %.2f expectancy %.3f%%/lot" % (
        base["profit_factor"], base["max_dd_pct"], base["return_pct"],
        base["worst_lot"], base["expectancy_pct_per_lot"]))
    print("accept: PF >= %.2f, DD <= %.2f%%, return >= %+.2f%%, worst >= %.2f, "
          "better on >=60%% of coins with stops" % (
              (base["profit_factor"] or 0) - 0.05, base["max_dd_pct"] - 3.0,
              0.8 * base["return_pct"], base["worst_lot"] * 0.85))
    for m in rows[1:]:
        helped = worse = 0
        for coin, v in (m.get("coins") or {}).items():
            b = (base.get("coins") or {}).get(coin)
            if not b or b["stops"] == 0:
                continue
            if v["usd"] > b["usd"] + 1e-9:
                helped += 1
            elif v["usd"] < b["usd"] - 1e-9:
                worse += 1
        share = 100.0 * helped / (helped + worse) if (helped + worse) else 0.0
        ok = (m["profit_factor"] is not None
              and m["profit_factor"] >= (base["profit_factor"] or 0) - 0.05
              and m["max_dd_pct"] <= base["max_dd_pct"] - 3.0
              and m["return_pct"] >= 0.8 * base["return_pct"]
              and m["worst_lot"] >= base["worst_lot"] * 0.85
              and share >= 60.0)
        print("%-9s PF %-6s DD %6.2f%% return %+7.2f%% worst %8.2f coins_better %3.0f%% "
              "(n=%d) -> %s" % (m["name"], m["profit_factor"], m["max_dd_pct"],
                                m["return_pct"], m["worst_lot"], share, helped + worse,
                                "ACCEPT" if ok else "REJECT"))
    json.dump(rows, open(os.path.join(HERE, "bt_grid_blocks.json"), "w"), indent=1)
