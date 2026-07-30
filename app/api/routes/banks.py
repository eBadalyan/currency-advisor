from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_exchange_rate_repository
from app.collectors.acba_bank import SOURCE_NAME as ACBA_BANK_SOURCE_NAME
from app.collectors.ameriabank import SOURCE_NAME as AMERIABANK_SOURCE_NAME
from app.collectors.evocabank import SOURCE_NAME as EVOCABANK_SOURCE_NAME
from app.collectors.vtb_am import SOURCE_NAME as VTB_AM_SOURCE_NAME
from app.repositories.exchange_rate import ExchangeRateRepository
from app.schemas.banks import BankRateResponse, BanksResponse
from app.services.bank_average_service import SOURCE_NAME as BANK_AVERAGE_SOURCE_NAME
from app.services.rate_service import RateService

router = APIRouter(tags=["banks"])

_BASE_CURRENCY = "RUB"
_QUOTE_CURRENCY = "AMD"
_BANK_SOURCES = (
    AMERIABANK_SOURCE_NAME,
    EVOCABANK_SOURCE_NAME,
    ACBA_BANK_SOURCE_NAME,
    VTB_AM_SOURCE_NAME,
)


@router.get("/banks", response_model=BanksResponse)
async def banks(
    repository: ExchangeRateRepository = Depends(get_exchange_rate_repository),
) -> BanksResponse:
    service = RateService(repository)
    bank_points = [
        (source, await service.get_latest_rate(source, _BASE_CURRENCY, _QUOTE_CURRENCY))
        for source in _BANK_SOURCES
    ]
    if all(point is None for _, point in bank_points):
        raise HTTPException(status_code=404, detail="No bank rate data available yet")

    median_point = await service.get_latest_rate(
        BANK_AVERAGE_SOURCE_NAME, _BASE_CURRENCY, _QUOTE_CURRENCY
    )

    return BanksResponse(
        banks=[
            BankRateResponse(source=source, value=point.value if point is not None else None)
            for source, point in bank_points
        ],
        median=median_point.value if median_point is not None else None,
    )
