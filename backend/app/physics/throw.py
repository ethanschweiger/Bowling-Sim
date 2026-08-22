"""What the bowler controls at the foul line."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Throw:
    speed_mph: float = 17.0        # ball speed off the hand
    rev_rate: float = 350.0        # revolutions per minute
    axis_rotation: float = 45.0    # degrees; 0 = full roll, 90 = full spinner
    axis_tilt: float = 15.0        # degrees; higher tilt = more skid, later hook
    launch_angle: float = 2.0      # degrees off the lane's centerline at release
    launch_position: float = 28.0  # starting board, 1-39 (right-handers start ~28-30)
