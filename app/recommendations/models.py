from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.analytics.service import RateIndicators
from app.collectors.base import RatePoint


class RecommendationAction(StrEnum):
    EXCHANGE_NOW = "exchange_now"
    WAIT = "wait"
    NEUTRAL = "neutral"


@dataclass(frozen=True, slots=True)
class RecommendationFactor:
    name: str
    weight: Decimal
    value: Decimal
    explanation: str


@dataclass(frozen=True, slots=True)
class Recommendation:
    action: RecommendationAction
    confidence: Decimal
    factors: list[RecommendationFactor]
    summary: str


@dataclass(frozen=True, slots=True)
class RecommendationContext:
    """Everything a RecommendationStrategy needs, already fetched —
    strategies do no I/O, they only interpret this."""

    official_rate: RatePoint
    indicators: RateIndicators
    synthetic_rate: Decimal | None
    source_deviation_percent: Decimal | None
    bank_median_rate: Decimal | None
    bank_rate_deviation_percent: Decimal | None
