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
    cbr_collection_interval_minutes: int = 30

    ameriabank_base_url: str = "https://ameriabank.am"
    ameriabank_timeout_seconds: float = 10.0
    ameriabank_max_retries: int = 3
    ameriabank_collection_interval_minutes: int = 30

    evocabank_base_url: str = "https://www.evoca.am/en"
    evocabank_timeout_seconds: float = 10.0
    evocabank_max_retries: int = 3
    evocabank_collection_interval_minutes: int = 30

    acba_bank_base_url: str = "https://www.acba.am/en/exchange-rates"
    acba_bank_timeout_seconds: float = 10.0
    acba_bank_max_retries: int = 3
    acba_bank_collection_interval_minutes: int = 30

    vtb_am_base_url: str = "https://www.vtb.am/en/currency"
    vtb_am_timeout_seconds: float = 10.0
    vtb_am_max_retries: int = 3
    vtb_am_collection_interval_minutes: int = 30

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
