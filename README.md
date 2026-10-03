# HL Sniper Bot v1.0

Hyperliquid perps sniper bot. Paper mode by default.

## Files
- src/config.py - all settings
- src/bot.py - main loop
- src/sniper.py - convergence engine
- src/levels.py - support/resistance levels
- src/indicators.py - EMA/ATR/ADX
- src/api_client.py - Hyperliquid REST client
- src/health_server.py + dashboard.html - web status page

## Deploy on Railway
1. Create GitHub repo, upload all files keeping structure.
2. Railway: New Project -> Deploy from GitHub -> pick repo.
3. Region: EU West.
4. Variables: TRADING_MODE=paper, INITIAL_CAPITAL=10000, SYMBOLS=BTC,ETH,SOL.
5. Optional alerts: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID.
6. Open the generated URL - dashboard.

## Rules
- Never commit PRIVATE_KEY. Live mode is phase 2.
- All logs are latin-only.
