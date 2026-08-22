# Bowling-Sim

A bowling simulator built around a physics model, not a random-number generator.
You pick a ball, an oil pattern, and throw parameters. The backend simulates
the ball's path down the lane and reports what happens at the pins.

## Status

Backend skeleton only. No frontend, no database, no auth. A single endpoint
runs one throw through the physics engine and returns a trajectory.

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
Ball    — mass, radius, RG, differential, surface, coverstock
Throw   — speed, rev rate, axis rotation, axis tilt, launch angle, launch position
Lane    — oil pattern, board-by-board friction map
```

Simulation steps down the lane in half-foot increments. At each step, the
lane's friction at that point slows the ball down and converts stored rev
rate into lateral drift. Friction is low on oil, so the ball skids straight;
it rises past the oil, so the ball starts to hook. That's the whole model:
no perfect physics, just enough to make ball and lane choices matter.

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
    "oil_pattern": "house",
    "speed_mph": 17,
    "rev_rate": 350,
    "axis_rotation": 45,
    "axis_tilt": 15,
    "launch_angle": 2,
    "launch_position": 28
  }'
```

Ball catalog: `house_ball`, `urethane_smooth`, `reactive_pearl`, `particle_beast`
(see `backend/app/physics/ball.py`). Oil patterns: `house`, `sport`
(see `backend/app/physics/lane.py`).

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
- `database_url` exists in config for the v3 milestone; nothing reads or
  writes to Postgres yet, and no migrations exist.
- No frontend yet. The endpoint is exercised through `curl`, `/docs`, or tests.

## Working in this repo

[`SKILL.md`](SKILL.md) sets the writing rules for anything prose in this
repo: README, docs, PR descriptions, commit messages. Read it before writing
any of those.
