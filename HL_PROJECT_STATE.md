# HL SNIPER BOT - PROJECT STATE (v1.2)

Date: 2026-10-03. Paper-first sniper bot for Hyperliquid perps.

## Repo layout (files in ROOT, not src/)
config.py, bot.py, sniper.py, api_client.py, indicators.py,
levels.py, utils.py, health_server.py, dashboard.html,
requirements.txt, Dockerfile, railway.toml, README.md, .env.example

## Status
- v1.2 code complete, syntax-checked, smoke-tested.
- Deploy to Railway EU West: PENDING. Variables: TRADING_MODE=paper,
  INITIAL_CAPITAL=10000, SYMBOLS=BTC,ETH,SOL.
- Old GitHub account: mavenpredict1987-afk/hl-sniper (out of tokens).
  Continue from a NEW GitHub account, upload files from this archive.

## Strategy (do not change without discussion)
- Universe: BTC,ETH,SOL. TF: 1h. Entry: breakout of tested S/R level
  (min 2 touches) + convergence of 4 signals (trend ADX>=25,
  volume >=1.5x, orderbook depth, hourly funding counter-skew).
- Convergence thresholds: entry 0.60, strong 0.75 (recalibrated;
  friend's 0.75/0.90 was unreachable, his bot never traded).
- Risk: ATR stop 1.35, target 4.8 (R:R 3.5), partial 50% at +2R with
  stop to breakeven, Kelly fraction 0.25 cap 2%, cluster risk cap 3%,
  premium filter 1.5%, viability floor: target >= 0.3%.
- Guards: time-stop 36h (hourly funding kills long holds - friend's
  400d backtest: funding was 89.5% of all costs), cooldown 6h after
  stop, daily stop -10% UTC, halt -15% DD.
- Execution gate from friend's sniper.py: spread <0.1%, slippage from
  book walk <0.5% (unfillable book = reject), liquidity >$5k.

## Key lessons baked in (from friend's FINAL_REPORT_v42)
- 1d TF incompatible with hourly funding. If Phase A paper shows edge,
  Phase B = 5m execution layer with maker (Alo) entries, holds <2h.
- His v4.1 monolith code NOT taken (pandas, emoji logs, -44% backtest).
  Concepts taken: time-stop, cooldown, viability filter, R-tracking.
- Funding on Hyperliquid is HOURLY - any funding_8h logic is broken.

## Next steps
1. New GitHub repo, upload all files from archive (root level).
2. Railway: Deploy from repo, region EU West, set variables, check logs
   for "bot v1.1 started" line, open dashboard URL.
3. Observe paper 3-7 days: events, avg R, winrate on dashboard.
4. Then: Phase B backtest engine (walk-forward) or 5m execution layer.

## Rules
- Latin-only code, no emoji (phone truncates Unicode files).
- Secrets only via Railway Variables, never in chat or code.
