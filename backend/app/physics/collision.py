"""Deterministic 2D ball-and-pin collision model.

The API's default pinfall model, replacing the entry-angle heuristic
(`pinfall.py`, kept as an explicitly labeled fallback). This is a *planar*
(2D, top-down) approximation of pin-deck contact — real pins are 15-inch
objects that tip and rotate in 3D; this model treats the ball and every pin
as a flat circle sliding on the lane, with no height, tilt, or rotation.
It's an accuracy improvement over a hand-tuned entry-angle rule, not a
claim to recreate a real pin deck. Deferred 3D effects: pin tilt/toppling
dynamics, loft, kickbacks, string pinsetters, and pinsetter placement
variance (see README).

`resolve`/`simulate_collision` accept an optional `standing_ids`: which
pins actually exist to be hit for this one impact (e.g. a `Rack.standing_ids`
from `app/physics/rack.py`, for a ball's second throw in a frame). Omit it
(or pass `None`) and every pin is simulated — the default, unchanged
behavior. A supplied selection is routed through `rack.validate_pin_ids`,
so it's held to the exact same standard `Rack` itself is: only plain
`int` IDs 1-10 (never a `bool` or `float` that merely equals one), no
duplicates, raising `RackError` otherwise — a typo'd `{11}` can never
silently simulate an empty deck. This validation is the *first* thing
either function does, ahead of every other short circuit (a gutter miss
in `resolve`, a non-positive speed in `simulate_collision`) — an invalid
selection raises independently of whether the ball could physically have
hit anything, and independently of which of the two functions a caller
uses (`simulate_collision` can be called directly, without going through
`resolve` at all, so it validates on its own rather than trusting a
caller already did). This module has no notion of a game or a frame; it
resolves exactly the one impact it's given against exactly the pins it's
told are there.

## Unit system

Position and velocity run in inches and seconds, in the same
lateral/downlane coordinate frame as `pin_deck.py` (origin at the No. 1
pin's spot, so the headpin plane is y=0). `ImpactState.speed_mph` is
converted to in/s exactly once, at the top of `simulate_collision`, via
`units.mph_to_in_per_s`.

Mass gets the same "convert once, at the boundary" treatment. `Ball.mass_lbs`
and the USBC pin weight are stated *weights* (lbf, a force), not inertial
masses, however colloquially they get called "mass" elsewhere in this
codebase. Every weight this module touches is converted through standard
gravity into a true mass (`units.weight_lbf_to_mass_blob`, in "blobs" —
the inch-pound-second consistent mass unit, 1 blob = 1 lbf*s^2/in) before
it's used in any impulse or kinetic-energy calculation. Ball and pin go
through the identical conversion, so their mass *ratio* — and therefore
every collision outcome — is unchanged from treating the raw weights as
mass directly; only the units become dimensionally honest, and "kinetic
energy" in this module means real energy (lbf*in), not a same-named but
dimensionally hollow proxy.

## Official USBC inputs vs. calibrated parameters

Official, from `pin_deck.py` (sourced from the equipment specifications
manual — see README for the verified citation with revision/access date):
pin weight, 12 in deck spacing and position, and `COLLISION_RESTITUTION`
(the manual's own target coefficient of restitution for a pin, 0.670 —
applied here to every collision, ball-pin and pin-pin alike, since no
separate figure is published for either case).

Calibrated — stated explicitly, not measured:

- `PIN_EFFECTIVE_RADIUS_IN`: half the pin's widest ("belly") diameter,
  4.766 in (`pin_deck.USBC_PIN_MAX_DIAMETER_IN`). A real pin isn't a
  cylinder — its cross-section varies by height — so representing it with
  one circle is an approximation, chosen at the pin's widest point because
  that's roughly where a ball at typical impact height makes contact.
- `LINEAR_DAMPING_PER_S`: a bounded per-second velocity decay standing in
  for lane friction and the energy an (unmodeled) tipping motion would
  have absorbed. Not derived from a measured friction coefficient.
- `FALL_DISPLACEMENT_THRESHOLD_IN`: a pin is scored as fallen once it has
  moved more than its own effective radius from its spot. A stand-in for
  toppling — this model has no angle or center-of-mass height, so it
  can't test an actual tip-over threshold.

## No energy from nothing

A non-positive impact speed short-circuits before any collision geometry
or positional correction runs — `simulate_collision` returns immediately,
even if the ball's starting position happens to overlap a pin's circle. A
stationary ball can never knock a pin down purely because they started
overlapped; positional correction only ever runs alongside (never instead
of) genuine contact physics.

`_resolve_pair` also handles the degenerate case of two circles at exactly
zero distance apart deterministically: it separates them along the
relative-velocity direction when that's nonzero, or along a fixed axis
(the downlane +y direction) when both bodies are exactly stationary — a
regularization for otherwise-undefined geometry, not a source of motion,
since with zero relative velocity the impulse step contributes nothing
either way.

## Fixed timestep, bounded simulation, no randomness

`simulate_collision` steps forward at `COLLISION_DT_S` seconds per step,
for at most `MAX_COLLISION_STEPS` steps, or until every body's speed drops
below `SETTLE_SPEED_IN_S` — whichever comes first. It always terminates in
one of those two ways, and a recorded replay reports which one as its
`termination_reason` (`step_cap` or `settled`). Neither is a statement
about real pins coming to rest; see `replay.py`'s "How a run ends".

For a fixed set of reference impacts and what this model currently produces
for them — plus which of the constants below are USBC specifications and
which are stated 2D assumptions — see
`backend/docs/planar-collision-calibration.md`. It is a measured baseline
for comparing future changes against, not a claim of real pin carry.
Each step: bodies move by velocity * dt, velocities
decay by the (< 1) damping factor, and any pair of circles found
overlapping is resolved by a standard elastic/inelastic impulse along
their contact normal (restitution <= 1, so a collision never adds kinetic
energy), followed by a positional correction that separates them along
that same normal so overlaps don't persist step to step. Nothing here
calls a random-number generator.
"""

