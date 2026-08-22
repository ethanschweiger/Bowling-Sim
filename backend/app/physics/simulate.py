"""The simplified throw simulator.

velocity -> friction -> angular velocity -> hook potential -> trajectory -> pin impact

We step down the lane in half-foot increments. At each step, friction (read
from the lane's oil map) does two things: it bleeds off forward speed, and
it converts stored rev rate into lateral motion — the hook. On oil, friction
is low and the ball mostly skids straight. Past the oil, friction rises and
the ball "reads the lane" and turns.
"""

import math
from dataclasses import dataclass

from app.physics.ball import Ball
from app.physics.lane import Lane
from app.physics.throw import Throw

STEP_FT = 0.5
FORWARD_DRAG = 0.35          # how hard friction slows the ball down
SPIN_DECAY = 0.15            # how hard friction bleeds off rev rate
HOOK_GAIN = 0.028            # how hard remaining rev rate turns into lateral motion


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


def simulate_throw(ball: Ball, throw: Throw, lane: Lane) -> SimulationResult:
    board = throw.launch_position
    # Positive lateral velocity = drifting toward higher board numbers (left, for a righty).
    lateral_velocity = math.tan(math.radians(throw.launch_angle)) * throw.speed_mph
    forward_velocity = throw.speed_mph
    rev_rate = throw.rev_rate

    # Axis rotation sets which way the ball wants to turn once it grips;
    # axis tilt delays that turn by keeping more of the roll "stored up."
    hook_direction = math.sin(math.radians(throw.axis_rotation))
    tilt_delay = math.cos(math.radians(throw.axis_tilt))

    path = [TrajectoryPoint(distance_ft=0.0, board=board)]
    distance = 0.0

    while distance < lane.length_ft and forward_velocity > 0.5:
        friction = lane.friction_at(distance, board)
        dt = STEP_FT / max(forward_velocity, 1.0)

        forward_velocity -= friction * FORWARD_DRAG * forward_velocity * dt
        rev_rate -= friction * SPIN_DECAY * rev_rate * dt

        hook_force = friction * HOOK_GAIN * rev_rate * ball.hook_potential * tilt_delay
        lateral_velocity += hook_force * hook_direction * dt

        board += lateral_velocity * dt
        board = max(0.0, min(lane.board_count + 1, board))

        distance += STEP_FT
        path.append(TrajectoryPoint(distance_ft=round(distance, 2), board=round(board, 3)))

    entry_angle = math.degrees(math.atan2(lateral_velocity, max(forward_velocity, 0.1)))

    return SimulationResult(
        path=path,
        entry_board=round(board, 2),
        entry_angle_deg=round(entry_angle, 2),
        speed_at_pins_mph=round(forward_velocity, 2),
    )
