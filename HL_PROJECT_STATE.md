# HL SNIPER BOT — PROJECT STATE

Updated: 2026-10-15. Paper-first Hyperliquid perp bot. **This file is the entry
point for a new session — read it first, then `research/REFERENCE_FRIEND_BOT.md`.**

---

## Current focus (2026-10-17)

**The sniper is OFF** (`SNIPER_ENABLED=0`) and a market-neutral **pairs module**
runs in its place. See `FROZEN.md` for the experiment and `RESEARCH_LOG.md` for
why everything else was rejected.

Pairs: every 48h rank the active universe by its 7 day return, long the three
weakest, short the three strongest, equal notional, 20% exposure. Backtest over
208 days: +17.9%, PF 1.22, DD 5.4%, correlation to the market -0.15, and both
halves positive (+10.5% / +7.8%) - the only strategy here to survive that split.
First three pairs opened 2026-10-17 07:31 UTC.

Frozen until 2026-10-31 or 30 closed pairs. Do not tune parameters before then.

## Live deployment

| | |
|---|---|
| Repo | `mavenpredict1987-afk/hl-sniper` (**public**), branch `main` |
| Railway | project `scintillating-rebirth`, service `hl-sniper`, EU West |
| URL | https://hl-sniper-production.up.railway.app |
| Endpoints | `/status` (JSON), `/health`, `/export` (full snapshot), `/` (dashboard) |
| Mode | paper. Live trading is NOT implemented (no order placement code) |
| Autodeploy | works — GitHub App installed, deploy trigger on `main` |
| Persistence | `/data/state.json` on a Railway volume (`STATE_DIR=/data`) |
| Env vars | `STATE_DIR=/data`, `UNIVERSE_SIZE=25`, `CONVERGENCE_THRESHOLD=0.55` |

Railway account is on a trial that has now lapsed into the Free plan ($1/month).
The bot costs ~$0.03/day, so it fits. Second account (`kasjanov1987big`) holds
`mavenpredict` and `grid-bot`; its trial expired ~2026-10-14 and its 500 MB
volume is deleted ~2026-11-13 unless a Hobby plan is taken. Back up that volume.

---

## Current live results (2026-10-15, 10 days of paper)

```
equity 9968   (start 10000)     peak 11570 (+15.7%)   drawdown from peak 13.8%
trades 60 (19W / 41L)           winrate 31.7%         avg R -0.13
long:  11 trades  WR 45%  PnL  -21.95
short: 19 trades  WR 32%  PnL  -84.04
open:  4 positions, all short
```

The bot rose 15.7% then gave it all back. It is 1.2% away from the 15% halt.

---

## Strategy (do not change without discussion)

- Universe: **25 coins, auto-scanned** (was 3 fixed majors). `universe.py`
  ranks every perp by ATR%, candle wickiness, Kaufman efficiency, impact spread
  and volume, then sorts into noise classes calm / normal / noisy / toxic.
  Toxic and flagged rows are not traded. Cached in `universe.json` on the
  volume, rescanned every 6h, cache invalidated on `SCANNER_VERSION` change.
- Timeframe 1h. Entry: breakout of a tested S/R level (min 2 touches) plus
  convergence of 4 weighted signals (trend 0.35 / volume 0.25 / orderbook 0.25
  / funding 0.15). Thresholds **0.55 entry / 0.70 strong**.
- Engine gate: spread <0.1%, book-walk slippage <0.5%, top-level liquidity
  >$5k, book freshness <3s, target clears expected costs by 1.5x.
- Risk: ATR stop 1.35, target 4.8 (R:R 3.5), partial 50% at +2R with stop to
  breakeven, Kelly fraction 0.25, cluster risk cap 3%, premium filter 1.5%.
- Guards: time-stop 36h, cooldown 6h after a stop, daily stop 10% UTC,
  halt at 15% drawdown.
- **Cost model**: fees, both-leg slippage and adverse hourly funding are paid
  out of the same risk budget as the stop distance.
- **Candle scan runs once an hour**, not every poll: 25 symbols at 5s would be
  ~18k candleSnapshot calls/hour against a ~1.2k budget.

---

## Code layout (all in repo root)

