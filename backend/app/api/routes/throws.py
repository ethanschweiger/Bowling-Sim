"""POST /api/v1/simulations/throws — DEPRECATED. Prefer /api/v1/games.

Kept temporarily for backward compatibility. Every call here shares ONE
game session (id `LEGACY_GAME_ID`, lazily created on first use) — there is
no per-caller isolation, so two unrelated clients hitting this route wear
the same lane, share the same scorecard, and see the same rack. It does
not own a separate hidden lane, scorecard, or rack of its own: it
delegates to the same `GameService`/`GameSession.throw` transaction the
game-scoped routes use, just against one well-known, fixed game_id
instead of a caller-chosen one.

New clients should use `POST /api/v1/games` to get their own game, then
`POST /api/v1/games/{game_id}/throws` — see `app/api/routes/games.py`.
"""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from app.api.routes.games import snapshot_to_game_state, truncated_trajectory_http_error
from app.games.service import GameCompleteError, default_game_service
from app.models.schemas import (
    PinfallInfo,
    ReleaseValues,
    ThrowRequest,
    ThrowResponse,
    TrajectoryPointResponse,
)
from app.physics.ball import BALL_CATALOG
from app.physics.collision import DEFAULT_PINFALL_MODEL
from app.physics.impact import TruncatedTrajectoryError, impact_state_from_result
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

    try:
        result, pinfall, snapshot = session.throw(
            simulate=lambda condition: simulate_throw(ball, actual_throw, condition),
            resolve_pinfall=lambda sim_result, standing_ids: DEFAULT_PINFALL_MODEL.resolve(
                impact_state_from_result(sim_result, ball), standing_ids=standing_ids
            ),
        )
    except GameCompleteError:
        # The shared legacy game finished its game; reset it via
        # POST /api/v1/games/legacy-default/reset (it's a real game_id
        # like any other) to keep using this deprecated route.
        raise HTTPException(
            status_code=409, detail="the shared legacy game is already complete"
        ) from None
    except TruncatedTrajectoryError:
        raise truncated_trajectory_http_error() from None

    return ThrowResponse(
        seed=seed,
        actual_release=ReleaseValues(**asdict(actual_throw)),
        path=[
            TrajectoryPointResponse(distance_ft=p.distance_ft, board=p.board) for p in result.path
        ],
        entry_board=result.entry_board,
        entry_angle_deg=result.entry_angle_deg,
        speed_at_pins_mph=result.speed_at_pins_mph,
        pins_knocked=pinfall.pins_knocked,
        pinfall=PinfallInfo(
            model_id=pinfall.model_id,
            limitations=pinfall.limitations,
            fallen_pin_ids=list(pinfall.fallen_pin_ids),
        ),
        lane_condition_version=result.lane_condition_version,
        game_state=snapshot_to_game_state(snapshot),
    )
