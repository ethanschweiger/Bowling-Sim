"""POST /api/v1/simulations/throws — DEPRECATED. Prefer /api/v1/games.

Kept temporarily for backward compatibility. Every call here shares ONE
game session (id `LEGACY_GAME_ID`, lazily created on first use) — there is
no per-caller isolation, so two unrelated clients hitting this route wear
the same lane. It does not own a separate hidden lane of its own: it
delegates to the same `GameService` the game-scoped routes use, just
against one well-known, fixed game_id instead of a caller-chosen one.

New clients should use `POST /api/v1/games` to get their own game, then
`POST /api/v1/games/{game_id}/throws` — see `app/api/routes/games.py`.
"""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from app.games.service import default_game_service
from app.models.schemas import PinfallInfo, ReleaseValues, ThrowRequest, ThrowResponse, TrajectoryPointResponse
from app.physics.ball import BALL_CATALOG
from app.physics.impact import impact_state_from_result
from app.physics.pinfall import DEFAULT_PINFALL_MODEL
from app.physics.simulate import simulate_throw
from app.physics.throw import Throw, sample_release

router = APIRouter(prefix="/simulations", tags=["simulations (deprecated)"])

LEGACY_GAME_ID = "legacy-default"


@router.post("/throws", response_model=ThrowResponse, deprecated=True)
def create_throw(request: ThrowRequest) -> ThrowResponse:
    session = default_game_service.get_or_create(LEGACY_GAME_ID)

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

    result = session.lane.run_throw(lambda condition: simulate_throw(ball, actual_throw, condition))
    impact = impact_state_from_result(result, ball)
    pinfall = DEFAULT_PINFALL_MODEL.resolve(impact)

    return ThrowResponse(
        seed=seed,
        actual_release=ReleaseValues(**asdict(actual_throw)),
        path=[TrajectoryPointResponse(distance_ft=p.distance_ft, board=p.board) for p in result.path],
        entry_board=result.entry_board,
        entry_angle_deg=result.entry_angle_deg,
        speed_at_pins_mph=result.speed_at_pins_mph,
        pins_knocked=pinfall.pins_knocked,
        pinfall=PinfallInfo(model_id=pinfall.model_id, limitations=pinfall.limitations),
        lane_condition_version=result.lane_condition_version,
    )
