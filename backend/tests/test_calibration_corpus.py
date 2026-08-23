"""A deterministic reference corpus for the planar collision model.

This is a *measurement baseline*, not a calibration claim. Every expectation
below was read off the current solver, not derived from a real pin deck, and
none of it is evidence that the model reproduces real pin carry. Its purpose
is narrow: give the next parameter or termination-policy change a fixed set
of reproducible outcomes to be compared against, so that change can be
argued from a diff rather than from an impression of the animation.

See `backend/docs/planar-collision-calibration.md` for the coordinate and
unit conventions, the constants being characterized, what this corpus does
and does not validate, and how to rerun it.

If a documented value here changes, that is the point: the expectation and
the note must be updated in the same commit, stating which model assumption
moved and why. A silent update would waste the baseline.
"""

# Plain dataclasses only, so deferred annotations are safe here and `X | None`
# stays usable on this project's Python 3.9 floor — the same reasoning as
# app/physics/collision.py. (Pydantic models cannot do this; see
# app/models/schemas.py.)
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.physics.collision import (
    COLLISION_DT_S,
    LINEAR_DAMPING_PER_S,
    MAX_COLLISION_SECONDS,
    MAX_COLLISION_STEPS,
    SETTLE_SPEED_IN_S,
    _simulate_collision_detail,
    simulate_collision,
)
from app.physics.impact import ImpactState
from app.physics.pin_deck import ALL_PIN_IDS
from app.physics.replay import (
    BALL_BODY_ID,
    MAX_REPLAY_FRAMES,
    REPLAY_MODEL_VERSION,
    REPLAY_SAMPLE_EVERY_STEPS,
    TERMINATION_REASONS,
)
from app.physics.units import mph_to_in_per_s

# Held fixed across the whole corpus so a case differs from its neighbours
# only in the release variables under study. A 15 lb ball of radius 4.29 in
# is the same reference ball the rest of the collision tests use.
CORPUS_BALL_MASS_LBS = 15.0
CORPUS_BALL_RADIUS_IN = 4.29
CORPUS_LANE_CONDITION_VERSION = 1


def _terminal_step_settle_speed_mph() -> float:
    """The release speed whose damping curve crosses `SETTLE_SPEED_IN_S`
    exactly on the last permitted step — the third termination category.

    Derived from the solver's constants rather than searched for, so it
    stays correct if damping, timestep, threshold, or cap change. A
    contact-free body's speed after `n` steps is `v0 * damping_factor**n`,
    so landing the crossing on step `MAX_COLLISION_STEPS` and not earlier
    requires

        v0 * damping_factor**(cap - 1) >= SETTLE_SPEED_IN_S
        v0 * damping_factor**cap        <  SETTLE_SPEED_IN_S

    an interval rather than a point. Its midpoint sits clear of both edges,
    where float rounding could shift the crossing a step either way. With
    today's constants this evaluates to 0.3132900502327218 mph.
    """
    damping_factor = 1.0 - LINEAR_DAMPING_PER_S * COLLISION_DT_S
    still_moving_at = SETTLE_SPEED_IN_S / damping_factor ** (MAX_COLLISION_STEPS - 1)
    settled_at = SETTLE_SPEED_IN_S / damping_factor**MAX_COLLISION_STEPS
    return ((still_moving_at + settled_at) / 2.0) / mph_to_in_per_s(1.0)


@dataclass(frozen=True)
class CorpusCase:
    """One named reference impact and everything the solver produced for it.

    Inputs and observations live together deliberately: the table below is
    meant to be read as documentation, and a reader should not have to run
    anything to see what a case is or what it currently does.
    """

    name: str
    summary: str

    # --- Inputs, stated in full ---
    lateral_position_in: float  # from lane center, + toward higher boards
    heading_deg: float  # off straight-ahead, same sign convention as entry angle
    speed_mph: float
    # None means the full ten-pin rack. A tuple restricts which pins exist.
    standing_ids: tuple | None = None

    # --- Observed, recorded from this solver ---
    fallen_pin_ids: tuple = ()
    steps_taken: int = 0
    termination_reason: str = ""
    final_t_s: float = 0.0
    frame_count: int = 0

    def impact(self) -> ImpactState:
        return ImpactState(
            lateral_position_in=self.lateral_position_in,
            heading_deg=self.heading_deg,
            speed_mph=self.speed_mph,
            ball_mass_lbs=CORPUS_BALL_MASS_LBS,
            ball_radius_in=CORPUS_BALL_RADIUS_IN,
            lane_condition_version=CORPUS_LANE_CONDITION_VERSION,
        )

    @property
    def requested_rack(self) -> frozenset:
        return ALL_PIN_IDS if self.standing_ids is None else frozenset(self.standing_ids)


