# Currency Advisor

Платформа сбора и анализа курсов RUB/AMD.

## Первый запуск в Windows PowerShell

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Закройте и заново откройте терминал, затем:

```powershell
uv --version
cd currency-advisor
Copy-Item .env.example .env
uv sync --extra dev
docker compose up -d postgres
uv run uvicorn app.main:app --reload
```

Проверка:
- http://127.0.0.1:8000/health
- http://127.0.0.1:8000/docs
- http://127.0.0.1:8000/dashboard/

## Тесты и проверка стиля

```powershell
uv run ruff check .
uv run pytest
```

## Текущий этап

- FastAPI
- асинхронный SQLAlchemy
- PostgreSQL через Docker
- модель хранения курсов
- интерфейс коллекторов
- Telegram bot skeleton
- CI, Ruff, pytest, pre-commit

Полный план этапов и архитектурные решения — в [ROADMAP.md](ROADMAP.md).
