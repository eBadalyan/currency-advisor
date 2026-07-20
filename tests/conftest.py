from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """A session against the real database, with exchange_rates truncated
    before each test. Repository tests rely on Postgres-specific behavior
    (ON CONFLICT), so they run against the real thing rather than a mock."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        await session.execute(text("TRUNCATE TABLE exchange_rates RESTART IDENTITY"))
        await session.commit()
        yield session

    await engine.dispose()
