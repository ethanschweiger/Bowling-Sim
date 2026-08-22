"""Request/response shapes for the REST API."""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.physics.throw import RELEASE_BOUNDS


class ThrowRequest(BaseModel):
    ball_id: str = Field(..., description="Key into the ball catalog, e.g. 'reactive_pearl'")
    seed: Optional[int] = Field(
        None, description="Reuse a seed to reproduce a throw's release exactly. Omit to get a random one back."
    )
    speed_mph: float = Field(17.0, ge=RELEASE_BOUNDS["speed_mph"][0], le=RELEASE_BOUNDS["speed_mph"][1])
    rev_rate: float = Field(350.0, ge=RELEASE_BOUNDS["rev_rate"][0], le=RELEASE_BOUNDS["rev_rate"][1])
    axis_rotation: float = Field(45.0, ge=RELEASE_BOUNDS["axis_rotation"][0], le=RELEASE_BOUNDS["axis_rotation"][1])
    axis_tilt: float = Field(15.0, ge=RELEASE_BOUNDS["axis_tilt"][0], le=RELEASE_BOUNDS["axis_tilt"][1])
    launch_angle: float = Field(0.5, ge=RELEASE_BOUNDS["launch_angle"][0], le=RELEASE_BOUNDS["launch_angle"][1])
    launch_position: float = Field(
        28.0, ge=RELEASE_BOUNDS["launch_position"][0], le=RELEASE_BOUNDS["launch_position"][1]
    )


class ReleaseValues(BaseModel):
    """What actually left the bowler's hand, after sampled release error is
    applied to the requested throw."""

    speed_mph: float
    rev_rate: float
    axis_rotation: float
    axis_tilt: float
    launch_angle: float
    launch_position: float


class TrajectoryPointResponse(BaseModel):
    distance_ft: float
    board: float


class PinfallInfo(BaseModel):
    """Which pinfall model produced `pins_knocked`, and its honest
    limitations — added so swapping in a different pinfall model later is
    a self-describing, visible change, not a silent one."""

    # `model_id` collides with pydantic's own reserved `model_*` attribute
    # namespace (model_dump, model_validate, ...) — it's still a plain data
    # field here, just needs the protected-namespace check turned off.
    model_config = ConfigDict(protected_namespaces=())

    model_id: str
    limitations: str
    # Which individual pins fell, where the model can identify them (empty
    # for a model that can't — see each model's `limitations`). A mutable
    # default needs default_factory, not a bare `[]`, so every response
    # gets its own list rather than one shared across instances.
    fallen_pin_ids: list[int] = Field(default_factory=list)


class FrameStateResponse(BaseModel):
    """One frame's state, straight off `app.scoring.scorecard.Frame`."""

    number: int
    rolls: list[int]
    is_strike: bool
    is_spare: bool
    is_complete: bool
    score: Optional[int]  # cumulative through this frame; None if a bonus it needs hasn't landed yet


class GameStateResponse(BaseModel):
    """A concise, self-contained snapshot of a game's scorecard and rack —
    everything needed to render a scoreboard or decide what the next
    throw should look like, without re-deriving any ten-pin rule
    client-side."""

    standing_pin_ids: list[int]
    frames: list[FrameStateResponse]
    total_score: Optional[int]  # cumulative through the most recently resolved frame; None if nothing resolved yet
    is_game_complete: bool
    # Both None exactly when is_game_complete is True — there is no next
    # roll. Otherwise the 1-based frame/ball the next legal roll belongs to.
    next_frame_number: Optional[int]
    next_ball_number: Optional[int]


class ThrowResponse(BaseModel):
    seed: int
    actual_release: ReleaseValues
    path: list[TrajectoryPointResponse]
    entry_board: float
    entry_angle_deg: float
    speed_at_pins_mph: float
    pins_knocked: int  # preserved for backward compatibility; see `pinfall` for how it was produced
    pinfall: PinfallInfo
    lane_condition_version: int
    game_state: GameStateResponse


class CreateGameRequest(BaseModel):
    # A plain string field (not just a bare literal in the URL) so a future
    # named-pattern selection or a temperature setting is an additive field
    # here, not a route/contract change. Only "house" exists this milestone.
    oil_pattern: Literal["house"] = Field(
        "house", description="Only 'house' is selectable this milestone."
    )


class CreateGameResponse(BaseModel):
    game_id: str
    lane_condition_version: int
    game_state: GameStateResponse


class GameThrowResponse(ThrowResponse):
    game_id: str


class GameResetResponse(BaseModel):
    game_id: str
    lane_condition_version: int
    game_state: GameStateResponse
