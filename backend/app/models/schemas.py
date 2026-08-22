"""Request/response shapes for the REST API."""

from typing import Literal, Optional

from pydantic import BaseModel, Field

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


class ThrowResponse(BaseModel):
    seed: int
    actual_release: ReleaseValues
    path: list[TrajectoryPointResponse]
    entry_board: float
    entry_angle_deg: float
    speed_at_pins_mph: float
    pins_knocked: int
    lane_condition_version: int


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


class GameThrowResponse(ThrowResponse):
    game_id: str


class GameResetResponse(BaseModel):
    game_id: str
    lane_condition_version: int
