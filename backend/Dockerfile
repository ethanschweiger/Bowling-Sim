# Local development image only -- runs the existing FastAPI app exactly as
# `README.md`'s "Run" section does, inside a container instead of a venv.
# Not a production image: no multi-stage build. That belongs to the
# separate, later production/deployment milestone (see the root README's
# "Future architecture constraints"), which also needs a stable
# persistence layer this app doesn't have yet. The final runtime process
# does run as a dedicated unprivileged user and reports its own container
# health via HEALTHCHECK (both below) -- narrower in scope than a full
# production hardening pass, but worth doing on their own regardless of
# when that later milestone lands.
#
# Built with the repository root as context (see docker-compose.yml), so
# every COPY source path below is relative to the repo root, not this
# file's own directory.
FROM python:3.11-slim

# Dedicated unprivileged runtime user, stable and explicit rather than
# auto-assigned -- UID/GID 1000:1000, confirmed absent from this base
# image's own /etc/passwd and /etc/group as shipped, so it's the same
# concrete numeric id on every build and every host. Created before any
# COPY so this layer's cache isn't invalidated by a later source change.
# --no-create-home: this user never needs a home directory, only read
# access to /app. --shell /usr/sbin/nologin: matches the base image's own
# existing system accounts (www-data, backup, and friends already use it)
# -- this account is never meant to be logged into interactively.
RUN groupadd --gid 1000 backend && \
    useradd --uid 1000 --gid backend --no-create-home --shell /usr/sbin/nologin backend

WORKDIR /app

# Only the runtime manifest -- requirements-dev.txt (ruff, mypy) is for
# local linting/type-checking, not for running the server, and this image
# never runs pytest/ruff/mypy inside itself. Installed while still root,
# same as always -- pip's system-wide install location is world-readable
# by default, so the unprivileged user below can import everything here
# without owning any of it.
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Only the package the server actually imports at runtime -- not
# backend/tests, backend/docs, or backend/pyproject.toml (dev-tool config
# only). Keeps the image and every rebuild smaller and faster.
# --chown=backend:backend: owned by the unprivileged user this image
# eventually switches to (see USER below), not left root-owned-but-
# world-readable by default COPY behavior -- explicit rather than
# incidental.
COPY --chown=backend:backend backend/app ./app

# alembic.ini's script_location (%(here)s/alembic) resolves relative to
# alembic.ini's own location, and alembic/env.py inserts its own parent
# directory onto sys.path to import app.core.config/app.db.schema -- both
# resolve correctly here because alembic.ini lands at /app/alembic.ini,
# alembic/ lands at /app/alembic, and app/ (copied above) is already at
# /app/app. This does not run the migration itself -- see README.md's
# "SQL-mode overlay (opt-in)" section for the explicit `alembic upgrade
# head` command run against a live container, same as the pre-existing
# host-side path; nothing here or in this image's CMD applies it
# automatically. Same --chown as app/ above, for the same reason.
COPY --chown=backend:backend backend/alembic ./alembic
COPY --chown=backend:backend backend/alembic.ini ./alembic.ini

# Everything from here on -- this image's own CMD below, and any
# `docker compose exec backend ...` command run against a live container
# (including the in-container Alembic migration path) -- runs as this
# unprivileged user, never root.
USER backend:backend

EXPOSE 8000

# Probes this same process's own GET /health -- see app/main.py's own
# docstring for the exact response shape in each storage mode; any 2xx
# status counts healthy, anything else (a non-2xx response, a refused
# connection, a timeout) counts unhealthy. Uses only urllib from Python's
# standard library, already present in this image -- no curl, no new
# package. Runs as the USER already set above (backend:backend, not
# root): urllib.request.urlopen needs no elevated privilege to open an
# outbound connection to a port it isn't binding.
#   --interval=30s: how often Docker re-probes once running.
#   --timeout=3s: this app answers /health in milliseconds even when
#     checking SQL connectivity, so 3s is generous, not tight.
#   --start-period=5s: a brief grace window while Uvicorn boots, so a
#     slow first request doesn't immediately count as a real failure.
#   --retries=3: three consecutive failures (not one) before Docker
#     reports the container unhealthy, so a single transient blip
#     doesn't flip the status.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request\ntry:\n    urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)\nexcept Exception:\n    sys.exit(1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
