"""
config.py
=========
Безопасная загрузка настроек проекта.

Все переменные читаются из файла `.env` (или из переменных окружения)
с помощью pydantic-settings. Это даёт:
  * автоматическую валидацию типов,
  * понятные ошибки при отсутствии обязательных переменных,
  * единую точку доступа к конфигурации через объект `settings`.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PaymentProvider(str, Enum):
    """Поддерживаемые способы оплаты Premium-подписки."""

    STARS = "stars"   # Telegram Stars (валюта XTR, provider_token не нужен)
    FIAT = "fiat"     # Классический платёжный провайдер (Stripe и т.п.)


class Settings(BaseSettings):
    """Схема конфигурации приложения."""

    # --- Telegram ---
    bot_token: str = Field(..., alias="BOT_TOKEN")

    # --- Groq ---
    groq_api_key: str = Field(..., alias="GROQ_API_KEY")
    groq_model: str = Field("llama-3.3-70b-versatile", alias="GROQ_MODEL")

    # --- Оплата ---
    payment_provider: PaymentProvider = Field(
        PaymentProvider.STARS, alias="PAYMENT_PROVIDER"
    )
    provider_token: str = Field("", alias="PROVIDER_TOKEN")
    premium_price: int = Field(100, alias="PREMIUM_PRICE", gt=0)
    premium_currency: str = Field("USD", alias="PREMIUM_CURRENCY")
    premium_days: int = Field(30, alias="PREMIUM_DAYS", gt=0)

    # --- Лимиты ---
    free_daily_limit: int = Field(3, alias="FREE_DAILY_LIMIT", ge=0)
    max_prompt_length: int = Field(1500, alias="MAX_PROMPT_LENGTH", gt=0)

    # --- База данных ---
    db_path: str = Field("bot_database.db", alias="DB_PATH")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Вспомогательные свойства
    # ------------------------------------------------------------------ #
    @property
    def is_stars(self) -> bool:
        """True, если оплата производится через Telegram Stars."""
        return self.payment_provider == PaymentProvider.STARS

    @property
    def currency(self) -> str:
        """Код валюты для инвойса (XTR для Stars, иначе фиатная валюта)."""
        return "XTR" if self.is_stars else self.premium_currency

    @field_validator("provider_token")
    @classmethod
    def _validate_provider_token(cls, value: str, info) -> str:
        """
        Для фиатного провайдера токен обязателен.
        Валидация выполняется мягко: если провайдер не указан явно как fiat,
        пустой токен допустим (режим Stars).
        """
        provider = info.data.get("payment_provider", PaymentProvider.STARS)
        if provider == PaymentProvider.FIAT and not value.strip():
            raise ValueError(
                "PROVIDER_TOKEN обязателен при PAYMENT_PROVIDER=fiat. "
                "Получите токен у @BotFather -> Payments."
            )
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Возвращает единственный (кэшированный) экземпляр настроек.

    Использование lru_cache гарантирует, что `.env` парсится один раз
    за время жизни процесса.
    """
    return Settings()  # type: ignore[call-arg]


# Готовый к импорту объект настроек: `from config import settings`
settings = get_settings()
