"""Global configuration loaded from environment variables."""

from pydantic_settings import BaseSettings
from pydantic import Field
from enum import Enum


class Environment(str, Enum):
    development = "development"
    staging = "staging"
    production = "production"


class Settings(BaseSettings):
    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://bf1user:password@localhost:5432/bf1_2026"
    )
    postgres_user: str = Field(default="bf1user")
    postgres_password: str = Field(default="password")
    postgres_db: str = Field(default="bf1_2026")

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0")

    # Telegram
    telegram_bot_token: str = Field(default="")
    telegram_admin_chat_id: str = Field(default="")

    # OpenWeather
    openweather_api_key: str = Field(default="")

    # App
    secret_key: str = Field(default="dev-secret-key-change-in-production")
    environment: Environment = Field(default=Environment.development)
    log_level: str = Field(default="INFO")
    allowed_origins: str = Field(default="*")

    # API authentication. Kept separate from secret_key so a token handed to an
    # integration cannot also trigger admin operations, and so an unset value
    # fails closed instead of falling back to a default published in this repo.
    write_api_key: str = Field(default="")
    admin_api_key: str = Field(default="")

    # Expose /docs, /redoc and /openapi.json. Off outside development: the
    # schema advertises the admin surface to anonymous visitors.
    enable_docs: bool = Field(default=False)

    # ML
    models_storage_path: str = Field(default="/app/models_storage")
    monte_carlo_simulations: int = Field(default=20000)
    default_aggressiveness: str = Field(default="balanced")

    # Scraping
    scraping_delay_seconds: float = Field(default=1.0)
    scraping_max_retries: int = Field(default=3)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
