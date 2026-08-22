"""Game-scoped endpoints: create a game, throw within it, reset it.

Each game gets its own lane (see `app.games.service`), so throws in one
game can never advance or change another game's condition — the isolation
the old single process-global lane (`/api/v1/simulations/throws`) couldn't
offer.
"""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from app.games.service import UnknownGameError, default_game_service
from app.models.schemas import (
    CreateGameRequest,
    CreateGameResponse,
    GameResetResponse,
    GameThrowResponse,
    ReleaseValues,
    ThrowRequest,
    TrajectoryPointResponse,
)
from app.physics.ball import BALL_CATALOG
from app.physics.scoring import pins_from_entry
from app.physics.simulate import simulate_throw
from app.physics.throw import Throw, sample_release

router = APIRouter(prefix="/games", tags=["games"])


@router.post("", response_model=CreateGameResponse, status_code=201)
def create_game(request: CreateGameRequest) -> CreateGameResponse:
    session = default_game_service.create_game(oil_pattern=request.oil_pattern)
    return CreateGameResponse(game_id=session.game_id, lane_condition_version=session.lane.condition.version)


@router.post("/{game_id}/throws", response_model=GameThrowResponse)
def create_game_throw(game_id: str, request: ThrowRequest) -> GameThrowResponse:
    try:
        session = default_game_service.get_game(game_id)
    except UnknownGameError:
        raise HTTPException(status_code=404, detail=f"Unknown game_id '{game_id}'")

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

    # Only this game's lane — never any other game's, and never a shared
    # process-global one. run_throw keeps the read/simulate/record atomic.
    result = session.lane.run_throw(lambda condition: simulate_throw(ball, actual_throw, condition))
    pins = pins_from_entry(result)

    return GameThrowResponse(
        game_id=game_id,
        seed=seed,
        actual_release=ReleaseValues(**asdict(actual_throw)),
        path=[TrajectoryPointResponse(distance_ft=p.distance_ft, board=p.board) for p in result.path],
        entry_board=result.entry_board,
        entry_angle_deg=result.entry_angle_deg,
        speed_at_pins_mph=result.speed_at_pins_mph,
        pins_knocked=pins,
        lane_condition_version=result.lane_condition_version,
    )


@router.post("/{game_id}/reset", response_model=GameResetResponse)
def reset_game(game_id: str) -> GameResetResponse:
    try:
        session = default_game_service.get_game(game_id)
    except UnknownGameError:
        raise HTTPException(status_code=404, detail=f"Unknown game_id '{game_id}'")

    condition = session.reset()
    return GameResetResponse(game_id=game_id, lane_condition_version=condition.version)
