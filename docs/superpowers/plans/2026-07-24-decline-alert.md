# Decline Alert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a proactive Telegram notification that fires when the bank
median RUB/AMD cash rate confirms a declining trend, so the user can
exchange before it drops further.

**Architecture:** A new persistent `NotificationState` table tracks a
decline streak per signal. `DeclineAlertService` (pure detection, no
Telegram knowledge) reads the latest `Bank Average` rate via the existing
`ExchangeRateRepository`, updates the streak via a new
`NotificationStateRepository`, and returns a `DeclineAlert` when the
trend-following trigger condition is met. `app/bot/main.py` gains its own
`AsyncIOScheduler` job (running alongside `dp.start_polling`) that calls the
service on an interval, formats a message combining the bank signal with the
official CBA rate for context, and sends it to a single admin chat via the
existing `Bot` instance.

**Tech Stack:** Python 3.13, SQLAlchemy Async, Alembic, aiogram, APScheduler
(already a dependency), pytest/pytest-asyncio, Postgres.

## Global Constraints

- Money/rate values are `Decimal`, never `float`.
- Timestamps are timezone-aware UTC (`datetime.now(UTC)`).
- Repository is the only layer touching the DB; Service layers do no
  Telegram/HTTP I/O; Bot layer does no direct SQLAlchemy model access.
- `mypy --strict` must pass; no `# type: ignore` without a documented reason.
- Required checks before any commit claiming "done": `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run mypy app`, `uv run pytest`.
- Trigger semantics are trend-following (a confirmed decline is assumed to
  persist), deliberately decoupled from `RuleBasedRecommendationStrategy`'s
  mean-reversion logic used by `/advice` — see
  `docs/superpowers/specs/2026-07-24-decline-alert-design.md` for the full
  rationale. Do not modify `/advice` or `RuleBasedRecommendationStrategy` as
  part of this plan.

## Setup (before Task 1)

This plan implements code, so it runs on a feature branch off `develop`, not
on the `docs/decline-alert-design` branch (docs-only). Before Task 1:

```bash
git switch develop
git pull origin develop
git switch -c feature/decline-alert-notifications
```

Local Postgres must be running for the test suite (`docker compose up -d
postgres` from the repo root, per this project's existing dev setup).

---

### Task 1: NotificationState model + Repository

**Files:**
- Create: `app/models/notification_state.py`
- Create: `app/repositories/notification_state_repository.py`
- Test: `tests/repositories/test_notification_state_repository.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Produces: `app.models.notification_state.NotificationState` (SQLAlchemy
  model, internal to the repository — never imported outside it).
- Produces: `app.repositories.notification_state_repository.NotificationStateSnapshot`
  (frozen dataclass: `signal_name: str`, `last_value: Decimal`,
  `streak_length: int`, `last_alerted_value: Decimal | None`,
  `updated_at: datetime`).
- Produces: `app.repositories.notification_state_repository.NotificationStateRepository`
  with `__init__(self, session: AsyncSession) -> None`,
  `async def get(self, signal_name: str) -> NotificationStateSnapshot | None`,
  `async def save(self, state: NotificationStateSnapshot) -> None` (upsert
  by `signal_name`).

- [ ] **Step 1: Write the failing test**

Create `tests/repositories/test_notification_state_repository.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.notification_state_repository import (
    NotificationStateRepository,
    NotificationStateSnapshot,
)

_UPDATED_AT = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)


def _snapshot(
    *,
    signal_name: str = "bank_average_decline",
    last_value: Decimal = Decimal("3.76"),
    streak_length: int = 0,
    last_alerted_value: Decimal | None = None,
    updated_at: datetime = _UPDATED_AT,
) -> NotificationStateSnapshot:
    return NotificationStateSnapshot(
        signal_name=signal_name,
        last_value=last_value,
        streak_length=streak_length,
        last_alerted_value=last_alerted_value,
        updated_at=updated_at,
    )


async def test_get_returns_none_when_no_state(db_session: AsyncSession) -> None:
    repository = NotificationStateRepository(db_session)

    assert await repository.get("bank_average_decline") is None


async def test_save_then_get_round_trips(db_session: AsyncSession) -> None:
    repository = NotificationStateRepository(db_session)
    state = _snapshot(streak_length=2, last_alerted_value=Decimal("3.72"))

    await repository.save(state)
    loaded = await repository.get("bank_average_decline")

    assert loaded == state


