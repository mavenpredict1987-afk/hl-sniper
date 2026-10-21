# HL SNIPER BOT — PROJECT STATE

Updated: 2026-10-21. Paper-first Hyperliquid perp bot. **This file is the entry
point for a new session — read it first, then `research/REFERENCE_FRIEND_BOT.md`.**

---

## Состояние на 2026-10-22 02:00 MSK (свежее; разделы ниже — история)

- Счёт бумажный: **$10 104.70**, начальный $10 000, пик $11 569.82, просадка от
  пика 12.66%. Снайпер выключен, живут пары + лабораторные сетки + сетка.
- **Цикл 3 пар идёт** (открыт 21.10 07:32 MSK: ADA/CRV, AVAX/WLD, JUP/XMR по
  $336.8 на ногу), 18.4ч из 48, нереализовано +$2.95, закроется 23.10 ~07:32.
  Реализовано пока +$19.17 на 6 парах, покрытие 5.12x (`thin`).
- **⚠️ Деплои не доезжают с 21.10 22:56 UTC.** Проверено без API: маркер
  `deploy-probe` в `dashboard.html` (коммит `48f94d8`) запушился, 11 минут
  опроса — маркера на сайте нет, `restarts` стоит на 64. Вчера такой же пуш
  деплоился за 1–3 мин. Торговля не затронута (контейнер живёт на старом коде),
  но новые изменения не применяются. Причина не установлена: либо пауза
  деплоев из-за лимитов триала, либо отвязался GitHub-источник. Нужен свежий
  взгляд пользователя в панели Railway/новый токен.
- **⚠️ Railway API-токен перестал авторизоваться 22.10** (`me`, `projects`,
  `deployments` → «Not Authorized», файл `.secrets/rw_token`). Бот и
  автодеплой от этого не зависят (деплой идёт через GitHub App), но логи
  деплоя и триггеры через API недоступны. Нужен новый токен от пользователя —
  до переезда это всё равно обязательный шаг (новый аккаунт = новый токен).
  Пока деплой проверяем косвенно: счётчик `restarts` в `/status` растёт на
  каждый старт контейнера.
- **Пары:** 2 цикла закрыто, реализовано **+$19.17** на 6 парах, среднее
  покрытие издержек 5.12x (grade `thin`). Цикл 2 (+$11.17) записан в
  `RESEARCH_LOG.md` §26; там же честная оговорка: плюс держится на одной паре
  (ONDO/PAXG = 165% итога), медианная пара убыточна, критерий концентрации
  пробит → ещё 2 цикла и лимиты на монету/сектор до реальных денег.
- **Дашборд v1.4** (деплой `8379501`): реестр стратегий вкладками (33 модуля:
  18 работает, 2 бумажных, 2 выключено, 10 отклонено, 3 не сделано), разбор по
  монетам, канал оповещений показывает реальную доставку (`status.telegram`),
  серая категория подписана «не сделано», а не «нет».
- **ALO / post-only измерен и отклонён 21.10** (`RESEARCH_LOG.md` §27,
  `research/ALO_PREREG.md`, скрипт `bt_alo.py`, тест `test_alo.py`): fill rate
  94% за 4ч при буфере 0.05%, издержки пары падают 0.230% → 0.146% (−36%), но
  сдвиг после филла −0.18% против экономии 0.043% на ногу; сквозной итог +0.046%
  на пару (~0.13% счёта за 208 дней) — внутри шума. Предрегистрированные A2 и A4
  провалены, сдвиг лимита на 0.5% дал +0.048% in-sample и −0.023% out-of-sample
  (подгонка). На реальные деньги не берём. Следующий шаг, если захотим добить —
  теневой учёт на живых входах (30–60 наблюдений), а не бэктест.
- **Отклонено по цифрам** (см. `RESEARCH_LOG.md` §22–§25, §27): RSI(2), блоки
  входа сетки, каскад-бай, фандинг-кэрри, ALO/post-only. В реестре они стоят как
  «отклонён»: реестр теперь 18 работает / 2 paper / 2 выключено / **10
  отклонено** / **3 не сделано** (остались охота за листингами, новостной фильтр,
  ИИ-аналитик).
- **Переезд на новый GitHub-аккаунт + Railway Hobby** — план в конце файла,
  ориентир ~28.10. До переезда поведение не меняем.
- Фриз измерения до **31.10.2026** действует: параметры пар/сетки не трогаем.

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

---

# Переезд на новый аккаунт GitHub + Railway (запланирован на ~2026-10-28)

