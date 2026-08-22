"""Impact construction: turning a completed trajectory into the ball's
state at the headpin plane.

`ImpactState` is the domain boundary a future pin-collision model will
consume. It's built once, here, from a `SimulationResult` and the `Ball`
that produced it — never inside an HTTP route handler, and never rebuilt
by a pinfall model from raw trajectory fields. Keeping this construction
separate from pin-deck geometry and from pinfall resolution means a real
collision solver can replace `pinfall.py`'s heuristic later without this
module, or `pin_deck.py`, changing at all.
"""

from dataclasses import dataclass

from app.physics.ball import Ball
from app.physics.pin_deck import LANE_CENTER_BOARD
from app.physics.simulate import SimulationResult
from app.physics.units import boards_to_in


@dataclass(frozen=True)
class ImpactState:
    """The ball's state at the headpin plane. Everything here is derived —
    nothing runs new physics, and nothing here reads or writes shared
    state.
    """

    lateral_position_in: float  # inches from lane center; + toward higher board numbers
    heading_deg: float          # angle off straight-ahead; same sign convention as entry_angle_deg
    speed_mph: float
    ball_mass_lbs: float
    ball_radius_in: float
    lane_condition_version: int


class TruncatedTrajectoryError(Exception):
    """Raised when a trajectory that never reached the headpin plane is
    handed to impact construction.

    A run can stop short of the lane's stated length by exhausting the
    integration-step cap or by decelerating below the simulator's minimum
    forward speed. Either way the ball is not at 60 ft, so its final
    lateral position is not an *entry* position, and letting the collision
    model start from it would silently score a throw that never arrived.
    Every legal release reaches the pin deck today; this exists so a
    future change to integration precision or drag can't quietly
    reintroduce a truncated route (see `simulate.step_cap_for`).
    """


def impact_state_from_result(result: SimulationResult, ball: Ball) -> ImpactState:
    """Build the headpin-plane impact state a completed throw produced.

    Derived from `result.terminal` — the one unrounded endpoint the
    simulation finished in — not from the rounded `entry_board`/
    `entry_angle_deg`/`speed_at_pins_mph` presentation fields. Those are
    rounded views of this same state; reading them here would make the
    collision model start from a slightly different place than the path
    the bowler was shown ends at. This function runs no new physics: it
    re-expresses that endpoint in the units/coordinate system a pinfall
    model consumes, and folds in the ball properties a bare trajectory
    doesn't carry.

    Raises `TruncatedTrajectoryError` if the trajectory never reached the
    headpin plane.
    """
    terminal = result.terminal
    if not terminal.reached_pin_deck:
        raise TruncatedTrajectoryError(
            f"trajectory stopped at {terminal.distance_ft:.3f} ft, short of the headpin plane; "
            "it has no entry state and must not be resolved as pinfall"
        )

    lateral_position_in = boards_to_in(terminal.board - LANE_CENTER_BOARD)
    return ImpactState(
        lateral_position_in=lateral_position_in,
        heading_deg=terminal.heading_deg,
        speed_mph=terminal.speed_mph,
        ball_mass_lbs=ball.mass_lbs,
        ball_radius_in=ball.radius_in,
        lane_condition_version=result.lane_condition_version,
    )
