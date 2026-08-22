"""Request/response shapes for the REST API."""

from pydantic import BaseModel, Field


class ThrowRequest(BaseModel):
    ball_id: str = Field(..., description="Key into the ball catalog, e.g. 'reactive_pearl'")
    oil_pattern: str = Field(..., description="Key into the oil pattern catalog, e.g. 'house'")
    speed_mph: float = Field(17.0, ge=10, le=25)
    rev_rate: float = Field(350.0, ge=0, le=600)
    axis_rotation: float = Field(45.0, ge=0, le=90)
    axis_tilt: float = Field(15.0, ge=0, le=90)
    launch_angle: float = Field(2.0, ge=-10, le=10)
    launch_position: float = Field(28.0, ge=1, le=39)


class TrajectoryPointResponse(BaseModel):
    distance_ft: float
    board: float


class ThrowResponse(BaseModel):
    path: list[TrajectoryPointResponse]
    entry_board: float
    entry_angle_deg: float
    speed_at_pins_mph: float
    pins_knocked: int
