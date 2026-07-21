from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.service import AnalyticsService
from app.collectors.base import RatePoint
from app.collectors.cba import SOURCE_NAME as CBA_SOURCE_NAME
from app.collectors.cbr import SOURCE_NAME as CBR_SOURCE_NAME
from app.recommendations.rule_based import RuleBasedRecommendationStrategy
from app.recommendations.service import RecommendationService
from app.repositories.exchange_rate import ExchangeRateRepository
from app.services.bank_average_service import SOURCE_NAME as BANK_AVERAGE_SOURCE_NAME

_START = datetime(2026, 7, 1, 20, 0, tzinfo=UTC)


def _cba_rub_amd(value: str, day_offset: int) -> RatePoint:
    return RatePoint(
        CBA_SOURCE_NAME, "RUB", "AMD", Decimal(value), _START + timedelta(days=day_offset)
    )


def _cba_usd_amd(value: str) -> RatePoint:
    return RatePoint(CBA_SOURCE_NAME, "USD", "AMD", Decimal(value), _START)


def _cbr_usd_rub(value: str) -> RatePoint:
    return RatePoint(CBR_SOURCE_NAME, "USD", "RUB", Decimal(value), _START)


def _bank_average_rub_amd(value: str) -> RatePoint:
    return RatePoint(BANK_AVERAGE_SOURCE_NAME, "RUB", "AMD", Decimal(value), _START)


def _make_service(repository: ExchangeRateRepository) -> RecommendationService:
    return RecommendationService(
        repository, AnalyticsService(repository), RuleBasedRecommendationStrategy()
    )


async def test_get_recommendation_returns_none_without_official_rate(
    db_session: AsyncSession,
) -> None:
    service = _make_service(ExchangeRateRepository(db_session))

    assert await service.get_recommendation() is None


async def test_get_recommendation_computes_source_deviation_via_usd_bridge(
    db_session: AsyncSession,
) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.bulk_save(
        [_cba_rub_amd("4.6", 0), _cba_usd_amd("368.0"), _cbr_usd_rub("80.0")]
    )
    service = _make_service(repository)

    recommendation = await service.get_recommendation()

    assert recommendation is not None
    # synthetic = 368.0 / 80.0 = 4.6 exactly -> official matches synthetic
    deviation_factor = next(
        f for f in recommendation.factors if f.name == "source_deviation_penalty"
    )
    assert deviation_factor.value == Decimal(0)


async def test_get_recommendation_without_bridge_data_has_no_deviation_factor(
    db_session: AsyncSession,
) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.save(_cba_rub_amd("4.6", 0))
    service = _make_service(repository)

    recommendation = await service.get_recommendation()

    assert recommendation is not None
    names = [factor.name for factor in recommendation.factors]
    assert "source_deviation_penalty" not in names


async def test_get_recommendation_reflects_upward_deviation_from_history(
    db_session: AsyncSession,
) -> None:
    repository = ExchangeRateRepository(db_session)
    history = [
        _cba_rub_amd("4.5", -3),
        _cba_rub_amd("4.5", -2),
        _cba_rub_amd("4.5", -1),
        _cba_rub_amd("5.0", 0),
    ]
    await repository.bulk_save(history)
    service = _make_service(repository)

    recommendation = await service.get_recommendation(window=4)

    assert recommendation is not None
    assert recommendation.action.value == "exchange_now"


async def test_get_recommendation_includes_bank_market_rate_factor(
    db_session: AsyncSession,
) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.bulk_save([_cba_rub_amd("4.6651", 0), _bank_average_rub_amd("3.75")])
    service = _make_service(repository)

    recommendation = await service.get_recommendation()

    assert recommendation is not None
    factor = next(f for f in recommendation.factors if f.name == "bank_market_rate")
    assert "3.75" in factor.explanation


async def test_get_recommendation_without_bank_data_has_not_yet_collected_factor(
    db_session: AsyncSession,
) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.save(_cba_rub_amd("4.6651", 0))
    service = _make_service(repository)

    recommendation = await service.get_recommendation()

    assert recommendation is not None
    factor = next(f for f in recommendation.factors if f.name == "bank_market_rate")
    assert "пока не собраны" in factor.explanation