Пользователь ставит это как факт, а не идею: **через неделю проект переезжает
в другой GitHub-аккаунт и другой репозиторий, а оттуда — на Railway, где у
нового аккаунта уже оплачен план Hobby.** Hobby-план снимает паузу деплоев,
которая сейчас висит на триальном аккаунте (см. ниже). До переезда ничего не
трогаем: текущий сервис продолжает торговать бумагой, правки копятся в git.

## Почему вообще переезжаем

- Trial-аккаунт Railway ставит деплои на паузу («Limited Access — Deploys have
  been paused temporarily»): 21.10 пауза провисела ~10 минут и отпустила сама,
  но это может вернуться в любой момент. Платные планы под паузу не попадают.
- У нового аккаунта Hobby уже оплачен, то есть за $5/мес платить дважды нельзя:
  апгрейд текущего триала до Hobby **не делаем** — через неделю этот аккаунт
  сдаётся, деньги ушли бы впустую.

## Что должно переехать целиком

Не только код, а **состояние измерения**: 11 дней фриза нельзя терять, иначе
циклы пар/сетки начнутся с нуля.

| Что | Откуда | Куда |
|---|---|---|
| Код | репозиторий `mavenpredict1987-afk/hl-sniper` (main) | новый репозиторий нового аккаунта, полная история |
| `state.json`, журналы, `grid_state`/`pairs_state`/`lab` | том `/data` (`hl-sniper-volume`, id `85c599f3-5dba-4b16-b795-b537eb2a2031`) | том нового сервиса, тот же `STATE_DIR=/data` |
| Переменные окружения | Railway Variables (20 шт.: `TRADING_MODE`, `INITIAL_CAPITAL`, `STATE_DIR`, `SYMBOLS`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`) | Variables нового сервиса |
| Домен | `hl-sniper-production.up.railway.app` | новый `*.up.railway.app` (старый при переезде умирает) |

`RAILWAY_*` переменные Railway выдаёт сам — их копировать не нужно и нельзя.

## Порядок работ (по шагам, ничего не забыть)

1. **Выгрузить состояние до переезда.** Активной деплой-паузы в этот момент
   может не быть, но полагаться на неё нельзя: сначала вытаскиваем `/data`
   (state.json + журналы + lab). **`/export` для этого НЕ годится** — проверено
   22.10: это тот же `bot_status`, что `/status`, с урезанными списками
   (journal 30, trades 30), а не файл состояния. Нужен доступ к тому: Railway
   CLI (`railway ssh` / `railway run cat /data/state.json`) под токеном проекта
   либо файловый браузер тома в панели. Бандл кладём в `.secrets/` и в
   приватную копию, в публичный репозиторий — никогда.
   Локально для этого есть node/npm (v26.4.0 / 11.20.0) — CLI ставится как
   `npm i -g @railway/cli`.
2. **Новый репозиторий.** `gitpush`-скрипт под новый `owner/repo` и новый
   токен; полная история (`main`), публичность — по желанию пользователя.
   **Токен Railway нужен новый в любом случае:** старый (`.secrets/rw_token`,
   аккаунт `kasjanov1987big`) с 22.10 отвечает «Not Authorized» на все запросы,
   а переезд идёт на другой аккаунт.
3. **Новый сервис на Railway.** Создать сервис из нового репозитория, ветка
   `main`, Dockerfile как сейчас (`railway.toml` едет вместе с кодом),
   `startCommand = python bot.py`.
4. **Том и переменные.** Примонтировать том к `/data`, проставить `STATE_DIR`,
   `TRADING_MODE=paper`, `INITIAL_CAPITAL`, `SYMBOLS`, телеграм; загрузить
   сохранённое состояние в том до первого старта бота.
5. **Деплой и сверка.** `GET /status`: `equity`, `trades`, `pairs`, `grid`,
   `journal_counts` должны совпасть с выгруженными до переезда. Расхождение —
   стоп, разбираемся до дальнейших действий.
6. **Перевод наблюдателя.** Панель/мониторинг/скрипты — на новый URL;
   зафиксировать `deploymentTrigger` (provider github, repository новый,
   branch main) и `serviceInstanceAutoDeployUpdate(enabled: true)`, иначе
   снова будем пушить в пустоту.
7. **Старый проект.** Остановить сервис, но том **не удалять**, пока новый не
   подтвердит сохранность состояния (и ещё 30 дней — Railway удаляет тома
   триальных аккаунтов через 30 дней после окончания кредита).

## Что проверить после переезда обязательно

- `/status`: `strategies.rejected == 10`, `missing == 3` (реестр из §22–§27),
  панель с вкладками жива;
- `watch.seen` растёт (поток замера монет);
- `trades`/`equity` продолжают ряд, а не начинают с 10000;
- деплой по пушу проходит без паузы (это и есть цель переезда).
