# HL Sniper Bot v1.0

Hyperliquid perps sniper bot. Paper mode by default.

## Files (repo root)
- config.py - all settings
- bot.py - main loop
- sniper.py - convergence engine
- levels.py - support/resistance levels
- indicators.py - EMA/ATR/ADX
- api_client.py - Hyperliquid REST client
- health_server.py + dashboard.html - web status page

## Deploy on Railway
1. GitHub repo with all files in root.
2. Railway: New Project -> Deploy from GitHub -> pick repo.
3. Region: EU West.
4. Variables: TRADING_MODE=paper, INITIAL_CAPITAL=10000, SYMBOLS=BTC,ETH,SOL.
5. Optional alerts: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID.
6. Open the generated URL - dashboard.

## Rules
- Never commit PRIVATE_KEY. Live mode is phase 2.
- All logs are latin-only.