# Keeps `X | None` usable on this project's Python 3.9 floor — see
# app/physics/throw.py's module docstring for the full explanation.
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from app.physics.impact import ImpactState
from app.physics.pin_deck import (
    ALL_PIN_IDS,
    GUTTER_ABS_LATERAL_IN,
    HEADPIN_DISTANCE_FT,
    STANDARD_DECK,
    USBC_PIN_COEFFICIENT_OF_RESTITUTION,
    USBC_PIN_MAX_DIAMETER_IN,
    USBC_PIN_WEIGHT_OZ,
)
from app.physics.pinfall import PinfallModel, PinfallResult
from app.physics.rack import validate_pin_ids
from app.physics.replay import (
    MAX_REPLAY_FRAMES,
    REPLAY_SAMPLE_EVERY_STEPS,
    TERMINATION_SETTLED,
    TERMINATION_STEP_CAP,
    CollisionReplay,
    TerminationReason,
    _ReplayRecorder,
)
from app.physics.units import IN_PER_FT, mph_to_in_per_s, weight_lbf_to_mass_blob

COLLISION_DT_S = 0.0005
MAX_COLLISION_SECONDS = 2.0
MAX_COLLISION_STEPS = int(MAX_COLLISION_SECONDS / COLLISION_DT_S)  # 4000
# The planar velocity threshold below which this model stops stepping.
# A purely numerical criterion on sliding circles: it does not observe a
# pin standing, lying down, or coming to physical rest, because nothing in
# this 2D model represents any of those. `replay.TERMINATION_SETTLED` names
# a run that ended this way, and is documented in exactly those terms.
SETTLE_SPEED_IN_S = 0.5
LINEAR_DAMPING_PER_S = 1.2

OZ_PER_LB = 16.0
PIN_WEIGHT_LBF = USBC_PIN_WEIGHT_OZ[0] / OZ_PER_LB  # target weight, pounds-force — not yet a mass
PIN_MASS_BLOB = weight_lbf_to_mass_blob(PIN_WEIGHT_LBF)  # true inertial mass
PIN_EFFECTIVE_RADIUS_IN = USBC_PIN_MAX_DIAMETER_IN[0] / 2.0
COLLISION_RESTITUTION = USBC_PIN_COEFFICIENT_OF_RESTITUTION[0]
FALL_DISPLACEMENT_THRESHOLD_IN = PIN_EFFECTIVE_RADIUS_IN

# The fixed fallback separating axis for two exactly-coincident, exactly-
# stationary bodies (see "No energy from nothing" above) — downlane, the
# same +y direction pin rows extend along.
_STATIONARY_FALLBACK_NORMAL = (0.0, 1.0)


