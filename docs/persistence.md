# Persistence

Bowling-Sim supports a zero-setup in-memory mode and an opt-in PostgreSQL mode.
Both expose the same HTTP routes; their retention and restart behavior differ.

## In-memory mode

`docker compose up --build` uses `InMemoryGameSessionRepository`. Games are
bounded to 1,000 retained sessions and are lost when the backend process stops.
No SQLAlchemy engine is created and no database connection is attempted.

## PostgreSQL mode

Start a local database and switch the backend repository:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.sql.yml \
  up --build -d
```

PostgreSQL health gates backend startup. Apply the committed migration from the
already-running backend image:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.sql.yml \
  exec backend alembic upgrade head
```

Confirm connectivity:

```bash
curl http://localhost:8000/health
# {"status":"ok","database":"ok"}
```

The `bowling_sim_postgres_data` named volume survives normal stack shutdown:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.sql.yml \
  down
```

Adding `-v` deliberately deletes that local database volume and its games.

## Restart-persistence check

Create a game:

```bash
GAME_JSON=$(curl --silent -X POST http://localhost:8000/api/v1/games \
  -H 'Content-Type: application/json' -d '{}')
GAME_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["game_id"])' \
  <<<"$GAME_JSON")
```

Record a deterministic throw:

```bash
curl --fail -X POST "http://localhost:8000/api/v1/games/$GAME_ID/throws" \
  -H 'Content-Type: application/json' \
  -d '{
    "ball_id":"reactive_pearl",
    "seed":17,
    "speed_mph":17,
    "rev_rate":350,
    "axis_rotation":45,
    "axis_tilt":15,
    "launch_angle":-1.5,
    "launch_position":28
  }'
```

Restart only the API and read the same game back:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.sql.yml \
  restart backend

curl --fail "http://localhost:8000/api/v1/games/$GAME_ID"
```

After one throw, the response retains its first-frame roll and reports lane
condition version `2`.

## Operational boundary

Migrations are explicit, and SQL health is a lightweight `SELECT 1`; health does
not prove that migrations are current. The local stack has no backups,
replication, TLS, accounts, or connection pooler. Same-game mutation coordination
is process-local, so SQL mode should run with one API worker until the row gains a
database-level version/check-and-set protocol.
