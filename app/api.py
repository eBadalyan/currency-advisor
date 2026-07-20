from fastapi import FastAPI, HTTPException

from app.bot.main import build_service

app = FastAPI(title="RUB/AMD Advisor API", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/rates/snapshot")
async def rates_snapshot() -> dict[str, str | None]:
    try:
        snapshot = await build_service().snapshot()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "official_rub_amd": str(snapshot.official_rub_amd),
        "official_usd_amd": str(snapshot.official_usd_amd),
        "market_usd_rub": (
            str(snapshot.market_usd_rub) 
            if snapshot.market_usd_rub 
            else None
        ),
        "synthetic_rub_amd": (
            str(snapshot.synthetic_rub_amd)
            if snapshot.synthetic_rub_amd
            else None
        ),
        "deviation_percent": (
            str(snapshot.deviation_percent)
            if snapshot.deviation_percent
            else None
        ),
    }
