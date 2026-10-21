"""Single registry of every strategy and every guard the bot has.

The dashboard used to show what the bot is doing but not what it decided to
leave alone, so the honest state of the bot (one measured strategy, one live
measurement, a pile of rejected ideas) was invisible. This module is the one
place where a strategy or a guard is declared, together with the evidence that
put it in that state, and the live numbers are merged in on top.

Statuses:
  on        working, allowed to trade
  paper     running for measurement only, no orders
  off       implemented but switched off by configuration
  rejected  measured, did not clear its own acceptance bar, never again
  missing   named idea, nothing built yet
"""
from __future__ import annotations

import config

ON, PAPER, OFF, REJECTED, MISSING = "on", "paper", "off", "rejected", "missing"

CAT_STRATEGY = "strategy"
CAT_FILTER = "filter"
CAT_RISK = "risk"
CAT_INFRA = "infra"
CAT_ORDER = (CAT_STRATEGY, CAT_FILTER, CAT_RISK, CAT_INFRA)


def pct(x, d=1):
    return None if x is None else round(float(x), d)


def _flag(x):
    return bool(x)


# The catalog. `evidence` is a measurement, not a promise: every string here is
# traceable to RESEARCH_LOG.md. If a number here is wrong the log is wrong, and
# that is the point - the dashboard cannot quietly drift from the research.
CATALOG = (
    # ---- strategies ------------------------------------------------------
    dict(id="pairs", cat=CAT_STRATEGY, name="Парный арбитраж", status=PAPER,
         evidence="+17.9%, PF 1.22, DD 5.4%, corr -0.146 - единственная стратегия, прошедшая OOS"),
    dict(id="grid", cat=CAT_STRATEGY, name="Сетка в боковике", status=PAPER,
         evidence="бэктест NEXT-BAR: PF 1.70, E +0.15..+0.40%/лот, 96% WR; DD 24%, 29% времени под водой"),
    dict(id="sniper", cat=CAT_STRATEGY, name="Снайпер (конвергенция)", status=OFF,
         evidence="бэктест +0.02%/сделку - шум; отключён, на реальные деньги не пойдёт"),
    dict(id="ma100", cat=CAT_STRATEGY, name="МА100 тренд", status=REJECTED,
         evidence="+0.575%/сделку, PF 1.32, t=1.50 - незначимо на 210 днях"),
    dict(id="breakout", cat=CAT_STRATEGY, name="Пробой Дончиана", status=REJECTED,
         evidence="+0.02%/сделку после издержек - ноль"),
    dict(id="rsi2", cat=CAT_STRATEGY, name="Свинг-реверсия RSI(2)", status=REJECTED,
         evidence="наш замер 21.10: PF 0.97 с фильтром SMA100, DD 47-77% без фильтра"),
    dict(id="levels", cat=CAT_STRATEGY, name="Отскок от уровня", status=OFF,
         evidence="уровни считаются (levels.py), входа по ним нет; у него бэктест -$37"),
    dict(id="momentum", cat=CAT_STRATEGY, name="Моментум интрадей", status=REJECTED,
         evidence="его данные: убыточен в 4/4 окнах, t=-5..-7; наш замер не понадобился"),
    dict(id="scalper", cat=CAT_STRATEGY, name="Скальпер", status=REJECTED,
         evidence="издержки 0.177R больше эджа 0.146R (его же расчёт)"),
    dict(id="impulse", cat=CAT_STRATEGY, name="Импульс", status=REJECTED,
         evidence="лучший конфиг t=1.17 - незначимо"),
    dict(id="cascade", cat=CAT_STRATEGY, name="Каскад-бай (покупка обвала)", status=REJECTED,
         evidence="наш замер 21.10 (#24): брутто +0.14%, нетто -0.05% при 0.19% издержек круга, t 0.67 на 226 независимых сделках - сигнал не покрывает комиссии"),
    dict(id="funding", cat=CAT_STRATEGY, name="Фандинг-кэрри", status=REJECTED,
         evidence="наш замер 21.10 (#25): фандинг +6.55% за 208 дней, но цена -26.64% (шорт горячих монет -52.42%), t -1.15 - ставка против моментума"),
    dict(id="althunt", cat=CAT_STRATEGY, name="Охота за новыми активами", status=MISSING,
         evidence="скан новых листингов/возвратных монет - не реализовано"),

    # ---- filters ---------------------------------------------------------
    dict(id="cost_gate", cat=CAT_FILTER, name="Гейт издержек", status=ON,
         evidence="цель должна покрывать комиссии и фандинг с запасом x1.5, до входа"),
    dict(id="adx_filter", cat=CAT_FILTER, name="Фильтр силы тренда (ADX)", status=ON,
         evidence="ADX ниже порога - сигнал не считается трендовым; плюс порог по режиму волатильности"),
    dict(id="quality_filter", cat=CAT_FILTER, name="Лимиты исполнения", status=ON,
         evidence="спред, слиппедж, ликвидность книги, свежесть книги - отказ до входа"),
    dict(id="universe", cat=CAT_FILTER, name="Подбор пула монет", status=ON,
         evidence="объём, спред, шум-класс; пул обновляется по расписанию"),
    dict(id="blacklist", cat=CAT_FILTER, name="Автоблэклист монет", status=ON,
         evidence="2 стопа за 30 дней -> пауза 14 дней, как у него"),
    dict(id="chart_blocks", cat=CAT_FILTER, name="Блоки входа по графику (каскад, сопротивление)", status=REJECTED,
         evidence="наш замер 21.10 (#23): PF 1.31-1.39 против 1.385 базы, стопы 4.4 на 100 лотов против 4.2 - режут хорошие входы наравне с плохими"),
    dict(id="news", cat=CAT_FILTER, name="Новостной фильтр RSS -> LLM", status=MISSING,
         evidence="не реализовано; требует ключа LLM и ленты, в paper не влияет на P&L"),

    # ---- risk ------------------------------------------------------------
    dict(id="risk_budget", cat=CAT_RISK, name="Риск-бюджет портфеля", status=ON,
         evidence="6% капитала до стопов против его 35%"),
    dict(id="cluster_limits", cat=CAT_RISK, name="Лимиты кластеров и направления", status=ON,
         evidence="максимум риска на кластер и на направление - не больше одной ставки на одну тему"),
    dict(id="kelly", cat=CAT_RISK, name="Дробный Келли", status=ON,
         evidence="f=0.25 от полного Келли, отрицательное матожидание режет размер"),
    dict(id="margin_cap", cat=CAT_RISK, name="Кэп маржи", status=ON,
         evidence="не более 50% капитала в залоге; у него сейчас 95%"),
    dict(id="max_positions", cat=CAT_RISK, name="Лимит позиций", status=ON,
         evidence="4 одновременно, у него 6"),
    dict(id="halt_dd", cat=CAT_RISK, name="Стоп-кран по просадке", status=ON,
         evidence="15% от пика - полная остановка"),
    dict(id="daily_stop", cat=CAT_RISK, name="Дневной стоп", status=ON,
         evidence="10% за UTC-день - стоп до следующих суток"),
    dict(id="compounding", cat=CAT_RISK, name="Компаундинг", status=ON,
         evidence="размер считается от текущего капитала, не от стартового"),

    # ---- infra -----------------------------------------------------------
    dict(id="journal", cat=CAT_INFRA, name="Журнал решений", status=ON,
         evidence="каждый вход и выход с числами, которые его обосновали"),
    dict(id="state", cat=CAT_INFRA, name="Сохранение состояния", status=ON,
         evidence="позиции и лоты переживают рестарт; состояние на томе Railway"),
    dict(id="alerts", cat=CAT_INFRA, name="Оповещения", status=OFF,
         evidence="Telegram подключён, но выключен - нужен токен и чат"),
    dict(id="ai_shadow", cat=CAT_INFRA, name="ИИ-аналитик (shadow)", status=MISSING,
         evidence="оценивает входы, не влияет на решения - не реализовано"),
    dict(id="alo", cat=CAT_INFRA, name="ALO / post-only ордера", status=MISSING,
         evidence="maker-исполнение 0.015% вместо 0.045%; в paper не нужны, понадобятся на реале"),
)


