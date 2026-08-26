"""Application settings, loaded from environment variables (see .env.example)."""

from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "Bowling-Sim API"
    api_v1_prefix: str = "/api/v1"

    # Postgres URL a "sql"-mode game_storage_mode (below) would connect to.
    # The default app never reads this: with the default "memory" mode,
    # nothing anywhere opens a database engine or connection, at import,
    # startup, or during an ordinary request.
    database_url: str = "postgresql://bowling:bowling@localhost:5432/bowling_sim"

    # Which GameSessionRepository backs GameService -- "memory"
    # (InMemoryGameSessionRepository, the default; unchanged behavior) or
    # "sql" (SqlAlchemyGameSessionRepository, built from database_url via
    # app.db.session's engine/session factory). See
    # app.api.dependencies.get_game_service for exactly what each mode
    # does and does not do. Explicit opt-in only -- an app run with the
    # default settings is bit-for-bit the same as before this field
    # existed.
    #
    # Typed Literal so pydantic-settings itself rejects an unrecognized
    # GAME_STORAGE_MODE env value at Settings construction time (app
    # startup); get_game_service also checks this value explicitly at
    # call time, so a value that reaches it some other way (most
    # directly, a test that mutates an already-constructed settings
    # object) still fails the same clear way instead of silently
    # behaving as "memory".
    game_storage_mode: Literal["memory", "sql"] = "memory"

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
