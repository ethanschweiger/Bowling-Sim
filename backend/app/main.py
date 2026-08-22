"""FastAPI entry point."""

from fastapi import FastAPI

from app.api.routes.throws import router as throws_router
from app.core.config import settings

app = FastAPI(title=settings.app_name)

app.include_router(throws_router, prefix=settings.api_v1_prefix)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
