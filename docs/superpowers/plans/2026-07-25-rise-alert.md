# Rise Alert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a proactive, purely informational Telegram notification that
fires when the bank median RUB/AMD cash rate confirms a rising trend
("RUB укрепляется N раз подряд"), symmetric to the already-shipped
`DeclineAlertService`, without predicting a peak or reversal.

**Architecture:** Extract the streak/last-alerted-value branching logic
shared by decline and rise detection into a pure function,
`evaluate_streak()` in a new `app/services/streak_detector.py`.
`DeclineAlertService` is refactored to call it (public contract and
behavior unchanged — a regression-only refactor, existing tests untouched).
A new `RiseAlertService`, structurally identical to `DeclineAlertService`,
calls the same function with the comparison direction flipped
(`operator.gt` instead of `operator.lt`) and writes to its own
`NotificationState` row (`signal_name="bank_average_rise"` — no schema
change, the table is already generic by `signal_name`). `app/bot/main.py`
gains a second `AsyncIOScheduler` job, alongside the existing decline job,
that calls `RiseAlertService`, formats a rise-specific message combining the
bank signal with the official CBA rate for context, and sends it to the
same single admin chat.

**Tech Stack:** Python 3.13, SQLAlchemy Async, aiogram, APScheduler
(already a dependency), pytest/pytest-asyncio, Postgres. No new migration —
reuses the existing `notification_states` table.

## Global Constraints

- Money/rate values are `Decimal`, never `float`.
- Timestamps are timezone-aware UTC (`datetime.now(UTC)`).
- Repository is the only layer touching the DB; Service layers do no
  Telegram/HTTP I/O; Bot layer does no direct SQLAlchemy model access.
- `mypy --strict` must pass; no `# type: ignore` without a documented reason.
- Required checks before any commit claiming "done": `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run mypy app`, `uv run pytest`.
- Rise alert semantics are purely descriptive ("N consecutive rises,
  currently strengthening") — it must NOT imply a prediction of when the
  rise will end/reverse. Do not add peak/reversal language to the message
  text or docstrings. See
  `docs/superpowers/specs/2026-07-25-rise-alert-design.md` for full
  rationale.
- Do not modify `/advice` or `RuleBasedRecommendationStrategy` as part of
  this plan.
- `DeclineAlertService`'s public contract (constructor signature, `check()`
  parameter/return types, `DeclineAlert` fields) must not change — Task 2 is
  a behavior-preserving refactor, verified by its existing test suite
  passing unmodified.

## Setup (before Task 1)

```bash
git switch develop
git pull origin develop
git switch -c feature/rise-alert-notifications
```

Local Postgres must be running for the test suite (`docker compose up -d
postgres` from the repo root).

---

### Task 1: streak_detector — shared pure streak/alert-arming logic

**Files:**
- Create: `app/services/streak_detector.py`
- Test: `tests/services/test_streak_detector.py`

**Interfaces:**
- Produces: `app.services.streak_detector.StreakEvaluation` (frozen
  dataclass: `streak_length: int`, `carried_last_alerted_value: Decimal | None`,
  `should_alert: bool`).
- Produces: `app.services.streak_detector.evaluate_streak(current_value:
  Decimal, previous_value: Decimal, previous_streak_length: int,
  previous_last_alerted_value: Decimal | None, streak_threshold: int,
  continues_trend: Callable[[Decimal, Decimal], bool]) -> StreakEvaluation`.
  Consumed by Task 2 (`DeclineAlertService`, with `operator.lt`) and Task 3
  (`RiseAlertService`, with `operator.gt`). Does NOT handle the
  bootstrap-no-state case or the current-equals-previous case — those stay
  in each service, exactly as they are in the current
  `DeclineAlertService.check()`.

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_streak_detector.py`:

```python
from __future__ import annotations

import operator
from decimal import Decimal

from app.services.streak_detector import evaluate_streak


def test_continuing_move_increments_streak_and_carries_last_alerted() -> None:
    result = evaluate_streak(
        current_value=Decimal("3.70"),
        previous_value=Decimal("3.72"),
        previous_streak_length=1,
        previous_last_alerted_value=None,
        streak_threshold=2,
        continues_trend=operator.lt,
    )

    assert result.streak_length == 2
    assert result.carried_last_alerted_value is None
    assert result.should_alert is True


