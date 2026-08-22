"""Application settings, loaded from environment variables (see .env.example)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "Bowling-Sim API"
    api_v1_prefix: str = "/api/v1"

    # Postgres URL for a future milestone. Nothing connects to it yet — this
    # app runs with zero external services.
    database_url: str = "postgresql://bowling:bowling@localhost:5432/bowling_sim"


settings = Settings()
