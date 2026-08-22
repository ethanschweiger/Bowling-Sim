"""Request/response shapes for the REST API."""

from typing import Optional

from pydantic import BaseModel, Field


class ThrowRequest(BaseModel):
    ball_id: str = Field(..., description="Key into the ball catalog, e.g. 'reactive_pearl'")
    seed: Optional[int] = Field(
        None, description="Reuse a seed to reproduce a throw's release exactly. Omit to get a random one back."
    )
    speed_mph: float = Field(17.0, ge=10, le=25)
    rev_rate: float = Field(350.0, ge=0, le=600)
    axis_rotation: float = Field(45.0, ge=0, le=90)
    axis_tilt: float = Field(15.0, ge=0, le=90)
    launch_angle: float = Field(2.0, ge=-10, le=10)
    launch_position: float = Field(28.0, ge=1, le=39)


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
