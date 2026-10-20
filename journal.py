"""journal.py - why the bot did what it did.

The trade log answers "what happened". This answers "on what grounds", and it is
the difference between a result you can debug and a number you have to trust.

His dashboard carries a signal_journal for exactly this reason: after a losing
week you need the numbers that were on the screen when the decision was taken,
not the conclusion you reach afterwards while looking at the chart.

Design rules:

* Bounded. A journal that grows without limit is re-serialised into every state
  save and eventually into every status response, so it is capped and the cap is
  enforced by dropping the oldest entries, not by refusing new ones.
* Deduplicated. A decision that is re-evaluated every two seconds (say, "skip
  this coin, too little liquidity") is not 43 200 journal entries a day. The
  same module+action+symbol+reason inside DEDUPE_SEC bumps a counter instead.
* Numbers travel with the reason. "skip, thin book" is an opinion; "skip,
  book $900 against $4000 needed" is a fact.
* Append-only and never rewritten. Editing history to look better is the one
  thing a journal must never allow.

The reason string is built by the caller because only the caller knows which
numbers mattered.
"""
import logging
import time

logger = logging.getLogger(__name__)

MAX_ENTRIES = 400
DEDUPE_SEC = 300.0


def fmt(num, digits=2):
    """Format a number for a reason string, tolerating None."""
    if num is None:
        return "n/a"
    try:
        return ("%." + str(digits) + "f") % float(num)
    except (TypeError, ValueError):
        return str(num)


class Journal:
    def __init__(self, max_entries=MAX_ENTRIES, dedupe_sec=DEDUPE_SEC):
        self.entries = []
        self.max_entries = max_entries
        self.dedupe_sec = dedupe_sec
        self.seq = 0
        self.dropped = 0
        self._last = {}

    def add(self, module, action, reason, symbol=None, key=None, dedupe=None, **data):
        """Record one decision. Returns the entry, or None if suppressed.

        `key` is what deduplication compares; it defaults to the reason string.
        Pass a stable key when the reason carries numbers that change every time
        (a book size, a risk figure) - otherwise every occurrence looks new and
        the journal fills with the same decision restated.

        `dedupe` overrides the default window in seconds; pass 0 to always keep.
        """
        now = time.time()
        window = self.dedupe_sec if dedupe is None else dedupe
        dedupe_key = key if key is not None else reason
        full_key = (module, action, symbol or None, dedupe_key)
        last = self._last.get(full_key)
        if window and last is not None and now - last < window:
            self.dropped += 1
            return None
        self._last[full_key] = now
        self._prune(now, window)
        self.seq += 1
        entry = {
            "seq": self.seq,
            "ts": round(now, 1),
            "time": time.strftime("%H:%M:%S", time.localtime(now)),
            "module": module,
            "action": action,
            "reason": reason,
        }
        if key is not None:
            # The dedupe key, when it differs from the reason. Kept so a restart
            # can rebuild the index and not log the same decision twice.
            entry["k"] = key
        if symbol:
            entry["symbol"] = symbol
        if data:
            entry["data"] = {k: (round(v, 6) if isinstance(v, float) else v)
                             for k, v in data.items() if v is not None}
        self.entries.append(entry)
        if len(self.entries) > self.max_entries:
            self.entries = self.entries[-self.max_entries:]
        return entry

    def _prune(self, now, window=None):
        """Drop dedupe timestamps that can no longer suppress anything."""
        if len(self._last) <= 4 * self.max_entries:
            return
        span = max((self.dedupe_sec if window is None else window) or 0.0,
                   self.dedupe_sec * 2.0)
        cutoff = now - span
        self._last = {k: v for k, v in self._last.items() if v >= cutoff}

    def recent(self, n=25):
        return list(reversed(self.entries[-n:]))

    def counts(self):
        """How many decisions per module and action - the shape, not the text."""
        out = {}
        for e in self.entries:
            k = "%s.%s" % (e.get("module"), e.get("action"))
            out[k] = out.get(k, 0) + 1
        return out

    def to_state(self):
        return {"entries": list(self.entries), "seq": self.seq,
                "dropped": self.dropped}

    def from_state(self, data):
        if not isinstance(data, dict):
            return
        entries = data.get("entries")
        if isinstance(entries, list):
            self.entries = [e for e in entries if isinstance(e, dict)][-self.max_entries:]
        try:
            self.seq = int(data.get("seq") or len(self.entries))
        except (TypeError, ValueError):
            self.seq = len(self.entries)
        try:
            self.dropped = int(data.get("dropped") or 0)
        except (TypeError, ValueError):
            self.dropped = 0
        # Rebuild the dedupe index from the restored entries. Without this every
        # restart starts with an empty index, so the decision the bot re-derives
        # in its first loop - a pair it just rebalanced, a config it just
        # fingerprinted - lands in the journal a second time, and the count of
        # how many times something happened becomes a count of restarts.
        self._last = {}
        for e in self.entries:
            ts = e.get("ts")
            if not isinstance(ts, (int, float)):
                continue
            dedupe_key = e.get("k") if e.get("k") is not None else e.get("reason")
            self._last[(e.get("module"), e.get("action"),
                        e.get("symbol") or None, dedupe_key)] = float(ts)
        self._prune(time.time())
