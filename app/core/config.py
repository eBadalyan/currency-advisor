from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Currency Advisor"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    database_url: str = "postgresql+asyncpg://currency:currency@localhost:5432/currency"
    telegram_bot_token: str = ""

    cba_base_url: str = "https://api.cba.am/exchangerates.asmx"
    cba_timeout_seconds: float = 10.0
    cba_max_retries: int = 3
    cba_collection_interval_minutes: int = 30

    cbr_base_url: str = "https://www.cbr.ru/scripts/XML_daily.asp"
    cbr_timeout_seconds: float = 10.0
    cbr_max_retries: int = 3

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
