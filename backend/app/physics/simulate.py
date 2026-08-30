"""The simplified throw simulator.

release -> side slip -> friction engagement -> skid / hook / roll -> pin impact

## The phase model

USBC's Bowling Ball Motion Study describes real ball motion in three
phases — **skid, hook, roll** — where the first segment is essentially
straight, the middle one curves, and the last is straight again in the new
direction. This simulator reproduces that ordering from one mechanism
rather than scripting three stages, because a scripted stage boundary is
exactly what produces an unphysical "snap" at the oil line.

The mechanism is **lateral slip**. A ball delivered with axis rotation has
its contact patch moving sideways relative to the lane. Friction acts on
that relative motion, and does two things at once: it pushes the ball
sideways (turning it), and it consumes the slip that was driving the push.
So:

- **Skid** — in the oiled heads friction is low, so very little slip is
  converted per foot. The ball holds close to its release heading.
- **Hook** — as the pattern thins, friction rises, slip converts quickly,
  and the ball turns. This is a curve, not a corner, because friction and
  the remaining slip both vary smoothly.
- **Roll** — once the slip is spent there is nothing left for friction to
  convert, lateral acceleration falls to zero, and the ball continues
  straight along whatever heading the hook left it with.

Nothing switches phases by name or by distance. A ball that never finds
friction never leaves skid; a ball that exhausts its slip early rolls the
rest of the way. The phases are what the integration does, not what it is
told to do.

This is a hand-tuned numerical model, not a rigid-body model of a bowling
ball. Coefficients below are modeling choices, not measurements; see
`docs/simulation.md` for sourced inputs and stated assumptions.

Everything inside the loop runs in one unit system — feet and seconds (see
`app.physics.units`). Speed and spin are converted to ft/s and rad/s once,
at the top; the timestep comes from feet-of-travel divided by feet-per-
second, never feet divided by mph. Lateral position gets the same
treatment: it accumulates as `lateral_offset_ft`, a real length, and is
converted to a board number only when a `TrajectoryPoint` is recorded —
never added to a board index directly. Results convert back to mph and
boards only when they leave this function.

## Coordinate conventions

- **Downlane distance** (`distance_ft`): 0.0 at the foul line, increasing
  toward the pins. The loop runs until this reaches the lane condition's
  length (60 ft) or the ball is effectively stopped.
- **Lateral direction**: positive `lateral_offset_ft` / positive
  `lateral_velocity_fps` means drifting toward *higher* board numbers.
  Board 1 is the right gutter and board 39 the left, in standard USBC
  numbering — so for a right-handed bowler, positive lateral motion is a
  drift to the left, which is the hook direction a right-handed reactive
  release produces.
- **Board numbering**: 1-39 across the lane, plus a small margin (0 and
  40) so a ball that drifts to the edge reads as "in the gutter" rather
  than clipping exactly at 1 or 39.
- **Release angle** (`throw.launch_angle`): degrees off the lane's
  centerline at release. Positive means aimed toward higher board numbers,
  same sign convention as lateral position.
- **Entry angle** (`result.entry_angle_deg`): the angle between the ball's
  path and straight-ahead at the pin deck, same sign convention — positive
  means the ball is still moving toward higher board numbers when it
  arrives.

Ball mass is deliberately not a term here. Under simple Coulomb friction,
deceleration is `a = mu * g`: mass cancels out of `F = ma` because both the
friction force and inertia scale with it. Mass enters the downstream planar
collision model, where it affects momentum transfer to pins.

This function is pure: it reads a `LaneCondition` snapshot but never
mutates it or any shared state. Wearing the lane in from a completed throw
is a separate step (`app.physics.lane.apply_wear`), applied atomically
alongside this call by `LaneSession.run_throw`.
"""

# Keeps `X | None` usable on this project's Python 3.9 floor — see
# app/physics/throw.py's module docstring for the full explanation.
from __future__ import annotations

import math
from dataclasses import dataclass

from app.physics.ball import Ball
from app.physics.lane import DRY_FRICTION, LaneCondition
from app.physics.throw import Throw
from app.physics.units import boards_to_ft, fps_to_mph, ft_to_boards, mph_to_fps, rpm_to_rad_per_s

# Integration stride. Fine enough that the trajectory is a numerical
# result rather than a chain of coarse visual segments; the returned path
# is sampled separately (PATH_SAMPLE_FT) so this can be refined without
# inflating the response.
STEP_FT = 0.05

# How far apart the *returned* path samples are. Independent of the
# integration stride on purpose: precision and payload are different
# concerns, and tying them together forces a choice between a smooth model
# and a small response.
PATH_SAMPLE_FT = 0.5

