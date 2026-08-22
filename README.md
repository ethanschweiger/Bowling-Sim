# Bowling-Sim

A bowling simulator built around a physics model, not a random-number generator.
You pick a ball, an oil pattern, and throw parameters. The backend simulates
the ball's path down the lane and reports what happens at the pins.

## Status

A backend, and a first connected frontend shell. No database, no auth. Each
game gets its own lane: create one, throw in it, reset it back to a fresh
house shot whenever you want. A deprecated single-lane endpoint from an
earlier milestone still works, shared by every caller that still uses it.
The frontend (`frontend/`) is a Vite + React + TypeScript single-page shell
that drives one game against the real API — see "Frontend" below for what
it does and doesn't do yet.

## Architecture

```
React + TypeScript frontend  <- frontend/src — Vite dev server proxies /api to the backend
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

### The standing-pin rack, and letting collision resolve a partial deck

`app/physics/rack.py`'s `Rack` tracks which of the ten pins are still
standing between throws — nothing else. Like `Scorecard`, it's
independent of everything around it: no lane oil, no scoring rules, no
collision math of its own.

```python
from app.physics.rack import Rack, RackError

rack = Rack.full()               # all ten standing
rack = rack.after_fallen([1, 2, 3])   # a new Rack — the original is untouched
7 in rack                        # True
rack.reset()                     # back to a fresh, all-ten Rack

try:
    rack.after_fallen([1])       # pin 1 is already down
except RackError:
    pass                          # rack is exactly as it was before the call
