from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.api.routes.rates import router as rates_router
from app.core.config import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(health_router)
app.include_router(rates_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "name": "Currency Advisor API",
        "status": "running",
        "docs": "/docs",
    }
