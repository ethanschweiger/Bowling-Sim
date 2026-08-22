# Bowling-Sim

A bowling simulator built around a physics model, not a random-number generator.
You pick a ball, an oil pattern, and throw parameters. The backend simulates
the ball's path down the lane and reports what happens at the pins.

## Status

Backend only. No frontend, no database, no auth. A single endpoint runs one
throw through the physics engine against a stateful house-shot lane and
returns a trajectory.

## Architecture

```
React frontend (later)
      |
   REST API  <- FastAPI, backend/app/api
      |
Physics engine  <- backend/app/physics — pure Python, no FastAPI or DB imports
      |
PostgreSQL (later) — a database URL is already in config; nothing connects yet
```

The physics module knows nothing about HTTP. `simulate_throw(ball, throw, lane)`
takes three plain dataclasses and returns a trajectory. The API layer is the
only thing that touches FastAPI, request parsing, or HTTP status codes.

### The model

```
Ball          — mass, radius, RG, differential, surface, coverstock
Throw         — speed, rev rate, axis rotation, axis tilt, launch angle, launch position
LaneCondition — oil grid (board x foot), derived from a standard house shot
```

Simulation steps down the lane in half-foot increments. At each step, the
lane condition's friction at that point slows the ball down and converts
stored rev rate into lateral drift. Friction is derived from how much oil
remains in that grid cell: heavy oil skids the ball straight, a dry board
grips it and starts the hook. That's the whole model — no perfect physics,
just enough to make ball and lane choices matter.

### Ball properties, what they do

- **Coverstock** and **surface** (grit) set how much the ball grips a dry
  board — plastic barely hooks, particle on a 500-grit surface hooks hard.
- **RG** and **differential** set how early and how sharply the ball flares
  into that hook.
- **Mass** scales deceleration directly: a heavier ball sheds less speed for
  the same friction (`F = ma`).
- **Radius** is on the model but unused this milestone — every catalog ball
  shares the regulation diameter, so it can't differentiate behavior yet.

### Lane state is stateful, not a fixed pattern

The house shot starts as a lateral/longitudinal oil grid (center boards
carry a stated 3:1 volume ratio over the pattern's outer edge, tapering to
dry over the last 6 of its 32 feet). Every throw wears that grid in: the
boards the ball actually crossed lose a small, bounded fraction of their
oil, and a smaller fraction of what was picked up resurfaces a few feet
further down those same boards (`app/physics/lane.py::apply_wear`). The
physics functions themselves stay pure — `apply_wear` returns a new
`LaneCondition` rather than mutating one. The only mutable state is
`LaneSession` (`app/physics/lane_session.py`), a small in-memory service
holding "the lane right now" for the process, built to be swapped for a
database row or a multiplayer session later without touching the physics.

### Release variance

Real bowlers don't repeat a shot exactly. `sample_release` (`app/physics/throw.py`)
draws small, bounded noise around the requested speed, rev rate, axis
rotation, axis tilt, launch angle, and launch position, then simulates the
sampled values, never the requested ones. Pass a `seed` to reproduce a throw
exactly; omit it and the response returns the seed it generated, so you can
replay that exact throw later. Pin count is always read off the resulting
trajectory — never sampled on its own.

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

```bash
cd backend
source .venv/bin/activate
pytest
```

## Try it

```bash
curl -X POST http://localhost:8000/api/v1/simulations/throws \
  -H "Content-Type: application/json" \
  -d '{
    "ball_id": "reactive_pearl",
    "seed": 42,
    "speed_mph": 17,
    "rev_rate": 350,
    "axis_rotation": 45,
    "axis_tilt": 15,
    "launch_angle": 2,
    "launch_position": 28
  }'
```

`seed` is optional — reuse the one an earlier response returned to replay
that exact throw. The lane is a single shared house shot for the process
right now, and it wears in with every throw; the response's
`lane_condition_version` tells you which state your throw actually ran
against.

Ball catalog: `house_ball`, `urethane_smooth`, `reactive_pearl`, `particle_beast`
(see `backend/app/physics/ball.py`).

## Roadmap

**v1** — draw the lane, pick an oil pattern and ball, enter throw parameters,
animate the throw, show pin impact, score the frame.

**v2** — more balls and surfaces, adjustable drilling layouts, an oil-pattern
editor, pin carry, misses and gutters, full 10-frame games.

**v3** — accounts, saved games, throw history, averages, strike/spare/pocket
percentages, leave tracking, ball usage stats.

## Known limitations (this milestone)

- Pin carry is a deterministic function of entry board and angle, not a
  pin-collision model. Good enough for v1, not physically accurate.
- No handedness distinction — the pocket model assumes a right-handed shot.
- Only the house shot is modeled; oil-pattern selection is deferred. It's a
  single shared, stateful lane for the whole process — no separate games or
  players yet, so concurrent requests wear the same lane.
- `radius_in` is on the `Ball` model but not used in any calculation this
  milestone (see `app/physics/ball.py`).
- `database_url` exists in config for the v3 milestone; nothing reads or
  writes to Postgres yet, and no migrations exist.
- Release-error bounds (`_RELEASE_NOISE_STD` in `app/physics/throw.py`) are
  reasoned estimates of human variance, not measured from real bowlers.
- No frontend yet. The endpoint is exercised through `curl`, `/docs`, or tests.
