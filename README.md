# Bowling-Sim

A bowling simulator built around a simplified physics model.
You pick a ball, an oil pattern, and throw parameters. The backend simulates
the ball's path down the lane and reports what happens at the pins.

## Status

Playable end to end, locally: pick a ball and a release, throw, watch that
shot replay down the lane, and score a full ten-frame game. A FastAPI
backend owns the trajectory simulation, the deterministic 2D pin collision,
and the ten-pin scoring rules; a Vite + React + TypeScript frontend
(`frontend/`) drives one game against that real API and renders exactly
what the server returns.

Each game gets its own lane — create one, throw in it, reset it back to a
fresh house shot whenever you want. Games live in memory only: there is no
database, no accounts, no authentication, and no multiplayer. A deprecated
single-lane endpoint from an earlier milestone still works, shared by every
caller that still uses it.

See "Frontend" for what the UI does and doesn't do yet, and "Known
limitations" for which numbers are sourced from USBC specifications versus
chosen as modeling assumptions.

## Architecture

```
React + TypeScript frontend  <- frontend/src — Vite dev server proxies /api to the backend
      |
   REST API  <- FastAPI, backend/app/api
      |
Physics engine  <- backend/app/physics — pure Python, no FastAPI or DB imports
      |
PostgreSQL (opt-in) — GAME_STORAGE_MODE=sql wires it up; default is in-memory only
```

The physics module knows nothing about HTTP. `simulate_throw(ball, throw, lane)`
takes three plain dataclasses and returns a trajectory. The API layer is the
only thing that touches FastAPI, request parsing, or HTTP status codes.

```
Ball          — mass, radius, RG, differential, surface, coverstock
Throw         — speed, rev rate, axis rotation, axis tilt, launch angle, launch position
LaneCondition — oil grid (board x foot), derived from a standard house shot
```

Four further modules stay deliberately independent of each other, each
usable and testable on its own: **pin-deck geometry** (`app/physics/pin_deck.py`,
USBC-sourced dimensions), **pinfall resolution** behind a `PinfallModel`
interface (`app/physics/pinfall.py`, `app/physics/collision.py` — a
deterministic 2D ball/pin simulation, no randomness), **scoring**
(`app/scoring/scorecard.py`, standard ten-pin rules from a plain sequence of
pinfall counts), and the **standing-pin rack** (`app/physics/rack.py`,
immutable, tracks which pins remain between throws). `app/games/service.py`'s
`GameSession` is what wires all four into one game: it owns one lane, one
`Scorecard`, and one `Rack`, and its `throw()` method is the single
transaction — under one per-game lock — that simulates the trajectory,
resolves pinfall, wears the lane, records the roll, and advances the rack.
Two games' sessions never share a lock or see each other's state.
`GameService` maps an opaque `game_id` to its `GameSession` and bounds
itself to `DEFAULT_MAX_GAMES` (1000) retained games — see "Known
limitations."

Every throw, create, reset, and `GET` response is built from one immutable,
already-computed snapshot of that state (never a live read after the fact),
so two concurrent requests against the same game can't produce a response
that describes a mix of before-and-after state.

### Skid, hook, roll, from one mechanism

The trajectory model reproduces USBC's documented three-phase ball
motion — skid, hook, roll — from one continuous mechanism (lateral slip
converting to sideways velocity as friction rises down the lane) rather
than scripting three stages with a boundary between them, which is what
would produce an unphysical snap at the oil line. Axis rotation and a
small track-flare-inspired residual put slip into a bounded reservoir at
release; axis tilt controls how fast that reservoir converts, not whether
it does. See `app/physics/simulate.py`'s module docstring for the full
mechanism and its coefficients, and "Known limitations" below for which
numbers are USBC-sourced versus chosen modeling assumptions.

### Board numbers and sign conventions

Board 1 is the bowler's right gutter, board 39 the left, board 20 the
center — higher board numbers run left. Positive lateral movement,
`launch_angle`, and `entry_angle_deg` all mean "toward higher boards."
The conventional right-handed line is therefore a **negative**
`launch_angle`, laid down around board 28. `launch_position` is the ball's
laydown board at the foul line, not where the bowler stands. See
"Coordinate conventions" at the top of `app/physics/simulate.py` for the
full reference.