@dataclass
class _Body:
    """Mutable working state for one circle during a single simulation run.
    Local to `simulate_collision` — never shared or reused across calls."""

    x_in: float
    y_in: float
    vx_in_s: float
    vy_in_s: float
    mass_blob: float  # true inertial mass — see module docstring
    radius_in: float
    origin_x_in: float
    origin_y_in: float
    pin_id: int  # 0 for the ball
    fell: bool = False

    def displacement_in(self) -> float:
        return math.hypot(self.x_in - self.origin_x_in, self.y_in - self.origin_y_in)

    def speed_in_s(self) -> float:
        return math.hypot(self.vx_in_s, self.vy_in_s)


def _resolve_pair(a: _Body, b: _Body) -> None:
    """Impulse + positional correction for one pair of circles, if they
    overlap. No-op if they're genuinely apart. Pure with respect to
    everything except a and b's own x/y/vx/vy, which it updates in place.
    """
    dx, dy = b.x_in - a.x_in, b.y_in - a.y_in
    dist = math.hypot(dx, dy)
    min_dist = a.radius_in + b.radius_in
    if dist >= min_dist:
        return

    rvx, rvy = a.vx_in_s - b.vx_in_s, a.vy_in_s - b.vy_in_s

    if dist == 0.0:
        # Degenerate geometry: no direction is defined by position alone.
        # Separate along the closing relative-velocity direction when
        # there is one; otherwise fall back to a fixed, documented axis.
        # See "No energy from nothing" in the module docstring.
        rel_speed = math.hypot(rvx, rvy)
        if rel_speed > 0.0:
            nx, ny = rvx / rel_speed, rvy / rel_speed
        else:
            nx, ny = _STATIONARY_FALLBACK_NORMAL
    else:
        nx, ny = dx / dist, dy / dist

    vrel_normal = rvx * nx + rvy * ny

    if vrel_normal > 0:  # still approaching along the contact normal
        inv_mass_sum = 1.0 / a.mass_blob + 1.0 / b.mass_blob
        j = -(1.0 + COLLISION_RESTITUTION) * vrel_normal / inv_mass_sum
        a.vx_in_s += (j / a.mass_blob) * nx
        a.vy_in_s += (j / a.mass_blob) * ny
        b.vx_in_s -= (j / b.mass_blob) * nx
        b.vy_in_s -= (j / b.mass_blob) * ny

    # Positional correction: separate along the normal, mass-weighted, so
    # the pair doesn't stay overlapped on the next step.
    penetration = min_dist - dist
    total_mass = a.mass_blob + b.mass_blob
    a.x_in -= nx * penetration * (b.mass_blob / total_mass)
    a.y_in -= ny * penetration * (b.mass_blob / total_mass)
    b.x_in += nx * penetration * (a.mass_blob / total_mass)
    b.y_in += ny * penetration * (a.mass_blob / total_mass)


@dataclass(frozen=True)
class _CollisionRun:
    """Everything one internal run produced. A detail of this module, not a
    public return type — `simulate_collision` still returns its original
    plain tuple, and `PlanarCollisionPinfallModel` reaches for the replay
    through `_simulate_collision_detail` instead of changing that."""

    fallen_pin_ids: tuple[int, ...]
    steps_taken: int
    # None exactly when no run happened (non-positive speed, or an empty
    # validated rack). A replay of a run that never occurred would be an
    # invented collision; absence says so honestly.
    replay: CollisionReplay | None


def simulate_collision(
    impact: ImpactState, standing_ids: Iterable[int] | None = None
) -> tuple[tuple[int, ...], int]:
    """Runs the fixed-timestep collision simulation for one impact.

    `standing_ids` restricts which pins exist in the simulation at all —
    omit it (or pass None) to simulate the full ten-pin rack, unchanged
    from before this parameter existed. A supplied value is validated via
    `rack.validate_pin_ids` (raising `RackError` for anything malformed,
    unknown, or duplicated) before it's used for anything. Only pins in
    the validated set are materialized as bodies, so any returned fallen
    ID is necessarily a member of it; a validated-empty `standing_ids`
    simulates the ball alone (nothing can fall — there's nothing to fall).

    Returns (fallen_pin_ids, steps_taken): fallen_pin_ids is a tuple of
    unique pin IDs sorted ascending, always a subset of `standing_ids`;
    steps_taken is always <= MAX_COLLISION_STEPS.

    A non-positive `impact.speed_mph` returns `((), 0)` immediately — no
    body is constructed, no geometry is touched, no positional correction
    runs. A stationary ball cannot dislodge a pin it happens to start
    overlapping. Validation of `standing_ids` happens first, before that
    (or any other) short circuit — an invalid selection raises regardless
    of whether the ball could physically have hit anything.

    This is the stable public entry point and its tuple return shape is
    unchanged. Callers that also want replay frames use
    `_simulate_collision_detail`, which runs the identical solver.
    """
    run = _simulate_collision_detail(impact, standing_ids=standing_ids, record_replay=False)
    return run.fallen_pin_ids, run.steps_taken