FORWARD_DRAG = 0.35           # empirical: how hard friction slows the ball down, per second

# --- Lateral slip model -------------------------------------------------
# See the module docstring. All four are chosen modeling coefficients, not
# measurements; they are tuned so a reactive ball on the bundled house shot
# skids through the heads, turns over in the midlane, and rolls out before
# the deck.

# Fraction of the contact patch's rotational speed that acts as usable
# sideways slip. A real ball's axis geometry, track flare, and coverstock
# absorb most of it; this stands in for all of that at once. Because the
# slip reservoir is spent by the impulse it produces (below), this
# effectively sets *how many boards of hook* a release is worth.
SLIP_EFFICIENCY = 0.17

# Peak lateral acceleration on a fully dry lane, as a fraction of g. This
# sets how *fast* slip converts — the hook's sharpness and where it
# finishes — not how much total turn is available.
LATERAL_TRACTION = 0.24

# Traction falls off faster than the nominal friction coefficient does,
# because an oil film carries part of the load rather than merely
# lubricating it: the ball is closer to hydroplaning than to sliding.
# Squaring the friction ratio turns the pattern's 5.3x oiled/dry
# coefficient spread into roughly a 28x traction spread, which is what
# separates a genuine skid phase from a gentle continuous curve.
TRACTION_FRICTION_EXPONENT = 2.0

# Axis tilt scales conversion *rate*, never the reservoir: at maximum tilt
# slip converts at (1 - TILT_DELAY) of its rate, so the ball skids longer
# and the backend is more gradual — but it still has every bit of its slip
# to spend, so it still hooks.
TILT_DELAY = 0.55

# Lane contact must settle before its available side slip can convert at the
# full rate. Higher rotation lengthens that settling distance, which gives the
# release a longer skid before its larger reservoir makes a sharper backend.
# This is a chosen empirical contact-engagement state, not a calculation of
# core-axis migration or lane topography.
BASE_CONTACT_ENGAGEMENT_FT = 1.5
ROTATION_CONTACT_DELAY_FT = 22.0

# --- Track-flare approximation: why a "straight axis" release still reads
# The model uses RG differential to scale a small residual side component,
# inspired by track flare. It does not calculate axis migration, drilling,
# cover tracks, or core dynamics. The residual prevents a nominally end-over-
# end reactive release from becoming an all-or-nothing no-hook special case.
#
# It is deliberately bounded and small: flare *supplements* the release's
# own rotation and never replaces it, so axis rotation stays a continuum
# — low rotation gives an earlier, gentler shape, not an off switch.
FLARE_REFERENCE_DIFFERENTIAL = 0.060  # top of the current ball catalog's range
FLARE_SIDE_FRACTION = 0.22            # residual side fraction at that differential

# Below this remaining slip the ball is rolling: friction has nothing
# sideways left to work on, so lateral acceleration is zero and the path
# continues straight. Expressed in ft/s.
ROLL_SLIP_FPS = 0.02

# Slip scale at which traction approaches its ceiling. Lateral force grows
# with how fast the patch is actually sliding sideways, saturating rather
# than growing without bound — so a release carrying more slip turns
# harder as well as longer. Setting this far below typical slip values
# would flatten that out: `tanh` would sit at 1.0 for every release and
# acceleration would ignore slip magnitude entirely, making every rotation
# above roughly 45 degrees produce the identical trajectory.
SLIP_REFERENCE_FPS = 1.0

# Standard gravity in ft/s^2, for the Coulomb lateral-acceleration term.
GRAVITY_FT_PER_S2 = 32.174

# Below this forward speed the ball is treated as carried by momentum to the
# pins rather than integrated further — avoids a division blowup as speed
# approaches zero, and matches "roughly stopped" at a plausible walking pace.
MIN_FORWARD_FPS = mph_to_fps(0.5)

# Spare steps allowed beyond the exact number a lane length needs, so the
# cap is a runaway guard rather than a silent truncation point.
STEP_CAP_GUARD = 8


def step_cap_for(length_ft: float, step_ft: float) -> int:
    """The integration-step cap for a given lane length and stride.

    Derived, never a fixed guess. A cap tuned for one stride silently
    truncates a finer one: a fixed 400 was ample for a 0.5 ft stride
    (a 60 ft lane needs 120 steps) but a refinement to 0.1 ft needs 600
    and would have stopped at 40 ft — while still being reported as an
    entry result.

    `step_ft` is deliberately REQUIRED. An earlier version defaulted it to
    `STEP_FT`, which Python evaluates once at import: the default captured
    0.5 permanently, so changing the actual stride left the cap stale and
    truncated every run (0.25 ft stopped at 32 ft, 0.1 ft at 12.8 ft).
    Callers must pass the stride the run is actually using.
    """
    if step_ft <= 0:
        raise ValueError(f"step_ft must be positive, got {step_ft}")
    return int(math.ceil(length_ft / step_ft)) + STEP_CAP_GUARD


