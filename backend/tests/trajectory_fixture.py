"""One deterministic release, traced end to end.

Codex's trajectory milestone asks for a fixed diagnostic fixture that
follows a single representative throw through every stage — sampled
release, simulated path, headpin-plane terminal state, impact, pinfall —
so the stages can be checked against *each other* rather than each being
spot-checked in isolation. This module is that fixture. It is shared test
support, not production code, and it runs no physics of its own: it only
calls the real pipeline and collects what each stage produced.

Determinism comes from an explicit seed passed to `sample_release`, so the
sampled release (and therefore every downstream value) repeats exactly.
"""

from dataclasses import dataclass
from typing import List, Tuple

from app.physics.ball import BALL_CATALOG
from app.physics.collision import DEFAULT_PINFALL_MODEL
from app.physics.impact import ImpactState, impact_state_from_result
from app.physics.lane import LaneCondition
from app.physics.pin_deck import ALL_PIN_IDS
from app.physics.pinfall import PinfallResult
from app.physics.simulate import SimulationResult, simulate_throw
from app.physics.throw import Throw, sample_release

# A representative right-handed reactive-ball house-shot release. The seed
# is fixed so the sampled release — and every value derived from it — is
# reproducible across runs and machines.
DIAGNOSTIC_BALL_ID = "reactive_pearl"
DIAGNOSTIC_SEED = 4242
DIAGNOSTIC_REQUEST = Throw(
    speed_mph=17.0,
    rev_rate=350.0,
    axis_rotation=45.0,
    axis_tilt=15.0,
    launch_angle=0.5,
    launch_position=28.0,
)


@dataclass(frozen=True)
class TrajectoryTrace:
    """Everything one throw produced, at every stage of the pipeline."""

    requested: Throw
    sampled: Throw
    seed: int
    result: SimulationResult
    impact: ImpactState
    pinfall: PinfallResult

    @property
    def last_path_point(self):
        return self.result.path[-1]

    def sampled_at(self, distance_ft: float) -> float:
        """The recorded board nearest a given downlane distance — for
        reporting a few representative points without assuming a stride."""
        nearest = min(self.result.path, key=lambda p: abs(p.distance_ft - distance_ft))
        return nearest.board

    def selected_points(self, distances: Tuple[float, ...] = (0.0, 20.0, 40.0, 60.0)) -> List[Tuple[float, float]]:
        return [(d, self.sampled_at(d)) for d in distances]


def trace_diagnostic_throw(
    ball_id: str = DIAGNOSTIC_BALL_ID,
    requested: Throw = DIAGNOSTIC_REQUEST,
    seed: int = DIAGNOSTIC_SEED,
    lane: LaneCondition = None,
) -> TrajectoryTrace:
    """Run the real pipeline once and collect each stage's output."""
    ball = BALL_CATALOG[ball_id]
    lane_condition = lane if lane is not None else LaneCondition.house_shot()

    sampled, used_seed = sample_release(requested, seed)
    result = simulate_throw(ball, sampled, lane_condition)
    impact = impact_state_from_result(result, ball)
    pinfall = DEFAULT_PINFALL_MODEL.resolve(impact, standing_ids=ALL_PIN_IDS)

    return TrajectoryTrace(
        requested=requested,
        sampled=sampled,
        seed=used_seed,
        result=result,
        impact=impact,
        pinfall=pinfall,
    )