def test_broken_move_resets_streak_and_clears_last_alerted() -> None:
    result = evaluate_streak(
        current_value=Decimal("3.80"),
        previous_value=Decimal("3.72"),
        previous_streak_length=2,
        previous_last_alerted_value=Decimal("3.72"),
        streak_threshold=2,
        continues_trend=operator.lt,
    )

    assert result.streak_length == 0
    assert result.carried_last_alerted_value is None
    assert result.should_alert is False


def test_no_alert_below_threshold() -> None:
    result = evaluate_streak(
        current_value=Decimal("3.74"),
        previous_value=Decimal("3.76"),
        previous_streak_length=0,
        previous_last_alerted_value=None,
        streak_threshold=2,
        continues_trend=operator.lt,
    )

    assert result.streak_length == 1
    assert result.should_alert is False


def test_realert_on_new_extreme_beyond_last_alerted() -> None:
    result = evaluate_streak(
        current_value=Decimal("3.71"),
        previous_value=Decimal("3.72"),
        previous_streak_length=2,
        previous_last_alerted_value=Decimal("3.72"),
        streak_threshold=2,
        continues_trend=operator.lt,
    )

    assert result.streak_length == 3
    assert result.should_alert is True


def test_no_realert_when_streak_continues_without_a_new_extreme() -> None:
    # Streak continues (3.72 < previous_value 3.73) but does not go past the
    # already-alerted floor of 3.72 itself — must not re-alert.
    result = evaluate_streak(
        current_value=Decimal("3.72"),
        previous_value=Decimal("3.73"),
        previous_streak_length=2,
        previous_last_alerted_value=Decimal("3.72"),
        streak_threshold=2,
        continues_trend=operator.lt,
    )

    assert result.streak_length == 3
    assert result.should_alert is False


def test_rise_direction_mirrors_decline_with_operator_gt() -> None:
    result = evaluate_streak(
        current_value=Decimal("3.80"),
        previous_value=Decimal("3.78"),
        previous_streak_length=1,
        previous_last_alerted_value=None,
        streak_threshold=2,
        continues_trend=operator.gt,
    )

    assert result.streak_length == 2
    assert result.carried_last_alerted_value is None
    assert result.should_alert is True


def test_rise_direction_resets_on_a_drop() -> None:
    result = evaluate_streak(
        current_value=Decimal("3.74"),
        previous_value=Decimal("3.80"),
        previous_streak_length=2,
        previous_last_alerted_value=Decimal("3.80"),
        streak_threshold=2,
        continues_trend=operator.gt,
    )

    assert result.streak_length == 0
    assert result.carried_last_alerted_value is None
    assert result.should_alert is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/services/test_streak_detector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.streak_detector'`.

- [ ] **Step 3: Write the implementation**

Create `app/services/streak_detector.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

Comparator = Callable[[Decimal, Decimal], bool]


@dataclass(frozen=True, slots=True)
class StreakEvaluation:
    streak_length: int
    carried_last_alerted_value: Decimal | None
    should_alert: bool


def evaluate_streak(
    current_value: Decimal,
    previous_value: Decimal,
    previous_streak_length: int,
    previous_last_alerted_value: Decimal | None,
    streak_threshold: int,
    continues_trend: Comparator,
) -> StreakEvaluation:
    """Shared branching logic behind DeclineAlertService/RiseAlertService.

    continues_trend(current, reference) must be True when `current` extends
    the tracked trend relative to `reference` (operator.lt for a decline
    streak, operator.gt for a rise streak). Callers are responsible for the
    bootstrap (no prior state) and current-equals-previous-value cases —
    this function only computes the streak/re-alert branching once a caller
    has already confirmed the value actually changed.
    """
    if continues_trend(current_value, previous_value):
        streak_length = previous_streak_length + 1
        carried_last_alerted_value = previous_last_alerted_value
    else:
        streak_length = 0
        carried_last_alerted_value = None

    should_alert = streak_length >= streak_threshold and (
        carried_last_alerted_value is None
        or continues_trend(current_value, carried_last_alerted_value)
    )

    return StreakEvaluation(streak_length, carried_last_alerted_value, should_alert)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/services/test_streak_detector.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/streak_detector.py tests/services/test_streak_detector.py
git commit -m "feat: add shared streak_detector core for alert services"
```

