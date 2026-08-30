# Architecture

Bowling-Sim separates transport, application state, and numerical/domain logic.
The API is the composition boundary; neither the browser nor the database owns
physics or scoring rules.

```text
frontend/src (React + TypeScript)
              │ relative /api requests through Vite
              ▼
backend/app/api (FastAPI routes and schemas)
              │
              ▼
backend/app/games/service.py (game transaction boundary)
       │              │                 │
       ▼              ▼                 ▼
trajectory/lane   pin collision     rack + scorecard
       │
       ▼
GameSessionRepository
       ├── InMemoryGameSessionRepository (default)
       └── SqlAlchemyGameSessionRepository (opt-in PostgreSQL)
```

## Frontend boundary

The frontend fetches the ball and oil-pattern catalogs from the API, creates or
resumes one game, submits releases, and replays the server-recorded path. It
formats `game_state`; it does not calculate pinfall, score, rack advancement,
lane wear, or trajectory locally. If the backend loses an in-memory game, the
client confirms the 404 and offers to start a new one.

## Application transaction

`GameSession.throw()` is the single mutation boundary for one roll. It checks
game completion, reads the standing rack, simulates the trajectory, resolves
pinfall, applies lane wear, records the roll, advances the rack, and builds the
response snapshot. A rejected throw changes none of those values.

Snapshots contain immutable values, never a live `Scorecard` or `Rack`
reference. A response therefore continues to describe the state committed by
its own request even if a later request changes the game.

Memory-mode sessions have independent per-game locks: requests for the same game
serialize, while different games do not block each other. SQL mode uses the
same process-scoped service and per-game coordination around the authoritative
repository read/modify/write sequence. That coordination does not extend across
multiple API worker processes; database-level optimistic locking would be
required before running more than one worker.

## Physics and scoring boundaries

`simulate_throw(ball, throw, lane)` is a pure function. It reads an immutable
lane snapshot and returns a trajectory plus a canonical terminal state. Lane
wear is applied separately and produces another immutable `LaneCondition`.

Pin-deck geometry, impact conversion, planar collision, standing-pin rack, and
ten-pin scoring are separate modules with their own tests. The collision model
only decides which standing pins fall; the scorecard only receives a pinfall
count.

## Persistence boundary

`GameService` depends on `GameSessionRepository`, not SQLAlchemy. The SQL adapter
serializes a complete `GameSessionRecord` into one PostgreSQL row and rehydrates
it for the next request. Schema creation is managed by Alembic and is never run
implicitly at API startup.

## Deployment shape

`docker-compose.yml` runs the FastAPI and Vite development servers in non-root
containers. `docker-compose.sql.yml` adds PostgreSQL and selects SQL storage.
Both modes expose container health checks. This is a reproducible local
evaluation environment, not a production topology.