### Lane state wears in, per game

The house shot is a lateral/longitudinal oil grid (documented modeling
assumptions, not a certified USBC pattern — see "Known limitations").
Every throw wears the boards it crossed and resurfaces a small fraction
of that oil further down the lane (`app/physics/lane.py::apply_wear`); the
physics functions stay pure, returning a new `LaneCondition` rather than
mutating one. `LaneSession` (`app/physics/lane_session.py`) is the one
mutable "current condition" for a lane, and reads/simulates/records wear
under one lock so concurrent throws against the same lane can't race.
`POST /api/v1/games/{game_id}/reset` restores a game's *own* starting
condition and never touches the shared, reusable pattern definition every
game is built from.

### Release variance

`sample_release` (`app/physics/throw.py`) draws small, bounded noise
around a requested release, clamps it to the same legal range the API
enforces on a request, and simulates the sampled values, never the
requested ones. Pass a `seed` to reproduce a throw exactly; omit one and
the response returns the seed it generated, so a later request can replay
it. These noise bounds are stated product assumptions, not measurements
of real bowlers.

## Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload
```

The API comes up at `http://localhost:8000`. Interactive docs at
`http://localhost:8000/docs`.

## Test

Backend — pytest:

```bash
cd backend
source .venv/bin/activate
pytest
```

Frontend — vitest, plus the TypeScript check and the linter:

```bash
cd frontend
npm run test    # vitest
npm run build   # tsc -b, then production build — doubles as the type check
npm run lint
```

## Continuous integration

GitHub Actions runs [`.github/workflows/ci.yml`](.github/workflows/ci.yml) on
every push and pull request:

| Check | Runner | What it runs |
|---|---|---|
| `backend` | Ubuntu, Python 3.11 | Installs `requirements.txt` plus `requirements-dev.txt`, then `ruff check .`, `mypy app`, and `python -m pytest -q` in `backend/` |
| `frontend` | Ubuntu, Node 20 | `npm ci`, `npm run lint`, `npm run build`, `npm run test -- --run` in `frontend/` |

Use those two names, `backend` and `frontend`, if you turn on branch
protection under Settings > Branches — Claude does not change repository
settings, secrets, protections, or external services itself.

`backend/pyproject.toml`'s strict mypy configuration is a required CI
step now, not local-only. It still checks against the Python 3.9 runtime
floor `backend/.venv` runs (`python_version = "3.9"` in that config)
even though the CI job's own interpreter is 3.11 — mypy's target version
is a config setting independent of whichever Python actually runs it.
It currently reports zero findings (29 source files checked); nothing
breaks when that count moves.

## Docker

A local-development alternative to the native `Setup`/`Run` steps above —
runs the same backend and frontend, unmodified, as two containers instead
of two manually-started host processes. Not a production/deployment
setup: no reverse proxy, no image publishing, and by default no database
container. See [`docker-compose.yml`](docker-compose.yml),
[`backend/Dockerfile`](backend/Dockerfile), and
[`frontend/Dockerfile`](frontend/Dockerfile).

```bash
docker compose up --build   # start (add -d to run detached)
docker compose down         # stop, removes containers and network
```

Requires Docker Desktop (or Docker Engine + Compose v2) with the daemon
running. Exposed at the same URLs as native local development —
`http://localhost:8000` (backend) and `http://localhost:5173` (frontend).

Source is copied into each image at build time, not bind-mounted, so a
host edit has no effect on a running container until you rebuild with
`docker compose up --build` again (`docker compose build` alone rebuilds
without starting anything). If port 8000 or 5173 is already in use, a
native (non-Docker) `uvicorn`/`vite` process from the `Setup`/`Run` steps
is the usual cause.

The backend's Uvicorn process (and any `docker compose exec backend ...`
command run against it, including the in-container Alembic migration
path below) runs as a dedicated unprivileged user (`backend`, stable
UID/GID `1000:1000`), not root — a narrower hardening step on its own,
not a claim that this image is otherwise production-ready.