def pin_deck_tolerance_for(step_ft: float) -> float:
    """How close to the lane's stated length counts as having reached the
    headpin plane, for the stride a run is actually using.

    Also resolved per run rather than at import, for the same reason
    `step_cap_for` takes its stride explicitly. Runs now land exactly on
    the lane length via a bounded final partial step, so this only has to
    absorb float accumulation across the additions that got there.
    """
    if step_ft <= 0:
        raise ValueError(f"step_ft must be positive, got {step_ft}")
    return step_ft / 10.0


# Float slack for "is the loop still short of the pin deck". Tiny: the
# final step is shortened to land exactly on the lane length, so this
# guards against re-entering the loop for a residue of a few ULPs.
DISTANCE_EPSILON_FT = 1e-9

# Decimal places the recorded path and the derived entry marker share.
# One precision, applied once to one value (see TerminalState) — not two
# independent roundings of the same quantity.
BOARD_DECIMALS = 3
DISTANCE_DECIMALS = 2

# Same idea for elapsed simulation time: milliseconds-equivalent precision
# on the one accumulated `elapsed_s` value, rounded once when a sample is
# recorded rather than independently at each consumer.
TIME_DECIMALS = 3


@dataclass(frozen=True)
class TrajectoryPoint:
    distance_ft: float
    board: float
    # Real accumulated simulation time (seconds since release) at the
    # moment this sample was recorded — observed from the same `dt` the
    # integration loop already advances by, never a second timing model.
    # Defaulted so existing direct constructions (fixtures, tests built
    # before this field existed) keep working unchanged.
    elapsed_s: float = 0.0


@dataclass(frozen=True)
class TerminalState:
    """The exact, unrounded state the integration finished in — the single
    source of truth for where this throw ended up.

    Everything downstream derives from this one object: the last recorded
    `TrajectoryPoint`, the API's `entry_board`/`entry_angle_deg`/
    `speed_at_pins_mph`, the `ImpactState` the collision model consumes,
    and the Canvas entry marker the browser draws. Before this existed
    they were computed in parallel from the same loop variables at
    different precisions (`round(board, 3)` for the path against
    `round(board, 2)` for `entry_board`), so the picture and the physics
    could disagree by a rounding step. Presentation may round this; it may
    never recompute it.

    `reached_pin_deck` records whether the loop actually got to the lane's
    stated length. A run that stopped early is a truncated route, not an
    entry result, and `impact.impact_state_from_result` refuses it.
    """

    distance_ft: float
    board: float
    heading_deg: float
    speed_mph: float
    reached_pin_deck: bool
    # Unrounded accumulated simulation time at this endpoint — the same
    # canonical source `elapsed_s` on the final recorded `TrajectoryPoint`
    # is a rounded view of. Defaulted for the same backward-compatibility
    # reason as `TrajectoryPoint.elapsed_s`.
    elapsed_s: float = 0.0


@dataclass(frozen=True)
class SimulationResult:
    path: list[TrajectoryPoint]
    entry_board: float
    entry_angle_deg: float
    speed_at_pins_mph: float
    lane_condition_version: int
    # The canonical unrounded endpoint the three fields above are rounded
    # views of. Added alongside them rather than replacing them so the
    # existing JSON contract is untouched.
    terminal: TerminalState