def build(status):
    """Merge live numbers onto the catalog.

    `status` is the dict the bot already publishes, so the registry never
    computes trading state a second way and can never disagree with the rest of
    the dashboard about what is open.
    """
    st = status or {}
    settings = st.get("settings") or {}
    pairs = st.get("pairs") or {}
    grid = st.get("grid") or {}
    sn = st.get("sniper_stats") or {}
    coins = st.get("coins") or []
    equity = st.get("equity")

    live = {
        "pairs": dict(
            value=("open %d, realised $%s" % (pairs.get("open", 0), pct(pairs.get("pnl"), 2))
                   if pairs.get("enabled") else "выключен"),
            detail="сделок %d, winrate %s%%, нереализовано $%s" % (
                pairs.get("trades", 0), pct(pairs.get("winrate"), 1),
                pct(pairs.get("unrealized"), 2))),
        "grid": dict(
            value="лотов %d, equity $%s" % (grid.get("open_count", 0), pct(grid.get("equity"), 2)),
            detail="закрыто %d, лот $%s, ошибка: %s" % (
                grid.get("lots_closed", 0), pct(grid.get("lot_usd"), 2),
                grid.get("last_error") or "нет")),
        "sniper": dict(
            value="снайпов %d" % sn.get("total_snipes", 0),
            detail="сильных %d, средний слиппедж %s bps" % (
                sn.get("strong_snipes", 0), pct(sn.get("avg_slippage_bps"), 1))),
        "blacklist": dict(value="в бане %d" % len(st.get("benched") or []),
                          detail=", ".join(st.get("benched") or []) or "чисто"),
        "universe": dict(value="%d монет" % ((st.get("universe") or {}).get("size") or 0),
                         detail="классов шума: %d" % len({
                             v for v in ((st.get("universe") or {}).get("classes") or {}).values() if v})),
        "risk_budget": dict(value="%s%% открыто" % pct(st.get("open_risk_pct"), 2),
                            detail="лимит %s%% капитала" % pct(100 * config.MAX_TOTAL_RISK_PCT, 1)),
        "halt_dd": dict(value="DD %s%%" % pct(st.get("drawdown_pct"), 2),
                        detail="стоп на %s%%" % pct(100 * config.HALT_DD_PCT, 0)),
        "daily_stop": dict(value="сегодня %s%%" % pct(st.get("day_pnl_pct"), 2),
                           detail="стоп на %s%%" % pct(100 * config.DAILY_STOP_PCT, 1)),
        "margin_cap": dict(value="капитал $%s" % pct(equity, 2),
                           detail="кэп маржи %s%%, у друга 95%%" % pct(
                               100 * config.MAX_MARGIN_UTILIZATION, 0)),
        "max_positions": dict(value="%d из %d" % (len(st.get("positions") or []), config.MAX_POSITIONS),
                              detail="у друга 6"),
        "kelly": dict(value="f=%s" % pct(config.KELLY_FRACTION, 2),
                      detail="ставка %s%% капитала" % pct(st.get("kelly_risk_pct"), 2)),
        "alerts": dict(value="Telegram", detail="не настроен - токена нет"),
        "cost_gate": dict(value="порог x%s" % pct(settings.get("min_rr_after_costs"), 1),
                          detail="базовая стоимость цикла %s%%" % pct(
                              100 * (settings.get("cost_pct_base") or 0), 3)),
        "chart_blocks": dict(value="0 блоков", detail="не реализовано"),
        "coins": dict(value="%d в разборе" % len(coins), detail=""),
    }

    items = []
    for row in CATALOG:
        item = dict(row)
        extra = live.get(item["id"])
        if extra:
            item["live"] = extra.get("value")
            item["live_detail"] = extra.get("detail")
        # An 'off' strategy that is actually enabled by config must say so,
        # otherwise the panel lies in the other direction.
        if item["id"] == "sniper":
            item["status"] = ON if st.get("sniper_enabled") else OFF
        if item["id"] == "pairs" and not pairs.get("enabled"):
            item["status"] = OFF
        if item["id"] == "grid" and grid.get("enabled") is False:
            item["status"] = OFF
        if item["id"] == "alerts" and config.TELEGRAM_TOKEN:
            item["status"] = ON
        items.append(item)

    counts = {CAT_STRATEGY: {}, CAT_FILTER: {}, CAT_RISK: {}, CAT_INFRA: {}}
    for item in items:
        bucket = counts[item["cat"]]
        bucket[item["status"]] = bucket.get(item["status"], 0) + 1
    total = len(items)
    ok = sum(1 for i in items if i["status"] in (ON, PAPER))
    return {
        "items": items,
        "counts": counts,
        "total": total,
        "working": ok,
        "off": sum(1 for i in items if i["status"] == OFF),
        "rejected": sum(1 for i in items if i["status"] == REJECTED),
        "missing": sum(1 for i in items if i["status"] == MISSING),
        "paper": sum(1 for i in items if i["status"] == PAPER),
    }