# The corpus. Six cases: three full-rack contact shots that span the useful
# range of entry lines, one partial rack, and the two ways a run can settle.
#
# Pin geometry for reading the lines (see pin_deck.STANDARD_DECK): pin 1 sits
# at lateral 0, pin 3 at -6 in, pin 2 at +6 in. Positive lateral is toward
# higher board numbers, so a negative line arrives on the 1-3 side — the
# pocket for a right-handed bowler — and a positive line crosses to the 1-2
# side.
CALIBRATION_CORPUS: tuple = (
    CorpusCase(
        name="pocket",
        summary="Right-hander's 1-3 pocket line at a typical entry speed.",
        lateral_position_in=-2.6,
        heading_deg=1.4,
        speed_mph=17.0,
        fallen_pin_ids=(1, 3, 5, 6, 8, 9, 10),
        steps_taken=4000,
        termination_reason="step_cap",
        final_t_s=2.0,
        frame_count=41,
    ),
    CorpusCase(
        name="light_hit",
        summary="Dead-on the headpin, no lateral offset and no heading.",
        lateral_position_in=0.0,
        heading_deg=0.0,
        speed_mph=17.0,
        fallen_pin_ids=(1, 2, 3, 5, 8, 9, 10),
        steps_taken=4000,
        termination_reason="step_cap",
        final_t_s=2.0,
        frame_count=41,
    ),
    CorpusCase(
        name="brooklyn",
        summary="Crossover to the 1-2 side, the opposite pocket.",
        lateral_position_in=3.0,
        heading_deg=-2.0,
        speed_mph=17.0,
        fallen_pin_ids=(1, 2, 4, 5, 7, 8, 9),
        steps_taken=4000,
        termination_reason="step_cap",
        final_t_s=2.0,
        frame_count=41,
    ),
    CorpusCase(
        name="spare_3_6_10",
        summary="Partial rack: a converted 3-6-10 spare, right-side pins only.",
        lateral_position_in=-8.0,
        heading_deg=-2.0,
        speed_mph=16.0,
        standing_ids=(3, 6, 10),
        fallen_pin_ids=(3, 6, 10),
        steps_taken=4000,
        termination_reason="step_cap",
        final_t_s=2.0,
        frame_count=41,
    ),
    CorpusCase(
        name="low_energy_settle",
        summary=(
            "Contact-free: too slow to reach a pin, so damping alone carries "
            "every body under the settle threshold well before the cap."
        ),
        lateral_position_in=-8.0,
        heading_deg=0.0,
        speed_mph=0.05,
        fallen_pin_ids=(),
        steps_taken=942,
        termination_reason="settled",
        final_t_s=0.471,
        frame_count=11,
    ),
    CorpusCase(
        name="terminal_settle",
        summary=(
            "Contact-free, placed off the rack, at the derived speed whose "
            "threshold crossing lands on the last permitted step — settled "
            "*at* the cap, not because of it."
        ),
        lateral_position_in=-30.0,
        heading_deg=0.0,
        speed_mph=_terminal_step_settle_speed_mph(),
        fallen_pin_ids=(),
        steps_taken=4000,
        termination_reason="settled",
        final_t_s=2.0,
        frame_count=41,
    ),
)

CORPUS_IDS = [case.name for case in CALIBRATION_CORPUS]


def _run(case: CorpusCase):
    return _simulate_collision_detail(
        case.impact(), standing_ids=case.standing_ids, record_replay=True
    )


# --- The table itself ----------------------------------------------------


@pytest.mark.parametrize("case", CALIBRATION_CORPUS, ids=CORPUS_IDS)
def test_case_still_produces_its_recorded_outcome(case):
    """The baseline assertion. A failure here is not necessarily a bug — it
    means the model moved, and the note plus this table must move with it,
    saying which assumption changed."""
    run = _run(case)

    assert run.fallen_pin_ids == case.fallen_pin_ids
    assert run.steps_taken == case.steps_taken
    assert run.replay.termination_reason == case.termination_reason
    assert len(run.replay.frames) == case.frame_count
    # Frame times are exact multiples of the timestep, but comparing them as
    # floats still deserves a tolerance rather than `==`.
    assert run.replay.frames[-1].t_s == pytest.approx(case.final_t_s)


def test_the_corpus_is_well_formed():
    names = [case.name for case in CALIBRATION_CORPUS]
    assert len(names) == len(set(names)), "case names double as test ids and must be unique"
    assert len(CALIBRATION_CORPUS) >= 6


# --- Determinism ---------------------------------------------------------


@pytest.mark.parametrize("case", CALIBRATION_CORPUS, ids=CORPUS_IDS)
def test_case_is_byte_repeatable(case):
    """Nothing in the solver reads a clock or a random source, so the same
    input must reproduce the same run exactly — otherwise the table above
    would be recording one sample of a distribution."""
    first, second = _run(case), _run(case)

    # Frozen dataclasses all the way down, so this compares every frame,
    # body, and coordinate structurally rather than by summary.
    assert first.replay == second.replay
    assert first.fallen_pin_ids == second.fallen_pin_ids
    assert first.steps_taken == second.steps_taken