def _simulate_collision_detail(
    impact: ImpactState,
    standing_ids: Iterable[int] | None = None,
    *,
    record_replay: bool = False,
) -> _CollisionRun:
    """The actual solver, plus optional passive replay recording.

    `record_replay=False` reproduces `simulate_collision`'s behavior
    exactly. Recording only reads body positions at a fixed step cadence
    and appends immutable frames — it never touches timestep, damping,
    impulse, restitution, the fall threshold, or termination, so both
    modes compute byte-identical fallen IDs and step counts.
    """
    # None keeps the pre-selection default (every pin); anything else is
    # routed through the same validation Rack itself uses, raising the
    # same RackError for a malformed, unknown, or duplicate ID — a
    # caller-supplied {11} can never silently become an empty deck, and
    # {True} or {1.0} can never silently become pin 1. This runs before
    # every other check in this function, including the non-positive-speed
    # short circuit below: an invalid selection is invalid regardless of
    # whether the ball could physically hit anything.
    standing_set = ALL_PIN_IDS if standing_ids is None else validate_pin_ids(standing_ids)

    # Both no-run cases return `replay=None` rather than a single-frame
    # replay: no collision happened, and fabricating bodies for one would
    # be inventing a scene the solver never simulated. Validation above
    # still runs first, so an invalid rack raises before either.
    if impact.speed_mph <= 0.0:
        return _CollisionRun(fallen_pin_ids=(), steps_taken=0, replay=None)
    if not standing_set:
        # nothing to fall — a valid no-op, not an error
        return _CollisionRun(fallen_pin_ids=(), steps_taken=0, replay=None)

    speed_in_s = mph_to_in_per_s(impact.speed_mph)
    heading_rad = math.radians(impact.heading_deg)
    ball_mass_blob = weight_lbf_to_mass_blob(impact.ball_mass_lbs)

    ball = _Body(
        x_in=impact.lateral_position_in,
        y_in=0.0,  # the headpin plane is y=0 in this frame
        vx_in_s=speed_in_s * math.sin(heading_rad),
        vy_in_s=speed_in_s * math.cos(heading_rad),
        mass_blob=ball_mass_blob,
        radius_in=impact.ball_radius_in,
        origin_x_in=impact.lateral_position_in,
        origin_y_in=0.0,
        pin_id=0,
    )
    pins = [
        _Body(
            x_in=pin.lateral_in,
            y_in=(pin.distance_ft - HEADPIN_DISTANCE_FT) * IN_PER_FT,
            vx_in_s=0.0,
            vy_in_s=0.0,
            mass_blob=PIN_MASS_BLOB,
            radius_in=PIN_EFFECTIVE_RADIUS_IN,
            origin_x_in=pin.lateral_in,
            origin_y_in=(pin.distance_ft - HEADPIN_DISTANCE_FT) * IN_PER_FT,
            pin_id=pin.id,
        )
        for pin in STANDARD_DECK
        if pin.id in standing_set
    ]
    bodies = [ball] + pins
    damping_factor = max(0.0, 1.0 - LINEAR_DAMPING_PER_S * COLLISION_DT_S)

    recorder = (
        _ReplayRecorder(
            dt_s=COLLISION_DT_S,
            sample_every_steps=REPLAY_SAMPLE_EVERY_STEPS,
            max_frames=MAX_REPLAY_FRAMES,
        )
        if record_replay
        else None
    )
    if recorder is not None:
        # The initial frame: bodies as placed, before any stepping.
        recorder.capture(0, bodies)

    # Set at the exit that actually fires. Initialized to the step cap
    # because that is what running the loop to exhaustion means: reaching
    # the last iteration without the settle condition ever holding. The
    # only way this stays `step_cap` is for `break` never to be taken.
    #
    # Note this is genuinely about the *predicate*, not the step count. The
    # settle check below runs after every step including the last permitted
    # one, so a run that crosses the threshold exactly on step
    # MAX_COLLISION_STEPS records `settled` even though it used every
    # iteration. `steps_taken == MAX_COLLISION_STEPS` therefore does not
    # imply `step_cap` — see "The reason is not a function of steps_taken"
    # in replay.py.
    #
    # Deliberately not derived afterwards from `steps_taken`, frame count,
    # or terminal positions — those are consequences of the exit, not the
    # exit itself, and inferring backwards from them is exactly the kind of
    # plausible-but-unfounded claim this field exists to remove.
    termination_reason: TerminationReason = TERMINATION_STEP_CAP

    steps_taken = 0
    for step in range(MAX_COLLISION_STEPS):
        steps_taken = step + 1

        for body in bodies:
            body.x_in += body.vx_in_s * COLLISION_DT_S
            body.y_in += body.vy_in_s * COLLISION_DT_S
            body.vx_in_s *= damping_factor
            body.vy_in_s *= damping_factor

        for i in range(len(bodies)):
            for j in range(i + 1, len(bodies)):
                _resolve_pair(bodies[i], bodies[j])

        for pin in pins:
            if not pin.fell and pin.displacement_in() >= FALL_DISPLACEMENT_THRESHOLD_IN:
                pin.fell = True
                # The same decision, timestamped. Recording is observation
                # only: this appends to the recorder and touches no body,
                # impulse, damping, termination, score, or rack, so a
                # recorded and an unrecorded run stay byte-identical in
                # everything except the presence of the replay itself.
                if recorder is not None:
                    recorder.record_threshold_crossing(pin.pin_id, steps_taken)

        # Read-only sampling, after this step's physics has fully resolved
        # (moves, impulses, and positional corrections all applied).
        # Nothing below influences the loop's own state or termination.
        if recorder is not None and recorder.should_capture(steps_taken):
            recorder.capture(steps_taken, bodies)

        if all(body.speed_in_s() < SETTLE_SPEED_IN_S for body in bodies):
            termination_reason = TERMINATION_SETTLED
            break

    fallen_ids = tuple(sorted(pin.pin_id for pin in pins if pin.fell))
    replay = (
        recorder.finish(steps_taken, bodies, termination_reason) if recorder is not None else None
    )
    return _CollisionRun(fallen_pin_ids=fallen_ids, steps_taken=steps_taken, replay=replay)


