# Bowling-Sim

A bowling simulator built around a physics model, not a random-number generator.
You pick a ball, an oil pattern, and throw parameters. The backend simulates
the ball's path down the lane and reports what happens at the pins.

## Status

Backend only. No frontend, no database, no auth. Each game gets its own
lane: create one, throw in it, reset it back to a fresh house shot whenever
you want. A deprecated single-lane endpoint from an earlier milestone still
works, shared by every caller that still uses it.

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
stored spin into lateral drift. Friction is derived from how much oil
remains in that grid cell: heavy oil skids the ball straight, a dry board
grips it and starts the hook. That's the whole model — no perfect physics,
just enough to make ball and lane choices matter.

### One unit system, declared once

Everything inside the integration loop runs in feet and seconds
(`app/physics/units.py`). A request's mph and RPM are converted to ft/s and
rad/s exactly once, at the top of `simulate_throw`; the timestep comes from
feet-of-travel divided by feet-per-second, never feet divided by mph.
Lateral position gets the same treatment: it accumulates as a length
(`lateral_offset_ft`) throughout the throw and converts to a board number
only at the trajectory boundary, via a declared board width (1.05 in —
close to a regulation lane's 41.5 in across 39 boards). It's never added to
a board index directly. Results convert back to mph and boards only when
they leave the simulator. `FORWARD_DRAG`, `SPIN_DECAY`, and `HOOK_GAIN` in
`simulate.py` are openly empirical — tuned so skid, hook, and roll stay
credible and bounded, not derived from rigid-body mechanics.

Because a board is a narrow unit (~0.0875 ft), `launch_angle`'s legal range
is deliberately tight (±2°, see `RELEASE_BOUNDS` in `throw.py`): this model
holds a release angle constant for the ball's entire trip down the lane —
nothing decays it back toward straight — so even a couple of degrees
integrates into many boards of drift over 60 ft. See "Coordinate
conventions" at the top of `simulate.py` for the full sign/direction
reference (downlane distance, lateral direction, board numbering, release
angle, entry angle).

### Ball properties, what they do

- **Coverstock** and **surface** (grit) set how much the ball grips a dry
  board — plastic barely hooks, particle on a 500-grit surface hooks hard.
- **RG** and **differential** set how early and how sharply the ball flares
  into that hook.
- **Mass** and **radius** are on the model but unused in this milestone's
  trajectory calculation, both deliberately: under simple Coulomb friction,
  `a = mu * g`, so mass cancels out of `F = ma` and doesn't change how far a
  ball coasts. Mass belongs in a future pin-carry / momentum-transfer model
  instead (v2+). Radius is unused because every catalog ball shares the
  regulation diameter — it can't differentiate behavior until custom or
  undersized balls exist.

### Lane state is stateful, not a fixed pattern

The house shot starts as a lateral/longitudinal oil grid, normalized so it
sums to exactly 22 mL — a documented, reasoned assumption (not a certified
USBC pattern) in line with a typical house shot: a 3:1 volume ratio between
the center boards and the pattern's outer edge, 40 feet long with a 6-foot
taper into the dry back end. Friction at any point is that cell's oil
relative to the pattern's fresh peak concentration, so a board that started
lighter (the pattern's own taper) already reads drier than the center, even
before any wear.

Every throw wears the grid in: the boards the ball actually crossed lose a
small, bounded fraction of their oil, and a smaller fraction of what was
picked up resurfaces a few feet further down those same boards
(`app/physics/lane.py::apply_wear`). The physics functions themselves stay
pure — `apply_wear` returns a new `LaneCondition` rather than mutating one,
and the reusable `OilPatternSpec` (the pattern's shape) is never mutated at
all. The only mutable state is `LaneSession` (`app/physics/lane_session.py`):
a small class holding "the lane right now" for one lane. Reading the
current condition, simulating a throw against it, and recording that
throw's wear happen inside one lock (`LaneSession.run_throw`) — two
requests against the *same* lane can't both read the same condition and
silently clobber each other's wear, and the version a response reports is
exactly the one it ran against.

`LaneCondition` also carries `temperature_f` (72°F by default), retained
unchanged through wear. It nudges friction by a small, bounded, documented
amount — at most ±10%, symmetric around 72°F — rather than doing nothing
with it (`LaneCondition.friction_at` / `_temperature_friction_multiplier`
in `lane.py`). The direction (warmer -> slightly higher friction) is a
stated modeling choice, not derived from thermodynamics.

### Games own their lane — state lifecycle

`LaneSession` is a primitive, not a place to put multiple players' state.
`app/games/service.py` owns that: a `GameSession` pairs one game's
immutable starting `LaneCondition` with the `LaneSession` built from it, and
`GameService` is the thread-safe map from an opaque `game_id` to its
`GameSession`. Two games' lanes never see each other's wear — each request
resolves `game_id` to exactly one `GameSession` and only ever touches that
one's lane. `GameService`'s own lock only protects the game_id -> session
mapping (create/lookup); each game's throws are still made atomic by that
game's own `LaneSession` lock, not the service's.

`POST /api/v1/games/{game_id}/reset` calls `GameSession.reset()`, which
replaces the lane with the *exact* `LaneCondition` that game started
with — same grid, same temperature — and lands back at version 1. It never
touches `OilPatternSpec`/`LaneCondition.house_shot()`, the reusable pattern
definition every game is built from; reset only ever affects the one game
whose ID you call it on.

### Pin deck, impact, and pinfall — separate concerns, on purpose

Four things that will eventually combine into real pin-collision physics
are kept deliberately separate, each in its own module, so a future
collision solver can replace one without touching the others:

- **Pin-deck geometry** (`app/physics/pin_deck.py`) — pure, static, and
  independent of any bowler. Ten individually identified pins (`Pin.id`
  1-10) in the standard triangular layout, 12 in center-to-center, No. 1
  pin 60 ft from the foul line. Positions are inches from lane center
  (board 20 of 39), same sign convention as everywhere else in this
  project. Every USBC figure here — spacing, the No. 1 pin's distance, pin
  weight/height/base-diameter/coefficient-of-restitution — is quoted
  directly from the official [USBC Equipment Specifications and Certifications Manual](https://bowl.com/getmedia/08ef148d-c0e4-4e00-9e0d-855ba4729ad5/equipment-specs-manual.pdf)
  (current as of its "10/25" revision), not estimated. The pin base
  diameter is cited but **not** turned into a 2D collision radius — that's
  a calibration decision for the collision milestone itself, not this one.
- **Impact construction** (`app/physics/impact.py`) — `impact_state_from_result`
  turns a completed trajectory into an `ImpactState`: the ball's lateral
  position, heading, and speed at the headpin plane, plus the mass and
  radius of the ball that got there. This is the one place a trajectory's
  raw fields get read for this purpose — pinfall models consume
  `ImpactState`, never a `SimulationResult` directly.
- **Pinfall resolution** (`app/physics/pinfall.py`) — sits behind a
  `PinfallModel` interface. `EntryAngleHeuristicPinfallModel` (the
  `pins_from_entry` heuristic from earlier milestones, replumbed onto
  `ImpactState`) is today's only implementation, explicitly named and
  explicitly not a collision model. Its `resolve()` is a pure function of
  its input — no random source, ever.
- **Frame scoring** doesn't exist yet. When it's built, it'll consume
  `PinfallResult`s the same way pinfall consumes `ImpactState`s.

`ThrowResponse.pins_knocked` still means exactly what it always has —
that field isn't going anywhere. `ThrowResponse.pinfall` is new: it names
which model produced that count (`model_id`) and states its limitations in
plain language, so swapping in a real collision model later is a visible,
self-describing change instead of a silent one.

### Release variance

Real bowlers don't repeat a shot exactly. `sample_release` (`app/physics/throw.py`)
draws small, bounded noise around the requested speed, rev rate, axis
rotation, axis tilt, launch angle, and launch position, clamps the result to
the same legal range the API enforces on a request (`RELEASE_BOUNDS`, the
single source both `sample_release` and the request schema read from), then
simulates the sampled values, never the requested ones. Pass a `seed` to
reproduce a throw exactly; omit it and the response returns the seed it
generated, so you can replay that exact throw later — clamping is
deterministic, so replay stays exact even at the edge of the legal range.
Pin count is always read off the resulting trajectory — never sampled on
its own.

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

## API

### Create a game

```bash
curl -X POST http://localhost:8000/api/v1/games \
  -H "Content-Type: application/json" -d '{}'
# {"game_id": "…", "lane_condition_version": 1}
```

`oil_pattern` is optional and defaults to `"house"` — the only pattern this
milestone supports. Anything else is a 422; the field exists now so a
future named-pattern (or temperature) selection is an additive change, not
a new route.

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
    "launch_angle": 0.5,
    "launch_position": 28
  }'
```

`seed` is optional — reuse the one an earlier response returned to replay
that exact throw. This game's lane wears in with every throw; the
response's `lane_condition_version` tells you which state your throw
actually ran against. An unknown `game_id` is a 404.

### Reset a game's lane

```bash
curl -X POST http://localhost:8000/api/v1/games/{game_id}/reset
# {"game_id": "…", "lane_condition_version": 1}
```

Restores this game's lane to exactly what it started with and puts the
version counter back at 1. Other games are untouched.

Ball catalog: `house_ball`, `urethane_smooth`, `reactive_pearl`, `particle_beast`
(see `backend/app/physics/ball.py`).

### Deprecated: `POST /api/v1/simulations/throws`

The single-lane endpoint from an earlier milestone still works, unchanged
in shape, but every caller shares one lazily-created game
(`legacy-default`) — there's no per-caller isolation. It's marked
`deprecated` in `/docs`. New integrations should create their own game
instead.

## Roadmap

**v1** — draw the lane, pick an oil pattern and ball, enter throw parameters,
animate the throw, show pin impact, score the frame.

**v2** — more balls and surfaces, adjustable drilling layouts, an oil-pattern
editor, pin carry, misses and gutters, full 10-frame games.

**v3** — accounts, saved games, throw history, averages, strike/spare/pocket
percentages, leave tracking, ball usage stats.

## Known limitations (this milestone)

- Pinfall is still `EntryAngleHeuristicPinfallModel` — a deterministic
  function of lateral position and heading, not a pin-collision model. The
  domain boundary (pin-deck geometry, `ImpactState`, `PinfallModel`) is now
  in place for a real collision solver to slot into later; that solver
  itself doesn't exist yet.
- No handedness distinction — the pocket model assumes a right-handed shot.
- Pin base diameter is cited in `pin_deck.py` but deliberately not turned
  into a 2D collision radius yet — see "Pin deck, impact, and pinfall"
  above.
- Only the house shot is modeled; named-pattern selection is deferred
  (`oil_pattern` on `POST /games` only accepts `"house"` today).
- Games live in memory only — restarting the process loses every game.
  There's no accounts/ownership layer yet: any caller who has a `game_id`
  can throw in or reset that game.
- `GameService` never expires old games, so a long-running process
  accumulates one `GameSession` per game created, indefinitely.
- The deprecated `/api/v1/simulations/throws` route shares one game across
  every caller — it's for backward compatibility, not isolation.
- `mass_lbs` and `radius_in` are on the `Ball` model but not used in this
  milestone's trajectory calculation — both deliberately, see "Ball
  properties" above (`app/physics/ball.py`).
- `FORWARD_DRAG`, `SPIN_DECAY`, and `HOOK_GAIN` (`app/physics/simulate.py`)
  are hand-tuned for bounded, credible motion, not fit to real ball-motion
  data.
- `launch_angle` is held constant for a throw's whole trip down the lane —
  nothing decays it back toward straight — which is why its legal range is
  a tight ±2° rather than a more generously "realistic" figure. A model
  that let a release angle fade out over the first several feet (closer to
  how a real ball settles onto its roll) could afford a wider range; that's
  a larger change than this milestone's scope.
- The house shot's length, taper, ratio, and 22 mL total volume are a
  reasoned assumption, not a certified USBC pattern.
- The board width (1.05 in) and the temperature-friction adjustment
  (±10% max, linear, 72°F reference) are both stated modeling constants,
  not measured or derived.
- `database_url` exists in config for the v3 milestone; nothing reads or
  writes to Postgres yet, and no migrations exist.
- Release-error bounds (`_RELEASE_NOISE_STD` in `app/physics/throw.py`) are
  reasoned estimates of human variance, not measured from real bowlers.
- No frontend yet. The endpoint is exercised through `curl`, `/docs`, or tests.