async def test_save_upserts_existing_signal(db_session: AsyncSession) -> None:
    repository = NotificationStateRepository(db_session)
    await repository.save(_snapshot(streak_length=1))

    await repository.save(_snapshot(streak_length=2, last_alerted_value=Decimal("3.70")))
    loaded = await repository.get("bank_average_decline")

    assert loaded is not None
    assert loaded.streak_length == 2
    assert loaded.last_alerted_value == Decimal("3.70")


async def test_save_can_clear_last_alerted_value(db_session: AsyncSession) -> None:
    repository = NotificationStateRepository(db_session)
    await repository.save(_snapshot(last_alerted_value=Decimal("3.70")))

    await repository.save(_snapshot(last_alerted_value=None))
    loaded = await repository.get("bank_average_decline")

    assert loaded is not None
    assert loaded.last_alerted_value is None


async def test_different_signal_names_are_independent(db_session: AsyncSession) -> None:
    repository = NotificationStateRepository(db_session)
    await repository.save(_snapshot(signal_name="signal_a", last_value=Decimal("1.0")))
    await repository.save(_snapshot(signal_name="signal_b", last_value=Decimal("2.0")))

    a = await repository.get("signal_a")
    b = await repository.get("signal_b")

    assert a is not None and a.last_value == Decimal("1.0")
    assert b is not None and b.last_value == Decimal("2.0")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/repositories/test_notification_state_repository.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named
'app.repositories.notification_state_repository'`.

- [ ] **Step 3: Write the model**

Create `app/models/notification_state.py`:

```python
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NotificationState(Base):
    __tablename__ = "notification_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_name: Mapped[str] = mapped_column(String(50), unique=True)
    last_value: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    streak_length: Mapped[int] = mapped_column()
    last_alerted_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

- [ ] **Step 4: Write the repository**

Create `app/repositories/notification_state_repository.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification_state import NotificationState


@dataclass(frozen=True, slots=True)
class NotificationStateSnapshot:
    signal_name: str
    last_value: Decimal
    streak_length: int
    last_alerted_value: Decimal | None
    updated_at: datetime


class NotificationStateRepository:
    """Sole owner of notification_states persistence.

    Works in NotificationStateSnapshot at its boundary in both directions —
    callers never see the SQLAlchemy model, same pattern as
    ExchangeRateRepository/RatePoint.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, signal_name: str) -> NotificationStateSnapshot | None:
        stmt = select(NotificationState).where(NotificationState.signal_name == signal_name)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return self._to_snapshot(row) if row is not None else None

    async def save(self, state: NotificationStateSnapshot) -> None:
        values = {
            "signal_name": state.signal_name,
            "last_value": state.last_value,
            "streak_length": state.streak_length,
            "last_alerted_value": state.last_alerted_value,
            "updated_at": state.updated_at,
        }
        stmt = insert(NotificationState).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=["signal_name"], set_=values)
        await self._session.execute(stmt)
        await self._session.commit()

    @staticmethod
    def _to_snapshot(row: NotificationState) -> NotificationStateSnapshot:
        return NotificationStateSnapshot(
            signal_name=row.signal_name,
            last_value=row.last_value,
            streak_length=row.streak_length,
            last_alerted_value=row.last_alerted_value,
            updated_at=row.updated_at,
        )
```

- [ ] **Step 5: Update conftest.py to truncate the new table**

In `tests/conftest.py`, the `db_session` fixture currently truncates only
`exchange_rates`. Update it to also truncate `notification_states`:

```python
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/repositories/test_notification_state_repository.py -v`
Expected: PASS (5 tests).

- [ ] **Step 7: Commit**

```bash
git add app/models/notification_state.py app/repositories/notification_state_repository.py tests/repositories/test_notification_state_repository.py tests/conftest.py
git commit -m "feat: add NotificationState model and repository"
```

---

### Task 2: Alembic migration for notification_states

**Files:**
- Create: `alembic/versions/<generated_hash>_create_notification_states_table.py`

**Interfaces:**
- Consumes: `app.models.notification_state.NotificationState` (Task 1).
- Produces: `notification_states` table in the dev/prod database (the test
  suite does not use Alembic — `tests/conftest.py`'s `test_engine` fixture
  creates tables straight from `Base.metadata`, so Task 1's tests already
  pass without this migration; this task is required for the actual running
  app/bot processes, and is what the existing `migrate` compose service —
  added earlier — will run automatically on the next deploy).

- [ ] **Step 1: Ensure local dev DB is at head**

Run: `uv run alembic upgrade head`
Expected: no pending migrations reported (or applies cleanly if behind).