class PlanarCollisionPinfallModel(PinfallModel):
    """Deterministic planar ball-and-pin collision model. See module
    docstring for the unit system, official-vs-calibrated inputs, and the
    fixed-timestep termination guarantee."""

    model_id = "planar-collision-2d-v1"
    LIMITATIONS = (
        "Flat 2D circles: no pin height, tilt, rotation, loft, kickbacks, "
        "or string-pinsetter/placement variance. PIN_EFFECTIVE_RADIUS_IN "
        "and the fall-displacement threshold are stated calibration "
        "choices, not measured. An accuracy improvement over the "
        "entry-angle heuristic, not a claim to real pin-deck fidelity."
    )

    def resolve(
        self, impact: ImpactState, *, standing_ids: Iterable[int] | None = None
    ) -> PinfallResult:
        # Validated before the gutter check (or any other short circuit)
        # below: an invalid selection must raise even for an impact that
        # never could have hit a pin anyway. `simulate_collision` also
        # validates independently, since it can be called directly without
        # going through resolve() at all — this isn't relying on that.
        validated_standing_ids = (
            ALL_PIN_IDS if standing_ids is None else validate_pin_ids(standing_ids)
        )

        if abs(impact.lateral_position_in) >= GUTTER_ABS_LATERAL_IN:
            # A gutter ball never reaches the deck, so no run happens and
            # there is nothing to replay — `replay=None`, not an empty or
            # fabricated one.
            return PinfallResult(
                pins_knocked=0,
                model_id=self.model_id,
                limitations=self.LIMITATIONS,
                fallen_pin_ids=(),
                replay=None,
            )

        run = _simulate_collision_detail(
            impact, standing_ids=validated_standing_ids, record_replay=True
        )
        return PinfallResult(
            pins_knocked=len(run.fallen_pin_ids),
            model_id=self.model_id,
            limitations=self.LIMITATIONS,
            fallen_pin_ids=run.fallen_pin_ids,
            replay=run.replay,
        )


DEFAULT_PINFALL_MODEL: PinfallModel = PlanarCollisionPinfallModel()
