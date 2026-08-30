# Testing and CI

## Native development

Backend setup:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload
```

Frontend setup, in another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Vite proxies relative `/api` requests to `http://127.0.0.1:8000`, so local
development needs no CORS configuration.

## Verification commands

Backend:

```bash
cd backend
source .venv/bin/activate
ruff check .
mypy app
python -m pytest -q
```

Frontend:

```bash
cd frontend
npm ci
npm run lint
npm run build
npm run test -- --run
```

Docker:

```bash
docker compose build
docker compose up -d
curl --fail http://localhost:8000/health
curl --fail http://localhost:5173/
docker compose down
```

The PostgreSQL migration/restart check is in [persistence.md](persistence.md).

## What the suites cover

Backend pytest exercises request validation, trajectory dynamics and units,
lane state and wear, deterministic replay, collision calibration, rack
selection, scorecard rules, game lifecycle and snapshots, concurrent service
behavior, SQL row conversion/repository behavior, migrations, health, and CORS.

Frontend Vitest exercises API mapping, catalog behavior, form bounds, seeded
release input, game recovery, score formatting, lane projection/orientation,
server-timed trajectory playback, collision replay, and shot summaries.

The current verified count is 603 backend tests and 300 frontend tests: **903
automated tests total**.

## Continuous integration

`.github/workflows/ci.yml` runs for every push and pull request:

| Job | Environment | Checks |
|---|---|---|
| `backend` | Ubuntu, Python 3.11 | Ruff, strict mypy, pytest |
| `frontend` | Ubuntu, Node 20 | npm clean install, lint, build, Vitest |

The benchmark is deliberately not a CI performance gate. Shared runners are too
variable for a stable latency threshold; the reproducible local workload is in
[`benchmarks/`](../benchmarks/README.md).
