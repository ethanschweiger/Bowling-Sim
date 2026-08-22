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


def impact_state_from_result(result: SimulationResult, ball: Ball) -> ImpactState:
    """Build the headpin-plane impact state a completed throw produced.

    `result.entry_board`/`entry_angle_deg`/`speed_at_pins_mph` already
    describe the ball at (approximately) the headpin plane — the
    simulation loop runs until it reaches the lane's length, which is the
    same 60 ft the No. 1 pin sits at (see pin_deck.py). This function just
    re-expresses that state in the units/coordinate system a pinfall model
    consumes, and folds in the ball properties a collision model will need
    that a bare trajectory doesn't carry.
    """
    lateral_position_in = boards_to_in(result.entry_board - LANE_CENTER_BOARD)
    return ImpactState(
        lateral_position_in=lateral_position_in,
        heading_deg=result.entry_angle_deg,
        speed_mph=result.speed_at_pins_mph,
        ball_mass_lbs=ball.mass_lbs,
        ball_radius_in=ball.radius_in,
        lane_condition_version=result.lane_condition_version,
    )
