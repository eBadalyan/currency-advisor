from __future__ import annotations

import httpx


async def test_dashboard_serves_index_html(client: httpx.AsyncClient) -> None:
    response = await client.get("/dashboard/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