- [ ] **Step 2: Autogenerate the migration**

Run: `uv run alembic revision --autogenerate -m "create notification states table"`

This creates a new file `alembic/versions/<hash>_create_notification_states_table.py`
with a random `<hash>`. Keep the generated filename and `revision` value.

- [ ] **Step 3: Replace the generated body with this exact content**

Open the generated file and replace its `upgrade()`/`downgrade()` (and
verify `down_revision` points to `"24d71e4e04e3"`, the current head) so the
file reads:

```python
"""create notification states table"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "<KEEP THE AUTOGENERATED VALUE>"
down_revision: str | None = "24d71e4e04e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signal_name", sa.String(length=50), nullable=False),
        sa.Column("last_value", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("streak_length", sa.Integer(), nullable=False),
        sa.Column("last_alerted_value", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("signal_name"),
    )


def downgrade() -> None:
    op.drop_table("notification_states")
```

- [ ] **Step 4: Apply and verify reversibility**

Run:
```bash
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```
Expected: all three commands succeed with no errors; the last one recreates
`notification_states`.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/
git commit -m "feat: add notification_states table migration"
```

---

### Task 3: DeclineAlertService

**Files:**
- Create: `app/services/decline_alert_service.py`
- Test: `tests/services/test_decline_alert_service.py`

**Interfaces:**
- Consumes: `app.repositories.exchange_rate.ExchangeRateRepository.get_latest(source, base_currency, quote_currency) -> RatePoint | None`
  (existing); `app.repositories.notification_state_repository.NotificationStateRepository.get`/`save`
  and `NotificationStateSnapshot` (Task 1); `app.services.bank_average_service.SOURCE_NAME`
  (existing, `"Bank Average (RUB cash, derived)"`).
- Produces: `app.services.decline_alert_service.DeclineAlert` (frozen
  dataclass: `current_value: Decimal`, `streak_length: int`,
  `previous_alerted_value: Decimal | None`) and
  `app.services.decline_alert_service.DeclineAlertService` with
  `__init__(self, exchange_rates: ExchangeRateRepository, notification_states: NotificationStateRepository, streak_threshold: int) -> None`
  and `async def check(self) -> DeclineAlert | None`.

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_decline_alert_service.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import RatePoint
from app.repositories.exchange_rate import ExchangeRateRepository
from app.repositories.notification_state_repository import NotificationStateRepository
from app.services.bank_average_service import SOURCE_NAME as BANK_AVERAGE_SOURCE_NAME
from app.services.decline_alert_service import DeclineAlertService

_START = datetime(2026, 7, 24, 8, 0, tzinfo=UTC)


def _bank_average_point(value: str, minutes_after_start: int) -> RatePoint:
    return RatePoint(
        BANK_AVERAGE_SOURCE_NAME,
        "RUB",
        "AMD",
        Decimal(value),
        _START + timedelta(minutes=minutes_after_start),
    )


def _service(db_session: AsyncSession, *, streak_threshold: int = 2) -> DeclineAlertService:
    return DeclineAlertService(
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
    await ExchangeRateRepository(db_session).save(_bank_average_point("3.76", 0))
    service = _service(db_session)

    assert await service.check() is None


async def test_repeated_identical_value_does_not_alert(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session)
    await repository.save(_bank_average_point("3.76", 0))
    await service.check()

    await repository.save(_bank_average_point("3.76", 30))

    assert await service.check() is None


async def test_single_drop_below_threshold_does_not_alert(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    await repository.save(_bank_average_point("3.76", 0))
    await service.check()

    await repository.save(_bank_average_point("3.74", 30))

    assert await service.check() is None


async def test_two_consecutive_drops_triggers_alert(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    await repository.save(_bank_average_point("3.76", 0))
    await service.check()
    await repository.save(_bank_average_point("3.74", 30))
    await service.check()

    await repository.save(_bank_average_point("3.72", 60))
    alert = await service.check()

    assert alert is not None
    assert alert.current_value == Decimal("3.72")
    assert alert.streak_length == 2
    assert alert.previous_alerted_value is None


async def test_repeated_value_at_already_alerted_low_does_not_realert(
    db_session: AsyncSession,
) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    for value, minutes in (("3.76", 0), ("3.74", 30), ("3.72", 60)):
        await repository.save(_bank_average_point(value, minutes))
        await service.check()

    await repository.save(_bank_average_point("3.72", 90))

    assert await service.check() is None


async def test_any_further_drop_after_arming_realerts_immediately(
    db_session: AsyncSession,
) -> None:
    # Once a decline streak has crossed the threshold and produced one
    # alert, streak_length only keeps growing (it doesn't reset except on a
    # rise) — so any further genuinely lower reading re-alerts right away,
    # rather than requiring a fresh N-in-a-row streak beneath the new floor.
    # This matches "alert on every new, deeper low relative to the last
    # alert" from the design spec, not "alert on every Nth step".
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    for value, minutes in (("3.76", 0), ("3.74", 30), ("3.72", 60)):
        await repository.save(_bank_average_point(value, minutes))
        await service.check()

    await repository.save(_bank_average_point("3.71", 90))
    alert = await service.check()

    assert alert is not None
    assert alert.current_value == Decimal("3.71")
    assert alert.previous_alerted_value == Decimal("3.72")


async def test_rate_increase_resets_streak_and_rearms_alerting(db_session: AsyncSession) -> None:
    repository = ExchangeRateRepository(db_session)
    service = _service(db_session, streak_threshold=2)
    for value, minutes in (("3.76", 0), ("3.74", 30), ("3.72", 60)):
        await repository.save(_bank_average_point(value, minutes))
        await service.check()

    await repository.save(_bank_average_point("3.80", 90))
    assert await service.check() is None  # increase: streak resets, no alert

    await repository.save(_bank_average_point("3.78", 120))
    assert await service.check() is None  # first drop after reset: streak=1

    await repository.save(_bank_average_point("3.76", 150))
    alert = await service.check()

    assert alert is not None
    assert alert.current_value == Decimal("3.76")
    assert alert.streak_length == 2
    # last_alerted_value was cleared by the increase, so this counts as a
    # fresh alert even though 3.76 is not below the old 3.72 low.
    assert alert.previous_alerted_value is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/services/test_decline_alert_service.py -v`
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named
'app.services.decline_alert_service'`.

- [ ] **Step 3: Write the implementation**

Create `app/services/decline_alert_service.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from app.repositories.exchange_rate import ExchangeRateRepository
from app.repositories.notification_state_repository import (
    NotificationStateRepository,
    NotificationStateSnapshot,
)
from app.services.bank_average_service import SOURCE_NAME as BANK_AVERAGE_SOURCE_NAME

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

        if point.value < state.last_value:
            streak_length = state.streak_length + 1
            last_alerted_value = state.last_alerted_value
        else:
            streak_length = 0
            last_alerted_value = None

        alert: DeclineAlert | None = None
        if streak_length >= self._streak_threshold and (
            last_alerted_value is None or point.value < last_alerted_value
        ):
            alert = DeclineAlert(
                current_value=point.value,
                streak_length=streak_length,
                previous_alerted_value=last_alerted_value,
            )
            last_alerted_value = point.value

        await self._notification_states.save(
            NotificationStateSnapshot(
                signal_name=_SIGNAL_NAME,
                last_value=point.value,
                streak_length=streak_length,
                last_alerted_value=last_alerted_value,
                updated_at=_utcnow(),
            )
        )
        return alert
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/services/test_decline_alert_service.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/decline_alert_service.py tests/services/test_decline_alert_service.py
git commit -m "feat: add DeclineAlertService trend-following detector"
```

---

### Task 4: Config settings

**Files:**
- Modify: `app/core/config.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `Settings.telegram_admin_chat_id: int` (default `0`),
  `Settings.decline_alert_streak_threshold: int` (default `2`),
  `Settings.decline_alert_check_interval_minutes: int` (default `30`),
  consumed by Task 5.

