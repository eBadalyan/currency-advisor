from fastapi import APIRouter

router = APIRouter(prefix="/rates", tags=["rates"])


@router.get("/rub-amd")
async def rub_amd() -> dict[str, str]:
    return {
        "pair": "RUB/AMD",
        "status": "collector_not_connected",
        "message": "The CBA collector will be added in the next milestone.",
    }
