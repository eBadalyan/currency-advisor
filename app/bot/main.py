from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.service import AnalyticsService
from app.collectors.bank_sources import BANK_SOURCES
from app.collectors.base import RatePoint
from app.collectors.cba import SOURCE_NAME
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionFactory
from app.recommendations.models import RecommendationAction
from app.recommendations.rule_based import RuleBasedRecommendationStrategy
from app.recommendations.service import RecommendationService
from app.repositories.exchange_rate import ExchangeRateRepository
from app.repositories.notification_state_repository import NotificationStateRepository
from app.services.bank_average_service import SOURCE_NAME as BANK_AVERAGE_SOURCE_NAME
from app.services.collector_health_service import CollectorHealthService, SourceHealth
from app.services.decline_alert_service import DeclineAlert, DeclineAlertService
from app.services.rate_service import RateService
from app.services.rise_alert_service import RiseAlert, RiseAlertService

_BASE_CURRENCY = "RUB"
_QUOTE_CURRENCY = "AMD"

_ACTION_LABELS = {
    RecommendationAction.EXCHANGE_NOW: "Менять сейчас",
    RecommendationAction.WAIT: "Подождать",
    RecommendationAction.NEUTRAL: "Нет чёткой рекомендации",
}

logger = logging.getLogger(__name__)

dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Currency Advisor отслеживает курс RUB/AMD по данным ЦБ Армении.\n"
        "Команда /rate — последний известный курс.\n"
        "Команда /banks — курс наличной покупки RUB в банках.\n"
        "Команда /advice — менять сейчас или подождать, и почему.\n"
        "Команда /status — актуальность данных по каждому источнику."
    )


async def format_rate_reply(service: RateService) -> str:
    point = await service.get_latest_rate(SOURCE_NAME, _BASE_CURRENCY, _QUOTE_CURRENCY)
    if point is None:
        return "Курс RUB/AMD пока не собран. Попробуйте позже."
    return f"RUB/AMD: {point.value} (на {point.observed_at:%Y-%m-%d}, источник: {point.source})"


@dp.message(Command("rate"))
async def rate(message: Message) -> None:
    async with SessionFactory() as session:
        service = RateService(ExchangeRateRepository(session))
        reply = await format_rate_reply(service)
    await message.answer(reply)


async def format_banks_reply(service: RateService) -> str:
    bank_points = [
        (source, await service.get_latest_rate(source, _BASE_CURRENCY, _QUOTE_CURRENCY))
        for source in BANK_SOURCES
    ]
    if all(point is None for _, point in bank_points):
        return "Банковские курсы наличного обмена пока не собраны. Попробуйте позже."

    lines = ["Курс наличной покупки RUB (банки):"]
    lines.extend(
        f"- {source}: {point.value}" if point is not None else f"- {source}: нет данных"
        for source, point in bank_points
    )

    median_point = await service.get_latest_rate(
        BANK_AVERAGE_SOURCE_NAME, _BASE_CURRENCY, _QUOTE_CURRENCY
    )
    if median_point is not None:
        lines.append(f"Медиана: {median_point.value}")

    return "\n".join(lines)


@dp.message(Command("banks"))
async def banks(message: Message) -> None:
    async with SessionFactory() as session:
        service = RateService(ExchangeRateRepository(session))
        reply = await format_banks_reply(service)
    await message.answer(reply)


async def format_advice_reply(service: RecommendationService) -> str:
    recommendation = await service.get_recommendation()
    if recommendation is None:
        return "Недостаточно данных для рекомендации. Попробуйте позже."

    lines = [_ACTION_LABELS[recommendation.action], "", recommendation.summary, "", "Факторы:"]
    lines.extend(f"- {factor.explanation}" for factor in recommendation.factors)
    return "\n".join(lines)


@dp.message(Command("advice"))
async def advice(message: Message) -> None:
    async with SessionFactory() as session:
        repository = ExchangeRateRepository(session)
        service = RecommendationService(
            repository, AnalyticsService(repository), RuleBasedRecommendationStrategy()
        )
        reply = await format_advice_reply(service)
    await message.answer(reply)


def _format_source_status(status: SourceHealth) -> str:
    pair = f"{status.base_currency}/{status.quote_currency}"
    if status.last_observed_at is None:
        state = "нет данных"
    else:
        timestamp = f"{status.last_observed_at:%Y-%m-%d %H:%M} UTC"
        state = f"устарело (последний раз {timestamp})" if status.is_stale else f"ок ({timestamp})"
    return f"- {status.source} ({pair}): {state}"


async def format_status_reply(service: CollectorHealthService) -> str:
    statuses = await service.get_status()
    lines = ["Статус источников данных:"]
    lines.extend(_format_source_status(status) for status in statuses)
    return "\n".join(lines)