- [ ] **Step 1: Add the new settings fields**

In `app/core/config.py`, add these fields to `Settings` right after
`telegram_bot_token: str = ""`:

```python
    telegram_admin_chat_id: int = 0
    decline_alert_streak_threshold: int = 2
    decline_alert_check_interval_minutes: int = 30
```

- [ ] **Step 2: Add the new env vars to .env.example**

In `.env.example`, add after `TELEGRAM_BOT_TOKEN=`:

```
TELEGRAM_ADMIN_CHAT_ID=
DECLINE_ALERT_STREAK_THRESHOLD=2
DECLINE_ALERT_CHECK_INTERVAL_MINUTES=30
```

- [ ] **Step 3: Verify the app still imports cleanly**

Run: `uv run python -c "from app.core.config import get_settings; print(get_settings().decline_alert_streak_threshold)"`
Expected: prints `2` (or whatever your local `.env` overrides it to).

- [ ] **Step 4: Commit**

```bash
git add app/core/config.py .env.example
git commit -m "feat: add decline alert configuration settings"
```

---

### Task 5: Bot integration — scheduler, message, delivery

**Files:**
- Modify: `app/bot/main.py`
- Test: `tests/bot/test_main.py`

**Interfaces:**
- Consumes: `app.services.decline_alert_service.DeclineAlert`,
  `DeclineAlertService` (Task 3); `app.repositories.notification_state_repository.NotificationStateRepository`
  (Task 1); `Settings.telegram_admin_chat_id`,
  `Settings.decline_alert_streak_threshold`,
  `Settings.decline_alert_check_interval_minutes` (Task 4).
