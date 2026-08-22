"""Game-scoped endpoints: create a game, throw within it, reset it.

Each game gets its own lane, scorecard, and rack (see `app.games.service`),
so throws in one game can never advance or change another game's
condition, score, or standing pins — the isolation the old single
process-global lane (`/api/v1/simulations/throws`) couldn't offer.
"""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from app.games.service import GameCompleteError, GameSession, UnknownGameError, default_game_service
from app.models.schemas import (
    CreateGameRequest,
    CreateGameResponse,
    FrameStateResponse,
    GameResetResponse,
    GameStateResponse,
    GameThrowResponse,
    PinfallInfo,
    ReleaseValues,
    ThrowRequest,
    TrajectoryPointResponse,
)
from app.physics.ball import BALL_CATALOG
from app.physics.collision import DEFAULT_PINFALL_MODEL
from app.physics.impact import impact_state_from_result
from app.physics.simulate import simulate_throw
from app.physics.throw import Throw, sample_release

router = APIRouter(prefix="/games", tags=["games"])


def build_game_state(session: GameSession) -> GameStateResponse:
    """A concise snapshot of one game's rack + scorecard. Reads
    `session.snapshot()` once (a single atomic lock acquisition), so a
    throw landing concurrently can't mix an old rack with a newer
    scorecard in the same response. Shared by every route below, and by
    the deprecated legacy route — this is the one place a `GameSession`'s
    state gets turned into `GameStateResponse`, so the shape can't drift
    between endpoints.
    """
    rack, scorecard = session.snapshot()
    next_frame_number, next_ball_number = scorecard.next_roll_position()
    return GameStateResponse(
        standing_pin_ids=sorted(rack.standing_ids),
        frames=[
            FrameStateResponse(
                number=frame.number,
                rolls=list(frame.rolls),
                is_strike=frame.is_strike,
                is_spare=frame.is_spare,
                is_complete=frame.is_complete,
                score=frame.score,
            )
            for frame in scorecard.frames
        ],
        total_score=scorecard.total_score,
        is_game_complete=scorecard.is_game_complete,
        next_frame_number=next_frame_number,
        next_ball_number=next_ball_number,
    )


@router.post("", response_model=CreateGameResponse, status_code=201)
def create_game(request: CreateGameRequest) -> CreateGameResponse:
    session = default_game_service.create_game(oil_pattern=request.oil_pattern)
    return CreateGameResponse(
        game_id=session.game_id,
        lane_condition_version=session.lane.condition.version,
        game_state=build_game_state(session),
    )


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

    # One atomic transaction: reads this game's rack, simulates the
    # trajectory, resolves pinfall against exactly the standing pins,
    # wears the lane in, records the roll in the scorecard, and replaces
    # the rack — all under this game's own lock. Never any other game's
    # state, never a shared process-global one.
    try:
        result, pinfall = session.throw(
            simulate=lambda condition: simulate_throw(ball, actual_throw, condition),
            resolve_pinfall=lambda sim_result, standing_ids: DEFAULT_PINFALL_MODEL.resolve(
                impact_state_from_result(sim_result, ball), standing_ids=standing_ids
            ),
        )
    except GameCompleteError:
        raise HTTPException(status_code=409, detail=f"game '{game_id}' is already complete")

    return GameThrowResponse(
        game_id=game_id,
        seed=seed,
        actual_release=ReleaseValues(**asdict(actual_throw)),
        path=[TrajectoryPointResponse(distance_ft=p.distance_ft, board=p.board) for p in result.path],
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
        game_state=build_game_state(session),
    )


@router.post("/{game_id}/reset", response_model=GameResetResponse)
def reset_game(game_id: str) -> GameResetResponse:
    try:
        session = default_game_service.get_game(game_id)
    except UnknownGameError:
        raise HTTPException(status_code=404, detail=f"Unknown game_id '{game_id}'")

    condition = session.reset()
    return GameResetResponse(
        game_id=game_id,
        lane_condition_version=condition.version,
        game_state=build_game_state(session),
    )