By default (no `-f` flags beyond the implicit `docker-compose.yml`),
Docker adds no persistence: a backend container restart or rebuild loses
every in-memory game, exactly like restarting the process natively, and
the bounded game registry (see "Known limitations") governs it identically
either way.

### SQL-mode overlay (opt-in)

[`docker-compose.sql.yml`](docker-compose.sql.yml) is a separate,
opt-in overlay adding a local PostgreSQL container and switching the
backend to `GAME_STORAGE_MODE=sql` against it — layered on top of, not
instead of, the default file above. Local development only, still not a
production setup: no backup strategy, no replication, no TLS, and
migrations are never run automatically (see "Known limitations" for what
`sql` mode does and doesn't do otherwise). Nothing about the default
`docker compose up --build` workflow above changes because this file
exists — it is only read when explicitly passed with `-f`.

```bash
# Start the backend, frontend, and a local Postgres together.
docker compose -f docker-compose.yml -f docker-compose.sql.yml up --build -d

# Apply the existing Alembic migration explicitly (never automatic) --
# once `db` reports healthy (docker compose ps). Either of these is
# equally valid; pick whichever fits what's already set up.

# Option A -- from the host, no container changes needed.
# settings.database_url already defaults to postgresql://bowling:bowling@localhost:5432/bowling_sim,
# matching this overlay's exposed port and dummy credentials, so no
# DATABASE_URL override is needed for this host-side command.
cd backend && .venv/bin/alembic upgrade head && cd ..

# Option B -- from inside the running backend container, no host venv
# needed. The image's own DATABASE_URL (the overlay's container-network
# postgresql://bowling:bowling@db:5432/bowling_sim) is already set, so no
# override is needed here either.
docker compose -f docker-compose.yml -f docker-compose.sql.yml exec backend alembic upgrade head

# Confirm the backend can actually reach the database.
curl http://localhost:8000/health   # {"status":"ok","database":"ok"}

# Stop the stack. Add -v to also delete the named Postgres volume
# (bowling_sim_postgres_data) and its data; omit -v to keep the data
# for next time.
docker compose -f docker-compose.yml -f docker-compose.sql.yml down
```

Postgres's own healthcheck (`pg_isready`) gates the backend container's
start — `depends_on: db: condition: service_healthy` in the overlay — so
the backend never starts racing an unready database. `GET /health`
still reports `{"status": "ok"}` with no `database` field at all if you
start the default file alone; see "Known limitations" for that
distinction. If host port 5432 is already in use (a native Postgres,
most likely), stop that first or edit the overlay's port mapping.

## Frontend

`frontend/` (Vite + React + TypeScript) is a first connected shell, not
the polished v1 experience the roadmap describes: it plays a completed
throw's own server-recorded path with server-timed visual playback, not
a literal real-time or client-side physics simulation of its own, and
has no charts, accounts, or persistence. It talks to the real API and nothing
else — on load it creates a game (or resumes the one this browser created
last time, if the server still has it) and renders exactly the
`game_state`/trajectory a response contains. No score, rack, or completion
rule is re-derived client-side; the frontend only formats what the server
returns.

The ball catalog and the oil-pattern notice are both fetched from the API
(`GET /api/v1/balls`, `GET /api/v1/oil-patterns`) rather than hardcoded,
so the frontend can never offer a value a throw or a game creation would
reject; a failed catalog load shows a retry rather than falling back to a
possibly-stale local copy. A saved `game_id` is checked proactively on
load and replaced if the server no longer has it; a game that disappears
later, while the tab stays open (most often the backend restarting), is
caught reactively on the next reset or throw and shows the same "Start a
new game" recovery action.

### Run it locally

```bash
cd frontend
npm install
npm run dev
```

Starts the Vite dev server at `http://localhost:5173`. Its dev-server
proxy (`frontend/vite.config.ts`) forwards relative `/api/...` requests to
`http://127.0.0.1:8000`, so start the backend first — this is the
supported local setup, and needs no CORS configuration at all since the
browser only ever talks to the Vite server. A frontend hosted on its own
origin instead needs the backend's `BACKEND_CORS_ORIGINS` allowlist set
to that origin (see `backend/.env.example`); CORS is off by default, and
this project still has no non-local deployment.

## API

### List the balls

```bash
curl http://localhost:8000/api/v1/balls
# {"balls": [ {"id": "house_ball", "name": "House Ball",
#    "coverstock": "plastic", "surface": "polished",
#    "description": "Plastic coverstock, polished. Near-zero hook, …",
#    "spec": {"mass_lbs": 15.0, "radius_in": 4.29, "rg_in": 2.75,
#             "differential": 0.02, "hook_potential": 0.0078}}, … ] }
```

Read-only, and the authority on which `ball_id` values a throw accepts —
serves the same catalog the throw routes validate against, in declared
order. An unknown `ball_id` on a throw is a 404.

### List the oil patterns

```bash
curl http://localhost:8000/api/v1/oil-patterns
# {"patterns": [
#   {"id": "house", "name": "House Shot",
#    "description": "Forgiving, with the oil concentrated in the middle …",
#    "spec": {"length_ft": 40.0, "taper_ft": 6.0, "center_boards": [8, 32],
#             "total_boards": [3, 37], "pattern_ratio": 3.0,
#             "total_volume_ml": 22.0}},
#   {"id": "challenge", "name": "Challenge Pattern",
#    "description": "Less forgiving than the house shot …",
#    "spec": {"length_ft": 34.0, "taper_ft": 3.0, "center_boards": [10, 30],
#             "total_boards": [5, 35], "pattern_ratio": 6.0,
#             "total_volume_ml": 24.0}} ] }
```

Read-only, and the authority on which `oil_pattern` values
`POST /api/v1/games` accepts. `house` (the default) and `challenge` both
exist today; an unsupported value on create is a 422.

### Create a game

```bash
curl -X POST http://localhost:8000/api/v1/games \
  -H "Content-Type: application/json" -d '{}'
# {"game_id": "…", "lane_condition_version": 1, "game_state": {
#    "standing_pin_ids": [1,2,3,4,5,6,7,8,9,10], "frames": [],
#    "total_score": null, "is_game_complete": false,
#    "next_frame_number": 1, "next_ball_number": 1 } }
```

`oil_pattern` is optional and defaults to `"house"`.

### Throw in that game

```bash
curl -X POST http://localhost:8000/api/v1/games/{game_id}/throws \
  -H "Content-Type: application/json" \
  -d '{
    "ball_id": "reactive_pearl",
    "seed": 42,
    "speed_mph": 17,
    "rev_rate": 350,
    "axis_rotation": 45,
    "axis_tilt": 15,
    "launch_angle": -1.5,
    "launch_position": 28
  }'
```

`seed` is optional — reuse one an earlier response returned to replay
that exact throw. The response's `lane_condition_version` is the version
this throw ran *against* (pre-wear), not the game's resulting current
version. This route's 404 is ambiguous by design — an unknown `game_id`
*or* an unknown `ball_id` both 404 — so a client that needs to
distinguish them confirms with a `GET` first (see `frontend/src/domain/gameLifecycle.ts`
for how the frontend does this). A throw against an already-finished game
is `409 Conflict`; nothing about the game changes when that happens.

### Reset a game

```bash
curl -X POST http://localhost:8000/api/v1/games/{game_id}/reset
# {"game_id": "…", "lane_condition_version": 1, "game_state": {...fresh...}}
```

Restores this game's lane to exactly what it started with, a blank
scorecard, and all ten pins standing. Other games are untouched.

### Read a game's current status

```bash
curl http://localhost:8000/api/v1/games/{game_id}
# {"game_id": "…", "lane_condition_version": 2, "game_state": {...}}
```

Read-only. `lane_condition_version` here is the game's *current* version
(after its last throw's wear), unlike a throw response's own version —
see "Throw in that game" above. An unknown `game_id` is a 404.

### Deprecated: `POST /api/v1/simulations/throws`

The single-lane endpoint from an earlier milestone still works, unchanged
in shape, but every caller shares one lazily-created game
(`legacy-default`) — there's no per-caller isolation. Marked `deprecated`
in `/docs`; new integrations should create their own game instead.

## Roadmap

**v1** — draw the lane, pick an oil pattern and ball, enter throw parameters,
animate the throw, show pin impact, score the frame. The frontend shell
now covers all of this, as a server-timed replay of each throw's own
server-recorded path rather than a from-scratch or literal real-time
simulation.

**v2** — more balls and surfaces, adjustable drilling layouts, an oil-pattern
editor, pin carry, misses and gutters, full 10-frame games.

**v3** — accounts, saved games, throw history, averages, strike/spare/pocket
percentages, leave tracking, ball usage stats.

## Known limitations (this milestone)

- Pinfall is a flat 2D approximation (`PlanarCollisionPinfallModel`) — real
  pins tip and rotate in 3D; this model only displaces circles. Pin tilt,
  loft off the foul line, kickbacks, string-pinsetter interaction, and
  pinsetter placement variance are none of them modeled.
- The collision model's effective pin radius, linear damping, and
  fall-displacement threshold are stated calibration choices, not fit
  against real ball-motion or pinfall data — no exact strike/pinfall count
  should be read as accurate yet, only as bounded and directionally
  sensible. Pin geometry, pin weight, and the 0.670 coefficient of
  restitution *are* taken directly from the USBC equipment manual. See
  [`backend/docs/planar-collision-calibration.md`](backend/docs/planar-collision-calibration.md)
  for the full measured evidence behind these claims.
- No handedness distinction — the pocket concept in both pinfall
  implementations assumes a right-handed shot, and the coordinate system
  is not mirrored to fake a left-handed one.
- Trajectory coefficients (`FORWARD_DRAG`, `SLIP_EFFICIENCY`,
  `LATERAL_TRACTION`, contact-engagement distances, and the oil pattern's
  own shape) are hand-tuned for bounded, credible motion, not fit to real
  ball-motion data. The model reproduces the skid → hook → roll
  *ordering* and responds sensibly to ball/release/oil changes, but entry
  boards, breakpoints, and hook magnitudes should be read as directionally
  credible, not as predictions of a real ball's behavior.
- `launch_angle` is held constant for a throw's entire trip down the lane
  — nothing decays it back toward straight — which is why its legal range
  is a tight ±2° rather than a more "realistic" figure.
- `mass_lbs` and `radius_in` are on the `Ball` model but unused in this
  milestone's trajectory calculation: mass cancels out of simple Coulomb
  friction, and every catalog ball shares the same regulation radius.
- The house shot's and challenge pattern's length, taper, volume ratio,
  and total volume are reasoned assumptions, not certified USBC patterns.
  `POST /games`'s `oil_pattern` accepts `"house"` (the default) or
  `"challenge"` — both entries in the same registry
  `GET /api/v1/oil-patterns` publishes, and the frontend's oil-pattern
  picker offers exactly this same catalog for the *next* new game — with
  no other named-pattern selection or temperature setting yet. The
  picker has no effect on a game already in progress: a created game's
  `oil_pattern` is fixed for its life (`reset()` returns to that same
  pattern, it never changes it), and `game_state.oil_pattern` reports it
  on every read — create, `GET /api/v1/games/{id}`, throw, and reset
  alike — so a client can always describe which pattern a loaded game is
  using, not only the one it just created. The frontend displays it
  read-only next to the scorecard, resolved to the catalog's own display
  name when available and falling back to the raw id otherwise; there is
  still no way to change an existing game's pattern.
- The board width (1.05 in) and the lane-temperature friction adjustment
  (±10% max, linear, 72°F reference) are stated modeling constants, not
  measured or derived.
- Release-error bounds (`app/physics/throw.py`) are a deliberately narrow
  product envelope for this simplified model, not measurements of human
  variance.
- Games live in memory only — restarting the process (or a Docker
  container) loses every game. There's no accounts/ownership layer:
  any caller who has a `game_id` can throw in or reset that game, and
  there's no per-player turn enforcement within one game.
- `GameService` bounds itself to `DEFAULT_MAX_GAMES` (1000) retained
  games. Creating one more once the registry is full evicts the single
  oldest game by creation order — never by last read or throw, no TTL,
  no background sweep. An evicted `game_id` then behaves exactly like one
  that never existed: a 404 the frontend already treats as a stale saved
  game. Sessions now sit behind a small `GameSessionRepository` boundary
  so a future persistent store can replace `InMemoryGameSessionRepository`
  without changing `GameService`'s API — but that in-memory
  implementation is still the only one, so a process/container restart
  still loses every game exactly as before. `GameSession.to_record()` /
  `from_record()` add a pure dump/rehydrate boundary for one game's
  complete state — still just an in-process serialization/rehydration
  shape a future repository could store, not a persistence mechanism
  itself. `app/games/record_payload.py` converts that same state to and
  from plain JSON-compatible primitives, for whatever a real storage
  boundary would actually write — no database or file writes it yet.
- `alembic/` and `app/db/schema.py` add a PostgreSQL migration scaffold
  for a `game_sessions` table shaped to hold that same payload — a
  scaffold to apply by hand, not something app startup runs
  automatically. `app/db/row_store.py` adds the row-value and
  SQL-statement helpers (a `SELECT`, a PostgreSQL upsert, and an
  insert-if-absent `ON CONFLICT DO NOTHING` statement) built from that
  table. Neither module opens a connection by itself — see the
  `database_url`/`GAME_STORAGE_MODE` bullet below for what actually can,
  and when.
- The deprecated `/api/v1/simulations/throws` route shares one game
  across every caller, for backward compatibility, not isolation; once
  that shared game finishes, calls return 409 until someone resets it via
  `POST /api/v1/games/legacy-default/reset`.
- `database_url` exists in config for the v3 milestone. A SQLAlchemy
  table schema, an Alembic migration, row-value/SQL-statement helpers,
  an `Engine`/session factory (`app/db/session.py`), and a concrete
  `SqlAlchemyGameSessionRepository` adapter (`app/db/sql_repository.py`)
  all exist for it. `GAME_STORAGE_MODE` (default `memory`) can now opt
  a running app into that adapter (`app.api.dependencies.get_game_service`
  builds it when set to `sql`), but this is wiring and tests, not a
  production deployment: no migrations run at startup, no retry, and no
  accounts/ownership. [`docker-compose.sql.yml`](docker-compose.sql.yml)
  (see "Docker" above) adds an opt-in local Postgres container for
  actually exercising `sql` mode — still local development only, not a
  deployment story: no backup strategy, no replication, no TLS. The
  default app is unchanged — nothing opens a database engine or
  connection unless `sql` mode is explicitly configured, and even then
  only once a request actually resolves the dependency or calls
  `GET /health`. In `sql` mode,
  `GET /health` now attempts one lightweight `SELECT 1` connectivity
  check and reports it: `{"status": "ok", "database": "ok"}` (HTTP 200)
  if it succeeds, or `{"status": "degraded", "database": "unreachable"}`
  (HTTP 503) if it doesn't — never an unhandled exception either way. In
  the default `memory` mode, `/health` is unchanged: `{"status": "ok"}`,
  HTTP 200, no engine ever built.
- The frontend has no chart suite, accounts, or persistence. Its ball and
  oil-pattern catalogs are fixed at startup — no adding, editing, or
  persisting either. A saved `game_id` is proactively checked once, at
  initial load; if the server loses that game later while the tab stays
  open, that's caught only reactively, on the next throw/reset attempt,
  not via a continuous background check.
- Each `path` point carries the backend's own observed `elapsed_s` (real
  simulation time since release). The animation both interpolates between
  points by that recorded time rather than by point count, *and* derives
  the path phase's total on-screen duration from each throw's own final
  `elapsed_s` — a throw the server simulated as travelling longer plays
  back longer, one that reached the pins faster plays back faster; it is
  no longer the same fixed number for every throw. That mapping is a
  deliberate, named display scale
  (`PATH_ANIMATION_MS_PER_ELAPSED_SECOND` in
  `frontend/src/domain/trajectoryAnimation.ts`), clamped to a usable
  on-screen range (`MIN_PATH_ANIMATION_MS`-`MAX_PATH_ANIMATION_MS`) — real
  travel time is on the order of 1.6-4.2 seconds, and this plays it back
  in well under a second, so it is still not literal real-time playback.
- The lane canvas's board spacing and its last stretch of downlane
  distance are both deliberately exaggerated for legibility, not drawn to
  true physical scale.
