from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.service import AnalyticsService
from app.bot.main import (
    advice,
    banks,
    format_advice_reply,
    format_banks_reply,
    format_rate_reply,
    format_status_reply,
    rate,
    start,
    status,
)
from app.collectors.acba_bank import SOURCE_NAME as ACBA_BANK_SOURCE_NAME
from app.collectors.ameriabank import SOURCE_NAME as AMERIABANK_SOURCE_NAME
from app.collectors.base import RatePoint
from app.collectors.cba import SOURCE_NAME
from app.collectors.evocabank import SOURCE_NAME as EVOCABANK_SOURCE_NAME
from app.collectors.vtb_am import SOURCE_NAME as VTB_AM_SOURCE_NAME
from app.recommendations.rule_based import RuleBasedRecommendationStrategy
from app.recommendations.service import RecommendationService
from app.repositories.exchange_rate import ExchangeRateRepository
from app.services.bank_average_service import SOURCE_NAME as BANK_AVERAGE_SOURCE_NAME
from app.services.collector_health_service import CollectorHealthService
from app.services.rate_service import RateService

_OBSERVED_AT = datetime(2026, 7, 19, 20, 0, tzinfo=UTC)


def _rate_point(value: str) -> RatePoint:
    return RatePoint(SOURCE_NAME, "RUB", "AMD", Decimal(value), _OBSERVED_AT)


def _bank_point(source: str, value: str) -> RatePoint:
    return RatePoint(source, "RUB", "AMD", Decimal(value), _OBSERVED_AT)


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


async def test_rate_handler_replies_with_formatted_rate(
    db_session: AsyncSession,
    test_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # rate() opens its own session via the module-level SessionFactory
    # rather than taking one as a parameter (it's a real polling-bot
    # handler, not FastAPI-DI'd) — point that at the isolated test
    # database so it sees what db_session just seeded.
    monkeypatch.setattr("app.bot.main.SessionFactory", test_session_factory)
    await ExchangeRateRepository(db_session).save(_rate_point("4.6651"))
    message = AsyncMock()

    await rate(message)

    message.answer.assert_awaited_once()
    assert "4.6651" in message.answer.call_args.args[0]


async def test_format_banks_reply_with_no_data(db_session: AsyncSession) -> None:
    service = RateService(ExchangeRateRepository(db_session))

    reply = await format_banks_reply(service)

    assert "пока не собраны" in reply


async def test_format_banks_reply_with_partial_data(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.bulk_save(
        [
            _bank_point(AMERIABANK_SOURCE_NAME, "3.72"),
            _bank_point(EVOCABANK_SOURCE_NAME, "3.80"),
        ]
    )
    service = RateService(repository)

    reply = await format_banks_reply(service)

    assert "3.72" in reply
    assert "3.80" in reply
    assert f"{ACBA_BANK_SOURCE_NAME}: нет данных" in reply
    assert f"{VTB_AM_SOURCE_NAME}: нет данных" in reply
    # median wasn't computed/persisted (below BankAverageService's quorum)
    assert "Медиана" not in reply


async def test_format_banks_reply_with_full_data_includes_median(
    db_session: AsyncSession,
) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.bulk_save(
        [
            _bank_point(AMERIABANK_SOURCE_NAME, "3.72"),
            _bank_point(EVOCABANK_SOURCE_NAME, "3.80"),
            _bank_point(ACBA_BANK_SOURCE_NAME, "3.73"),
            _bank_point(VTB_AM_SOURCE_NAME, "3.75"),
            _bank_point(BANK_AVERAGE_SOURCE_NAME, "3.74"),
        ]
    )
    service = RateService(repository)

    reply = await format_banks_reply(service)

    assert "3.72" in reply
    assert "3.80" in reply
    assert "3.73" in reply
    assert "3.75" in reply
    assert "Медиана: 3.74" in reply


async def test_banks_handler_replies_with_formatted_rates(
    db_session: AsyncSession,
    test_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.bot.main.SessionFactory", test_session_factory)
    await ExchangeRateRepository(db_session).save(_bank_point(AMERIABANK_SOURCE_NAME, "3.72"))
    message = AsyncMock()

    await banks(message)

    message.answer.assert_awaited_once()
    assert "3.72" in message.answer.call_args.args[0]


async def test_format_advice_reply_with_no_data(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    service = RecommendationService(
        repository, AnalyticsService(repository), RuleBasedRecommendationStrategy()
    )

    reply = await format_advice_reply(service)

    assert "Недостаточно данных" in reply


async def test_format_advice_reply_with_data(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.save(_rate_point("4.6651"))
    service = RecommendationService(
        repository, AnalyticsService(repository), RuleBasedRecommendationStrategy()
    )

    reply = await format_advice_reply(service)

    assert "не предсказывает будущее" in reply
    assert "Факторы:" in reply


async def test_advice_handler_replies_with_formatted_recommendation(
    db_session: AsyncSession,
    test_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.bot.main.SessionFactory", test_session_factory)
    await ExchangeRateRepository(db_session).save(_rate_point("4.6651"))
    message = AsyncMock()

    await advice(message)

    message.answer.assert_awaited_once()
    assert "не предсказывает будущее" in message.answer.call_args.args[0]


async def test_format_status_reply_lists_every_tracked_source(db_session: AsyncSession) -> None:
    service = CollectorHealthService(ExchangeRateRepository(db_session))

    reply = await format_status_reply(service)

    assert "Статус источников данных:" in reply
    assert f"- {SOURCE_NAME} (RUB/AMD): нет данных" in reply
    assert f"- {AMERIABANK_SOURCE_NAME} (RUB/AMD): нет данных" in reply


async def test_format_status_reply_shows_fresh_source_as_ok(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.services.collector_health_service._utcnow", lambda: _OBSERVED_AT)
    await ExchangeRateRepository(db_session).save(_rate_point("4.6651"))
    service = CollectorHealthService(ExchangeRateRepository(db_session))

    reply = await format_status_reply(service)

    assert f"- {SOURCE_NAME} (RUB/AMD): ок (2026-07-19 20:00 UTC)" in reply


async def test_status_handler_replies_with_formatted_status(
    db_session: AsyncSession,
    test_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.bot.main.SessionFactory", test_session_factory)
    monkeypatch.setattr("app.services.collector_health_service._utcnow", lambda: _OBSERVED_AT)
    await ExchangeRateRepository(db_session).save(_rate_point("4.6651"))
    message = AsyncMock()

    await status(message)

    message.answer.assert_awaited_once()
    assert "Статус источников данных:" in message.answer.call_args.args[0]
