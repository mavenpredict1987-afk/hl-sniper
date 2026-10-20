"""Self-check for journal.py - the decision log's caps, dedupe and state round trip.

Run: PYTHONPATH=.devlibs python3 test_journal.py
Offline: no network calls are made.

The journal is what makes a losing week debuggable, so the properties that matter
are the boring ones: it must stay bounded, it must not turn a decision repeated
every two seconds into 43 200 entries, and it must survive a save/restore cycle
without losing the tail.
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import journal                                          # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + ((" :: " + str(detail)) if detail else ""))
    if not cond:
        failures.append(name)


print("max_entries=%d dedupe_sec=%.0f" % (journal.MAX_ENTRIES, journal.DEDUPE_SEC))
print()

# 1. a plain add records the fields a dashboard needs
j = journal.Journal(max_entries=10, dedupe_sec=60.0)
e = j.add("pairs", "open", "long ONDO / short PAXG", symbol="ONDO",
          notional=336.0, coverage_x=4.4)
check("add returns the entry", isinstance(e, dict), type(e).__name__)
check("seq starts at 1", e["seq"] == 1, e["seq"])
check("module/action kept", (e["module"], e["action"]) == ("pairs", "open"))
check("symbol kept", e.get("symbol") == "ONDO")
check("reason kept verbatim", e["reason"] == "long ONDO / short PAXG")
check("numbers travel with the reason", e["data"]["notional"] == 336.0, e["data"])
check("time is HH:MM:SS", len(e["time"]) == 8 and e["time"].count(":") == 2, e["time"])

# 2. the same decision inside the window is counted, not appended
n_before = len(j.entries)
again = j.add("pairs", "open", "long ONDO / short PAXG", symbol="ONDO")
check("repeat inside window is suppressed", again is None)
check("suppressed entry not appended", len(j.entries) == n_before, len(j.entries))
check("suppression counted in dropped", j.dropped == 1, j.dropped)

# 3. a different symbol, action or key is a different decision
check("other symbol not suppressed",
      j.add("pairs", "open", "long X / short Y", symbol="FIL") is not None)
check("other action not suppressed",
      j.add("pairs", "close", "long ONDO / short PAXG", symbol="ONDO") is not None)
check("explicit key separates two reasons with the same text",
      j.add("sniper", "skip", "book too thin", symbol="AAA", key="thin") is not None
      and j.add("sniper", "skip", "book too thin", symbol="AAA", key="depth") is not None)

# 4. key is what dedupe compares, so volatile numbers can live in the reason
jv = journal.Journal(max_entries=50, dedupe_sec=60.0)
for i in range(5):
    jv.add("guards", "skip", "book $%d against $4000 needed" % (900 + i),
           symbol="BBB", key="thin_book")
check("changing reason with stable key is deduped",
      len(jv.entries) == 1 and jv.dropped == 4, (len(jv.entries), jv.dropped))

# 5. dedupe=0 records every occurrence
jz = journal.Journal(max_entries=50, dedupe_sec=60.0)
for _ in range(4):
    jz.add("guards", "skip", "same", dedupe=0)
check("dedupe=0 keeps every occurrence", len(jz.entries) == 4, len(jz.entries))

# 6. the window expires
jw = journal.Journal(max_entries=50, dedupe_sec=0.05)
jw.add("m", "a", "r")
time.sleep(0.08)
check("same decision after the window is recorded again",
      jw.add("m", "a", "r") is not None, jw.dropped)

# 7. the cap drops the oldest, not the newest
jc = journal.Journal(max_entries=3, dedupe_sec=0.0)
for i in range(6):
    jc.add("m", "a", "r%d" % i)
check("cap enforced", len(jc.entries) == 3, len(jc.entries))
check("newest kept", [x["reason"] for x in jc.entries] == ["r3", "r4", "r5"],
      [x["reason"] for x in jc.entries])
check("seq keeps counting past the cap", jc.seq == 6, jc.seq)

# 8. recent() is newest first and bounded
rec = jc.recent(2)
check("recent is newest first", [x["reason"] for x in rec] == ["r5", "r4"],
      [x["reason"] for x in rec])

# 9. counts() describes the shape, not the text
jc2 = journal.Journal(max_entries=50, dedupe_sec=0.0)
jc2.add("pairs", "open", "a")
jc2.add("pairs", "open", "b")
jc2.add("pairs", "close", "c")
check("counts shape", jc2.counts() == {"pairs.open": 2, "pairs.close": 1}, jc2.counts())

# 10. the dedupe index itself stays bounded
jl = journal.Journal(max_entries=5, dedupe_sec=0.01)
for i in range(30):
    jl.add("m", "a", "r%d" % i, symbol="S%d" % i)
    time.sleep(0.012)
check("dedupe index is pruned", len(jl._last) <= 4 * jl.max_entries + 1, len(jl._last))

# 11. state round trip keeps entries, seq and dropped
src = journal.Journal(max_entries=10, dedupe_sec=60.0)
src.add("pairs", "open", "long A / short B", symbol="A", notional=100.0)
src.add("pairs", "close", "long A / short B", symbol="A", pnl=1.5)
state = src.to_state()
dst = journal.Journal(max_entries=10, dedupe_sec=60.0)
dst.from_state(state)
check("round trip keeps entries", len(dst.entries) == 2, len(dst.entries))
check("round trip keeps seq", dst.seq == src.seq, (dst.seq, src.seq))
check("round trip keeps dropped", dst.dropped == src.dropped)
check("round trip keeps the data payload",
      dst.entries[-1]["data"]["pnl"] == 1.5, dst.entries[-1].get("data"))

# 11b. the dedupe index survives the round trip, so a restart does not double log
check("repeat after restore is suppressed",
      dst.add("pairs", "close", "long A / short B", symbol="A") is None)
check("restored suppression counted", dst.dropped == 1, dst.dropped)
check("a decision the restore did not see still lands",
      dst.add("pairs", "open", "long C / short D", symbol="C") is not None)
# and a key that differs from the reason is rebuilt too
srck = journal.Journal(max_entries=10, dedupe_sec=60.0)
srck.add("sniper", "skip", "book $901 against $4000 needed", symbol="Z", key="thin")
dstk = journal.Journal(max_entries=10, dedupe_sec=60.0)
dstk.from_state(srck.to_state())
check("stable key survives restore",
      dstk.add("sniper", "skip", "book $1234 against $4000 needed", symbol="Z",
               key="thin") is None)

# 12. restore tolerates a state file written before the journal existed
for bad in (None, {}, [], "garbage", {"entries": "nope"}, {"entries": [1, 2, {}]}):
    jbad = journal.Journal(max_entries=10, dedupe_sec=60.0)
    try:
        jbad.from_state(bad)
        ok = True
    except Exception as exc:                            # noqa: BLE001
        ok = False
        print("      from_state(%r) raised %s" % (bad, exc))
    check("from_state survives %r" % (bad,), ok)

# 13. formatting helper used in reason strings
check("fmt(None) is n/a", journal.fmt(None) == "n/a", journal.fmt(None))
check("fmt(1.2345) respects digits", journal.fmt(1.2345, 2) == "1.23", journal.fmt(1.2345, 2))

# 14. None values are dropped from the payload instead of serialised
jnon = journal.Journal(max_entries=10, dedupe_sec=0.0)
enon = jnon.add("m", "a", "r", good=1.0, bad=None)
check("None payload values dropped", "bad" not in enon["data"], enon["data"])
check("absent payload omits the key entirely",
      "data" not in journal.Journal(max_entries=5, dedupe_sec=0.0).add("m", "a", "r"))

print()
if failures:
    print("%d FAILED: %s" % (len(failures), ", ".join(failures)))
    raise SystemExit(1)
print("ALL CHECKS PASSED")
