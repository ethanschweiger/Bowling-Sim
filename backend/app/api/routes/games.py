"""Game-scoped endpoints: create a game, throw within it, reset it, read
its current status.

Each game gets its own lane, scorecard, and rack (see `app.games.service`),
so throws in one game can never advance or change another game's
condition, score, or standing pins — the isolation the old single
process-global lane (`/api/v1/simulations/throws`) couldn't offer.
Every response here is rendered from a `GameStateSnapshot` — never from a
live `Scorecard`/`Rack` read after a throw or reset's lock has been
released (see `app.games.service`'s "Durable snapshots").
"""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException

from app.games.service import (
    GameCompleteError,
    GameStateSnapshot,
    UnknownGameError,
    default_game_service,
)
from app.models.schemas import (
    CollisionReplayResponse,
    CreateGameRequest,
    CreateGameResponse,
    FrameStateResponse,
    GameResetResponse,
    GameStateResponse,
    GameStatusResponse,
    GameThrowResponse,
    PinfallInfo,
    ReleaseValues,
    ReplayBodyResponse,
    ReplayFrameResponse,
    ThresholdCrossingResponse,
    ThrowRequest,
    TrajectoryPointResponse,
)
from app.physics.ball import BALL_CATALOG
from app.physics.collision import DEFAULT_PINFALL_MODEL
from app.physics.impact import TruncatedTrajectoryError, impact_state_from_result
from app.physics.pinfall import PinfallResult
from app.physics.simulate import simulate_throw
from app.physics.throw import Throw, sample_release

router = APIRouter(prefix="/games", tags=["games"])

# Stable, internals-free text for a throw whose simulated trajectory never
# reached the pin deck (see `app.physics.impact.TruncatedTrajectoryError`).
# `GameSession.throw` guarantees this is raised before lane wear, rack
# change, or scorecard update — see its own docstring — so the response
# can honestly promise nothing was recorded. Never include the exception's
# own message here: that carries a raw distance/stride value, which is a
# solver internal, not API surface.
TRUNCATED_TRAJECTORY_DETAIL = (
    "the simulated trajectory did not reach the pin deck; this throw was "
    "not recorded and game state is unchanged. Retrying is expected to work."
)


def truncated_trajectory_http_error() -> HTTPException:
    """The one response both throw routes give for `TruncatedTrajectoryError`.

    503, not 4xx: the release itself was valid input (it already passed
    the same `ThrowRequest`/`RELEASE_BOUNDS` validation as any other
    throw) — this is the server's simplified solver failing to complete a
    physical computation, not a malformed request. A 4xx would incorrectly
    tell the caller their input was the problem and retrying with the same
    release won't help; a 503 correctly says the *server* couldn't
    complete this attempt and a retry is reasonable.
    """
    return HTTPException(status_code=503, detail=TRUNCATED_TRAJECTORY_DETAIL)


def pinfall_to_response(pinfall: PinfallResult) -> PinfallInfo:
    """The one place a domain `PinfallResult` turns into the API's
    `PinfallInfo` shape — used by both throw routes (game-scoped and the
    deprecated legacy one), so the serialized contract can't drift between
    them. Pure mapping over already-immutable data.

    A `replay` of `None` stays `None`: no collision was simulated (the
    heuristic model, a gutter miss, a non-positive speed, or an empty
    rack), and inventing frames for one would describe a scene the solver
    never produced.
    """
    replay = getattr(pinfall, "replay", None)
    return PinfallInfo(
        model_id=pinfall.model_id,
        limitations=pinfall.limitations,
        fallen_pin_ids=list(pinfall.fallen_pin_ids),
        replay=(
            None
            if replay is None
            else CollisionReplayResponse(
                model_version=replay.model_version,
                dt_s=replay.dt_s,
                sample_every_steps=replay.sample_every_steps,
                steps_taken=replay.steps_taken,
                frames=[
                    ReplayFrameResponse(
                        t_s=frame.t_s,
                        bodies=[
                            ReplayBodyResponse(body_id=b.body_id, x_in=b.x_in, y_in=b.y_in)
                            for b in frame.bodies
                        ],
                    )
                    for frame in replay.frames
                ],
                # Carried straight through from the solver's own recorded
                # exit. This mapping never re-derives it — if the domain
                # ever failed to set one, the Literal above must reject the
                # response rather than let this layer invent a plausible
                # value.
                termination_reason=replay.termination_reason,
                # Likewise the threshold crossings: copied in recorded order,
                # never re-sorted, re-derived from positions, or filtered
                # against the fallen set. If the two ever disagreed, that is
                # a fact the client should see and reject on, not something
                # this layer should quietly reconcile.
                threshold_crossings=[
                    ThresholdCrossingResponse(pin_id=c.pin_id, step_index=c.step_index)
                    for c in replay.threshold_crossings
                ],
            )
        ),
    )


