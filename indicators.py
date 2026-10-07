def ema(values, period):
    if not values:
        return 0.0
    k = 2.0 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def sma(values, period):
    if not values:
        return 0.0
    p = values[-period:]
    return sum(p) / len(p)


def atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:period])
    for i in range(period, len(trs)):
        a = (a * (period - 1) + trs[i]) / period
    return a


def atr_series(highs, lows, closes, period=14, from_len=1):
    """Wilder ATR for every prefix length from `from_len` to len(closes).

    atr(highs[:k], lows[:k], closes[:k]) follows the same smoothing recursion
    for every k, so the whole family costs one pass instead of one pass per
    prefix (which is O(n^2) and dominates a backtest). Values are identical to
    calling atr() on each prefix - see the equality check in the tests.
    """
    n = len(closes)
    if n < period + 1:
        return []
    trs = []
    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    out = []
    a = 0.0
    for k in range(period + 1, n + 1):
        m = k - 1
        if m == period:
            a = sum(trs[:period])
        else:
            a = (a * (period - 1) + trs[m - 1]) / period
        if k >= from_len:
            out.append(a)
    return out


def adx(highs, lows, closes, period=14):
    if len(closes) < period * 2 + 1:
        return 0.0
    trs, pdm, mdm = [], [], []
    for i in range(1, len(closes)):
        h, l = highs[i], lows[i]
        ph, pl = highs[i - 1], lows[i - 1]
        pc = closes[i - 1]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        up, dn = h - ph, pl - l
        pdm.append(up if (up > dn and up > 0) else 0.0)
        mdm.append(dn if (dn > up and dn > 0) else 0.0)
    a = sum(trs[:period])
    ps = sum(pdm[:period])
    ms = sum(mdm[:period])
    dxs = []
    for i in range(period, len(trs)):
        a = a - a / period + trs[i]
        ps = ps - ps / period + pdm[i]
        ms = ms - ms / period + mdm[i]
        pdi = 100.0 * ps / a if a else 0.0
        mdi = 100.0 * ms / a if a else 0.0
        den = pdi + mdi
        dxs.append(100.0 * abs(pdi - mdi) / den if den else 0.0)
    if not dxs:
        return 0.0
    if len(dxs) < period:
        return sum(dxs) / len(dxs)
    v = sum(dxs[:period])
    for i in range(period, len(dxs)):
        v = (v * (period - 1) + dxs[i]) / period
    return v