```

`PinfallModel.resolve` (and `collision.simulate_collision`) now accept an
optional `standing_ids` — pass a `Rack.standing_ids` and
`PlanarCollisionPinfallModel` materializes and resolves *only* those pins;
any fallen ID it returns is guaranteed to be a member of that set. Omit
it and every pin is simulated, byte-for-byte the same as before this
parameter existed — nothing about the default API responses changed in
this milestone. `EntryAngleHeuristicPinfallModel` accepts the same
parameter for interface consistency but ignores it; it never identified
individual pins to begin with.

`Rack` is a genuinely immutable value, not just by convention: a
`standing_ids` you pass in — even your own plain `set` or `list` — is
canonicalized into an owned `frozenset` before it's ever stored, so
mutating your original collection afterward can't reach back into the
`Rack`. Every rack boundary (direct construction, `after_fallen`, and a
`standing_ids` passed straight to the collision model) accepts only exact
`int`s 1-10 through the same `rack.validate_pin_ids` — a `bool` or
`float` that happens to equal a valid ID (`True == 1`) is rejected, not
silently treated as that pin, and a duplicate or unknown ID raises
`RackError` rather than silently deduping or vanishing pins from the deck.

### A real game: `GameSession` owns a lane, a scorecard, and a rack

Every `GameSession` (`app/games/service.py`) now owns three independently
mutable slots — its `LaneCondition`/`LaneSession`, a `Scorecard`, and a
rack slot holding an immutable `Rack` — and none of the three value/rule
objects is aware of the others or of `GameSession` itself. The reusable,
never-mutated definitions (`OilPatternSpec`/`LaneCondition.house_shot()`,
`pin_deck.STANDARD_DECK`) stay shared across every game, exactly as
before. All three slots are **in-memory and per-game**, same as the lane
always was — nothing here is persisted, and there's no accounts/ownership
layer; a future move to shared storage or WebSocket-synchronized
multiplayer changes who holds a `GameSession`, not the collision solver,
the scorecard rules, or the rack's own logic.

`GameSession.throw` is the one transaction that changes all three. It
holds a single per-game lock across the whole operation: reject the call
outright if the game's already complete (before touching anything);
otherwise read the current rack, run the trajectory and pinfall
resolution the caller supplies, wear the lane in, record the pinfall in
the scorecard, and replace the rack according to the standard ten-pin
fresh-rack rules — a strike or a spare's bonus always gets a fresh rack;
an ordinary ball leaves the collision's complement standing for the next
ball in the same frame. Which of those applies is answered by
`Scorecard.next_ball_starts_fresh_rack()`, a small read-only query over
the frames `add_roll` already computed — the game session never
re-derives or duplicates a ten-pin rule of its own. Two different games'
sessions have entirely separate locks, so they never block each other.

Every throw, create, reset, and `GET` response carries a `game_state`: the
standing pin IDs, every frame's rolls/strike/spare/complete/score, the
resolved `total_score`, `is_game_complete`, and the 1-based
`next_frame_number`/`next_ball_number` (both `null` exactly when the game
is complete). A throw against an already-complete game is rejected with
`409 Conflict` — `POST .../reset` starts the game over with a fresh rack,
a blank scorecard, and lane version 1.

`game_state` is never built by reading a live `Scorecard` after the fact —
`Scorecard` is a genuinely mutable object (`add_roll` reassigns state on
the same instance), so holding a reference to it and reading from that
reference *later* is unsafe: a second throw landing in between can make
an earlier response describe newer state. Instead, `throw()` and `reset()`
each build their own immutable `GameStateSnapshot` — a frozen dataclass of
plain values (an int, a frozenset, a tuple of already-immutable `Frame`
objects) — from *inside* the same lock that did the mutation, and return
it as part of their own result. Every response, including `GET`, is
rendered from one of these snapshots, never from a second lock/read taken
after the operation that produced it has already returned. `GameSession`
itself has no public accessor for either live slot — earlier versions had
`.scorecard` and `.rack` properties, each handing out its live value
under the lock, then releasing it. `.scorecard` was a real instance of
this same race; `.rack` wasn't, since `Rack` is immutable, but leaving a
second, narrower inspection path public still undercut the same
single-read-model contract. Both are gone now: `current_snapshot()`/the
returned `GameStateSnapshot` are the *only* public way to inspect a
game's lane version, standing pins, frames, score, completion state, and
next roll.

### Read a game without changing it

`GET /api/v1/games/{game_id}` takes its own fresh, lock-protected
snapshot (`GameSession.current_snapshot()`) — safe to call anytime,
including concurrently with other games' throws, since it never mutates
anything. Same `game_state` mapper as create/throw/reset, so the shape
can't drift between endpoints. This is current, in-memory, per-process
state, not persisted or multiplayer-synced — restarting the server, or a
second server process, would not see it.

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

## Frontend

`frontend/` (Vite + React + TypeScript, the official `react-ts` template)
is a first connected shell, not the polished v1 experience the roadmap
describes — it plays one fixed-duration replay animation of a completed
throw's own server-recorded path, not a real-time physics simulation of
its own, and has no charts, accounts, or persistence. It talks to the
real API and nothing else: on load it creates a game (or resumes the one
this browser created last time, from `localStorage`, if the server still
has it) and renders exactly the `game_state`/trajectory a response
contains. No score, rack, or completion rule is re-derived client-side —
`is_strike`/`is_spare`/`score`/`is_complete`/`total_score` are read
straight off the response; the frontend only formats them (see
`frontend/src/domain/scoreDisplay.ts`'s module docstring for the one
deliberately-incomplete corner: a bonus ball that lands on a fresh rack
after a 10th-frame strike shows its own plain pin count rather than a
synthesized "X", since telling which bonus ball was fresh is a rack rule
this client leaves to the server).

That create-or-load bootstrap is idempotent even under React `StrictMode`'s
deliberate double-invoke of a mount effect in development: the attempt is
memoized at module scope (`frontend/src/domain/gameLifecycle.ts`), so a
second concurrent caller shares the first one's in-flight request instead
of starting a second one and orphaning a game. A failed bootstrap shows a
"Retry" action rather than getting stuck; a reset that 404s (unambiguous —
it can only mean the saved game is gone, most likely the backend
restarted, per "Known limitations" below) shows a distinct "Start a new
game" recovery action instead of silently discarding whatever the UI was
last showing. A throw's own 404 is *not* treated the same way outright,
because `POST .../throws` also 404s for an unrelated reason (an unknown
`ball_id`, see "Throw in that game" below) — the frontend confirms with a
fresh `GET` first (`gameLifecycle.ts`'s `classifyThrowFailure`) and only
shows the recovery action once that confirms the game itself is gone;
otherwise the throw's own error is what the user sees, and the live game
is never discarded on a guess. The footer's lane-
condition-version line is careful about a real backend distinction: a
throw response's version is the one that throw ran *against* (documented,
pre-wear), not the game's current version, so the footer labels it
"ran against" rather than presenting it as current — the truthfully-
current number (from create/reset/GET) is what shows the rest of the time.

The six release inputs share `RELEASE_BOUNDS` and `ThrowRequest`'s field
defaults with the backend (`frontend/src/domain/releaseFields.ts`), so the
UI can't offer a value the API would reject. The ball catalog
(`frontend/src/domain/ballCatalog.ts`) is a small hardcoded list of today's
four ball IDs — there's no `GET /api/v1/balls` yet — and only the `house`
oil pattern is shown as available, matching what `POST /api/v1/games`
actually accepts. The lane `<canvas>` draws the documented 39-board/60 ft
lane, the foul line, and the standard pin deck (filled = standing,
outlined and faded with an "×" = fallen — never color alone, sourced from
`game_state.standing_pin_ids` in its final, already-server-confirmed
state throughout — see below); both board spacing and the last stretch
of downlane distance are deliberately exaggerated for legibility (see
`frontend/src/domain/laneProjection.ts`), not drawn to true physical
scale. The canvas is decorative (`aria-hidden`); the visible text beside
it is the real result summary, and it's never re-announced per animation
frame.

A completed throw plays once: a marker advances along that response's
*exact* recorded `path`, interpolated between those exact points (never a
recalculated path of its own — `frontend/src/domain/trajectoryAnimation.ts`),
then settles into the same static trajectory/entry-marker end-state the
canvas always showed. `path` points are recorded at fixed downlane-*distance*
steps, not fixed *time* steps, so there's no per-point timestamp to
animate against — playback runs over one fixed, documented visual
duration (900 ms, eased), not a reproduction of the throw's real speed or
travel time. The standing/fallen pins shown during that playback are
still the throw's *final* rack the whole time, never a client-reconstructed
"before this throw" rack — building that correctly would mean re-deriving
the same fresh-rack-on-frame-completion rule this project keeps
server-side (the same principle `scoreDisplay.ts` already applies to
scoresheet glyphs). A "Replay last shot" button appears once a throw has
completed: it restarts that same animation over the same stored path,
makes no request, and cannot change score, pins, lane condition, game id,
or release values (`trajectoryAnimation.ts`'s `canReplay` — a pure
predicate with no access to the API client); it's disabled while no shot
exists yet, a request is in flight, or the game is confirmed stale.
Submitting a new throw (or a reset, or "Start a new game") settles any
still-playing animation of the *preceding* result immediately, the
instant the request is sent — not only once its response arrives. That
preceding result stays visible as a static image while the new request
is pending, and "Replay last shot" is disabled for it in the meantime
(`canReplay` already treats "a request is in flight" as not replayable).
If the new request then fails for an ordinary reason, that settled
result simply stays as it is — nothing auto-replays it just because the
pending state cleared. Only a *successful* response (a fresh path) or an
explicit "Replay last shot" press ever starts a new animation
(`trajectoryAnimation.ts`'s `decidePlaybackAction` makes this decision
from plain before/after state, independent of React or the DOM, and is
unit-tested directly). A reset or confirmed-stale-game replacement
clears the previous throw outright once it succeeds, so a new game's
rack is never drawn under an old throw's leftover animation. Under
`prefers-reduced-motion`, both a fresh throw and "Replay last shot" skip
straight to the settled static state — no autoplay, and nothing to
replay differently.

### Run it locally

```bash
cd frontend
npm install
npm run dev
```

This starts the Vite dev server at `http://localhost:5173`. Its dev-server
proxy (`frontend/vite.config.ts`) forwards relative `/api/...` requests to
`http://127.0.0.1:8000`, so start the backend first (see "Run" above) —
no CORS configuration was needed on the FastAPI side; the browser only
ever talks to the Vite server, which makes the cross-origin hop itself.
This proxy is the only supported local setup. `frontend/.env.example`
documents `VITE_API_BASE_URL`, which points the API client at a different
origin entirely, bypassing the proxy — that needs the backend's own CORS
configuration to work, which doesn't exist yet (`backend/app/main.py` has
none), so don't set it expecting a different origin to just work.