---

### Task 2: Refactor DeclineAlertService onto streak_detector (behavior-preserving)

**Files:**
- Modify: `app/services/decline_alert_service.py`

**Interfaces:**
- Consumes: `app.services.streak_detector.evaluate_streak`, `StreakEvaluation` (Task 1).
- Unchanged (must still hold after this task): `DeclineAlertService.__init__(self,
  exchange_rates: ExchangeRateRepository, notification_states:
  NotificationStateRepository, streak_threshold: int) -> None`,
  `async def check(self) -> DeclineAlert | None`, `DeclineAlert` fields
  (`current_value: Decimal`, `streak_length: int`,
  `previous_alerted_value: Decimal | None`).

This is a pure refactor: no new test file, no behavior change. The existing
`tests/services/test_decline_alert_service.py` (8 tests) is the safety net —
it is not modified.

- [ ] **Step 1: Confirm the baseline passes before touching anything**

Run: `uv run pytest tests/services/test_decline_alert_service.py -v`
Expected: PASS (8 tests) — this is the pre-refactor baseline.

- [ ] **Step 2: Replace the implementation**

Replace the full contents of `app/services/decline_alert_service.py` with:

```python
from __future__ import annotations

import operator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.repositories.exchange_rate import ExchangeRateRepository
from app.repositories.notification_state_repository import (
    NotificationStateRepository,
    NotificationStateSnapshot,
)
from app.services.bank_average_service import SOURCE_NAME as BANK_AVERAGE_SOURCE_NAME
from app.services.streak_detector import evaluate_streak

_BASE_CURRENCY = "RUB"
_QUOTE_CURRENCY = "AMD"
_SIGNAL_NAME = "bank_average_decline"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class DeclineAlert:
    current_value: Decimal
    streak_length: int
    previous_alerted_value: Decimal | None


class DeclineAlertService:
    """Trend-following decline detector over the Bank Average median.

    Deliberately decoupled from RuleBasedRecommendationStrategy (mean-
    reversion): a push alert is an early-warning signal, and the RUB cash
    market in Armenia is flow-driven/thin rather than arbitraged, so a
    confirmed decline is treated as likely to persist, not to revert. See
    docs/superpowers/specs/2026-07-24-decline-alert-design.md.

    check() does a read (NotificationStateRepository.get) then a write
    (NotificationStateRepository.save) as two separate DB round trips, not
    one transaction. This is safe only because the caller (app/bot/main.py)
    runs this job with max_instances=1 in a single bot process, so calls to
    check() never run concurrently. Running multiple bot replicas, or
    dropping max_instances=1, would introduce a race between the read and
    the write.

    The streak/last-alerted-value branching itself lives in
    app.services.streak_detector.evaluate_streak, shared with
    RiseAlertService (operator.lt here, operator.gt there). See
    docs/superpowers/specs/2026-07-25-rise-alert-design.md for why this was
    extracted rather than duplicated.
    """

    def __init__(
        self,
        exchange_rates: ExchangeRateRepository,
        notification_states: NotificationStateRepository,
        streak_threshold: int,
    ) -> None:
        self._exchange_rates = exchange_rates
        self._notification_states = notification_states
        self._streak_threshold = streak_threshold

    async def check(self) -> DeclineAlert | None:
        point = await self._exchange_rates.get_latest(
            BANK_AVERAGE_SOURCE_NAME, _BASE_CURRENCY, _QUOTE_CURRENCY
        )
        if point is None:
            return None

        state = await self._notification_states.get(_SIGNAL_NAME)
        if state is None:
            await self._notification_states.save(
                NotificationStateSnapshot(
                    signal_name=_SIGNAL_NAME,
                    last_value=point.value,
                    streak_length=0,
                    last_alerted_value=None,
                    updated_at=_utcnow(),
                )
            )
            return None

        if point.value == state.last_value:
            return None

        evaluation = evaluate_streak(
            current_value=point.value,
            previous_value=state.last_value,
            previous_streak_length=state.streak_length,
            previous_last_alerted_value=state.last_alerted_value,
            streak_threshold=self._streak_threshold,
            continues_trend=operator.lt,
        )

        alert: DeclineAlert | None = None
        last_alerted_value = evaluation.carried_last_alerted_value
        if evaluation.should_alert:
            alert = DeclineAlert(
                current_value=point.value,
                streak_length=evaluation.streak_length,
                previous_alerted_value=evaluation.carried_last_alerted_value,
            )
            last_alerted_value = point.value

        await self._notification_states.save(
            NotificationStateSnapshot(
                signal_name=_SIGNAL_NAME,
                last_value=point.value,
                streak_length=evaluation.streak_length,
                last_alerted_value=last_alerted_value,
                updated_at=_utcnow(),
            )
        )
        return alert
```

