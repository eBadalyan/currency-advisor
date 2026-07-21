from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import get_session
from app.main import app


@pytest_asyncio.fixture
async def client(
    test_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[httpx.AsyncClient]:
    # httpx.AsyncClient + ASGITransport runs requests in-process on this
    # test's own event loop — unlike Starlette's TestClient, which drives
    # the app from a separate thread/loop via an anyio portal, and breaks
    # once the DB-backed routes share an async engine with the db_session
    # fixture (connections get opened on one loop, torn down on another).
    #
    # get_session is overridden to the isolated test database too, so the
    # routes under test read/write the same data db_session seeds instead
    # of the real dev database the app's default engine points at.
    async def _get_test_session() -> AsyncIterator[AsyncSession]:
        async with test_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _get_test_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as async_client:
            yield async_client
    finally:
        app.dependency_overrides.pop(get_session, None)
