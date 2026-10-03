from config import LEVEL_TOUCH_MIN, LEVEL_CLUSTER_ATR, BREAKOUT_CONFIRM_ATR


def find_levels(highs, lows, volumes, atr_val, max_levels=8):
    n = len(highs)
    w = 3
    pivots = []
    for i in range(w, n - w):
        if highs[i] == max(highs[i - w:i + w + 1]):
            pivots.append((highs[i], i, volumes[i]))
        if lows[i] == min(lows[i - w:i + w + 1]):
            pivots.append((lows[i], i, volumes[i]))
    if not pivots or atr_val <= 0:
        return []
    tol = LEVEL_CLUSTER_ATR * atr_val
    clusters = []
    for price, idx, vol in pivots:
        placed = False
        for c in clusters:
            if abs(c["price"] - price) <= tol:
                c["members"].append((price, vol))
                c["price"] = sum(m[0] for m in c["members"]) / len(c["members"])
                placed = True
                break
        if not placed:
            clusters.append({"price": price, "members": [(price, vol)]})
    levels = []
    for c in clusters:
        touches = len(c["members"])
        vol_at = sum(m[1] for m in c["members"])
        strength = touches + min(vol_at / 1000.0, 5.0)
        levels.append({"price": c["price"], "strength": strength, "touches": touches})
    levels.sort(key=lambda x: -x["strength"])
    return levels[:max_levels]


def nearest_support(levels, price):
    below = [l for l in levels if l["price"] < price]
    if not below:
        return None
    return max(below, key=lambda l: l["price"])


def nearest_resistance(levels, price):
    above = [l for l in levels if l["price"] > price]
    if not above:
        return None
    return min(above, key=lambda l: l["price"])


def breakout_confirmed(levels, direction, prev_low, prev_high, last_close, atr_val):
    """True if last candle closed through a tested level with room beyond it."""
    if atr_val <= 0:
        return False
    confirm = BREAKOUT_CONFIRM_ATR * atr_val
    tested = [l for l in levels if l["touches"] >= LEVEL_TOUCH_MIN]
    if direction == "long":
        for l in tested:
            if prev_low <= l["price"] and last_close >= l["price"] + confirm:
                return True
        res = nearest_resistance(levels, last_close)
        return res is None or (res["price"] - last_close) >= 0.5 * atr_val
    else:
        for l in tested:
            if prev_high >= l["price"] and last_close <= l["price"] - confirm:
                return True
        sup = nearest_support(levels, last_close)
        return sup is None or (last_close - sup["price"]) >= 0.5 * atr_val
