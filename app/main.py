from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from app.api.routes.advice import router as advice_router
from app.api.routes.banks import router as banks_router
from app.api.routes.health import router as health_router
from app.api.routes.rates import router as rates_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionFactory
from app.services.collection_scheduler import build_scheduler

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    async with httpx.AsyncClient() as client:
        scheduler = build_scheduler(client, SessionFactory)
        scheduler.start()
        try:
            yield
        finally:
            scheduler.shutdown(wait=False)


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(health_router)
app.include_router(rates_router)
app.include_router(advice_router)
app.include_router(banks_router)
app.mount("/dashboard", StaticFiles(directory="app/static", html=True), name="dashboard")


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "name": "Currency Advisor API",
        "status": "running",
        "docs": "/docs",
    }
