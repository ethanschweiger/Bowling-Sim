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

# A representative right-handed reactive-ball house-shot release, on the
# conventional line: laid down around board 28 (left of the board-20
# centre) and aimed toward *lower* boards — the bowler's right — so the
# ball skids out through the heads, finds friction past the pattern, and
# turns back toward higher boards into the 1-3 pocket.
#
# `launch_angle` is negative for exactly that reason. It is the release's
# persistent initial heading, and on this coordinate system a right-handed
# outside line points at lower board numbers. A positive angle here would
# aim left, the same direction the hook already goes, and run the ball off
# the left edge.
#
# The seed is fixed so the sampled release — and every value derived from
# it — is reproducible across runs and machines. It is a real sample, not
# a hand-picked strike: about a fifth of seeds put this release in the
# pocket, which is the honest spread of the modelled human variance.
DIAGNOSTIC_BALL_ID = "reactive_pearl"
DIAGNOSTIC_SEED = 17
DIAGNOSTIC_REQUEST = Throw(
    speed_mph=17.0,
    rev_rate=350.0,
    axis_rotation=45.0,
    axis_tilt=15.0,
    launch_angle=-1.5,
    launch_position=28.0,
)

# The 1-3 pocket, in boards at the headpin plane. Pin 1 sits on board 20
# (lane centre) and pin 3 is 12 in to its right, so the gap between them
# spans roughly boards 15-20; a pocket entry is conventionally quoted near
# board 17-18.
POCKET_BOARD_RANGE = (16.5, 18.5)


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

    @property
    def breakpoint(self):
        """The recorded sample at the lowest board — where a right-handed
        shot stops going right and starts coming back."""
        return min(self.result.path, key=lambda p: p.board)

    def slope_between(self, from_ft: float, to_ft: float) -> float:
        """Average lateral slope in boards per foot over a span, using the
        recorded samples nearest each end. Positive means moving toward
        higher boards (the bowler's left)."""
        a = min(self.result.path, key=lambda p: abs(p.distance_ft - from_ft))
        b = min(self.result.path, key=lambda p: abs(p.distance_ft - to_ft))
        if a.distance_ft == b.distance_ft:
            raise ValueError(f"span {from_ft}-{to_ft} ft resolved to a single sample")
        return (b.board - a.board) / (b.distance_ft - a.distance_ft)

    def max_deviation_from_straight(self, from_ft: float, to_ft: float) -> float:
        """How far the path bends away from a straight line across a span,
        in boards. Small means that span is essentially linear — the test
        for a genuine skid (or roll) phase as opposed to a continuous arc."""
        span = [p for p in self.result.path if from_ft <= p.distance_ft <= to_ft]
        if len(span) < 3:
            raise ValueError(f"span {from_ft}-{to_ft} ft has too few samples to assess")
        first, last = span[0], span[-1]
        slope = (last.board - first.board) / (last.distance_ft - first.distance_ft)
        return max(
            abs(p.board - (first.board + slope * (p.distance_ft - first.distance_ft))) for p in span
        )

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
