# Local development image only -- runs the existing FastAPI app exactly as
# `README.md`'s "Run" section does, inside a container instead of a venv.
# Not a production image: no multi-stage build, non-root user, or health
# check. Those belong to the separate, later production/deployment
# milestone (see the root README's "Future architecture constraints"),
# which also needs a stable persistence layer this app doesn't have yet.
#
# Built with the repository root as context (see docker-compose.yml), so
# every COPY source path below is relative to the repo root, not this
# file's own directory.
FROM python:3.11-slim

WORKDIR /app

# Only the runtime manifest -- requirements-dev.txt (ruff, mypy) is for
# local linting/type-checking, not for running the server, and this image
# never runs pytest/ruff/mypy inside itself.
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Only the package the server actually imports at runtime -- not
# backend/tests, backend/docs, or backend/pyproject.toml (dev-tool config
# only). Keeps the image and every rebuild smaller and faster.
COPY backend/app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
