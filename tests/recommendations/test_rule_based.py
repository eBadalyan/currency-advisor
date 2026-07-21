from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.analytics.service import RateIndicators
from app.collectors.base import RatePoint
from app.recommendations.models import RecommendationAction, RecommendationContext
from app.recommendations.rule_based import RuleBasedRecommendationStrategy, _pluralize_points

_OBSERVED_AT = datetime(2026, 7, 20, tzinfo=UTC)
_STRATEGY = RuleBasedRecommendationStrategy()


def _official_rate(value: str) -> RatePoint:
    return RatePoint("Central Bank of Armenia", "RUB", "AMD", Decimal(value), _OBSERVED_AT)


def _indicators(
    *,
    sample_size: int = 30,
    moving_average: str | None = None,
    rate_change_percent: str | None = None,
    volatility: str | None = "0",
    minimum: RatePoint | None = None,
    maximum: RatePoint | None = None,
) -> RateIndicators:
    return RateIndicators(
        sample_size=sample_size,
        moving_average=Decimal(moving_average) if moving_average is not None else None,
        rate_change_percent=(
            Decimal(rate_change_percent) if rate_change_percent is not None else None
        ),
        volatility=Decimal(volatility) if volatility is not None else None,
        minimum=minimum,
        maximum=maximum,
    )


def _context(
    *,
    official_value: str = "4.6",
    moving_average: str | None = "4.6",
    rate_change_percent: str | None = "0",
    volatility: str | None = "0",
    sample_size: int = 30,
    synthetic_rate: str | None = None,
    source_deviation_percent: str | None = None,
    bank_median_rate: str | None = None,
    bank_rate_deviation_percent: str | None = None,
) -> RecommendationContext:
    return RecommendationContext(
        official_rate=_official_rate(official_value),
        indicators=_indicators(
            sample_size=sample_size,
            moving_average=moving_average,
            rate_change_percent=rate_change_percent,
            volatility=volatility,
        ),
        synthetic_rate=Decimal(synthetic_rate) if synthetic_rate is not None else None,
        source_deviation_percent=(
            Decimal(source_deviation_percent) if source_deviation_percent is not None else None
        ),
        bank_median_rate=Decimal(bank_median_rate) if bank_median_rate is not None else None,
        bank_rate_deviation_percent=(
            Decimal(bank_rate_deviation_percent)
            if bank_rate_deviation_percent is not None
            else None
        ),
    )


def test_rate_well_above_average_recommends_exchange_now() -> None:
    context = _context(official_value="5.0", moving_average="4.6", rate_change_percent="5")

    recommendation = _STRATEGY.evaluate(context)

    assert recommendation.action == RecommendationAction.EXCHANGE_NOW
    assert recommendation.confidence > 0


def test_rate_well_below_average_recommends_wait() -> None:
    context = _context(official_value="4.2", moving_average="4.6", rate_change_percent="-5")

    recommendation = _STRATEGY.evaluate(context)

    assert recommendation.action == RecommendationAction.WAIT


def test_rate_close_to_average_is_neutral() -> None:
    context = _context(official_value="4.6", moving_average="4.6", rate_change_percent="0")

    recommendation = _STRATEGY.evaluate(context)

    assert recommendation.action == RecommendationAction.NEUTRAL


def test_missing_moving_average_and_trend_is_neutral_with_low_confidence() -> None:
    context = _context(
        official_value="4.6",
        moving_average=None,
        rate_change_percent=None,
        volatility=None,
        sample_size=1,
    )

    recommendation = _STRATEGY.evaluate(context)

    assert recommendation.action == RecommendationAction.NEUTRAL
    assert recommendation.confidence < Decimal("0.5")
    names = [factor.name for factor in recommendation.factors]
    assert "insufficient_history_penalty" in names
    penalty_factor = next(
        f for f in recommendation.factors if f.name == "insufficient_history_penalty"
    )
    assert "1 точка истории" in penalty_factor.explanation


def test_high_volatility_reduces_confidence() -> None:
    calm = _context(
        official_value="5.0", moving_average="4.6", rate_change_percent="5", volatility="0"
    )
    volatile = _context(
        official_value="5.0", moving_average="4.6", rate_change_percent="5", volatility="0.1"
    )

    calm_confidence = _STRATEGY.evaluate(calm).confidence
    volatile_confidence = _STRATEGY.evaluate(volatile).confidence

    assert volatile_confidence < calm_confidence


def test_large_source_deviation_reduces_confidence_without_changing_action() -> None:
    aligned = _context(
        official_value="5.0",
        moving_average="4.6",
        rate_change_percent="5",
        source_deviation_percent="0",
    )
    diverged = _context(
        official_value="5.0",
        moving_average="4.6",
        rate_change_percent="5",
        source_deviation_percent="10",
    )

    aligned_result = _STRATEGY.evaluate(aligned)
    diverged_result = _STRATEGY.evaluate(diverged)

    assert diverged_result.confidence < aligned_result.confidence
    assert aligned_result.action == diverged_result.action == RecommendationAction.EXCHANGE_NOW


def test_factors_expose_explanations_for_every_recommendation() -> None:
    recommendation = _STRATEGY.evaluate(_context())

    assert len(recommendation.factors) > 0
    assert all(factor.explanation for factor in recommendation.factors)


def test_summary_always_notes_no_future_prediction() -> None:
    recommendation = _STRATEGY.evaluate(_context())

    assert "не предсказывает будущее" in recommendation.summary


def test_missing_bank_rate_reports_not_yet_collected() -> None:
    recommendation = _STRATEGY.evaluate(_context(bank_median_rate=None))

    factor = next(f for f in recommendation.factors if f.name == "bank_market_rate")
    assert "пока не собраны" in factor.explanation
    assert factor.weight == Decimal(0)


def test_bank_rate_factor_never_affects_confidence_or_action() -> None:
    without_bank_data = _context(
        official_value="5.0", moving_average="4.6", rate_change_percent="5"
    )
    with_bank_data = _context(
        official_value="5.0",
        moving_average="4.6",
        rate_change_percent="5",
        bank_median_rate="3.75",
        bank_rate_deviation_percent="24.4",
    )

    without_result = _STRATEGY.evaluate(without_bank_data)
    with_result = _STRATEGY.evaluate(with_bank_data)

    assert without_result.action == with_result.action
    assert without_result.confidence == with_result.confidence
    factor = next(f for f in with_result.factors if f.name == "bank_market_rate")
    assert factor.weight == Decimal(0)
    assert factor.value == Decimal(0)
    assert "3.75" in factor.explanation
    assert "24.4" in factor.explanation
    assert "выше банковского" in factor.explanation


def test_pluralize_points_handles_russian_numeral_agreement() -> None:
    assert _pluralize_points(1) == "точка"
    assert _pluralize_points(21) == "точка"
    assert _pluralize_points(2) == "точки"
    assert _pluralize_points(3) == "точки"
    assert _pluralize_points(4) == "точки"
    assert _pluralize_points(24) == "точки"
    assert _pluralize_points(5) == "точек"
    assert _pluralize_points(11) == "точек"
    assert _pluralize_points(12) == "точек"
    assert _pluralize_points(25) == "точек"
    assert _pluralize_points(111) == "точек"
