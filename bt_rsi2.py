"""RSI(2) mean reversion - the idea the reference bot rates best.

His research panel claims +7.4% with 20% drawdown and a 65% win rate over 401
days. This is an independent implementation on our own 52 coins so the claim is
checked rather than trusted.

Rules (Connors style):
  entry  long when RSI(2) drops below ENTRY
  filter optional: only when price is above its slow SMA
  exit   when RSI(2) climbs above EXIT, or the ATR stop is hit, or TIME_STOP
"""
import json, os, sys, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from indicators import atr_series

COINS = open("bt_coins_all.txt").read().strip().split(",")


def load():
    import pickle
    out = {}
    for coin in COINS:
        p = os.path.join("bt_cache", coin + ".json")
        if not os.path.exists(p):
            continue
        blob = json.load(open(p))
        out[coin] = blob
    return out


def rsi_wilder(closes, period):
    """Wilder RSI. Returns a list aligned with closes (None before warmup)."""
    n = len(closes)
    out = [None] * n
    if n < period + 1:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / period, losses / period
    out[period] = 100.0 - 100.0 / (1.0 + (ag / al if al > 0 else 999.0))
    for i in range(period + 1, n):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0.0)) / period
        al = (al * (period - 1) + max(-d, 0.0)) / period
        out[i] = 100.0 - 100.0 / (1.0 + (ag / al if al > 0 else 999.0))
    return out


def sma_series(vals, period):
    out = [None] * len(vals)
    run = 0.0
    for i, v in enumerate(vals):
        run += v
        if i >= period:
            run -= vals[i - period]
        if i >= period - 1:
            out[i] = run / period
    return out


def atr_at(s, i):
    """atr_series returns one value per full ATR window, so it lags by period."""
    j = i - s["atr_off"]
    if 0 <= j < len(s["atr"]):
        return s["atr"][j]
    return None


def run(entry=10.0, exit_=70.0, trend=0, stop_atr=2.0, time_stop=10,
        risk_pct=0.01, capital=10000.0):
    """One pass over all coins with shared equity. Returns a metrics dict."""
    data = load()
    state = {}
    for coin, blob in data.items():
        c = [b["c"] for b in blob["candles"]]
        h = [b["h"] for b in blob["candles"]]
        l = [b["l"] for b in blob["candles"]]
        atr = atr_series(h, l, c)
        state[coin] = {
            "c": c, "h": h, "l": l,
            "rsi": rsi_wilder(c, 2),
            "atr": atr, "atr_off": len(c) - len(atr),
            "sma": sma_series(c, trend) if trend else None,
        }
    length = min(len(s["c"]) for s in state.values())
    equity = capital
    peak = capital
    max_dd = 0.0
    trades = []
    positions = {}
    warm = max(60, trend + 5 if trend else 60)
    for i in range(warm, length):
        for coin, s in state.items():
            px = s["c"][i]
            pos = positions.get(coin)
            if pos:
                r = s["rsi"][i]
                a = atr_at(s, i)
                exit_px = None
                reason = None
                if pos["dir"] == "long":
                    if px <= pos["stop"]:
                        exit_px, reason = pos["stop"], "stop"
                    elif r is not None and r > exit_:
                        exit_px, reason = px, "rsi"
                if exit_px is None and i - pos["bar"] >= time_stop:
                    exit_px, reason = px, "time"
                if exit_px is not None:
                    pnl = (exit_px - pos["entry"]) * pos["size"]
                    fee = (pos["entry"] + exit_px) * pos["size"] * config.TAKER_FEE
                    equity += pnl - fee
                    trades.append({"coin": coin, "pnl": pnl - fee, "reason": reason,
                                   "risk": pos["risk"]})
                    del positions[coin]
                continue
            r = s["rsi"][i]
            a = atr_at(s, i)
            if r is None or not a or a <= 0:
                continue
            if r >= entry:
                continue
            if trend and s["sma"][i] is not None and px < s["sma"][i]:
                continue
            stop = px - stop_atr * a
            risk = stop_atr * a
            risk_usd = equity * risk_pct
            size = risk_usd / risk
            positions[coin] = {"dir": "long", "entry": px, "size": size,
                               "stop": stop, "bar": i, "risk": risk_usd}
        for coin, pos in list(positions.items()):
            px = state[coin]["c"][i]
            equity += 0.0
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gw = sum(t["pnl"] for t in wins)
    gl = -sum(t["pnl"] for t in losses)
    return {
        "entry": entry, "exit": exit_, "trend": trend, "stop_atr": stop_atr,
        "time_stop": time_stop,
        "return_pct": round((equity / capital - 1) * 100, 2),
        "trades": len(trades),
        "winrate": round(100.0 * len(wins) / len(trades), 1) if trades else 0.0,
        "profit_factor": round(gw / gl, 2) if gl > 0 else None,
        "max_dd_pct": round(max_dd * 100, 2),
        "open_at_end": len(positions),
    }


if __name__ == "__main__":
    rows = []
    for entry in (5.0, 10.0, 15.0):
        for trend in (0, 100, 200):
            m = run(entry=entry, trend=trend)
            rows.append(m)
            print("%-42s %+7.1f%% | %4d | WR %4.1f%% | PF %-5s | DD %5.1f%%" % (
                "entry<%.0f trend=%s" % (entry, trend or "none"),
                m["return_pct"], m["trades"], m["winrate"], m["profit_factor"],
                m["max_dd_pct"]), flush=True)
    json.dump(rows, open("bt_rsi2.json", "w"), indent=1)
