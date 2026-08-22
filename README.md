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
  weight/height/max-diameter/coefficient-of-restitution — is quoted
  directly from the official [USBC Equipment Specifications and Certifications Manual](https://images.bowl.com/bowl/media/assets/usbc/equipment%20specs/26_231-26-march-es-manual.pdf)
  (bowl.com; current revision, every page footer reads "Last updated on
  03/26" — March 2026 — verified directly against the document's own
  pin-dimension and pin-spot tables, accessed 2026-08-22), not estimated.
  Re-verified against the manual's prior revision too: every figure used
  here is unchanged across both.
- **Impact construction** (`app/physics/impact.py`) — `impact_state_from_result`
  turns a completed trajectory into an `ImpactState`: the ball's lateral
  position, heading, and speed at the headpin plane, plus the mass and
  radius of the ball that got there. This is the one place a trajectory's
  raw fields get read for this purpose — pinfall models consume
  `ImpactState`, never a `SimulationResult` directly.
- **Pinfall resolution** sits behind a `PinfallModel` interface
  (`app/physics/pinfall.py`), with two implementations:
  - `PlanarCollisionPinfallModel` (`app/physics/collision.py`) — the API's
    default. A deterministic 2D (top-down, flat-circle) simulation of the
    ball and all ten pins: fixed 0.0005s timestep, up to 4000 steps (2s
    simulated) or until everything settles, elastic/inelastic impulses on
    contact, bounded linear damping, no randomness. See "Official inputs
    vs. calibrated parameters" below.
  - `EntryAngleHeuristicPinfallModel` — the original formula-based rule
    from earlier milestones, kept as an explicitly labeled fallback for
    comparison and tests. It can't identify individual pins
    (`fallen_pin_ids` is always empty from this model).
  Both are pure functions of their input `ImpactState` — no random source,
  ever, in either.
- **Frame scoring** doesn't exist yet. When it's built, it'll consume
  `PinfallResult`s the same way pinfall consumes `ImpactState`s.

`ThrowResponse.pins_knocked` still means exactly what it always has —
that field isn't going anywhere. `ThrowResponse.pinfall` is new: it names
which model produced that count (`model_id`), lists which pins fell
(`fallen_pin_ids`, empty for a model that can't identify them), and states
the model's limitations in plain language — so swapping pinfall models is
a visible, self-describing change instead of a silent one.

#### The collision model: official inputs vs. calibrated parameters

`PlanarCollisionPinfallModel` runs entirely in inches and seconds
(`ImpactState.speed_mph` converts once, at the top, via
`units.mph_to_in_per_s`). What it takes directly from the USBC manual:
pin weight (3 lb 8 oz target), the 12 in triangular deck geometry, and a
coefficient of restitution of 0.670 (the manual's own target value for a
pin), applied to every collision — ball-pin and pin-pin alike, since no
separate figure is published for either.

A ball or pin spec sheet states a *weight* (lbf, a force), not an
inertial mass — even though `Ball.mass_lbs` and "pin mass" get used
informally elsewhere in this project. Real impulse and kinetic-energy math
needs genuine inertia, so `collision.py` converts every weight it touches
through standard gravity into a true mass (`units.weight_lbf_to_mass_blob`,
in "blobs" — the inch-pound-second consistent unit, 1 blob = 1 lbf·s²/in)
before using it in any calculation. Ball and pin go through the identical
conversion, so their mass *ratio* — and every collision outcome — is
unchanged from treating the raw weights as mass directly; only the units
become honest, and "kinetic energy" in this model means real energy
(lbf·in), not a same-named but dimensionally hollow number.

What's calibrated — stated explicitly, never silently guessed:

- **Effective pin radius** (`PIN_EFFECTIVE_RADIUS_IN`, ~2.38 in): half the
  pin's widest diameter (its "belly," 4.5 in above the base — not the base
  itself, which is only 2.03 in). A real pin's cross-section varies by
  height; using one circle for it is an approximation, chosen at the
  widest point because that's roughly where a ball at typical impact
  height makes contact.
- **Linear damping** (`LINEAR_DAMPING_PER_S`): a bounded per-second
  velocity decay standing in for lane friction and the energy an
  (unmodeled) tipping motion would absorb. Not a measured friction
  coefficient.
- **Fall-displacement threshold**: a pin counts as fallen once it has
  moved more than its own effective radius from its spot — a proxy for
  toppling in a model with no angle or center-of-mass height to test an
  actual tip-over threshold against.

A non-positive impact speed short-circuits before any collision geometry
or positional correction runs, even when the ball's starting position
overlaps a pin's circle — a stationary ball can never dislodge a pin
purely because they started overlapped. Two circles found at exactly zero
distance apart (a genuine edge case mid-simulation, not just the
zero-speed scenario) separate along their relative-velocity direction when
there is one, or a fixed downlane axis when both are exactly stationary —
deterministic either way, and never a source of added energy.

**Deferred 3D effects** — not modeled, and each is a real source of error
against a genuine pin deck: pin tilt/shape (pins aren't disks — a real one
tips over an axis, this one just displaces), loft off the foul line,
kickbacks, string-pinsetter interaction, and pinsetter placement variance
(real pin spots drift slightly, tested to their own ±1/16 in tolerance).

### Scoring — a separate, pure domain model

`app/scoring/scorecard.py` turns a sequence of pinfall counts into frame
states and a score, per standard USBC ten-pin rules. It's deliberately
isolated from everything above it: `Scorecard` takes one `int` (0-10) per
roll and knows nothing about a lane, a collision, HTTP, or a database — it
doesn't decide which pins are standing for the next ball, only what a
given sequence of pinfall counts *means* as a score. A future
game-session integration supplies each roll from a `PinfallResult.pins_knocked`
and separately manages the rack between rolls; this module isn't wired
into the API yet.

```python
from app.scoring.scorecard import Scorecard, ScorecardError

card = Scorecard()
card.add_roll(10)   # frame 1: strike — its score is unresolved until the next two balls land
card.add_roll(7)
card.add_roll(3)    # frame 2: spare — frame 1 now resolves to 10+7+3=20; frame 2 needs one more ball
card.add_roll(4)    # frame 3, ball 1 — resolves frame 2 to 20+(10+4)=34

card.frames[0].score   # 20
card.total_score       # 34 — cumulative through the last frame that's been resolved

try:
    card.add_roll(11)  # out of range
except ScorecardError:
    pass  # the scorecard is exactly as it was before the call
```

A frame's `score` is `None` — never a number computed by treating a
missing bonus as zero — until every ball it depends on has actually been
thrown; once any frame is unresolved, every later frame's cumulative
score is `None` too, since a running total can't skip past a gap. Frame
10 is self-contained (its own strike/spare bonus balls live inside its own
`rolls`), so it never depends on anything outside itself. Every
`add_roll` call is validate-then-commit: an illegal roll — out of 0-10,
more pins than remain in that frame, an illegal tenth-frame bonus
sequence, or a roll after the game is already complete — raises
`ScorecardError` and leaves the scorecard exactly as it was.

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

- Pinfall is a flat 2D approximation (`PlanarCollisionPinfallModel`) — real
  pins tip and rotate in 3D; this model only displaces circles. See
  "Deferred 3D effects" above for what's not modeled (pin tilt, loft,
  kickbacks, string pinsetters, placement variance).
- No handedness distinction — the pocket model (both pinfall
  implementations' pocket concept) assumes a right-handed shot.
- The collision model's effective pin radius, damping, and fall threshold
  are stated calibration choices, not fit against real ball-motion or
  pinfall data — no exact strike/pinfall count should be read as accurate
  yet, only as bounded and directionally sensible.
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
