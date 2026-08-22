"""The simplified throw simulator.

velocity -> friction -> angular velocity -> hook potential -> trajectory -> pin impact

We step down the lane in half-foot increments. At each step, friction (read
from the lane condition's oil grid) does two things: it bleeds off forward
speed, and it converts stored spin into lateral motion — the hook. On oil,
friction is low and the ball mostly skids straight. Past the oil, friction
rises and the ball "reads the lane" and turns.

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
deceleration is `a = mu * g` — mass cancels out of `F = ma` because both
the friction force and the object's inertia scale with it. A heavier ball
does not coast further down the lane for that reason alone; where mass
actually matters is momentum transfer at pin impact, which this milestone
doesn't model yet (pin carry is a v2+ concern). See `ball.py` for how the
other five ball properties are used.

This function is pure: it reads a `LaneCondition` snapshot but never
mutates it or any shared state. Wearing the lane in from a completed throw
is a separate step (`app.physics.lane.apply_wear`), applied atomically
alongside this call by `LaneSession.run_throw`.
"""

import math
from dataclasses import dataclass
from typing import Optional

from app.physics.ball import Ball
from app.physics.lane import LaneCondition
from app.physics.throw import Throw
from app.physics.units import boards_to_ft, fps_to_mph, ft_to_boards, mph_to_fps, rpm_to_rad_per_s

STEP_FT = 0.5
FORWARD_DRAG = 0.35           # empirical: how hard friction slows the ball down, per second
SPIN_DECAY = 0.55             # empirical: how hard friction bleeds off spin, per second
HOOK_GAIN = 0.18               # empirical: ft/s^2 of lateral accel per (friction * rad/s * hook_potential)

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


@dataclass(frozen=True)
class TrajectoryPoint:
    distance_ft: float
    board: float


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
    step_ft: Optional[float] = None,
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
    lateral_velocity_fps = math.tan(math.radians(throw.launch_angle)) * forward_velocity_fps
    angular_velocity_rad_s = rpm_to_rad_per_s(throw.rev_rate)

    # Axis rotation sets which way the ball wants to turn once it grips;
    # axis tilt delays that turn by keeping more of the roll "stored up."
    hook_direction = math.sin(math.radians(throw.axis_rotation))
    tilt_delay = math.cos(math.radians(throw.axis_tilt))

    board_lo, board_hi = 0.0, lane_condition.board_count + 1

    def board_from_offset(offset_ft: float) -> float:
        return launch_board + ft_to_boards(offset_ft)

    board = board_from_offset(lateral_offset_ft)
    path = [TrajectoryPoint(distance_ft=0.0, board=round(board, BOARD_DECIMALS))]
    distance = 0.0
    steps = 0
    length_ft = lane_condition.length_ft
    max_steps = step_cap_for(length_ft, active_step_ft)

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

        forward_velocity_fps = max(0.0, forward_velocity_fps - friction * FORWARD_DRAG * forward_velocity_fps * dt)
        angular_velocity_rad_s = max(0.0, angular_velocity_rad_s - friction * SPIN_DECAY * angular_velocity_rad_s * dt)

        hook_force_fps2 = friction * HOOK_GAIN * angular_velocity_rad_s * ball.hook_potential * tilt_delay
        lateral_velocity_fps += hook_force_fps2 * hook_direction * dt

        lateral_offset_ft += lateral_velocity_fps * dt  # length + length — never a raw feet-onto-board add
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
        path.append(
            TrajectoryPoint(
                distance_ft=round(distance, DISTANCE_DECIMALS),
                board=round(board, BOARD_DECIMALS),
            )
        )

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
