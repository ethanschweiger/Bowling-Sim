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

## Unit system

Everything here runs in inches and seconds, in the same lateral/downlane
coordinate frame as `pin_deck.py` (origin at the No. 1 pin's spot, so the
headpin plane is y=0). `ImpactState.speed_mph` is converted to in/s exactly
once, at the top of `simulate_collision`, via `units.mph_to_in_per_s`.
Mass is in pounds throughout: ball mass from `ImpactState.ball_mass_lbs`,
pin mass from the USBC target weight (`PIN_MASS_LBS`).

## Official USBC inputs vs. calibrated parameters

Official, from `pin_deck.py` (sourced from the equipment specifications
manual): pin mass, 12 in deck spacing and position, and
`COLLISION_RESTITUTION` (the manual's own target coefficient of
restitution for a pin, 0.670 — applied here to every collision, ball-pin
and pin-pin alike, since no separate figure is published for either case).

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

## Fixed timestep, bounded simulation, no randomness

`simulate_collision` steps forward at `COLLISION_DT_S` seconds per step,
for at most `MAX_COLLISION_STEPS` steps, or until every body's speed drops
below `SETTLE_SPEED_IN_S` — whichever comes first. It always terminates in
one of those two ways. Each step: bodies move by velocity * dt, velocities
decay by the (< 1) damping factor, and any pair of circles found
overlapping is resolved by a standard elastic/inelastic impulse along
their contact normal (restitution <= 1, so a collision never adds kinetic
energy), followed by a positional correction that separates them along
that same normal so overlaps don't persist step to step. Nothing here
calls a random-number generator.
"""

import math
from dataclasses import dataclass

from app.physics.impact import ImpactState
from app.physics.pin_deck import (
    GUTTER_ABS_LATERAL_IN,
    HEADPIN_DISTANCE_FT,
    STANDARD_DECK,
    USBC_PIN_COEFFICIENT_OF_RESTITUTION,
    USBC_PIN_MAX_DIAMETER_IN,
    USBC_PIN_WEIGHT_OZ,
)
from app.physics.pinfall import PinfallModel, PinfallResult
from app.physics.units import IN_PER_FT, mph_to_in_per_s

COLLISION_DT_S = 0.0005
MAX_COLLISION_SECONDS = 2.0
MAX_COLLISION_STEPS = int(MAX_COLLISION_SECONDS / COLLISION_DT_S)  # 4000
SETTLE_SPEED_IN_S = 0.5  # every body below this speed counts as "settled"
LINEAR_DAMPING_PER_S = 1.2

OZ_PER_LB = 16.0
PIN_MASS_LBS = USBC_PIN_WEIGHT_OZ[0] / OZ_PER_LB
PIN_EFFECTIVE_RADIUS_IN = USBC_PIN_MAX_DIAMETER_IN[0] / 2.0
COLLISION_RESTITUTION = USBC_PIN_COEFFICIENT_OF_RESTITUTION[0]
FALL_DISPLACEMENT_THRESHOLD_IN = PIN_EFFECTIVE_RADIUS_IN


@dataclass
class _Body:
    """Mutable working state for one circle during a single simulation run.
    Local to `simulate_collision` — never shared or reused across calls."""

    x_in: float
    y_in: float
    vx_in_s: float
    vy_in_s: float
    mass_lbs: float
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
    overlap. No-op otherwise. Pure with respect to everything except a
    and b's own x/y/vx/vy, which it updates in place."""
    dx, dy = b.x_in - a.x_in, b.y_in - a.y_in
    dist = math.hypot(dx, dy)
    min_dist = a.radius_in + b.radius_in
    if dist >= min_dist or dist == 0.0:
        return

    nx, ny = dx / dist, dy / dist
    rvx, rvy = a.vx_in_s - b.vx_in_s, a.vy_in_s - b.vy_in_s
    vrel_normal = rvx * nx + rvy * ny

    if vrel_normal > 0:  # still approaching along the contact normal
        inv_mass_sum = 1.0 / a.mass_lbs + 1.0 / b.mass_lbs
        j = -(1.0 + COLLISION_RESTITUTION) * vrel_normal / inv_mass_sum
        a.vx_in_s += (j / a.mass_lbs) * nx
        a.vy_in_s += (j / a.mass_lbs) * ny
        b.vx_in_s -= (j / b.mass_lbs) * nx
        b.vy_in_s -= (j / b.mass_lbs) * ny

    # Positional correction: separate along the normal, mass-weighted, so
    # the pair doesn't stay overlapped on the next step.
    penetration = min_dist - dist
    total_mass = a.mass_lbs + b.mass_lbs
    a.x_in -= nx * penetration * (b.mass_lbs / total_mass)
    a.y_in -= ny * penetration * (b.mass_lbs / total_mass)
    b.x_in += nx * penetration * (a.mass_lbs / total_mass)
    b.y_in += ny * penetration * (a.mass_lbs / total_mass)


def simulate_collision(impact: ImpactState):
    """Runs the fixed-timestep collision simulation for one impact.

    Returns (fallen_pin_ids, steps_taken): fallen_pin_ids is a tuple of
    unique pin IDs sorted ascending; steps_taken is always
    <= MAX_COLLISION_STEPS.
    """
    speed_in_s = mph_to_in_per_s(impact.speed_mph)
    heading_rad = math.radians(impact.heading_deg)

    ball = _Body(
        x_in=impact.lateral_position_in,
        y_in=0.0,  # the headpin plane is y=0 in this frame
        vx_in_s=speed_in_s * math.sin(heading_rad),
        vy_in_s=speed_in_s * math.cos(heading_rad),
        mass_lbs=impact.ball_mass_lbs,
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
            mass_lbs=PIN_MASS_LBS,
            radius_in=PIN_EFFECTIVE_RADIUS_IN,
            origin_x_in=pin.lateral_in,
            origin_y_in=(pin.distance_ft - HEADPIN_DISTANCE_FT) * IN_PER_FT,
            pin_id=pin.id,
        )
        for pin in STANDARD_DECK
    ]
    bodies = [ball] + pins
    damping_factor = max(0.0, 1.0 - LINEAR_DAMPING_PER_S * COLLISION_DT_S)

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

        if all(body.speed_in_s() < SETTLE_SPEED_IN_S for body in bodies):
            break

    fallen_ids = tuple(sorted(pin.pin_id for pin in pins if pin.fell))
    return fallen_ids, steps_taken


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

    def resolve(self, impact: ImpactState) -> PinfallResult:
        if abs(impact.lateral_position_in) >= GUTTER_ABS_LATERAL_IN:
            return PinfallResult(
                pins_knocked=0, model_id=self.model_id, limitations=self.LIMITATIONS, fallen_pin_ids=()
            )

        fallen_ids, _steps_taken = simulate_collision(impact)
        return PinfallResult(
            pins_knocked=len(fallen_ids),
            model_id=self.model_id,
            limitations=self.LIMITATIONS,
            fallen_pin_ids=fallen_ids,
        )


DEFAULT_PINFALL_MODEL: PinfallModel = PlanarCollisionPinfallModel()