- [ ] **Step 3: Run tests to verify nothing broke**

Run: `uv run pytest tests/services/test_decline_alert_service.py tests/services/test_streak_detector.py -v`
Expected: PASS (8 + 7 tests, all unchanged assertions from before this task).

- [ ] **Step 4: Commit**

```bash
git add app/services/decline_alert_service.py
git commit -m "refactor: extract DeclineAlertService streak logic into streak_detector"
```

---

### Task 3: RiseAlertService

**Files:**
- Create: `app/services/rise_alert_service.py`
- Test: `tests/services/test_rise_alert_service.py`

**Interfaces:**
- Consumes: `app.services.streak_detector.evaluate_streak` (Task 1),
  `app.repositories.notification_state_repository.NotificationStateRepository`/
  `NotificationStateSnapshot` (existing), `app.repositories.exchange_rate.ExchangeRateRepository`
  (existing), `app.services.bank_average_service.SOURCE_NAME` (existing).
- Produces: `app.services.rise_alert_service.RiseAlert` (frozen dataclass:
  `current_value: Decimal`, `streak_length: int`,
  `previous_alerted_value: Decimal | None`), `app.services.rise_alert_service.RiseAlertService`
  with `__init__(self, exchange_rates: ExchangeRateRepository,
  notification_states: NotificationStateRepository, streak_threshold: int) -> None`
  and `async def check(self) -> RiseAlert | None`. Consumed by Task 5 (bot
  wiring).

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_rise_alert_service.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import RatePoint
from app.repositories.exchange_rate import ExchangeRateRepository
from app.repositories.notification_state_repository import NotificationStateRepository
from app.services.bank_average_service import SOURCE_NAME as BANK_AVERAGE_SOURCE_NAME
from app.services.rise_alert_service import RiseAlertService

_START = datetime(2026, 7, 25, 8, 0, tzinfo=UTC)


def _bank_average_point(value: str, minutes_after_start: int) -> RatePoint:
    return RatePoint(
        BANK_AVERAGE_SOURCE_NAME,
        "RUB",
        "AMD",
        Decimal(value),
        _START + timedelta(minutes=minutes_after_start),
    )


def _service(db_session: AsyncSession, *, streak_threshold: int = 2) -> RiseAlertService:
    return RiseAlertService(
        ExchangeRateRepository(db_session),
        NotificationStateRepository(db_session),
        streak_threshold,
    )


async def test_no_data_returns_no_alert(db_session: AsyncSession) -> None:
    service = _service(db_session)

    assert await service.check() is None


async def test_first_ever_check_bootstraps_state_without_alerting(
    db_session: AsyncSession,
) -> None:
    await ExchangeRateRepository(db_session).save(_bank_average_point("3.72", 0))
    service = _service(db_session)

    assert await service.check() is None


async def test_repeated_identical_value_does_not_alert(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session)
    await repository.save(_bank_average_point("3.72", 0))
    await service.check()

    await repository.save(_bank_average_point("3.72", 30))

    assert await service.check() is None