- Produces: `app.bot.main.format_decline_alert_message(alert: DeclineAlert, official_rate: RatePoint | None) -> str`
  and `app.bot.main.check_decline_and_notify(bot: Bot, session_factory: async_sessionmaker[AsyncSession], admin_chat_id: int, streak_threshold: int) -> None`,
  wired into `main()`.

- [ ] **Step 1: Write the failing tests**

`tests/bot/test_main.py` already imports `AsyncMock`, `AsyncSession`,
`async_sessionmaker`, `RatePoint`, `BANK_AVERAGE_SOURCE_NAME`, `Decimal`,
`datetime`/`UTC`, and `pytest` — do not re-import these (a duplicate import
of an already-imported name is a ruff F811 error, which would fail Task 6's
`ruff check .`).

Make exactly two import changes:

1. Change the existing `from datetime import UTC, datetime` line to:
   ```python
   from datetime import UTC, datetime, timedelta
   ```
2. Extend the existing `from app.bot.main import (...)` block to add
   `check_decline_and_notify` and `format_decline_alert_message` to the
   list (keep the existing names), and add one new import line right after
   it:
   ```python
   from app.services.decline_alert_service import DeclineAlert
   ```

Append these tests to `tests/bot/test_main.py`:

```python
def test_format_decline_alert_message_without_previous_alert_or_official_rate() -> None:
    alert = DeclineAlert(
        current_value=Decimal("3.72"), streak_length=2, previous_alerted_value=None
    )

    text = format_decline_alert_message(alert, official_rate=None)

    assert "RUB слабеет" in text
    assert "2 раз подряд" in text
    assert "3.72" in text
    assert "прошлый раз" not in text
    assert "Официальный курс" not in text


def test_format_decline_alert_message_with_previous_alert_and_official_rate() -> None:
    alert = DeclineAlert(
        current_value=Decimal("3.70"), streak_length=2, previous_alerted_value=Decimal("3.72")
    )
    official_rate = _rate_point("4.60")

    text = format_decline_alert_message(alert, official_rate)

    assert "прошлый раз я предупреждал (3.72)" in text
    assert "Официальный курс ЦБ Армении: 4.60" in text


async def test_check_decline_and_notify_sends_message_when_alert_fires(
    db_session: AsyncSession,
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = ExchangeRateRepository(db_session)
    await repository.save(_rate_point("4.60"))
    bot = AsyncMock()

    for value, minutes in (("3.76", 0), ("3.74", 30), ("3.72", 60)):
        await repository.save(
            RatePoint(
                BANK_AVERAGE_SOURCE_NAME,
                "RUB",
                "AMD",
                Decimal(value),
                _OBSERVED_AT + timedelta(minutes=minutes),
            )
        )
        await check_decline_and_notify(
            bot, test_session_factory, admin_chat_id=42, streak_threshold=2
        )

    bot.send_message.assert_awaited_once()
    call_args = bot.send_message.call_args
    assert call_args.args[0] == 42
    assert "3.72" in call_args.args[1]
    assert "4.60" in call_args.args[1]


async def test_check_decline_and_notify_does_not_send_when_no_alert(
    db_session: AsyncSession,
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    bot = AsyncMock()

    await check_decline_and_notify(
        bot, test_session_factory, admin_chat_id=42, streak_threshold=2
    )

    bot.send_message.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/bot/test_main.py -v`
Expected: FAIL — `ImportError: cannot import name 'check_decline_and_notify'
from 'app.bot.main'` (and `DeclineAlert` not yet consumed by `app.bot.main`).

- [ ] **Step 3: Add imports to app/bot/main.py**

At the top of `app/bot/main.py`, add:

```python
import logging
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
```

and, in alphabetical position among the existing `app.*` imports (there is
no pre-existing `app.collectors.base` import in this file — these three
are new):

