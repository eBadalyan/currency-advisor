# Web Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a minimal public read-only web dashboard (tabs for course/recommendation, banks, source status, plus a rate-history chart) served as static files from the existing `api` container, backed by two new thin JSON routes that reuse existing services.

**Architecture:** Two new FastAPI routes (`/advice`, `/banks`) built as thin wrappers over the already-existing `RecommendationService`/`RateService`, mirroring the pattern already used by `/rates/*` and `/health/collectors`. A third data need — per-source status — is **already served** by the existing `/health/collectors` endpoint; no new route needed for it. A single static `index.html` (vanilla JS, no build step) is mounted via Starlette's `StaticFiles` (already bundled with FastAPI) at `/dashboard`, and fetches all of `/advice`, `/banks`, `/health/collectors`, `/rates/latest`, `/rates/history` client-side.

**Tech Stack:** FastAPI (existing), Pydantic v2 (existing), Starlette `StaticFiles` (bundled, no new dependency), vanilla JS + Chart.js via CDN (frontend only, not a Python dependency).

## Global Constraints

- No new Python/uv dependencies — `StaticFiles` ships with Starlette/FastAPI already in `pyproject.toml`.
- `ruff`/`mypy --strict` cover only `.py` files; `app/static/index.html` (including its inline JS) is not linted or type-checked by CI — this is expected, not a gap to close in this plan.
- Existing `GET /` (JSON `{"name","status","docs"}`) and `GET /rates/*` behavior must not change.
- Dashboard is fully public — no authentication, matching `/rates/latest` today.
- No auto-refresh, no WebSocket/SSE — the page loads data once, on page load/reload only.
- Chart.js is loaded from a CDN `<script>` tag, not vendored or version-pinned in the repo.
- Route error semantics: `404` when there is genuinely nothing to show (`/advice` — no official rate at all; `/banks` — all four banks `None`); otherwise `200` tolerating partial `null`s, matching the bot's existing tolerance in `app/bot/main.py`.
- All money/rate values remain `Decimal` end-to-end (Pydantic `Decimal` fields, serialized as JSON strings by FastAPI's default encoder — same as the existing `RateResponse`).

---

### Task 1: `/advice` endpoint

**Files:**
- Create: `app/schemas/advice.py`
- Create: `app/api/routes/advice.py`
- Modify: `app/main.py:7-31` (add the `advice_router` import and `app.include_router(advice_router)` call — see Step 5 below; Task 2 adds `banks_router` the same way, Task 3 adds only the `StaticFiles` mount on top of both)
- Test: `tests/api/test_advice.py`

**Interfaces:**
- Consumes: `app.recommendations.service.RecommendationService.get_recommendation(self, *, window_days: int = 30) -> Recommendation | None` (existing); `app.recommendations.models.Recommendation` (`action: RecommendationAction`, `confidence: Decimal`, `factors: list[RecommendationFactor]`, `summary: str`); `RecommendationFactor` (`name: str`, `weight: Decimal`, `value: Decimal`, `explanation: str`); `app.recommendations.rule_based.RuleBasedRecommendationStrategy` (existing, no-arg constructor); `app.analytics.service.AnalyticsService(repository)` (existing); `app.api.deps.get_exchange_rate_repository` (existing FastAPI dependency).
- Produces: `router` (FastAPI `APIRouter`) importable from `app.api.routes.advice`, registered by Task 3. `GET /advice` returns `RecommendationResponse` JSON on `200`, raises `404` when `get_recommendation()` returns `None`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_advice.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import RatePoint
from app.collectors.cba import SOURCE_NAME as CBA_SOURCE_NAME
from app.repositories.exchange_rate import ExchangeRateRepository

_OBSERVED_AT = datetime(2026, 7, 19, 20, 0, tzinfo=UTC)


async def test_advice_returns_404_when_no_data(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    response = await client.get("/advice")

    assert response.status_code == 404


async def test_advice_returns_recommendation_when_data_present(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    await ExchangeRateRepository(db_session).save(
        RatePoint(CBA_SOURCE_NAME, "RUB", "AMD", Decimal("4.6651"), _OBSERVED_AT)
    )

    response = await client.get("/advice")

    assert response.status_code == 200
    body = response.json()
    assert body["action"] in ("exchange_now", "wait", "neutral")
    assert 0 <= Decimal(body["confidence"]) <= 1
    assert isinstance(body["summary"], str) and body["summary"]
    assert isinstance(body["factors"], list) and len(body["factors"]) > 0
    factor = body["factors"][0]
    assert set(factor.keys()) == {"name", "weight", "value", "explanation"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_advice.py -v`
Expected: FAIL — `404 Not Found` for the route itself (route doesn't exist yet), not the assertion errors above.

- [ ] **Step 3: Write the schema**

Create `app/schemas/advice.py`:

```python
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.recommendations.models import RecommendationAction


class RecommendationFactorResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    weight: Decimal
    value: Decimal
    explanation: str


class RecommendationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    action: RecommendationAction
    confidence: Decimal
    factors: list[RecommendationFactorResponse]
    summary: str
```

- [ ] **Step 4: Write the route**

Create `app/api/routes/advice.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.analytics.service import AnalyticsService
from app.api.deps import get_exchange_rate_repository
from app.recommendations.rule_based import RuleBasedRecommendationStrategy
from app.recommendations.service import RecommendationService
from app.repositories.exchange_rate import ExchangeRateRepository
from app.schemas.advice import RecommendationResponse

router = APIRouter(tags=["advice"])


@router.get("/advice", response_model=RecommendationResponse)
async def advice(
    repository: ExchangeRateRepository = Depends(get_exchange_rate_repository),
) -> RecommendationResponse:
    service = RecommendationService(
        repository, AnalyticsService(repository), RuleBasedRecommendationStrategy()
    )
    recommendation = await service.get_recommendation()
    if recommendation is None:
        raise HTTPException(status_code=404, detail="Insufficient data for a recommendation yet")
    return RecommendationResponse.model_validate(recommendation)
```

- [ ] **Step 5: Temporarily register the router to run the test**

This route needs to be registered in `app/main.py` for the test client to reach it. Task 3 does this permanently alongside the `/banks` route and the static mount; to keep this task independently testable, add the two lines now:

In `app/main.py`, after the existing `from app.api.routes.rates import router as rates_router` (line 8), add:

```python
from app.api.routes.advice import router as advice_router
```

After the existing `app.include_router(rates_router)` (line 31), add:

```python
app.include_router(advice_router)
```

(Task 3 will add the `/banks` import/include lines the same way — these two additions are not reverted, they are the permanent wiring; Task 3's own step list only adds what Task 1/2 did not already add.)

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/api/test_advice.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Run ruff and mypy on the new files**

Run: `uv run ruff check app/schemas/advice.py app/api/routes/advice.py tests/api/test_advice.py`
Run: `uv run ruff format --check app/schemas/advice.py app/api/routes/advice.py tests/api/test_advice.py`
Run: `uv run mypy app/schemas/advice.py app/api/routes/advice.py`
Expected: all clean. Fix any reported issues before proceeding.

- [ ] **Step 8: Commit**

```bash
git add app/schemas/advice.py app/api/routes/advice.py tests/api/test_advice.py app/main.py
git commit -m "feat: add GET /advice endpoint"
```

---

### Task 2: `/banks` endpoint

**Files:**
- Create: `app/schemas/banks.py`
- Create: `app/api/routes/banks.py`
- Modify: `app/main.py` (import + include, same mechanism as Task 1 Step 5)
- Test: `tests/api/test_banks.py`

**Interfaces:**
- Consumes: `app.services.rate_service.RateService.get_latest_rate(self, source: str, base_currency: str, quote_currency: str) -> RatePoint | None` (existing); source name constants: `app.collectors.ameriabank.SOURCE_NAME = "Ameriabank"`, `app.collectors.evocabank.SOURCE_NAME = "Evocabank"`, `app.collectors.acba_bank.SOURCE_NAME = "ACBA Bank"`, `app.collectors.vtb_am.SOURCE_NAME = "VTB Bank (Armenia)"`, `app.services.bank_average_service.SOURCE_NAME = "Bank Average (RUB cash, derived)"` (all existing); `app.api.deps.get_exchange_rate_repository` (existing).
- Produces: `router` importable from `app.api.routes.banks`, registered by Task 3. `GET /banks` returns `BanksResponse` JSON on `200` (tolerating partial `null` bank values), raises `404` only when every bank is `None`.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_banks.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.ameriabank import SOURCE_NAME as AMERIABANK_SOURCE_NAME
from app.collectors.base import RatePoint
from app.collectors.evocabank import SOURCE_NAME as EVOCABANK_SOURCE_NAME
from app.repositories.exchange_rate import ExchangeRateRepository
from app.services.bank_average_service import SOURCE_NAME as BANK_AVERAGE_SOURCE_NAME

_OBSERVED_AT = datetime(2026, 7, 19, 20, 0, tzinfo=UTC)


async def test_banks_returns_404_when_no_bank_has_data(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    response = await client.get("/banks")

    assert response.status_code == 404


async def test_banks_returns_partial_data_and_median(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    repo = ExchangeRateRepository(db_session)
    await repo.save(RatePoint(AMERIABANK_SOURCE_NAME, "RUB", "AMD", Decimal("4.00"), _OBSERVED_AT))
    await repo.save(RatePoint(EVOCABANK_SOURCE_NAME, "RUB", "AMD", Decimal("4.36"), _OBSERVED_AT))
    await repo.save(
        RatePoint(BANK_AVERAGE_SOURCE_NAME, "RUB", "AMD", Decimal("4.18"), _OBSERVED_AT)
    )

    response = await client.get("/banks")

    assert response.status_code == 200
    body = response.json()
    assert len(body["banks"]) == 4
    by_source = {entry["source"]: entry["value"] for entry in body["banks"]}
    assert Decimal(by_source[AMERIABANK_SOURCE_NAME]) == Decimal("4.00")
    assert Decimal(by_source[EVOCABANK_SOURCE_NAME]) == Decimal("4.36")
    assert by_source["ACBA Bank"] is None
    assert by_source["VTB Bank (Armenia)"] is None
    assert Decimal(body["median"]) == Decimal("4.18")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_banks.py -v`
Expected: FAIL — `404 Not Found` for the route itself (route doesn't exist yet).

- [ ] **Step 3: Write the schema**

Create `app/schemas/banks.py`:

```python
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class BankRateResponse(BaseModel):
    source: str
    value: Decimal | None


class BanksResponse(BaseModel):
    banks: list[BankRateResponse]
    median: Decimal | None
```

- [ ] **Step 4: Write the route**

Create `app/api/routes/banks.py`:

```python
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
```

- [ ] **Step 5: Register the router**

In `app/main.py`, add alongside the Task 1 addition:

```python
from app.api.routes.banks import router as banks_router
```

and:

```python
app.include_router(banks_router)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/api/test_banks.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Run ruff and mypy on the new files**

Run: `uv run ruff check app/schemas/banks.py app/api/routes/banks.py tests/api/test_banks.py`
Run: `uv run ruff format --check app/schemas/banks.py app/api/routes/banks.py tests/api/test_banks.py`
Run: `uv run mypy app/schemas/banks.py app/api/routes/banks.py`
Expected: all clean. Fix any reported issues before proceeding.

- [ ] **Step 8: Commit**

```bash
git add app/schemas/banks.py app/api/routes/banks.py tests/api/test_banks.py app/main.py
git commit -m "feat: add GET /banks endpoint"
```

---

### Task 3: Static file mount

**Files:**
- Create: `app/static/.gitkeep` (placeholder so the directory exists in git before Task 4 adds `index.html`; removed implicitly once `index.html` is added in Task 4 — a directory with a real file no longer needs it, but committing it now keeps this task's commit self-contained and buildable on its own)
- Modify: `app/main.py`

**Interfaces:**
- Consumes: nothing new — `app/main.py` already has `advice_router` and `banks_router` imported/included from Tasks 1–2.
- Produces: `GET /dashboard/` serves `app/static/index.html` once Task 4 adds it (Starlette's `StaticFiles(..., html=True)` serves `index.html` for the directory root automatically). For this task alone (before Task 4), `GET /dashboard/` will 404 on the missing `index.html` — that's expected and verified explicitly below, not a bug to fix here.

- [ ] **Step 1: Create the static directory placeholder**

Run: `mkdir -p app/static && touch app/static/.gitkeep`

- [ ] **Step 2: Mount StaticFiles in app/main.py**

In `app/main.py`, add to the imports (after the existing `from fastapi import FastAPI` on line 5):

```python
from starlette.staticfiles import StaticFiles
```

After the existing `app.include_router(rates_router)` and the two router includes added in Tasks 1–2 (i.e., at the end of that block of `include_router` calls), add:

```python
app.mount("/dashboard", StaticFiles(directory="app/static", html=True), name="dashboard")
```

The full router-registration block in `app/main.py` should now read:

```python
app.include_router(health_router)
app.include_router(rates_router)
app.include_router(advice_router)
app.include_router(banks_router)
app.mount("/dashboard", StaticFiles(directory="app/static", html=True), name="dashboard")
```

- [ ] **Step 3: Verify the app still starts and existing routes are unaffected**

Run: `uv run pytest tests/api/ -v`
Expected: PASS — all existing `/rates/*`, `/health/*`, and the new `/advice`, `/banks` tests from Tasks 1–2 still pass unchanged. This confirms the mount didn't shadow or break any existing route.

- [ ] **Step 4: Confirm the not-yet-built dashboard 404s cleanly (not a 500)**

Run: `uv run python -c "
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
response = client.get('/dashboard/')
print(response.status_code)
"`
Expected: `404` (StaticFiles' own not-found handling for a missing `index.html`), not a `500` crash. If this raises an exception instead of returning 404, the `directory=` path is wrong relative to the working directory the app runs from — check it matches where `app/main.py` expects to run from (repo root, same as every other relative import in this codebase).

- [ ] **Step 5: Run ruff and mypy**

Run: `uv run ruff check app/main.py`
Run: `uv run ruff format --check app/main.py`
Run: `uv run mypy app/main.py`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/static/.gitkeep
git commit -m "feat: mount static dashboard directory on the api app"
```

---

### Task 4: Dashboard page

**Files:**
- Create: `app/static/index.html`

**Interfaces:**
- Consumes (all via `fetch()` from the browser, no build step): `GET /advice` (Task 1), `GET /banks` (Task 2), `GET /health/collectors` (pre-existing — returns `list[SourceHealthResponse]`, fields `source, base_currency, quote_currency, last_observed_at, is_stale`), `GET /rates/latest?base_currency=RUB&quote_currency=AMD&source=<name>` (pre-existing), `GET /rates/history?base_currency=RUB&quote_currency=AMD&source=<name>&since=<ISO8601>` (pre-existing, returns newest-first `list[RateResponse]`).
- Produces: nothing consumed by later tasks — this is the last task before verification.

- [ ] **Step 1: Remove the Task 3 placeholder**

`index.html` now populates the directory, so the empty placeholder from Task 3 is no longer needed:

```bash
git rm app/static/.gitkeep
```

- [ ] **Step 2: Write the page**

Create `app/static/index.html`:

```html
<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Currency Advisor</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body { font-family: system-ui, sans-serif; max-width: 720px; margin: 2rem auto; padding: 0 1rem; }
  h1 { font-size: 1.4rem; }
  nav { display: flex; gap: 0.5rem; margin-bottom: 1.5rem; }
  nav button {
    padding: 0.5rem 1rem; border: 1px solid #ccc; background: #f5f5f5;
    border-radius: 6px; cursor: pointer; font-size: 1rem;
  }
  nav button.active { background: #2563eb; color: white; border-color: #2563eb; }
  section { display: none; }
  section.active { display: block; }
  .placeholder { color: #888; font-style: italic; }
  table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
  td, th { padding: 0.4rem 0.6rem; text-align: left; border-bottom: 1px solid #eee; }
  .factor { margin: 0.5rem 0; padding: 0.5rem; background: #f9fafb; border-radius: 6px; }
  .stale { color: #b91c1c; }
  .ok { color: #15803d; }
  canvas { max-width: 100%; }
</style>
</head>
<body>

<h1>Currency Advisor</h1>

<nav>
  <button data-tab="advice" class="active">Курс и рекомендация</button>
  <button data-tab="banks">Банки</button>
  <button data-tab="status">Статус</button>
</nav>

<section id="advice" class="active">
  <div id="advice-content" class="placeholder">Загрузка…</div>
  <canvas id="rate-chart" height="120"></canvas>
</section>

<section id="banks">
  <div id="banks-content" class="placeholder">Загрузка…</div>
</section>

<section id="status">
  <div id="status-content" class="placeholder">Загрузка…</div>
</section>

<script>
const ACTION_LABELS = { exchange_now: "Менять сейчас", wait: "Подождать", neutral: "Нет чёткой рекомендации" };

document.querySelectorAll("nav button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("nav button").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll("section").forEach((s) => s.classList.remove("active"));
    button.classList.add("active");
    document.getElementById(button.dataset.tab).classList.add("active");
  });
});

async function loadAdvice() {
  const el = document.getElementById("advice-content");
  try {
    const response = await fetch("/advice");
    if (response.status === 404) {
      el.textContent = "Пока нет данных для рекомендации.";
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    el.classList.remove("placeholder");
    el.innerHTML = `
      <h2>${ACTION_LABELS[data.action] ?? data.action}</h2>
      <p>${data.summary}</p>
      ${data.factors.map((f) => `<div class="factor">${f.explanation}</div>`).join("")}
    `;
  } catch (err) {
    el.textContent = "Не удалось загрузить рекомендацию.";
  }
}

async function loadBanks() {
  const el = document.getElementById("banks-content");
  try {
    const response = await fetch("/banks");
    if (response.status === 404) {
      el.textContent = "Пока нет банковских курсов.";
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    el.classList.remove("placeholder");
    const rows = data.banks
      .map((b) => `<tr><td>${b.source}</td><td>${b.value ?? "нет данных"}</td></tr>`)
      .join("");
    el.innerHTML = `
      <table>
        <thead><tr><th>Банк</th><th>Курс наличной покупки RUB</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <p>Медиана: ${data.median ?? "нет данных"}</p>
    `;
  } catch (err) {
    el.textContent = "Не удалось загрузить банковские курсы.";
  }
}

async function loadStatus() {
  const el = document.getElementById("status-content");
  try {
    const response = await fetch("/health/collectors");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    el.classList.remove("placeholder");
    const rows = data
      .map((s) => {
        const pair = `${s.base_currency}/${s.quote_currency}`;
        const state = s.last_observed_at === null
          ? "нет данных"
          : new Date(s.last_observed_at).toLocaleString("ru-RU");
        const cls = s.is_stale ? "stale" : "ok";
        return `<tr><td>${s.source}</td><td>${pair}</td><td class="${cls}">${state}</td></tr>`;
      })
      .join("");
    el.innerHTML = `
      <table>
        <thead><tr><th>Источник</th><th>Пара</th><th>Последние данные</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  } catch (err) {
    el.textContent = "Не удалось загрузить статус источников.";
  }
}

async function loadChart() {
  const since = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString();
  async function series(source) {
    const url = `/rates/history?base_currency=RUB&quote_currency=AMD&source=${encodeURIComponent(source)}&since=${since}&limit=1000`;
    const response = await fetch(url);
    if (!response.ok) return [];
    const points = await response.json();
    return points.reverse().map((p) => ({ x: p.observed_at, y: p.value }));
  }

  try {
    const [official, bankMedian] = await Promise.all([
      series("Central Bank of Armenia"),
      series("Bank Average (RUB cash, derived)"),
    ]);
    if (official.length === 0 && bankMedian.length === 0) return;

    new Chart(document.getElementById("rate-chart"), {
      type: "line",
      data: {
        datasets: [
          { label: "Официальный курс ЦБ Армении", data: official, borderColor: "#2563eb", pointRadius: 0 },
          { label: "Банковская медиана", data: bankMedian, borderColor: "#f59e0b", pointRadius: 0 },
        ],
      },
      options: {
        parsing: false,
        scales: { x: { type: "time", time: { unit: "day" } } },
      },
    });
  } catch (err) {
    // Chart is a bonus visualization on the advice tab — a failure here
    // must not block the recommendation text above it from rendering.
  }
}

loadAdvice();
loadBanks();
loadStatus();
loadChart();
</script>

</body>
</html>
```

Note on the chart's `x` axis: Chart.js's `type: "time"` scale needs a date adapter to parse ISO strings into positions. Step 2 verifies this renders; if the browser console shows an adapter-missing error, add `<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3"></script>` right after the existing Chart.js `<script>` tag and re-check.

- [ ] **Step 3: Manually verify in a browser**

Run the app locally (`uv run uvicorn app.main:app --reload`, with a Postgres instance available per the existing local dev setup in `docker/`), then open `http://localhost:8000/dashboard/` in a browser and check:
- All three tabs switch correctly and show real data (seed a couple of rate points first via the existing collectors/scheduler if the local DB is empty, or temporarily insert test rows).
- The chart renders two lines without a console error. If a date-adapter error appears, apply the fix noted in Step 1.
- Reloading the page re-fetches fresh data.
- Temporarily stop the `api`'s DB connection or clear all tables to confirm the `404`/empty-state text appears per tab instead of a blank page or JS crash (check the browser console for uncaught errors).

This step has no automated pass/fail — record what you saw and fix anything broken before moving on, per this project's rule that UI changes are verified in a real browser, not assumed from reading the code.

- [ ] **Step 4: Commit**

```bash
git add app/static/index.html
git commit -m "feat: add static dashboard page with tabs and rate history chart"
```

---

### Task 5: Final verification

**Files:** none created or modified — this task only runs checks across everything from Tasks 1–4.

**Interfaces:** none.

- [ ] **Step 1: Full lint/type/test sweep**

Run: `uv run ruff check .`
Run: `uv run ruff format --check .`
Run: `uv run mypy app`
Run: `uv run pytest`
Expected: all green. Fix anything that fails before proceeding — do not skip or exclude files to force a pass.

- [ ] **Step 2: Confirm no unintended changes to existing routes**

Run: `uv run pytest tests/api/test_rates.py tests/test_health.py -v`
Expected: PASS unchanged — confirms `GET /` and `/rates/*` still behave exactly as before this plan.

- [ ] **Step 3: Re-run the manual browser check from Task 4 Step 2 one more time end-to-end**

With all tasks merged together (not just Task 4 in isolation), repeat Task 4 Step 3's browser check once more, confirming nothing introduced in Task 3's static mount or Tasks 1–2's routes broke anything visible.

- [ ] **Step 4: Update ROADMAP.md**

`ROADMAP.md`'s "Далее (не детализировано)" section does not yet mention a web dashboard as a completed or in-progress item (this feature came from a direct user request outside the numbered stages, per this project's precedent of the decline/rise-alert features which were also added as unplanned stages). Add a short bullet under "Далее" documenting what shipped, matching the style of the existing "Банки" and "Банковский рыночный сигнал" bullets:

```
  - Веб-дашборд (сделано, 2026-07-30): минимальная статическая страница
    (`app/static/index.html`) поверх существующего `api`-контейнера —
    `StaticFiles`, без новых процессов/зависимостей. Три вкладки (курс и
    рекомендация, банки, статус источников) плюс график курса (официальный
    + банковская медиана) через `/rates/history`. Новые тонкие роуты
    `GET /advice` и `GET /banks`; статус переиспользует уже существующий
    `GET /health/collectors`. Публичный, без авторизации, без авто-рефреша.
```

- [ ] **Step 5: Commit the ROADMAP update**

```bash
git add ROADMAP.md
git commit -m "docs: note web dashboard in ROADMAP"
```
