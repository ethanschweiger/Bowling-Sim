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

# Hard cap on integration steps. At STEP_FT=0.5 and a 60 ft lane this never
# binds in practice; it exists so a pathological input can't loop forever
# and every quantity in the simulation stays bounded.
MAX_STEPS = 400


@dataclass(frozen=True)
class TrajectoryPoint:
    distance_ft: float
    board: float


@dataclass(frozen=True)
class SimulationResult:
    path: list[TrajectoryPoint]
    entry_board: float
    entry_angle_deg: float
    speed_at_pins_mph: float
    lane_condition_version: int


def simulate_throw(ball: Ball, throw: Throw, lane_condition: LaneCondition) -> SimulationResult:
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
    path = [TrajectoryPoint(distance_ft=0.0, board=board)]
    distance = 0.0
    steps = 0

    while distance < lane_condition.length_ft and forward_velocity_fps > MIN_FORWARD_FPS and steps < MAX_STEPS:
        friction = lane_condition.friction_at(distance, board)
        dt = STEP_FT / forward_velocity_fps  # ft / (ft/s) = s — consistent units throughout

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

        distance += STEP_FT
        steps += 1
        path.append(TrajectoryPoint(distance_ft=round(distance, 2), board=round(board, 3)))

    entry_angle = math.degrees(math.atan2(lateral_velocity_fps, max(forward_velocity_fps, 1e-6)))

    return SimulationResult(
        path=path,
        entry_board=round(board, 2),
        entry_angle_deg=round(entry_angle, 2),
        speed_at_pins_mph=round(fps_to_mph(forward_velocity_fps), 2),
        lane_condition_version=lane_condition.version,
    )