@dp.message(Command("status"))
async def status(message: Message) -> None:
    async with SessionFactory() as session:
        service = CollectorHealthService(ExchangeRateRepository(session))
        reply = await format_status_reply(service)
    await message.answer(reply)


def _times_word(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "раз"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "раза"
    return "раз"


def format_decline_alert_message(alert: DeclineAlert, official_rate: RatePoint | None) -> str:
    lines = [
        "⚠️ RUB слабеет",
        (
            f"Банковский курс наличной покупки RUB упал {alert.streak_length} "
            f"{_times_word(alert.streak_length)} подряд, сейчас {alert.current_value}."
        ),
    ]
    if alert.previous_alerted_value is not None:
        lines.append(
            f"Ещё ниже, чем в прошлый раз я предупреждал ({alert.previous_alerted_value})."
        )
    if official_rate is not None:
        lines.append(f"Официальный курс ЦБ Армении: {official_rate.value}.")
    lines.append("Возможно, стоит разменять сейчас, пока не упал ещё ниже.")
    return "\n".join(lines)


async def check_decline_and_notify(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    admin_chat_id: int,
    streak_threshold: int,
) -> None:
    try:
        async with session_factory() as session:
            exchange_rates = ExchangeRateRepository(session)
            service = DeclineAlertService(
                exchange_rates,
                NotificationStateRepository(session),
                streak_threshold,
            )
            alert = await service.check()
            if alert is None:
                return
            official_rate = await exchange_rates.get_latest(
                SOURCE_NAME, _BASE_CURRENCY, _QUOTE_CURRENCY
            )
        text = format_decline_alert_message(alert, official_rate)
        # Trade-off: service.check() already persisted last_alerted_value above,
        # so delivery here is at-most-once, not exactly-once. If send_message fails
        # (Telegram outage, network issue), this specific alert is silently dropped —
        # it only re-fires on a further decline below this same floor. Accepted
        # because the harm is bounded (a persistent decline keeps re-alerting on the
        # next drop) and this is a single-admin, fixed-interval bot, not a system
        # with a delivery guarantee.
        await bot.send_message(admin_chat_id, text)
    except Exception:
        logger.exception("decline_alert.check_failed")


def format_rise_alert_message(alert: RiseAlert, official_rate: RatePoint | None) -> str:
    lines = [
        "📈 RUB укрепляется",
        (
            f"Банковский курс наличной покупки RUB вырос {alert.streak_length} "
            f"{_times_word(alert.streak_length)} подряд, сейчас {alert.current_value}."
        ),
    ]
    if alert.previous_alerted_value is not None:
        lines.append(f"Ещё выше, чем в прошлый раз я сообщал ({alert.previous_alerted_value}).")
    if official_rate is not None:
        lines.append(f"Официальный курс ЦБ Армении: {official_rate.value}.")
    return "\n".join(lines)


async def check_rise_and_notify(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    admin_chat_id: int,
    streak_threshold: int,
) -> None:
    try:
        async with session_factory() as session:
            exchange_rates = ExchangeRateRepository(session)
            service = RiseAlertService(
                exchange_rates,
                NotificationStateRepository(session),
                streak_threshold,
            )
            alert = await service.check()
            if alert is None:
                return
            official_rate = await exchange_rates.get_latest(
                SOURCE_NAME, _BASE_CURRENCY, _QUOTE_CURRENCY
            )
        text = format_rise_alert_message(alert, official_rate)
        # Same at-most-once delivery trade-off as check_decline_and_notify:
        # service.check() already persisted last_alerted_value above, so a
        # send_message failure here silently drops this specific alert.
        await bot.send_message(admin_chat_id, text)
    except Exception:
        logger.exception("rise_alert.check_failed")


async def main() -> None:
    configure_logging()
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is empty in .env")
    if not settings.telegram_admin_chat_id:
        raise RuntimeError("TELEGRAM_ADMIN_CHAT_ID is empty in .env")
    bot = Bot(settings.telegram_bot_token)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_decline_and_notify,
        trigger=IntervalTrigger(minutes=settings.alert_check_interval_minutes),
        args=(
            bot,
            SessionFactory,
            settings.telegram_admin_chat_id,
            settings.decline_alert_streak_threshold,
        ),
        id="decline_alert_check",
        next_run_time=datetime.now(UTC),
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    scheduler.add_job(
        check_rise_and_notify,
        trigger=IntervalTrigger(minutes=settings.alert_check_interval_minutes),
        args=(
            bot,
            SessionFactory,
            settings.telegram_admin_chat_id,
            settings.rise_alert_streak_threshold,
        ),
        id="rise_alert_check",
        next_run_time=datetime.now(UTC),
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    scheduler.start()
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
