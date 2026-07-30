from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.recommendations.models import RecommendationAction


class RecommendationFactorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    weight: Decimal
    value: Decimal
    explanation: str


class RecommendationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    action: RecommendationAction
    confidence: Decimal
    factors: list[RecommendationFactorResponse]
    summary: str