def simulate_throw(
    ball: Ball,
    throw: Throw,
    lane_condition: LaneCondition,
    step_ft: float | None = None,
) -> SimulationResult:
    """Integrate one throw down the lane.

    `step_ft` is the integration stride. Resolved once per run — from the
    argument when given, otherwise from the module's `STEP_FT` *at call
    time* — and then used for the step cap, the loop, the arrival
    tolerance, and the recorded distance alike. Sharing one per-run value
    across all four is the point: when they were allowed to disagree, the
    cap kept a stride the loop was no longer using and every run
    truncated.
    """
    active_step_ft = STEP_FT if step_ft is None else step_ft
    if active_step_ft <= 0:
        raise ValueError(f"step_ft must be positive, got {active_step_ft}")

    launch_board = throw.launch_position
    lateral_offset_ft = 0.0  # a length, not a board number — see module docstring

    forward_velocity_fps = mph_to_fps(throw.speed_mph)
    # Launch angle is the release's persistent initial heading, nothing
    # more. It sets where the ball is pointed leaving the hand; it does not
    # curve anything. All curvature below comes from slip.
    lateral_velocity_fps = math.tan(math.radians(throw.launch_angle)) * forward_velocity_fps

    # The slip reservoir the hook will spend. Two things put a sideways
    # component into the contact patch:
    #
    #   1. Axis rotation — how much of the release's own rotation is
    #      oriented across the direction of travel. A full spinner (90 deg)
    #      contributes all of it, an end-over-end release (0 deg) none.
    #   2. A track-flare-inspired residual, scaled from ball differential.
    #      This empirical term keeps a small side component present even at
    #      zero release rotation; it is not a core-axis-migration model.
    #
    # Because of (2), axis rotation is a bounded continuum rather than an
    # on/off switch: a 0 deg reactive release still reads the lane, just
    # earlier and more gently than a rotated one, and a low-differential
    # plastic ball still barely reads it at all.
    #
    # This is a *reservoir*, not a force multiplier — that distinction is
    # the whole point. More slip means the turn continues further down the
    # lane, not that every foot of lane gets a proportionally harder shove.
    angular_velocity_rad_s = rpm_to_rad_per_s(throw.rev_rate)
    ball_radius_ft = ball.radius_in / 12.0
    rotation_side = math.sin(math.radians(throw.axis_rotation))
    flare_side = FLARE_SIDE_FRACTION * min(1.0, ball.differential / FLARE_REFERENCE_DIFFERENTIAL)
    # Blended so flare fills in what the release axis doesn't supply and the
    # total stays within [flare_side, 1.0] — never pushing past a full
    # spinner's own contribution.
    side_fraction = rotation_side + (1.0 - rotation_side) * flare_side
    lateral_slip_fps = (
        angular_velocity_rad_s
        * ball_radius_ft
        * side_fraction
        * SLIP_EFFICIENCY
        * ball.hook_potential
    )
    # Accumulated dry-equivalent contact length. Oil contributes only its
    # declared traction fraction, so a higher-rotation release carries more
    # slip through the heads before it can convert at full strength.
    contact_exposure_ft = 0.0

    # Axis tilt scales how fast that slip converts, never how much exists.
    # High tilt therefore delays and softens the hook instead of capping it:
    # the ball still has every bit of its slip to spend, it just takes
    # longer to spend it, so the backend is later and more continuous.
    tilt_conversion = 1.0 - TILT_DELAY * math.sin(math.radians(throw.axis_tilt))

    board_lo, board_hi = 0.0, lane_condition.board_count + 1

    def board_from_offset(offset_ft: float) -> float:
        return launch_board + ft_to_boards(offset_ft)

    board = board_from_offset(lateral_offset_ft)
    path = [TrajectoryPoint(distance_ft=0.0, board=round(board, BOARD_DECIMALS), elapsed_s=0.0)]
    distance = 0.0
    elapsed_s = 0.0
    steps = 0
    length_ft = lane_condition.length_ft
    max_steps = step_cap_for(length_ft, active_step_ft)
    # Never sample finer than the integration itself: a sampling interval
    # below the stride would just repeat steps.
    sample_every_ft = max(PATH_SAMPLE_FT, active_step_ft)
    next_sample_ft = sample_every_ft

    while (
        distance < length_ft - DISTANCE_EPSILON_FT
        and forward_velocity_fps > MIN_FORWARD_FPS
        and steps < max_steps
    ):
        # Bounded final partial step: when the lane length is not an exact
        # multiple of the stride, the last step is shortened to land on the
        # headpin plane rather than overshooting past it. Without this a
        # 0.3 ft-style stride would end somewhere near 60 ft instead of at
        # it, and the "canonical endpoint" would be a different place than
        # the pins actually sit.
        remaining_ft = length_ft - distance
        # The epsilon matters: after ~1200 additions of 0.05 the remaining
        # distance is 0.05 plus a few ULPs, so a bare `>=` would call the
        # last full step "not final" and land a hair past the plane rather
        # than exactly on it.
        is_final_step = active_step_ft >= remaining_ft - DISTANCE_EPSILON_FT
        this_step_ft = remaining_ft if is_final_step else active_step_ft

        friction = lane_condition.friction_at(distance, board)
        dt = this_step_ft / forward_velocity_fps  # ft / (ft/s) = s — consistent units throughout
        # The same dt every velocity update below already uses — observed
        # simulation time, not a second clock invented for display.
        elapsed_s += dt

        forward_velocity_fps = max(
            0.0, forward_velocity_fps - friction * FORWARD_DRAG * forward_velocity_fps * dt
        )

        # --- skid / hook / roll, from one mechanism ---------------------
        # Lateral acceleration is Coulomb friction acting on the remaining
        # slip. It is bounded by traction, and it scales with how much slip
        # is left, so it fades to nothing as the ball rolls out. No branch
        # decides which phase this is; the numbers do.
        if lateral_slip_fps > ROLL_SLIP_FPS:
            # Traction rises steeply as the pattern thins (see
            # TRACTION_FRICTION_EXPONENT), and `tanh` tapers it smoothly to
            # zero as the last of the slip goes — a hard `min(...)` would
            # put a corner in the acceleration exactly where the ball stops
            # hooking, which is the one place a corner would be visible.
            grip = (friction / DRY_FRICTION) ** TRACTION_FRICTION_EXPONENT
            contact_exposure_ft += grip * this_step_ft
            engagement_length_ft = BASE_CONTACT_ENGAGEMENT_FT + (
                ROTATION_CONTACT_DELAY_FT * rotation_side
            )
            rotation_conversion = 1.0 - math.exp(-contact_exposure_ft / engagement_length_ft)
            taper = math.tanh(lateral_slip_fps / SLIP_REFERENCE_FPS)
            lateral_accel_fps2 = (
                LATERAL_TRACTION
                * GRAVITY_FT_PER_S2
                * grip
                * taper
                * tilt_conversion
                * rotation_conversion
            )
            candidate_transfer = lateral_accel_fps2 * dt
            # A coarse dry step can ask for more impulse than remains in the
            # reservoir. Spend only what exists, and apply that same amount
            # to lateral velocity, so the self-limiting bound stays exact.
            lateral_transfer = min(candidate_transfer, lateral_slip_fps)
            lateral_velocity_fps += lateral_transfer

            # The slip is spent by exactly the impulse it produced. That
            # single line is what makes the hook self-limiting and bounds
            # the whole turn: total lateral velocity gained over a throw can
            # never exceed the slip the release started with, so a ball
            # cannot accelerate sideways forever the way the old always-on
            # force did.
            lateral_slip_fps -= lateral_transfer
        # else: rolling — no sideways slip left, so no lateral force, and
        # the ball holds whatever heading the hook left it with.

        # length + length — never a raw feet-onto-board add
        lateral_offset_ft += lateral_velocity_fps * dt
        raw_board = board_from_offset(lateral_offset_ft)
        board = max(board_lo, min(board_hi, raw_board))
        if board != raw_board:
            # Clamped to the lane edge — keep the offset consistent with the
            # displayed board so a later step derives the same clamped value
            # instead of silently drifting past it.
            lateral_offset_ft = boards_to_ft(board - launch_board)

        # Snapped, not accumulated, on the final step: the run ends exactly
        # at the lane length instead of a few float ULPs either side of it.
        distance = length_ft if is_final_step else distance + this_step_ft
        steps += 1

        # Sample the returned path on its own interval, not every
        # integration step. The model can be integrated as finely as it
        # needs without the response growing to match. The last step always
        # records, so the final sample is the canonical endpoint itself.
        if is_final_step or distance >= next_sample_ft - DISTANCE_EPSILON_FT:
            path.append(
                TrajectoryPoint(
                    distance_ft=round(distance, DISTANCE_DECIMALS),
                    board=round(board, BOARD_DECIMALS),
                    elapsed_s=round(elapsed_s, TIME_DECIMALS),
                )
            )
            while next_sample_ft <= distance + DISTANCE_EPSILON_FT:
                next_sample_ft += sample_every_ft

    entry_angle = math.degrees(math.atan2(lateral_velocity_fps, max(forward_velocity_fps, 1e-6)))

    # One unrounded endpoint, built once. Every field below is a rounded
    # *view* of it, never a parallel recomputation — so the last path
    # point, the API entry marker, and the collision model's starting
    # state cannot drift apart.
    terminal = TerminalState(
        distance_ft=distance,
        board=board,
        heading_deg=entry_angle,
        speed_mph=fps_to_mph(forward_velocity_fps),
        reached_pin_deck=distance >= length_ft - pin_deck_tolerance_for(active_step_ft),
        elapsed_s=elapsed_s,
    )

    return SimulationResult(
        path=path,
        # Same value, same precision as the final recorded path point —
        # the entry marker the browser draws is that point, not a
        # separately rounded near-miss of it.
        entry_board=round(terminal.board, BOARD_DECIMALS),
        entry_angle_deg=round(terminal.heading_deg, 2),
        speed_at_pins_mph=round(terminal.speed_mph, 2),
        lane_condition_version=lane_condition.version,
        terminal=terminal,
    )
