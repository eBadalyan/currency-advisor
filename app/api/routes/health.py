from fastapi import APIRouter, Depends

from app.api.deps import get_exchange_rate_repository
from app.repositories.exchange_rate import ExchangeRateRepository
from app.schemas.collector_health import SourceHealthResponse
from app.services.collector_health_service import CollectorHealthService

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/collectors", response_model=list[SourceHealthResponse])
async def collector_health(
    repository: ExchangeRateRepository = Depends(get_exchange_rate_repository),
) -> list[SourceHealthResponse]:
    statuses = await CollectorHealthService(repository).get_status()
    return [SourceHealthResponse.model_validate(status) for status in statuses]
