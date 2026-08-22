"""POST /api/v1/simulations/throws — run one throw through the physics engine.

The lane is stateful: this throw reads whatever condition the shared lane
session is currently in, then wears the lane in along the path this throw
actually took. The next request sees the result.
"""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from app.models.schemas import ReleaseValues, ThrowRequest, ThrowResponse, TrajectoryPointResponse
from app.physics.ball import BALL_CATALOG
from app.physics.lane_session import default_session
from app.physics.scoring import pins_from_entry
from app.physics.simulate import simulate_throw
from app.physics.throw import Throw, sample_release

router = APIRouter(prefix="/simulations", tags=["simulations"])


@router.post("/throws", response_model=ThrowResponse)
def create_throw(request: ThrowRequest) -> ThrowResponse:
    ball = BALL_CATALOG.get(request.ball_id)
    if ball is None:
        raise HTTPException(status_code=404, detail=f"Unknown ball_id '{request.ball_id}'")

    requested_throw = Throw(
        speed_mph=request.speed_mph,
        rev_rate=request.rev_rate,
        axis_rotation=request.axis_rotation,
        axis_tilt=request.axis_tilt,
        launch_angle=request.launch_angle,
        launch_position=request.launch_position,
    )
    actual_throw, seed = sample_release(requested_throw, request.seed)

    lane_condition = default_session.condition
    result = simulate_throw(ball, actual_throw, lane_condition)
    pins = pins_from_entry(result)
    default_session.record_throw(result.path)

    return ThrowResponse(
        seed=seed,
        actual_release=ReleaseValues(**asdict(actual_throw)),
        path=[TrajectoryPointResponse(distance_ft=p.distance_ft, board=p.board) for p in result.path],
        entry_board=result.entry_board,
        entry_angle_deg=result.entry_angle_deg,
        speed_at_pins_mph=result.speed_at_pins_mph,
        pins_knocked=pins,
        lane_condition_version=result.lane_condition_version,
    )