```python
from app.collectors.base import RatePoint
from app.repositories.notification_state_repository import NotificationStateRepository
from app.services.decline_alert_service import DeclineAlert, DeclineAlertService
```

Add a module-level logger right after the existing module-level constants
(`_BASE_CURRENCY`, `_QUOTE_CURRENCY`, etc.):

```python
logger = logging.getLogger(__name__)
```

- [ ] **Step 4: Add format_decline_alert_message and check_decline_and_notify**

Add these functions to `app/bot/main.py`, after `format_status_reply`/`status`
and before `main()`:

```python
def format_decline_alert_message(alert: DeclineAlert, official_rate: RatePoint | None) -> str:
    lines = [
        "⚠️ RUB слабеет",
        (
            f"Банковский курс наличной покупки RUB упал {alert.streak_length} раз подряд, "
            f"сейчас {alert.current_value}."
        ),
    ]
    if alert.previous_alerted_value is not None:
        lines.append(
            f"Ещё ниже, чем в прошлый раз я предупреждал ({alert.previous_alerted_value})."
        )
    if official_rate is not None:
        lines.append(f"Официальный курс ЦБ Армении: {official_rate.value}.")
    lines.append("Возможно, стоит разменять сейчас, пока не упал ещё ниже.")
    return "\n".join(lines)


async def check_decline_and_notify(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    admin_chat_id: int,
    streak_threshold: int,
) -> None:
    try:
        async with session_factory() as session:
            exchange_rates = ExchangeRateRepository(session)
            service = DeclineAlertService(
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
        text = format_decline_alert_message(alert, official_rate)
        await bot.send_message(admin_chat_id, text)
    except Exception:
        logger.exception("decline_alert.check_failed")
```

- [ ] **Step 5: Wire the scheduler into main()**

Replace the existing `main()` function in `app/bot/main.py`:

```python
async def main() -> None:
    configure_logging()
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is empty in .env")
    bot = Bot(settings.telegram_bot_token)
    await dp.start_polling(bot)
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

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/bot/test_main.py -v`
Expected: PASS (all tests, including the 4 new ones).

- [ ] **Step 7: Commit**

```bash
git add app/bot/main.py tests/bot/test_main.py
git commit -m "feat: send proactive Telegram alert on confirmed rate decline"
```

---

### Task 6: Full verification and local .env setup

**Files:** none (verification only).

- [ ] **Step 1: Run the full required check suite**

Run, in order:
```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest
```
Expected: all four succeed with no errors (per CLAUDE.md, a PR is not
ready until these pass, in addition to green CI). If `ruff check .` flags
import ordering (rule `I001`) from the new imports added across Tasks 1–5,
run `uv run ruff check --fix .` to auto-sort them, then re-run
`ruff format --check .`.

- [ ] **Step 2: Add the new variables to your local .env**

Your local `.env` (not committed) needs real values to run the bot
end-to-end:
```
TELEGRAM_ADMIN_CHAT_ID=<your Telegram numeric chat id>
```
To find your chat id: message your bot once, then either check
`https://api.telegram.org/bot<TOKEN>/getUpdates` for the `chat.id` field, or
message a helper bot like `@userinfobot`. `DECLINE_ALERT_STREAK_THRESHOLD`
and `DECLINE_ALERT_CHECK_INTERVAL_MINUTES` can be left unset to use their
defaults (`2` and `30`).

- [ ] **Step 3: Manual smoke check (optional but recommended)**

With local Postgres and the bot's dependent services running
(`docker compose up -d postgres`), run the bot locally:
```bash
uv run python -m app.bot.main
```
Confirm it logs `Start polling` / `Run polling` as before, with no
exceptions from the new `decline_alert_check` job (it will run once
immediately on startup per `next_run_time=datetime.now(UTC)` and log nothing
observable unless a `DeclineAlert` actually fires — that's expected,
matching `collection_scheduler`'s existing swallow-and-log error boundary).
Stop with Ctrl+C.

## Deployment note (not a task — informational)

Production's `compose.yml` already runs `alembic upgrade head` via the
`migrate` service (added in `chore/compose-migration-step`) before `api`/
`bot` start, so Task 2's migration will apply automatically on the next
deploy — no manual `alembic upgrade head` needed on the VM. The VM's `.env`
does need `TELEGRAM_ADMIN_CHAT_ID` added before that deploy, or the bot
container will crash-loop on the new startup check from Task 5, Step 5.