async def test_single_rise_below_threshold_does_not_alert(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    await repository.save(_bank_average_point("3.72", 0))
    await service.check()

    await repository.save(_bank_average_point("3.74", 30))

    assert await service.check() is None


async def test_two_consecutive_rises_triggers_alert(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    await repository.save(_bank_average_point("3.72", 0))
    await service.check()
    await repository.save(_bank_average_point("3.74", 30))
    await service.check()

    await repository.save(_bank_average_point("3.76", 60))
    alert = await service.check()

    assert alert is not None
    assert alert.current_value == Decimal("3.76")
    assert alert.streak_length == 2
    assert alert.previous_alerted_value is None


async def test_repeated_value_at_already_alerted_high_does_not_realert(
    db_session: AsyncSession,
) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    for value, minutes in (("3.72", 0), ("3.74", 30), ("3.76", 60)):
        await repository.save(_bank_average_point(value, minutes))
        await service.check()

    await repository.save(_bank_average_point("3.76", 90))

    assert await service.check() is None


async def test_any_further_rise_after_arming_realerts_immediately(
    db_session: AsyncSession,
) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    for value, minutes in (("3.72", 0), ("3.74", 30), ("3.76", 60)):
        await repository.save(_bank_average_point(value, minutes))
        await service.check()

    await repository.save(_bank_average_point("3.77", 90))
    alert = await service.check()

    assert alert is not None
    assert alert.current_value == Decimal("3.77")
    assert alert.previous_alerted_value == Decimal("3.76")


async def test_rate_decrease_resets_streak_and_rearms_alerting(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    for value, minutes in (("3.72", 0), ("3.74", 30), ("3.76", 60)):
        await repository.save(_bank_average_point(value, minutes))
        await service.check()

    await repository.save(_bank_average_point("3.68", 90))
    assert await service.check() is None  # decrease: streak resets, no alert

    await repository.save(_bank_average_point("3.70", 120))
    assert await service.check() is None  # first rise after reset: streak=1

    await repository.save(_bank_average_point("3.72", 150))
    alert = await service.check()

    assert alert is not None
    assert alert.current_value == Decimal("3.72")
    assert alert.streak_length == 2
    assert alert.previous_alerted_value is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/services/test_rise_alert_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.rise_alert_service'`.

- [ ] **Step 3: Write the implementation**

Create `app/services/rise_alert_service.py`:

```python
from __future__ import annotations

import operator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.repositories.exchange_rate import ExchangeRateRepository
from app.repositories.notification_state_repository import (
    NotificationStateRepository,
    NotificationStateSnapshot,
)
from app.services.bank_average_service import SOURCE_NAME as BANK_AVERAGE_SOURCE_NAME
from app.services.streak_detector import evaluate_streak

_BASE_CURRENCY = "RUB"
_QUOTE_CURRENCY = "AMD"
_SIGNAL_NAME = "bank_average_rise"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class RiseAlert:
    current_value: Decimal
    streak_length: int
    previous_alerted_value: Decimal | None


class RiseAlertService:
    """Informational rise detector over the Bank Average median.

    Purely descriptive ("RUB is strengthening N times in a row") — does
    NOT predict a peak or reversal; that is deferred to a future ML-based
    phase (see docs/superpowers/specs/2026-07-25-rise-alert-design.md).
    Structurally identical to DeclineAlertService with the streak direction
    flipped (operator.gt instead of operator.lt); the shared branching logic
    lives in app.services.streak_detector.evaluate_streak.

    Same non-atomic get-then-save caveat as DeclineAlertService: check()
    does a read then a write as two separate DB round trips, safe only
    because the caller (app/bot/main.py) runs this job with max_instances=1
    in a single bot process.
    """

    def __init__(
        self,
        exchange_rates: ExchangeRateRepository,
        notification_states: NotificationStateRepository,
        streak_threshold: int,
    ) -> None:
        self._exchange_rates = exchange_rates
        self._notification_states = notification_states
        self._streak_threshold = streak_threshold

    async def check(self) -> RiseAlert | None:
        point = await self._exchange_rates.get_latest(
            BANK_AVERAGE_SOURCE_NAME, _BASE_CURRENCY, _QUOTE_CURRENCY
        )
        if point is None:
            return None

        state = await self._notification_states.get(_SIGNAL_NAME)
        if state is None:
            await self._notification_states.save(
                NotificationStateSnapshot(
                    signal_name=_SIGNAL_NAME,
                    last_value=point.value,
                    streak_length=0,
                    last_alerted_value=None,
                    updated_at=_utcnow(),
                )
            )
            return None

        if point.value == state.last_value:
            return None

        evaluation = evaluate_streak(
            current_value=point.value,
            previous_value=state.last_value,
            previous_streak_length=state.streak_length,
            previous_last_alerted_value=state.last_alerted_value,
            streak_threshold=self._streak_threshold,
            continues_trend=operator.gt,
        )

        alert: RiseAlert | None = None
        last_alerted_value = evaluation.carried_last_alerted_value
        if evaluation.should_alert:
            alert = RiseAlert(
                current_value=point.value,
                streak_length=evaluation.streak_length,
                previous_alerted_value=evaluation.carried_last_alerted_value,
            )
            last_alerted_value = point.value

        await self._notification_states.save(
            NotificationStateSnapshot(
                signal_name=_SIGNAL_NAME,
                last_value=point.value,
                streak_length=evaluation.streak_length,
                last_alerted_value=last_alerted_value,
                updated_at=_utcnow(),
            )
        )
        return alert
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/services/test_rise_alert_service.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/rise_alert_service.py tests/services/test_rise_alert_service.py
git commit -m "feat: add RiseAlertService rising-trend detector"
```

---

### Task 4: Config setting for the rise alert threshold

**Files:**
- Modify: `app/core/config.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `Settings.rise_alert_streak_threshold: int` (default `2`),
  consumed by Task 5.

This task is purely additive (no rename yet — the existing
`decline_alert_check_interval_minutes` field is renamed in Task 5, together
with its only call site, so the repo never sits in a broken intermediate
state).

- [ ] **Step 1: Add the new setting field**

In `app/core/config.py`, add this field to `Settings` right after
`decline_alert_check_interval_minutes: int = 30`:

```python
    rise_alert_streak_threshold: int = 2
```

- [ ] **Step 2: Add the new env var to .env.example**

In `.env.example`, add after `DECLINE_ALERT_CHECK_INTERVAL_MINUTES=30`:

```
RISE_ALERT_STREAK_THRESHOLD=2
```

- [ ] **Step 3: Verify the app still imports cleanly**

Run: `uv run python -c "from app.core.config import get_settings; print(get_settings().rise_alert_streak_threshold)"`
Expected: prints `2` (or whatever your local `.env` overrides it to).

- [ ] **Step 4: Commit**

```bash
git add app/core/config.py .env.example
git commit -m "feat: add rise alert streak threshold setting"
```

---

### Task 5: Bot integration — rise scheduler job, message, shared interval rename

**Files:**
- Modify: `app/core/config.py`
- Modify: `.env.example`
- Modify: `app/bot/main.py`
- Test: `tests/bot/test_main.py`

**Interfaces:**
- Consumes: `app.services.rise_alert_service.RiseAlert`, `RiseAlertService`
  (Task 3); `Settings.rise_alert_streak_threshold` (Task 4).
- Produces: `app.bot.main.format_rise_alert_message(alert: RiseAlert,
  official_rate: RatePoint | None) -> str` and
  `app.bot.main.check_rise_and_notify(bot: Bot, session_factory:
  async_sessionmaker[AsyncSession], admin_chat_id: int, streak_threshold:
  int) -> None`, wired into `main()` as a second scheduler job alongside the
  existing decline one.
- Renames `Settings.decline_alert_check_interval_minutes` →
  `Settings.alert_check_interval_minutes` (both jobs share one interval —
  they poll the same underlying `Bank Average` signal on the same cadence,
  see the spec's config section for why a second identical interval field
  was rejected). This is the only rename in the whole plan; it happens in
  the same step as updating its one call site in `app/bot/main.py`, so
  `mypy`/tests never see a broken intermediate state.

- [ ] **Step 1: Rename the shared interval setting**

In `app/core/config.py`, rename:
```python
    decline_alert_check_interval_minutes: int = 30
```
to:
```python
    alert_check_interval_minutes: int = 30
```
(leave `decline_alert_streak_threshold` and `rise_alert_streak_threshold`
as-is — only the interval is shared).

In `.env.example`, rename the line:
```
DECLINE_ALERT_CHECK_INTERVAL_MINUTES=30
```
to:
```
ALERT_CHECK_INTERVAL_MINUTES=30
```

- [ ] **Step 2: Write the failing tests**

`tests/bot/test_main.py` already imports `AsyncMock`, `AsyncSession`,
`async_sessionmaker`, `RatePoint`, `BANK_AVERAGE_SOURCE_NAME`, `Decimal`,
`datetime`/`UTC`/`timedelta`, and `pytest` — do not re-import these (a
duplicate import of an already-imported name is a ruff F811 error).

Make exactly two import changes:

1. Extend the existing `from app.bot.main import (...)` block to add
   `check_rise_and_notify` and `format_rise_alert_message` to the list
   (keep the existing names, alphabetical order).
2. Add one new import line after the existing
   `from app.services.decline_alert_service import DeclineAlert` line:
   ```python
   from app.services.rise_alert_service import RiseAlert
   ```

Append these tests to `tests/bot/test_main.py`:

```python
def test_format_rise_alert_message_without_previous_alert_or_official_rate() -> None:
    alert = RiseAlert(
        current_value=Decimal("3.80"), streak_length=2, previous_alerted_value=None
    )

    text = format_rise_alert_message(alert, official_rate=None)

    assert "RUB укрепляется" in text
    assert "2 раз подряд" in text
    assert "3.80" in text
    assert "прошлый раз" not in text
    assert "Официальный курс" not in text


def test_format_rise_alert_message_with_previous_alert_and_official_rate() -> None:
    alert = RiseAlert(
        current_value=Decimal("3.82"), streak_length=2, previous_alerted_value=Decimal("3.80")
    )
    official_rate = _rate_point("4.60")

    text = format_rise_alert_message(alert, official_rate)

    assert "прошлый раз я сообщал (3.80)" in text
    assert "Официальный курс ЦБ Армении: 4.60" in text


async def test_check_rise_and_notify_sends_message_when_alert_fires(
    db_session: AsyncSession,
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.save(_rate_point("4.60"))
    bot = AsyncMock()

    for value, minutes in (("3.72", 0), ("3.74", 30), ("3.76", 60)):
        await repository.save(
            RatePoint(
                BANK_AVERAGE_SOURCE_NAME,
                "RUB",
                "AMD",
                Decimal(value),
                _OBSERVED_AT + timedelta(minutes=minutes),
            )
        )
        await check_rise_and_notify(
            bot, test_session_factory, admin_chat_id=42, streak_threshold=2
        )

    bot.send_message.assert_awaited_once()
    call_args = bot.send_message.call_args
    assert call_args.args[0] == 42
    assert "3.76" in call_args.args[1]
    assert "4.60" in call_args.args[1]


async def test_check_rise_and_notify_does_not_send_when_no_alert(
    db_session: AsyncSession,
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bot = AsyncMock()

    await check_rise_and_notify(
        bot, test_session_factory, admin_chat_id=42, streak_threshold=2
    )

    bot.send_message.assert_not_awaited()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/bot/test_main.py -v`
Expected: FAIL — `ImportError: cannot import name 'check_rise_and_notify' from
'app.bot.main'`.

- [ ] **Step 4: Add the RiseAlertService import to app/bot/main.py**

In `app/bot/main.py`, change the existing line:
```python
from app.services.decline_alert_service import DeclineAlert, DeclineAlertService
```
to add a new import right after it (alphabetical position among `app.services.*`):
```python
from app.services.decline_alert_service import DeclineAlert, DeclineAlertService
from app.services.rise_alert_service import RiseAlert, RiseAlertService
```

- [ ] **Step 5: Add format_rise_alert_message and check_rise_and_notify**

Add these functions to `app/bot/main.py`, right after
`check_decline_and_notify` and before `main()`:

```python
def format_rise_alert_message(alert: RiseAlert, official_rate: RatePoint | None) -> str:
    lines = [
        "📈 RUB укрепляется",
        (
            f"Банковский курс наличной покупки RUB вырос {alert.streak_length} раз подряд, "
            f"сейчас {alert.current_value}."
        ),
    ]
    if alert.previous_alerted_value is not None:
        lines.append(
            f"Ещё выше, чем в прошлый раз я сообщал ({alert.previous_alerted_value})."
        )
    if official_rate is not None:
        lines.append(f"Официальный курс ЦБ Армении: {official_rate.value}.")
    return "\n".join(lines)


async def check_rise_and_notify(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    admin_chat_id: int,
    streak_threshold: int,
) -> None:
    try:
        async with session_factory() as session:
            exchange_rates = ExchangeRateRepository(session)
            service = RiseAlertService(
                exchange_rates,
                NotificationStateRepository(session),
                streak_threshold,
            )
            alert = await service.check()
            if alert is None:
                return
            official_rate = await exchange_rates.get_latest(
                SOURCE_NAME, _BASE_CURRENCY, _QUOTE_CURRENCY
            )
        text = format_rise_alert_message(alert, official_rate)
        # Same at-most-once delivery trade-off as check_decline_and_notify:
        # service.check() already persisted last_alerted_value above, so a
        # send_message failure here silently drops this specific alert.
        await bot.send_message(admin_chat_id, text)
    except Exception:
        logger.exception("rise_alert.check_failed")
```

- [ ] **Step 6: Wire the second scheduler job into main()**

Replace the existing `main()` function in `app/bot/main.py`:

```python
async def main() -> None:
    configure_logging()
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is empty in .env")
    if not settings.telegram_admin_chat_id:
        raise RuntimeError("TELEGRAM_ADMIN_CHAT_ID is empty in .env")
    bot = Bot(settings.telegram_bot_token)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_decline_and_notify,
        trigger=IntervalTrigger(minutes=settings.decline_alert_check_interval_minutes),
        args=(
            bot,
            SessionFactory,
            settings.telegram_admin_chat_id,
            settings.decline_alert_streak_threshold,
        ),
        id="decline_alert_check",
        next_run_time=datetime.now(UTC),
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    scheduler.start()
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
```

with:

```python
async def main() -> None:
    configure_logging()
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is empty in .env")
    if not settings.telegram_admin_chat_id:
        raise RuntimeError("TELEGRAM_ADMIN_CHAT_ID is empty in .env")
    bot = Bot(settings.telegram_bot_token)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_decline_and_notify,
        trigger=IntervalTrigger(minutes=settings.alert_check_interval_minutes),
        args=(
            bot,
            SessionFactory,
            settings.telegram_admin_chat_id,
            settings.decline_alert_streak_threshold,
        ),
        id="decline_alert_check",
        next_run_time=datetime.now(UTC),
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    scheduler.add_job(
        check_rise_and_notify,
        trigger=IntervalTrigger(minutes=settings.alert_check_interval_minutes),
        args=(
            bot,
            SessionFactory,
            settings.telegram_admin_chat_id,
            settings.rise_alert_streak_threshold,
        ),
        id="rise_alert_check",
        next_run_time=datetime.now(UTC),
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    scheduler.start()
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/bot/test_main.py -v`
Expected: PASS (all tests, including the 4 new ones).

- [ ] **Step 8: Commit**

```bash
git add app/core/config.py .env.example app/bot/main.py tests/bot/test_main.py
git commit -m "feat: send proactive Telegram alert on confirmed rate rise"
```

---

### Task 6: Full verification and local/VM .env setup

**Files:** none (verification only).

- [ ] **Step 1: Run the full required check suite**

Run, in order:
```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest
```
Expected: all four succeed with no errors. If `ruff check .` flags import
ordering (rule `I001`) from the new imports added across Tasks 1–5, run
`uv run ruff check --fix .` to auto-sort them, then re-run
`ruff format --check .`.

- [ ] **Step 2: Update your local .env**

Your local `.env` (not committed) needs the renamed variable:
```
ALERT_CHECK_INTERVAL_MINUTES=30
```
Remove the old `DECLINE_ALERT_CHECK_INTERVAL_MINUTES` line if present.
`RISE_ALERT_STREAK_THRESHOLD` can be left unset to use its default (`2`).

- [ ] **Step 3: Manual smoke check (optional but recommended)**

With local Postgres running (`docker compose up -d postgres`), run the bot
locally:
```bash
uv run python -m app.bot.main
```
Confirm it logs `Start polling` / `Run polling` as before, with no
exceptions from either the `decline_alert_check` or the new
`rise_alert_check` job (both run once immediately on startup per
`next_run_time=datetime.now(UTC)`). Stop with Ctrl+C.

## Deployment note (not a task — informational)

The VM's `.env` currently has `DECLINE_ALERT_CHECK_INTERVAL_MINUTES` set.
Task 5 renames this to `ALERT_CHECK_INTERVAL_MINUTES` in `.env.example` and
`app/core/config.py` — the VM's actual `.env` file must be updated with the
new name (and the old one removed) as part of deploying this branch, or the
setting will silently fall back to its default (`30`, which happens to
match the current value, so this is a naming-hygiene fix rather than a
behavior change — but should still be done for clarity before the next
person edits it).
