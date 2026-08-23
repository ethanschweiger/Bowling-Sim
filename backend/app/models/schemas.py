"""Request/response shapes for the REST API."""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.physics.throw import RELEASE_BOUNDS

# Pydantic resolves a field's annotation at class-creation time regardless
# of `from __future__ import annotations`, so a nullable int field here
# needs `typing.Optional`, not the newer `X | None` spelling -- that needs
# Python 3.10+ to evaluate, and this project's runtime floor is 3.9 (see
# the plain dataclasses elsewhere in this codebase for where the newer
# spelling stays safe once deferred). The accepted Ruff config targets
# py311 though, and keeps preferring `X | None` for literal `Optional[X]`/
# `Union[X, None]` usage regardless of runtime version -- both spellings
# are flagged (UP045/UP007) the same way.
#
# `NullableInt` sidesteps that by calling the exact same method the `[]`
# subscript syntax calls, just not spelled as a subscript: Ruff's pyupgrade
# rules pattern-match the literal `Optional[...]`/`Union[...]` syntax
# shapes, not "is this object equal to Optional[int]". The result is the
# identical object `Optional[int]` would be (`NullableInt == Optional[int]`
# is True; it even reprs as `typing.Optional[int]`) -- not an approximation,
# a different route to the same value. Used only in this module, only for
# the five Pydantic fields that need it; every other Optional-typed
# annotation in the codebase still spells it as plain `Optional[X]` or
# (where deferred annotations apply) `X | None`.
NullableInt = Optional.__getitem__(int)


class ThrowRequest(BaseModel):
    ball_id: str = Field(..., description="Key into the ball catalog, e.g. 'reactive_pearl'")
    seed: NullableInt = Field(
        None,
        description="Reuse a seed to reproduce a throw's release exactly. Omit to get a random one "
        "back.",
    )
    speed_mph: float = Field(
        17.0, ge=RELEASE_BOUNDS["speed_mph"][0], le=RELEASE_BOUNDS["speed_mph"][1]
    )
    rev_rate: float = Field(
        350.0, ge=RELEASE_BOUNDS["rev_rate"][0], le=RELEASE_BOUNDS["rev_rate"][1]
    )
    axis_rotation: float = Field(
        45.0, ge=RELEASE_BOUNDS["axis_rotation"][0], le=RELEASE_BOUNDS["axis_rotation"][1]
    )
    axis_tilt: float = Field(
        15.0, ge=RELEASE_BOUNDS["axis_tilt"][0], le=RELEASE_BOUNDS["axis_tilt"][1]
    )
    launch_angle: float = Field(
        -1.5, ge=RELEASE_BOUNDS["launch_angle"][0], le=RELEASE_BOUNDS["launch_angle"][1]
    )
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


class ReplayBodyResponse(BaseModel):
    """One body's position at one instant of a collision replay.

    Coordinates are inches in the same frame `app/physics/pin_deck.py`
    uses: `x_in` lateral from lane center, positive toward higher board
    numbers (the bowler's left); `y_in` downlane from the headpin plane,
    which is y=0, increasing toward the back of the deck.
    """

    body_id: int  # 0 = ball; otherwise the pin's own 1-10 id
    x_in: float
    y_in: float


class ReplayFrameResponse(BaseModel):
    """All participating bodies at one simulation timestamp.

    `t_s` is seconds of *simulation* time since impact — solver steps times
    the fixed timestep, not wall-clock and not tied to browser paint rate.
    It increases strictly across a replay's frames.
    """

    t_s: float
    bodies: list[ReplayBodyResponse] = Field(default_factory=list)


class CollisionReplayResponse(BaseModel):
    """Bounded, deterministic playback of a planar collision run.

    Present only when a run actually happened. See `PinfallInfo.replay`
    for when it's absent, and `app/physics/replay.py` for the recording
    cadence, frame bound, and coordinate/time conventions.
    """

    model_config = ConfigDict(protected_namespaces=())

    # Names the replay *shape/semantics*, distinct from `PinfallInfo.model_id`
    # which names the model that resolved the pins. Stamped so a future
    # solver can't silently reinterpret data recorded by this one.
    model_version: str
    dt_s: float
    sample_every_steps: int
    steps_taken: int
    frames: list[ReplayFrameResponse] = Field(default_factory=list)


# Same Pydantic/Python-3.9 constraint as `NullableInt` above, for an
# optional nested model rather than an int.
NullableReplay = Optional.__getitem__(CollisionReplayResponse)


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
    # Null whenever no collision run was simulated: the heuristic model
    # (which has no bodies at all), a gutter miss, a non-positive impact
    # speed, or an empty validated rack. Never a fabricated single-frame
    # scene for a collision that didn't happen. Additive — `pins_knocked`
    # and `fallen_pin_ids` are unchanged whether this is present or not.
    replay: NullableReplay = None


class FrameStateResponse(BaseModel):
    """One frame's state, straight off `app.scoring.scorecard.Frame`."""

    number: int
    rolls: list[int]
    is_strike: bool
    is_spare: bool
    is_complete: bool
    score: NullableInt  # cumulative through this frame; None if unresolved


class GameStateResponse(BaseModel):
    """A concise, self-contained snapshot of a game's scorecard and rack —
    everything needed to render a scoreboard or decide what the next
    throw should look like, without re-deriving any ten-pin rule
    client-side."""

    standing_pin_ids: list[int]
    frames: list[FrameStateResponse]
    # cumulative through the most recently resolved frame; None if nothing resolved yet
    total_score: NullableInt
    is_game_complete: bool
    # Both None exactly when is_game_complete is True — there is no next
    # roll. Otherwise the 1-based frame/ball the next legal roll belongs to.
    next_frame_number: NullableInt
    next_ball_number: NullableInt


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


class GameStatusResponse(BaseModel):
    """`GET /api/v1/games/{game_id}` — a read-only snapshot of a game's
    current state. Same `game_state` shape every other game response
    uses, built through the same snapshot-to-schema mapper so the
    contract can't drift between endpoints."""

    game_id: str
    lane_condition_version: int
    game_state: GameStateResponse