def snapshot_to_game_state(snapshot: GameStateSnapshot) -> GameStateResponse:
    """The one place a `GameStateSnapshot` turns into the API's
    `GameStateResponse` shape — used by every route below (and by the
    deprecated legacy route), so the contract can't drift between them.
    Pure mapping over already-immutable data; touches no session state.
    """
    return GameStateResponse(
        standing_pin_ids=sorted(snapshot.standing_pin_ids),
        frames=[
            FrameStateResponse(
                number=frame.number,
                rolls=list(frame.rolls),
                is_strike=frame.is_strike,
                is_spare=frame.is_spare,
                is_complete=frame.is_complete,
                score=frame.score,
            )
            for frame in snapshot.frames
        ],
        total_score=snapshot.total_score,
        is_game_complete=snapshot.is_game_complete,
        next_frame_number=snapshot.next_frame_number,
        next_ball_number=snapshot.next_ball_number,
    )


@router.post("", response_model=CreateGameResponse, status_code=201)
def create_game(request: CreateGameRequest) -> CreateGameResponse:
    session = default_game_service.create_game(oil_pattern=request.oil_pattern)
    snapshot = session.current_snapshot()
    return CreateGameResponse(
        game_id=session.game_id,
        lane_condition_version=snapshot.lane_condition_version,
        game_state=snapshot_to_game_state(snapshot),
    )


@router.get("/{game_id}", response_model=GameStatusResponse)
def get_game(game_id: str) -> GameStatusResponse:
    try:
        session = default_game_service.get_game(game_id)
    except UnknownGameError:
        raise HTTPException(status_code=404, detail=f"Unknown game_id '{game_id}'") from None

    snapshot = session.current_snapshot()
    return GameStatusResponse(
        game_id=game_id,
        lane_condition_version=snapshot.lane_condition_version,
        game_state=snapshot_to_game_state(snapshot),
    )


@router.post("/{game_id}/throws", response_model=GameThrowResponse)
def create_game_throw(game_id: str, request: ThrowRequest) -> GameThrowResponse:
    try:
        session = default_game_service.get_game(game_id)
    except UnknownGameError:
        raise HTTPException(status_code=404, detail=f"Unknown game_id '{game_id}'") from None

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
    # wears the lane in, records the roll in the scorecard, replaces the
    # rack, and hands back this throw's own durable snapshot — all under
    # this game's own lock. Never any other game's state, never a shared
    # process-global one, and never a snapshot a later throw could change.
    try:
        result, pinfall, snapshot = session.throw(
            simulate=lambda condition: simulate_throw(ball, actual_throw, condition),
            resolve_pinfall=lambda sim_result, standing_ids: DEFAULT_PINFALL_MODEL.resolve(
                impact_state_from_result(sim_result, ball), standing_ids=standing_ids
            ),
        )
    except GameCompleteError:
        raise HTTPException(
            status_code=409, detail=f"game '{game_id}' is already complete"
        ) from None
    except TruncatedTrajectoryError:
        raise truncated_trajectory_http_error() from None

    return GameThrowResponse(
        game_id=game_id,
        seed=seed,
        actual_release=ReleaseValues(**asdict(actual_throw)),
        path=[
            TrajectoryPointResponse(distance_ft=p.distance_ft, board=p.board) for p in result.path
        ],
        entry_board=result.entry_board,
        entry_angle_deg=result.entry_angle_deg,
        speed_at_pins_mph=result.speed_at_pins_mph,
        pins_knocked=pinfall.pins_knocked,
        pinfall=pinfall_to_response(pinfall),
        lane_condition_version=result.lane_condition_version,
        game_state=snapshot_to_game_state(snapshot),
    )


@router.post("/{game_id}/reset", response_model=GameResetResponse)
def reset_game(game_id: str) -> GameResetResponse:
    try:
        session = default_game_service.get_game(game_id)
    except UnknownGameError:
        raise HTTPException(status_code=404, detail=f"Unknown game_id '{game_id}'") from None

    condition, snapshot = session.reset()
    return GameResetResponse(
        game_id=game_id,
        lane_condition_version=condition.version,
        game_state=snapshot_to_game_state(snapshot),
    )