| File | Role |
|---|---|
| `bot.py` | main loop, entries, exits, risk, universe wiring, `/status` |
| `config.py` | all settings and env overrides |
| `sniper.py` | ConvergenceEngine + SniperModule (execution gate) |
| `levels.py`, `indicators.py` | pivots/breakouts, EMA/SMA/ATR/ADX |
| `universe.py` | perp screener + noise classification + cache |
| `state_store.py` | atomic JSON snapshots (survives restarts and redeploys) |
| `backtest.py` | walk-forward backtest with cached history in `bt_cache/` |
| `health_server.py`, `dashboard.html` | status server and terminal |
| `api_client.py` | Hyperliquid REST client |
| `test_*.py` | 116 self-checks, see below |

### Tests (all offline, all passing)

```
test_state_store.py    26   durable state, corruption fallback
test_costs.py          17   cost model, sizing, thresholds
test_universe.py       32   metrics, classification, cache invalidation
test_indicators.py     18   atr_series bit-identical to per-prefix atr
test_trade_accounting  14   partial PnL included, plain trade unchanged
test_backtest.py        9   funding alignment to the hour
```
Run: `PYTHONPATH=.devlibs python3 test_<name>.py`

---

## Backtest (208 days, cached history)

```
python3 backtest.py fetch --coins ...          # download + cache
python3 backtest.py run --coins ... --stops 1.35,2.5   # resumable, checkpoints
```

| Universe | Stop | Return | Trades | Winrate | avg R | PF | MaxDD |
|---|---|---|---|---|---|---|---|
| 12 majors | 1.35 | +7.7% | 35 | 42.9% | +0.30 | 1.42 | 6.7% |
| 12 majors | 2.50 | **+11.9%** | 31 | 45.2% | +0.30 | **1.68** | 7.3% |
| 25 live coins | 1.35 | +2.5% | 71 | 38.0% | +0.17 | 1.12 | 10.7% |
| 25 live coins | 2.50 | **+14.7%** | 62 | 46.8% | +0.35 | 1.43 | 10.3% |

Funding is small for us ($14–58 over 208 days, 0.1–0.6% of capital) because
holds are hours, not days. The friend's 89.5%-of-costs figure came from 1d
holds and does not apply here.

**Modelling caveats**: entries at the signal bar close; stops assumed before
targets when one bar could hit both (pessimistic); order book synthesised as
balanced (no historical L2); funding from the exchange's hourly history.

---

## Open decision — run the matrix

Their 400-day grid search landed on stop 1.35 (same as ours); our 208-day
backtest prefers 2.5. The difference may be their `PARTIAL_CLOSE_RATIO = 0.3`.
Resolve by measurement, not argument:

| Variant | Shorts | ATR trail | Partial | Stop |
|---|---|---|---|---|
| A (current) | on | off | 0.5 | 1.35 |
| B | **off** | off | 0.5 | 1.35 |
| C | off | **on** | 0.3 | 1.35 |
| D | off | on | 0.3 | **2.5** |

Rationale for each is in `research/REFERENCE_FRIEND_BOT.md`.

---

## Their reference bot — what we take (see the reference file for values)

Taken: shorts disabled, continuous ATR trailing after the partial, partial 30%
instead of 50%, Kelly without a floor (ours is a constant 1.25%), consecutive
loss pause, daily limit 2.5%, 5 correlation clusters, regime threshold
multiplier, anti-chase filters, signal reset on close, black/white lists,
hourly-tier exit latency, time-stop extension for winners, and for real money
EIP-712 signing, Decimal tick rounding, native reduce-only stops.

Not taken: their `screener.py` (hardcoded placeholders), `sandbox.py` (mock,
plus an 8x funding APR error), `integrated_strategies.py` (copy trading and
market making), `hyperliquid_ultimate_bot_v4_1.py` (no orders, no funding,
double-charged fees).

---

## Next steps

1. Run the A/B/C/D matrix above and pick the configuration on evidence.
2. Implement the taken items in priority order, with a test for each.
3. Consider a WebSocket feed (`ws_feed`): exits are checked every 5s while the
   backtest assumes an exact stop fill, so live stops fill worse than modelled.
4. Real-money layer only after the matrix, and only with an agent wallet
   (never the main key).

---

## Rules

- Latin-only code, no emoji (phone truncates Unicode files).
- Secrets only via Railway Variables, never in chat or code.
- The repo is PUBLIC: never commit credentials or third-party source.
- Strategy changes are discussed before they are deployed.
