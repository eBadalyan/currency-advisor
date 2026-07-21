from __future__ import annotations

from decimal import Decimal

from app.recommendations.models import (
    Recommendation,
    RecommendationAction,
    RecommendationContext,
    RecommendationFactor,
)
from app.recommendations.strategy import RecommendationStrategy

_MOVING_AVERAGE_DEVIATION_WEIGHT = Decimal("0.6")
_TREND_WEIGHT = Decimal("0.4")
_ACTION_THRESHOLD = Decimal("0.2")

# A directional factor (deviation from MA, trend) saturates to +-1 once the
# underlying percentage change reaches this magnitude.
_SATURATION_PERCENT = Decimal(5)

# Volatility (stdev of returns, e.g. 0.05 = 5%) fully zeroes confidence once
# it reaches 1 / this scale.
_VOLATILITY_CONFIDENCE_SCALE = Decimal(20)

# Cross-source deviation (official vs synthetic, in percent) fully zeroes
# confidence once its magnitude reaches this value.
_SOURCE_DEVIATION_CONFIDENCE_SCALE = Decimal(3)

_MIN_SAMPLE_SIZE = 3
_INSUFFICIENT_HISTORY_CONFIDENCE_MULTIPLIER = Decimal("0.3")

_SUMMARY_BY_ACTION = {
    RecommendationAction.EXCHANGE_NOW: (
        "Сейчас курс RUB/AMD выглядит выгоднее, чем обычно за последнее время."
    ),
    RecommendationAction.WAIT: (
        "Сейчас курс RUB/AMD выглядит менее выгодным, чем обычно за последнее "
        "время — возможно, стоит подождать."
    ),
    RecommendationAction.NEUTRAL: (
        "Заметного отклонения от недавней истории не видно — явных оснований "
        "менять сейчас или ждать нет."
    ),
}


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


