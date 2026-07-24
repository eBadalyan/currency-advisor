from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.db.base import Base


def _test_database_url() -> str:
    url = make_url(get_settings().database_url)
    # str(url) masks the password as "***" by default (SQLAlchemy's
    # anti-accidental-logging behavior) — render_as_string(hide_password=False)
    # is required to get a URL that actually authenticates.
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


async def _ensure_test_database_exists(test_url: str) -> None:
    url = make_url(test_url)
    admin_engine = create_async_engine(
        url.set(database="postgres").render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    async with admin_engine.connect() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
        )
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    await admin_engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def test_engine() -> AsyncIterator[AsyncEngine]:
    """Engine for a dedicated `<database>_test` database — created and
    schema-provisioned once per test session, so running the test suite
    never touches the real dev/demo database that `.env`'s DATABASE_URL
    points at (and that a locally running scheduler/API accumulates real
    history in)."""
    test_url = _test_database_url()
    await _ensure_test_database_exists(test_url)

    engine = create_async_engine(test_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
def test_session_factory(
    test_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(test_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A session against the isolated test database, with exchange_rates
    and notification_states truncated before each test. Repository tests
    rely on Postgres-specific behavior (ON CONFLICT), so they run against a
    real Postgres rather than a mock — just not the dev one."""
    async with test_session_factory() as session:
        await session.execute(text("TRUNCATE TABLE exchange_rates RESTART IDENTITY"))
        await session.execute(text("TRUNCATE TABLE notification_states RESTART IDENTITY"))
        await session.commit()
        yield session