```bash
cd frontend
npm run build   # TypeScript check + production build
npm run test    # vitest — request mapping and game-state display logic
```

## API

### Create a game

```bash
curl -X POST http://localhost:8000/api/v1/games \
  -H "Content-Type: application/json" -d '{}'
# {"game_id": "…", "lane_condition_version": 1, "game_state": {
#    "standing_pin_ids": [1,2,3,4,5,6,7,8,9,10], "frames": [],
#    "total_score": null, "is_game_complete": false,
#    "next_frame_number": 1, "next_ball_number": 1 } }
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
actually ran against, and `game_state` tells you the resulting rack,
frames, and score. This route's 404 is ambiguous by design, not just an
unknown `game_id` — an unknown `ball_id` is *also* a 404 (checked after
the game itself is confirmed to exist), so a client can't tell "the game
is gone" from "that ball doesn't exist" by status code alone; the
frontend's own handling of this (`GET`-confirming before ever treating a
throw's 404 as a missing game) is under "Frontend" above. A throw against
a finished game (ten frames plus any required bonus balls) is a
`409 Conflict` — nothing about the game changes when that happens.

### Reset a game

```bash
curl -X POST http://localhost:8000/api/v1/games/{game_id}/reset
# {"game_id": "…", "lane_condition_version": 1, "game_state": {...fresh...}}
```

Restores this game's lane to exactly what it started with, starts a new
blank `Scorecard`, and returns the rack to all ten pins standing — the
same `game_state` shape a freshly created game has. Other games are
untouched.

### Read a game's current status

```bash
curl http://localhost:8000/api/v1/games/{game_id}
# {"game_id": "…", "lane_condition_version": 2, "game_state": {...}}
```

Read-only — throws nothing, changes nothing. Same `game_state` shape as
create/throw/reset, built through the identical mapper so the contract
can't drift between endpoints. `lane_condition_version` here is the
game's *current* version (after whatever wear its last throw applied) —
not to be confused with a throw response's own `lane_condition_version`,
which is (unchanged, documented) the version that specific throw ran
*against*, one lower once that throw's own wear has landed. An unknown
`game_id` is a 404. This reflects current, in-memory, per-process
state — nothing here is persisted, and there's no multiplayer sync; a
second server process, or this one restarting, would not see it.

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
animate the throw, show pin impact, score the frame. The frontend shell
(see "Frontend" above) now covers all of this: a fixed-duration replay
animation of each throw's own server-recorded path, not a from-scratch
physics simulation or true-to-real-time playback.

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
  every caller — it's for backward compatibility, not isolation. It now
  goes through the same `GameSession.throw` transaction the game-scoped
  route uses, so its rack/scorecard genuinely progress across calls too
  (no longer an independent full-rack simulation on every request); once
  that shared game finishes, calls to it return 409 until someone resets
  it via `POST /api/v1/games/legacy-default/reset`.
- Every caller who knows a `game_id` can throw in it — there's no
  per-player turn enforcement within a game yet (relevant once
  multiplayer exists; a single caller driving one game is the only
  supported use today).
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
- The frontend shell (`frontend/`) has no chart suite, accounts, or
  persistence — see "Frontend" above for what it does cover.
- The trajectory animation's timing is visual only, not calibrated to the
  throw's own `speed_at_pins_mph` or any real ball-travel time: `path`
  points are recorded at fixed downlane-distance steps, not fixed time
  steps, so there's no per-point timestamp to play back against. Every
  throw animates over the same fixed, eased duration regardless of how
  fast or slow that ball actually traveled. See
  `frontend/src/domain/trajectoryAnimation.ts`.
- The ball catalog and "only house is available" pattern notice are
  hardcoded in the frontend (`frontend/src/domain/ballCatalog.ts`), not
  fetched from the API — there's no `GET /api/v1/balls` yet.
- The frontend only detects a stale saved `game_id` (the backend
  restarted, dropping all in-memory games, per the limitation above)
  reactively — when a reset against it 404s directly, or a throw's 404 is
  confirmed against a fresh `GET`. It then shows a "Start a new game"
  recovery action rather than silently discarding the last good state;
  there's no proactive background check, so a tab left open across a
  backend restart won't notice until the next throw or reset is
  attempted.
- A 10th-frame bonus ball that lands on a fresh rack after an opening
  strike (e.g. the frame's 2nd or 3rd ball) displays as its own plain pin
  count rather than a traditional synthesized "X" glyph — deriving that
  would mean the client re-deriving which rack a bonus ball faced, a rack
  rule this frontend deliberately leaves to the server. See the module
  docstring in `frontend/src/domain/scoreDisplay.ts`.
- The lane canvas's board spacing and its last stretch of downlane
  distance are both deliberately exaggerated for legibility (see
  `frontend/src/domain/laneProjection.ts`) — the diagram is faithful to
  ordering and relative position, not to true physical scale.
