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

import math
from dataclasses import dataclass

import pytest

from app.physics import collision
from app.physics.collision import (
    COLLISION_DT_S,
    FALL_DISPLACEMENT_THRESHOLD_IN,
    LINEAR_DAMPING_PER_S,
    MAX_COLLISION_SECONDS,
    MAX_COLLISION_STEPS,
    PIN_EFFECTIVE_RADIUS_IN,
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

# Two circles touch when their centres are closer than the sum of their radii.
# The ball starts on the headpin plane (y = 0) and pin 1 stands at lateral 0,
# so for the headpin specifically the initial centre separation is just
# `abs(lateral_position_in)`, and this sum is the whole contact criterion.
CONTACT_DISTANCE_IN = CORPUS_BALL_RADIUS_IN + PIN_EFFECTIVE_RADIUS_IN  # 6.673 in

# How thin an overlap still counts as a *light* hit rather than a solid one,
# as a fraction of the contact distance so it tracks the geometry rather than
# being a second magic number.
#
# A quarter, chosen for margin rather than tightness. The two real lines in
# this corpus sit far apart on this scale — the light line overlaps by about
# 10% of the contact distance, the pocket line by about 61%, and a dead-on
# line by 100% — so a quarter separates them with room on both sides instead
# of grazing either. A test below checks it actually discriminates, since a
# bound only the intended case can clear is a bound worth doubting.
LIGHT_HIT_MAX_OVERLAP_FRACTION = 0.25


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


# The corpus. Seven cases: four full-rack contact shots spanning the useful
# range of entry lines (pocket, head-on, light, Brooklyn), one partial rack,
# and the two ways a run can settle.
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
        name="head_on",
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
        name="light_hit",
        summary=(
            "A genuinely light (thin) hit: the ball's circle overlaps the "
            "headpin's by only 0.673 in of the 6.673 in that count as "
            "contact at all, so pin 1 is nudged 0.547 in — well under the "
            "2.383 in fall threshold — and is left standing while the ball "
            "carries on into the 3-5-6-7-9."
        ),
        lateral_position_in=-6.0,
        heading_deg=1.4,
        speed_mph=17.0,
        fallen_pin_ids=(3, 5, 6, 7, 9),
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


def _case(name: str) -> CorpusCase:
    return next(c for c in CALIBRATION_CORPUS if c.name == name)


def _pin_displacements(run) -> dict:
    """How far each pin ended from where it started, in inches.

    Read off the recorded replay's first and last frames rather than from
    solver internals — the recorder publishes positions, and nothing here
    needs velocities.
    """
    first = {b.body_id: (b.x_in, b.y_in) for b in run.replay.frames[0].bodies if b.body_id}
    last = {b.body_id: (b.x_in, b.y_in) for b in run.replay.frames[-1].bodies if b.body_id}
    return {
        pin_id: math.hypot(last[pin_id][0] - x0, last[pin_id][1] - y0)
        for pin_id, (x0, y0) in first.items()
    }


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


def test_the_light_hit_is_a_real_near_edge_contact_not_just_a_name():
    """`light_hit` has to be light *geometrically*, not by label.

    Three things make it so: the line is off-centre, the ball's circle still
    overlaps the headpin's (so contact genuinely occurs), and the overlap is
    a thin sliver of the contact distance. The outcome follows — pin 1 is
    moved but not past the fall threshold, so the headpin is left standing,
    which is what a light hit means at the pin deck.
    """
    case = _case("light_hit")
    separation_in = abs(case.lateral_position_in)
    overlap_in = CONTACT_DISTANCE_IN - separation_in

    assert separation_in > 0.0, "a head-on line is not a light hit"
    assert overlap_in > 0.0, "must actually touch the headpin"
    assert overlap_in < CONTACT_DISTANCE_IN * LIGHT_HIT_MAX_OVERLAP_FRACTION, (
        "a thick overlap is a solid hit, not a light one"
    )

    run = _run(case)
    pin_one_displacement = _pin_displacements(run)[1]

    assert pin_one_displacement > 0.0, "contact must actually move the headpin"
    assert pin_one_displacement < FALL_DISPLACEMENT_THRESHOLD_IN
    assert 1 not in run.fallen_pin_ids, "a light hit leaves the headpin standing"


def test_the_light_hit_criterion_actually_discriminates():
    """The overlap bound has to reject the corpus's solid lines, not merely
    admit the light one. Otherwise it would be a bound in name only."""
    overlap_fraction = {
        case.name: (CONTACT_DISTANCE_IN - abs(case.lateral_position_in)) / CONTACT_DISTANCE_IN
        for case in CALIBRATION_CORPUS
    }

    assert overlap_fraction["light_hit"] < LIGHT_HIT_MAX_OVERLAP_FRACTION
    # The pocket line and a dead-on line are both solid by this measure.
    assert overlap_fraction["pocket"] > LIGHT_HIT_MAX_OVERLAP_FRACTION
    assert overlap_fraction["head_on"] == pytest.approx(1.0), "no offset at all"

    # And the outcomes agree with the geometry: the solid lines carry the
    # headpin, the light one does not.
    assert 1 in _run(_case("pocket")).fallen_pin_ids
    assert 1 in _run(_case("head_on")).fallen_pin_ids
    assert 1 not in _run(_case("light_hit")).fallen_pin_ids


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


# --- What this corpus can actually detect -------------------------------
#
# Backs the sensitivity claims in docs/planar-collision-calibration.md. The
# threshold is varied through pytest's `monkeypatch`, which restores the
# module attribute at teardown whether the test passes or fails — the
# production constant is never edited.

# Two boundaries bracket the current 2.383 in threshold, each set by one
# pin's final displacement:
#
#   0.547127 in  light_hit pin 1 -- moved but standing; the LOWER boundary
#   2.383    in  the production threshold
#   2.979317 in  brooklyn  pin 1 -- falls, but only just; the UPPER boundary
#
# Between them no corpus outcome changes. Crossing either reclassifies
# exactly one pin in exactly one case. The lower boundary is read from the
# replay at runtime rather than written down, so it cannot drift from the
# fixture it describes; the upper one is expressed as multipliers of the real
# constant for the same reason.
BROOKLYN_PIN_ONE_DISPLACEMENT_IN = 2.979317
FALL_THRESHOLD_JUST_BELOW = 1.2502
FALL_THRESHOLD_JUST_ABOVE = 1.2503


def _light_hit_pin_one_displacement() -> float:
    """The lower boundary, taken from the authoritative recorded replay."""
    return _pin_displacements(_run(_case("light_hit")))[1]


def _fallen_with_threshold(monkeypatch, threshold_in: float, case: CorpusCase) -> tuple:
    monkeypatch.setattr(collision, "FALL_DISPLACEMENT_THRESHOLD_IN", threshold_in)
    return _simulate_collision_detail(
        case.impact(), standing_ids=case.standing_ids
    ).fallen_pin_ids


def test_brooklyn_is_the_case_nearest_the_current_threshold_from_above():
    """Only Brooklyn sits close to the threshold on the falling side. The
    other struck headpins are not near it, in either direction, so the upper
    boundary is Brooklyn's alone."""
    displacement = _pin_displacements(_run(_case("brooklyn")))[1]

    assert displacement == pytest.approx(BROOKLYN_PIN_ONE_DISPLACEMENT_IN, abs=1e-6)
    # Above the threshold, so it falls -- but only barely.
    assert displacement > FALL_DISPLACEMENT_THRESHOLD_IN
    assert displacement < FALL_DISPLACEMENT_THRESHOLD_IN * FALL_THRESHOLD_JUST_ABOVE
    assert displacement > FALL_DISPLACEMENT_THRESHOLD_IN * FALL_THRESHOLD_JUST_BELOW

    # It is the closest from above: pocket and head-on clear the threshold by
    # a wide margin, and the light hit is below it entirely.
    headpin = {
        name: _pin_displacements(_run(_case(name)))[1]
        for name in ("pocket", "head_on", "light_hit", "brooklyn")
    }
    above = {n: d for n, d in headpin.items() if d >= FALL_DISPLACEMENT_THRESHOLD_IN}
    assert min(above, key=lambda n: above[n]) == "brooklyn"
    assert headpin["light_hit"] < FALL_DISPLACEMENT_THRESHOLD_IN, "below, and stands"
    assert headpin["head_on"] > 2 * FALL_DISPLACEMENT_THRESHOLD_IN, "well above"


def test_raising_the_fall_threshold_past_that_pin_changes_the_outcome(monkeypatch):
    brooklyn = _case("brooklyn")
    base = FALL_DISPLACEMENT_THRESHOLD_IN

    below = _fallen_with_threshold(monkeypatch, base * FALL_THRESHOLD_JUST_BELOW, brooklyn)
    above = _fallen_with_threshold(monkeypatch, base * FALL_THRESHOLD_JUST_ABOVE, brooklyn)

    assert below == brooklyn.fallen_pin_ids, "just under the marginal pin: unchanged"
    assert above == tuple(p for p in brooklyn.fallen_pin_ids if p != 1), (
        "just over it: the headpin alone stops falling"
    )


def _all_fallen_with_threshold(monkeypatch, threshold_in: float) -> dict:
    """Every corpus case's fallen set at one threshold.

    Corpus-wide on purpose. An earlier version of this regression looked only
    at `pocket`, which is above every interesting boundary, and so missed
    that the light hit's headpin crosses one.
    """
    monkeypatch.setattr(collision, "FALL_DISPLACEMENT_THRESHOLD_IN", threshold_in)
    return {
        case.name: _simulate_collision_detail(
            case.impact(), standing_ids=case.standing_ids
        ).fallen_pin_ids
        for case in CALIBRATION_CORPUS
    }


def _baseline_fallen() -> dict:
    return {case.name: case.fallen_pin_ids for case in CALIBRATION_CORPUS}


def test_the_lowest_nonzero_displacement_is_the_light_hit_headpin():
    """The lower boundary is derived from the authoritative replay, not
    hard-coded: it is simply the smallest distance any pin actually moved."""
    moved = sorted(
        displacement
        for case in CALIBRATION_CORPUS
        for displacement in _pin_displacements(_run(case)).values()
        if displacement > 0.0
    )

    assert moved[0] == pytest.approx(_light_hit_pin_one_displacement(), abs=1e-12)
    # And it is the only movement anywhere below the current threshold, which
    # is why exactly one case changes when the threshold crosses it.
    assert len([d for d in moved if d < FALL_DISPLACEMENT_THRESHOLD_IN]) == 1


def test_lowering_the_threshold_changes_nothing_until_it_reaches_that_pin(monkeypatch):
    """The unchanged interval is (light-hit displacement, default] — open at
    the bottom, because the predicate is `>=` and so the boundary itself
    already changes the outcome."""
    boundary = _light_hit_pin_one_displacement()
    baseline = _baseline_fallen()

    for threshold in (FALL_DISPLACEMENT_THRESHOLD_IN, 1.0, 0.6, boundary + 1e-12):
        assert _all_fallen_with_threshold(monkeypatch, threshold) == baseline, threshold


def test_at_and_below_that_pin_exactly_the_light_hit_headpin_starts_falling(monkeypatch):
    boundary = _light_hit_pin_one_displacement()
    baseline = _baseline_fallen()
    light = _case("light_hit")
    expected_light = tuple(sorted(light.fallen_pin_ids + (1,)))

    # Inclusive at the boundary: `displacement >= threshold` is satisfied when
    # the two are equal, so the boundary belongs to the changed side.
    for threshold in (boundary, boundary - 1e-12, 0.1, 1e-9):
        result = _all_fallen_with_threshold(monkeypatch, threshold)

        assert result["light_hit"] == expected_light, threshold
        assert result["light_hit"] == (1, 3, 5, 6, 7, 9), threshold
        # Exactly one case moves: nothing else in the corpus has a pin in
        # this band, and untouched pins sit at exactly zero.
        others = {k: v for k, v in result.items() if k != "light_hit"}
        assert others == {k: v for k, v in baseline.items() if k != "light_hit"}, threshold


def test_a_zero_threshold_is_a_different_predicate_again(monkeypatch):
    """Zero is not just "very small". Displacement is non-negative and the
    fall test is `>=`, so at zero even a pin that was never touched satisfies
    it and every standing pin is reported fallen. That is why the interval
    above is bounded below by a positive number rather than by zero."""
    result = _all_fallen_with_threshold(monkeypatch, 0.0)

    for case in CALIBRATION_CORPUS:
        assert result[case.name] == tuple(sorted(case.requested_rack)), case.name

    # Including the contact-free runs, where nothing moved at all.
    assert result["low_energy_settle"] == tuple(sorted(ALL_PIN_IDS))


def test_the_production_threshold_is_restored_after_those_experiments():
    # monkeypatch undoes each change at teardown; this asserts it rather than
    # trusting it, since a leaked value would silently corrupt every later test.
    assert collision.FALL_DISPLACEMENT_THRESHOLD_IN == FALL_DISPLACEMENT_THRESHOLD_IN
    assert _run(_case("brooklyn")).fallen_pin_ids == _case("brooklyn").fallen_pin_ids


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
