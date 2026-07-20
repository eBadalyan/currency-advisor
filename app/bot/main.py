from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.collectors.cba import SOURCE_NAME
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionFactory
from app.repositories.exchange_rate import ExchangeRateRepository
from app.services.rate_service import RateService

_BASE_CURRENCY = "RUB"
_QUOTE_CURRENCY = "AMD"

dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Currency Advisor отслеживает курс RUB/AMD по данным ЦБ Армении.\n"
        "Команда /rate — последний известный курс."
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


async def main() -> None:
    configure_logging()
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is empty in .env")
    bot = Bot(settings.telegram_bot_token)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