def _pluralize_points(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return "точка"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return "точки"
    return "точек"


class RuleBasedRecommendationStrategy(RecommendationStrategy):
    """First RecommendationStrategy implementation: no ML, no future
    prediction — compares the current official rate to its own recent
    moving average and trend. "Is now favorable relative to recent
    history", not "where will the rate go", matching the product's stated
    premise that it does not predict the future.
    """

    def evaluate(self, context: RecommendationContext) -> Recommendation:
        factors: list[RecommendationFactor] = []

        deviation_score = self._deviation_from_average_factor(context, factors)
        trend_score = self._trend_factor(context, factors)
        confidence = self._confidence(context, factors)
        self._bank_market_rate_factor(context, factors)

        directional_score = (
            deviation_score * _MOVING_AVERAGE_DEVIATION_WEIGHT + trend_score * _TREND_WEIGHT
        )
        action = self._action_from_score(directional_score)
        summary = self._summary(action, confidence)

        return Recommendation(
            action=action, confidence=confidence, factors=factors, summary=summary
        )

    @staticmethod
    def _deviation_from_average_factor(
        context: RecommendationContext, factors: list[RecommendationFactor]
    ) -> Decimal:
        moving_average = context.indicators.moving_average
        if moving_average is None or moving_average == 0:
            factors.append(
                RecommendationFactor(
                    name="deviation_from_moving_average",
                    weight=_MOVING_AVERAGE_DEVIATION_WEIGHT,
                    value=Decimal(0),
                    explanation="Недостаточно истории для скользящей средней.",
                )
            )
            return Decimal(0)

        deviation_percent = (
            (context.official_rate.value - moving_average) / moving_average * Decimal(100)
        )
        score = _clamp(deviation_percent / _SATURATION_PERCENT, Decimal(-1), Decimal(1))
        direction = (
            "выше" if deviation_percent > 0 else "ниже" if deviation_percent < 0 else "на уровне"
        )
        factors.append(
            RecommendationFactor(
                name="deviation_from_moving_average",
                weight=_MOVING_AVERAGE_DEVIATION_WEIGHT,
                value=score,
                explanation=(
                    f"Текущий курс {direction} своей скользящей средней "
                    f"на {abs(deviation_percent):.2f}%."
                ),
            )
        )
        return score

    @staticmethod
    def _trend_factor(
        context: RecommendationContext, factors: list[RecommendationFactor]
    ) -> Decimal:
        change = context.indicators.rate_change_percent
        if change is None:
            factors.append(
                RecommendationFactor(
                    name="recent_trend",
                    weight=_TREND_WEIGHT,
                    value=Decimal(0),
                    explanation="Недостаточно истории для оценки тренда.",
                )
            )
            return Decimal(0)

        score = _clamp(change / _SATURATION_PERCENT, Decimal(-1), Decimal(1))
        direction = "вырос" if change > 0 else "снизился" if change < 0 else "не изменился"
        factors.append(
            RecommendationFactor(
                name="recent_trend",
                weight=_TREND_WEIGHT,
                value=score,
                explanation=f"За рассматриваемый период курс {direction} на {abs(change):.2f}%.",
            )
        )
        return score

    @staticmethod
    def _bank_market_rate_factor(
        context: RecommendationContext, factors: list[RecommendationFactor]
    ) -> None:
        """Informational only: weight=0 and never touches confidence.

        The official rate has years of history to judge "favorable relative
        to recent history" against; the bank median only started
        accumulating recently and can't yet support a trend/moving-average
        of its own (see ROADMAP.md). Surfacing it as plain context — what a
        person actually gets in cash — without pretending it drives the
        action or confidence math would be overclaiming precision the data
        doesn't support yet.
        """
        bank_rate = context.bank_median_rate
        if bank_rate is None:
            factors.append(
                RecommendationFactor(
                    name="bank_market_rate",
                    weight=Decimal(0),
                    value=Decimal(0),
                    explanation="Банковские курсы наличного обмена пока не собраны.",
                )
            )
            return

        deviation = context.bank_rate_deviation_percent
        deviation_text = ""
        if deviation is not None and deviation != 0:
            direction = "выше" if deviation > 0 else "ниже"
            deviation_text = f" — официальный курс {direction} банковского на {abs(deviation):.1f}%"

        factors.append(
            RecommendationFactor(
                name="bank_market_rate",
                weight=Decimal(0),
                value=Decimal(0),
                explanation=(
                    f"По данным банков (медиана) курс наличной покупки RUB сейчас "
                    f"{bank_rate}{deviation_text}. Именно этот курс вы фактически "
                    "получите при обмене наличных, а не официальный курс ЦБ."
                ),
            )
        )

    @staticmethod
    def _confidence(context: RecommendationContext, factors: list[RecommendationFactor]) -> Decimal:
        confidence = Decimal(1)

        volatility = context.indicators.volatility
        if volatility is not None:
            penalty = _clamp(volatility * _VOLATILITY_CONFIDENCE_SCALE, Decimal(0), Decimal(1))
            confidence -= penalty
            factors.append(
                RecommendationFactor(
                    name="volatility_penalty",
                    weight=Decimal(0),
                    value=-penalty,
                    explanation=f"Волатильность снижает уверенность на {penalty * 100:.0f}%.",
                )
            )

        deviation = context.source_deviation_percent
        if deviation is not None:
            penalty = _clamp(
                abs(deviation) / _SOURCE_DEVIATION_CONFIDENCE_SCALE, Decimal(0), Decimal(1)
            )
            confidence -= penalty
            factors.append(
                RecommendationFactor(
                    name="source_deviation_penalty",
                    weight=Decimal(0),
                    value=-penalty,
                    explanation=(
                        "Официальный курс отличается от синтетического (через USD) "
                        f"на {abs(deviation):.2f}%, что снижает уверенность на "
                        f"{penalty * 100:.0f}%."
                    ),
                )
            )

        if context.indicators.sample_size < _MIN_SAMPLE_SIZE:
            confidence *= _INSUFFICIENT_HISTORY_CONFIDENCE_MULTIPLIER
            factors.append(
                RecommendationFactor(
                    name="insufficient_history_penalty",
                    weight=Decimal(0),
                    value=_INSUFFICIENT_HISTORY_CONFIDENCE_MULTIPLIER - Decimal(1),
                    explanation=(
                        f"Накоплено только {context.indicators.sample_size} "
                        f"{_pluralize_points(context.indicators.sample_size)} истории — "
                        "уверенность занижена."
                    ),
                )
            )

        return _clamp(confidence, Decimal(0), Decimal(1))

    @staticmethod
    def _action_from_score(score: Decimal) -> RecommendationAction:
        if score > _ACTION_THRESHOLD:
            return RecommendationAction.EXCHANGE_NOW
        if score < -_ACTION_THRESHOLD:
            return RecommendationAction.WAIT
        return RecommendationAction.NEUTRAL

    @staticmethod
    def _summary(action: RecommendationAction, confidence: Decimal) -> str:
        return (
            f"{_SUMMARY_BY_ACTION[action]} Уверенность: {confidence * 100:.0f}%. "
            "Сервис не предсказывает будущее — оценка основана только на недавней истории."
        )
