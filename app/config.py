from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Usage Metering & Billing Engine"
    environment: str = Field(default="local", alias="ENVIRONMENT")
    database_url: str = Field(
        default="postgresql+asyncpg://metering:metering@localhost:5432/metering",
        alias="DATABASE_URL",
    )

    stripe_secret_key: str = Field(default="", alias="STRIPE_SECRET_KEY")
    stripe_webhook_secret: str = Field(default="", alias="STRIPE_WEBHOOK_SECRET")
    stripe_pro_price_id: str = Field(default="", alias="STRIPE_PRO_PRICE_ID")
    checkout_success_url: str = Field(
        default="http://localhost:8000/billing/success",
        alias="CHECKOUT_SUCCESS_URL",
    )
    checkout_cancel_url: str = Field(
        default="http://localhost:8000/billing/cancel",
        alias="CHECKOUT_CANCEL_URL",
    )

    fresh_input_price_per_million_usd: Decimal = Field(
        default=Decimal("1.50"),
        alias="FRESH_INPUT_PRICE_PER_MILLION_USD",
    )
    cached_input_price_per_million_usd: Decimal = Field(
        default=Decimal("0.375"),
        alias="CACHED_INPUT_PRICE_PER_MILLION_USD",
    )
    output_price_per_million_usd: Decimal = Field(
        default=Decimal("6.00"),
        alias="OUTPUT_PRICE_PER_MILLION_USD",
    )
    reasoning_price_per_million_usd: Decimal = Field(
        default=Decimal("6.00"),
        alias="REASONING_PRICE_PER_MILLION_USD",
    )
    api_call_price_usd: Decimal = Field(default=Decimal("0.001"), alias="API_CALL_PRICE_USD")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
