from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.analytics.service import AnalyticsService
from app.collectors.cba import SOURCE_NAME
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionFactory
from app.recommendations.models import RecommendationAction
from app.recommendations.rule_based import RuleBasedRecommendationStrategy
from app.recommendations.service import RecommendationService
from app.repositories.exchange_rate import ExchangeRateRepository
from app.services.rate_service import RateService

_BASE_CURRENCY = "RUB"
_QUOTE_CURRENCY = "AMD"

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
        "Команда /advice — менять сейчас или подождать, и почему."
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


async def main() -> None:
    configure_logging()
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is empty in .env")
    bot = Bot(settings.telegram_bot_token)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
