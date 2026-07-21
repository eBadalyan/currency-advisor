from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.analytics.service import AnalyticsService
from app.collectors.acba_bank import SOURCE_NAME as ACBA_BANK_SOURCE_NAME
from app.collectors.ameriabank import SOURCE_NAME as AMERIABANK_SOURCE_NAME
from app.collectors.cba import SOURCE_NAME
from app.collectors.evocabank import SOURCE_NAME as EVOCABANK_SOURCE_NAME
from app.collectors.vtb_am import SOURCE_NAME as VTB_AM_SOURCE_NAME
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionFactory
from app.recommendations.models import RecommendationAction
from app.recommendations.rule_based import RuleBasedRecommendationStrategy
from app.recommendations.service import RecommendationService
from app.repositories.exchange_rate import ExchangeRateRepository
from app.services.bank_average_service import SOURCE_NAME as BANK_AVERAGE_SOURCE_NAME
from app.services.collector_health_service import CollectorHealthService, SourceHealth
from app.services.rate_service import RateService

_BASE_CURRENCY = "RUB"
_QUOTE_CURRENCY = "AMD"

# Display order for /banks — not alphabetical, just a stable, readable order.
_BANK_SOURCES = (
    AMERIABANK_SOURCE_NAME,
    EVOCABANK_SOURCE_NAME,
    ACBA_BANK_SOURCE_NAME,
    VTB_AM_SOURCE_NAME,
)

_ACTION_LABELS = {
    RecommendationAction.EXCHANGE_NOW: "Менять сейчас",
    RecommendationAction.WAIT: "Подождать",
    RecommendationAction.NEUTRAL: "Нет чёткой рекомендации",
}

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
        for source in _BANK_SOURCES
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


async def main() -> None:
    configure_logging()
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is empty in .env")
    bot = Bot(settings.telegram_bot_token)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
