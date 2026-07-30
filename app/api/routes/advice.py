from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.analytics.service import AnalyticsService
from app.api.deps import get_exchange_rate_repository
from app.recommendations.rule_based import RuleBasedRecommendationStrategy
from app.recommendations.service import RecommendationService
from app.repositories.exchange_rate import ExchangeRateRepository
from app.schemas.advice import RecommendationResponse

router = APIRouter(tags=["advice"])


@router.get("/advice", response_model=RecommendationResponse)
async def advice(
    repository: ExchangeRateRepository = Depends(get_exchange_rate_repository),
) -> RecommendationResponse:
    service = RecommendationService(
        repository, AnalyticsService(repository), RuleBasedRecommendationStrategy()
    )
    recommendation = await service.get_recommendation()
    if recommendation is None:
        raise HTTPException(status_code=404, detail="Insufficient data for a recommendation yet")
    return RecommendationResponse.model_validate(recommendation)
