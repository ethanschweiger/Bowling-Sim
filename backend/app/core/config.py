"""Application settings, loaded from environment variables (see .env.example)."""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "Bowling-Sim API"
    api_v1_prefix: str = "/api/v1"

    # Postgres URL for a future milestone. Nothing connects to it yet — this
    # app runs with zero external services.
    database_url: str = "postgresql://bowling:bowling@localhost:5432/bowling_sim"

    # Origins allowed to make cross-origin requests to this API, e.g. a
    # frontend hosted on its own domain rather than served through Vite's
    # same-origin dev proxy. A JSON array in .env
    # (BACKEND_CORS_ORIGINS=["https://frontend.example"]) -- pydantic-settings
    # parses a list-typed field from an env var as JSON natively, so this
    # needs no custom parsing. Empty by default: CORSMiddleware is only
    # installed (see app/main.py) when this list is non-empty, so the
    # out-of-the-box app sends no CORS allow headers to anyone.
    backend_cors_origins: list[str] = []

    @field_validator("backend_cors_origins")
    @classmethod
    def _strip_empty_origins(cls, value: list[str]) -> list[str]:
        """Drops blank/whitespace-only entries rather than registering them
        as an allowed origin -- a stray `""` in the JSON array should never
        silently become "match nothing" turning into "match unexpectedly."
        """
        return [origin for origin in value if origin and origin.strip()]


settings = Settings()
