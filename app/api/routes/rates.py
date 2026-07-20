from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_exchange_rate_repository
from app.collectors.cba import SOURCE_NAME as CBA_SOURCE_NAME
from app.repositories.exchange_rate import ExchangeRateRepository
from app.schemas.rates import RateResponse

router = APIRouter(prefix="/rates", tags=["rates"])


@router.get("/latest", response_model=RateResponse)
async def latest_rate(
    base_currency: str = Query(min_length=3, max_length=3),
    quote_currency: str = Query(min_length=3, max_length=3),
    source: str = CBA_SOURCE_NAME,
    repository: ExchangeRateRepository = Depends(get_exchange_rate_repository),
) -> RateResponse:
    rate = await repository.get_latest(source, base_currency.upper(), quote_currency.upper())
    if rate is None:
        raise HTTPException(status_code=404, detail="No rate data available for this pair yet")
    return RateResponse.model_validate(rate)


@router.get("/history", response_model=list[RateResponse])
async def rate_history(
    base_currency: str = Query(min_length=3, max_length=3),
    quote_currency: str = Query(min_length=3, max_length=3),
    source: str = CBA_SOURCE_NAME,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    repository: ExchangeRateRepository = Depends(get_exchange_rate_repository),
) -> list[RateResponse]:
    points = await repository.list_history(
        source,
        base_currency.upper(),
        quote_currency.upper(),
        since=since,
        until=until,
        limit=limit,
    )
    return [RateResponse.model_validate(point) for point in points]
