# Rise Alert — проактивное уведомление о росте RUB, симметричное Decline Alert

## Проблема

`DeclineAlertService` (Этап "decline alert", смержено в `main` 2026-07-24)
уведомляет, когда банковский курс наличной покупки RUB подтверждённо падает.
Пользователь хочет симметричную функцию: уведомление о подтверждённом росте
курса — чисто информационное ("курс растёт, следите"), **не** предсказание
пика/разворота.

## Семантика: информационная, не прогноз разворота

Изначальная формулировка запроса ("следите внимательнее, чтобы не упустить
момент роста") могла подразумевать сигнал вида "скоро рост закончится,
успейте". Это противоречило бы принципу проекта "не предсказывает будущее"
(см. CLAUDE.md/ROADMAP.md) — предсказание разворота/пика требует модели
кривой, а не просто счётчика последовательных повышений.

Пользователь подтвердил: сейчас делаем только констатацию факта ("курс N раз
подряд вырос, укрепляется"), без намёка на то, когда рост закончится.
Предсказание разворота — явно отложено до ML-этапа.

## Архитектура: общее ядро вместо дублирования

`DeclineAlertService.check()` устроен как: fetch последней точки →
bootstrap/equal-case на состоянии → branching серии (сравнение с
`last_value`, инкремент/сброс `streak_length`, инкремент/сброс
`last_alerted_value`) → решение об алерте → `save`. Разница между decline и
rise — только направление сравнения (`<` вместо `>`), это ~15 строк внутри
уже дважды отревьюженной ветки. Полное дублирование класса скопировало бы
эту логику и весь её тест-сьют один в один — в отличие от банковских
коллекторов (`AmeriabankCollector`/`EvocabankCollector`/...), где дублирование
неустранимо (разный HTML каждого банка), здесь оно устранимо и не несёт
доменной специфики.

**Решение:** вынести branching серии в чистую функцию без побочных эффектов —
`app/services/streak_detector.py`. `DeclineAlertService` рефакторится на её
использование (**публичный контракт — конструктор, сигнатура и тип возврата
`check()` — не меняется**, поведение побитово идентично, существующие тесты
не переписываются). `RiseAlertService` — новый тонкий класс поверх той же
функции с обратным сравнением.

```python
# app/services/streak_detector.py
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
    """continues_trend(current, reference) — True, если `current` продолжает
    отслеживаемый тренд относительно `reference` (operator.lt для decline,
    operator.gt для rise). Не отвечает за bootstrap/equal-case/persist —
    это остаётся на вызывающем сервисе."""
    if continues_trend(current_value, previous_value):
        streak_length = previous_streak_length + 1
        carried = previous_last_alerted_value
    else:
        streak_length = 0
        carried = None

    should_alert = streak_length >= streak_threshold and (
        carried is None or continues_trend(current_value, carried)
    )
    return StreakEvaluation(streak_length, carried, should_alert)
```

Разделение ответственности не меняется относительно decline-alert:
`app/services/*` — чистая детект-логика без Telegram, `app/bot/main.py` —
планировщик + сборка сообщения + отправка.

## RiseAlertService

```python
# app/services/rise_alert_service.py
import operator
...
_SIGNAL_NAME = "bank_average_rise"

@dataclass(frozen=True, slots=True)
class RiseAlert:
    current_value: Decimal
    streak_length: int
    previous_alerted_value: Decimal | None

class RiseAlertService:
    def __init__(self, exchange_rates, notification_states, streak_threshold) -> None: ...
    async def check(self) -> RiseAlert | None:
        # идентичная DeclineAlertService.check() структура:
        # fetch → bootstrap (signal_name="bank_average_rise") → equal-case →
        # evaluate_streak(..., continues_trend=operator.gt) → construct RiseAlert → save
```

Использует ту же таблицу `notification_states` (не меняем схему —
`signal_name` уже generic) с отдельной строкой `"bank_average_rise"`,
независимой от `"bank_average_decline"`. Источник сигнала — тот же `Bank
Average (RUB cash, derived)`, что и у decline (см. `BankAverageService`).

## Конфигурация

Новое поле:
```python
rise_alert_streak_threshold: int = 2
```

Переименование существующего поля (единственное изменение с
production-эффектом): `decline_alert_check_interval_minutes` →
`alert_check_interval_minutes` — интервал определяет, как часто вообще
проверяется сигнал по `Bank Average`, decline и rise проверяются на одних и
тех же свежих данных с одной кадентностью, заводить второй идентичный
интервал под другим именем было бы дублированием конфига без смысла.
Требует правки `.env` на VM при деплое (`DECLINE_ALERT_CHECK_INTERVAL_MINUTES`
→ `ALERT_CHECK_INTERVAL_MINUTES`).

`.env.example`:
```
RISE_ALERT_STREAK_THRESHOLD=2
ALERT_CHECK_INTERVAL_MINUTES=30
```
(строка `DECLINE_ALERT_CHECK_INTERVAL_MINUTES=30` удаляется).

## Текст уведомления

```
📈 RUB укрепляется
Банковский курс наличной покупки RUB вырос {streak_length} раз подряд, сейчас {current_value}.
[Ещё выше, чем в прошлый раз я сообщал ({previous_alerted_value}).] — только если previous_alerted_value не пусто
Официальный курс ЦБ Армении: {cba_value}.
```

Без рекомендации действия (в отличие от decline-сообщения "возможно, стоит
разменять сейчас") — рост курса не подразумевает срочность для пользователя,
меняющего RUB→AMD; это чисто информационная сводка.

## Bot-уровень

В `app/bot/main.py`, рядом с существующей регистрацией decline-джобы:
```python
scheduler.add_job(
    check_rise_and_notify,
    trigger=IntervalTrigger(minutes=settings.alert_check_interval_minutes),
    args=[bot, session_factory, settings.telegram_admin_chat_id, settings.rise_alert_streak_threshold],
    max_instances=1,
    coalesce=True,
    misfire_grace_time=60,
    next_run_time=datetime.now(UTC),
)
```
Новые `format_rise_alert_message(alert, official_rate) -> str` и
`check_rise_and_notify(bot, session_factory, admin_chat_id, streak_threshold) -> None`
— зеркало decline-версий, включая `try/except Exception` →
`logger.exception("rise_alert.check_failed")` и тот же комментарий про
at-most-once доставку (тот же паттерн отправки, то же ограничение).

Существующая decline-джоба переключается на `settings.alert_check_interval_minutes`
вместо переименованного поля — единственная правка в её регистрации.

## Тестирование

- `tests/services/test_streak_detector.py` (новый) — юнит-тесты чистой
  функции: серия продолжается (decline/rise), серия ломается, порог не
  достигнут/достигнут, ре-алерт на новом экстремуме за уже объявленным,
  отсутствие ре-алерта на повторном значении на уже объявленном уровне —
  для обоих направлений (`operator.lt`, `operator.gt`).
- `tests/services/test_decline_alert_service.py` — без изменений
  (behavior-preserving рефакторинг), прогоняется как регресс.
- `tests/services/test_rise_alert_service.py` (новый) — те же сценарии, что
  у decline, зеркально (рост вместо падения).
- `tests/bot/test_main.py` — расширяется тестами `format_rise_alert_message`
  и `check_rise_and_notify`, по образцу decline-тестов.

## Что сознательно не делаем сейчас

- Предсказание пика/разворота роста — по договорённости с пользователем,
  это ML-этап поверх того же контракта `RiseAlertService`.
- Многопользовательская подписка — тот же отложенный пункт, что и у decline.
- Изменение `RuleBasedRecommendationStrategy`/`/advice` — не трогается.
- Объединение Decline/RiseAlertService в один параметризованный класс —
  сознательно оставляем два тонких класса поверх общего ядра, а не один
  класс с флагом направления: раздельные классы проще тестировать по
  отдельности и естественнее ложатся на разные `signal_name`/сообщения/
  будущие независимые пороги.