@pytest.mark.parametrize("case", CALIBRATION_CORPUS, ids=CORPUS_IDS)
def test_recording_a_case_does_not_change_its_outcome(case):
    """Recording must stay passive observation. If it could perturb the
    solver, every value in this corpus would describe a different collision
    than the one the game actually scores."""
    plain = simulate_collision(case.impact(), standing_ids=case.standing_ids)
    recorded = _run(case)

    assert recorded.fallen_pin_ids == plain[0]
    assert recorded.steps_taken == plain[1]


# --- Invariants that must hold whatever the constants become -------------


@pytest.mark.parametrize("case", CALIBRATION_CORPUS, ids=CORPUS_IDS)
def test_fallen_ids_are_sorted_unique_and_within_the_requested_rack(case):
    run = _run(case)
    fallen = run.fallen_pin_ids

    assert list(fallen) == sorted(fallen)
    assert len(set(fallen)) == len(fallen)
    assert set(fallen) <= case.requested_rack, "a pin that was not standing cannot fall"


@pytest.mark.parametrize("case", CALIBRATION_CORPUS, ids=CORPUS_IDS)
def test_bodies_are_exactly_the_ball_plus_the_requested_rack(case):
    run = _run(case)
    expected_ids = [BALL_BODY_ID] + sorted(case.requested_rack)

    for frame in run.replay.frames:
        assert [body.body_id for body in frame.bodies] == expected_ids


@pytest.mark.parametrize("case", CALIBRATION_CORPUS, ids=CORPUS_IDS)
def test_frames_advance_in_time_and_stay_finite(case):
    replay = _run(case).replay
    times = [frame.t_s for frame in replay.frames]

    assert times[0] == 0.0, "the first frame is the initial placement"
    assert times == sorted(times)
    assert len(set(times)) == len(times), "no duplicate timestamps"

    for frame in replay.frames:
        for body in frame.bodies:
            # NaN fails both comparisons; infinities fail the bound.
            assert abs(body.x_in) < 500.0
            assert abs(body.y_in) < 500.0


@pytest.mark.parametrize("case", CALIBRATION_CORPUS, ids=CORPUS_IDS)
def test_every_run_stays_within_the_declared_bounds(case):
    run = _run(case)
    replay = run.replay

    assert 0 < run.steps_taken <= MAX_COLLISION_STEPS
    assert replay.frames[-1].t_s <= MAX_COLLISION_SECONDS
    assert 0 < len(replay.frames) <= MAX_REPLAY_FRAMES
    assert replay.model_version == REPLAY_MODEL_VERSION
    assert replay.sample_every_steps == REPLAY_SAMPLE_EVERY_STEPS
    assert replay.termination_reason in TERMINATION_REASONS


# --- All three termination categories ------------------------------------
#
# A termination reason names the solver exit that actually fired. It is not
# a statement that a real pin deck finished doing anything: `settled` is
# this planar model's velocity threshold on sliding circles, and `step_cap`
# is a numerical safety stop. Neither observes a pin standing or falling.


def test_the_corpus_covers_all_three_termination_categories():
    observed = set()
    for case in CALIBRATION_CORPUS:
        run = _run(case)
        observed.add(
            (run.replay.termination_reason, run.steps_taken == MAX_COLLISION_STEPS)
        )

    assert ("step_cap", True) in observed, "ordinary contact run that exhausts the loop"
    assert ("settled", False) in observed, "early settle, well before the cap"
    assert ("settled", True) in observed, "threshold crossing on the last permitted step"
    # And the impossible fourth: stopping early can only happen by settling.
    assert ("step_cap", False) not in observed


def test_the_boundary_case_really_straddles_the_final_step():
    """Without this, a constants change could slide the crossing earlier or
    later and `terminal_settle` would quietly become an ordinary early
    settle or an ordinary cap — still passing, but no longer covering the
    category it exists for."""
    case = next(c for c in CALIBRATION_CORPUS if c.name == "terminal_settle")
    damping_factor = 1.0 - LINEAR_DAMPING_PER_S * COLLISION_DT_S
    v0_in_s = mph_to_in_per_s(case.speed_mph)

    assert v0_in_s * damping_factor ** (MAX_COLLISION_STEPS - 1) >= SETTLE_SPEED_IN_S
    assert v0_in_s * damping_factor**MAX_COLLISION_STEPS < SETTLE_SPEED_IN_S


def test_the_contact_free_cases_really_are_contact_free():
    """The two settle cases are only interpretable as pure damping if the
    ball never touches a pin — otherwise an impulse, not the damping curve,
    explains the speed at which they stopped."""
    for name in ("low_energy_settle", "terminal_settle"):
        case = next(c for c in CALIBRATION_CORPUS if c.name == name)
        run = _run(case)

        assert run.fallen_pin_ids == (), name
        # Every pin ends exactly where it started: nothing was nudged, not
        # even below the fall threshold.
        first, last = run.replay.frames[0], run.replay.frames[-1]
        pins_first = {b.body_id: (b.x_in, b.y_in) for b in first.bodies if b.body_id}
        pins_last = {b.body_id: (b.x_in, b.y_in) for b in last.bodies if b.body_id}
        assert pins_first == pins_last, name
