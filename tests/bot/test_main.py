from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.main import format_rate_reply, rate, start
from app.collectors.base import RatePoint
from app.collectors.cba import SOURCE_NAME
from app.repositories.exchange_rate import ExchangeRateRepository
from app.services.rate_service import RateService

_OBSERVED_AT = datetime(2026, 7, 19, 20, 0, tzinfo=UTC)


def _rate_point(value: str) -> RatePoint:
    return RatePoint(SOURCE_NAME, "RUB", "AMD", Decimal(value), _OBSERVED_AT)


async def test_start_sends_welcome_message() -> None:
    message = AsyncMock()

    await start(message)

    message.answer.assert_awaited_once()
    assert "RUB/AMD" in message.answer.call_args.args[0]


async def test_format_rate_reply_with_data(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.save(_rate_point("4.6651"))
    service = RateService(repository)

    reply = await format_rate_reply(service)

    assert "RUB/AMD" in reply
    assert "4.6651" in reply
    assert "2026-07-19" in reply


async def test_format_rate_reply_with_no_data(db_session: AsyncSession) -> None:
    service = RateService(ExchangeRateRepository(db_session))

    reply = await format_rate_reply(service)

    assert "пока не собран" in reply


async def test_rate_handler_replies_with_formatted_rate(db_session: AsyncSession) -> None:
    await ExchangeRateRepository(db_session).save(_rate_point("4.6651"))
    message = AsyncMock()

    await rate(message)

    message.answer.assert_awaited_once()
    assert "4.6651" in message.answer.call_args.args[0]
