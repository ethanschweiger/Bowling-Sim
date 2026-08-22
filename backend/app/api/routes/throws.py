"""POST /api/v1/simulations/throws — run one throw through the physics engine."""

from fastapi import APIRouter, HTTPException

from app.models.schemas import ThrowRequest, ThrowResponse, TrajectoryPointResponse
from app.physics.ball import BALL_CATALOG
from app.physics.lane import OIL_PATTERNS, Lane
from app.physics.scoring import pins_from_entry
from app.physics.simulate import simulate_throw
from app.physics.throw import Throw

router = APIRouter(prefix="/simulations", tags=["simulations"])


@router.post("/throws", response_model=ThrowResponse)
def create_throw(request: ThrowRequest) -> ThrowResponse:
    ball = BALL_CATALOG.get(request.ball_id)
    if ball is None:
        raise HTTPException(status_code=404, detail=f"Unknown ball_id '{request.ball_id}'")

    oil_pattern = OIL_PATTERNS.get(request.oil_pattern)
    if oil_pattern is None:
        raise HTTPException(status_code=404, detail=f"Unknown oil_pattern '{request.oil_pattern}'")

    lane = Lane(oil_pattern=oil_pattern)
    throw = Throw(
        speed_mph=request.speed_mph,
        rev_rate=request.rev_rate,
        axis_rotation=request.axis_rotation,
        axis_tilt=request.axis_tilt,
        launch_angle=request.launch_angle,
        launch_position=request.launch_position,
    )

    result = simulate_throw(ball, throw, lane)
    pins = pins_from_entry(result)

    return ThrowResponse(
        path=[TrajectoryPointResponse(distance_ft=p.distance_ft, board=p.board) for p in result.path],
        entry_board=result.entry_board,
        entry_angle_deg=result.entry_angle_deg,
        speed_at_pins_mph=result.speed_at_pins_mph,
        pins_knocked=pins,
    )
