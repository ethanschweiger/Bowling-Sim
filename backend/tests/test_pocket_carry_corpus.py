"""A fixed-seed corpus for planar pocket carry, and what it shows.

The question this exists to answer: a legitimate right-handed pocket entry
ends at about seven pins, and we wanted to know whether the existing 2D
collision constants can be calibrated to produce believable *typical* carry.

The answer recorded here is **no**, and the reason is structural rather than
a coefficient. Every numbered claim below is backed by a test in this file
that runs the claim rather than describing it:

1. The pins the *actual seeded representative shot* (seed 17, board 28,
   -1.5 deg) leaves standing (2, 4, 7) have **exactly zero** displacement.
   They are never contacted at all, so they are not marginal calls that a
   threshold tweak could reclassify.
2. `LINEAR_DAMPING_PER_S` therefore has no effect on the outcome across its
   entire plausible range. Extra travel time cannot help a pin that never
   receives an impulse.
3. `FALL_DISPLACEMENT_THRESHOLD_IN` cannot help for the same reason: with
   nothing between zero and the threshold, lowering it reclassifies nothing.
4. `COLLISION_RESTITUTION`, swept across the full USBC-published range
   (0.605-0.735) and materially beyond it (up to 0.95) over the same
   20-line right-handed entry sweep, never lifts the mean above 7 pins and
   never lifts the max above 8. Typical carry does not move. Each named
   control is pinned individually at every swept value too, so no value can
   flatten one without failing a test. The single exception to "never
   nine", reported rather than buried: the seeded representative line --
   not one of the 20 sweep lines -- reaches nine at 0.95, about 29% above
   the USBC-published maximum, where the thin hit is still 5 and the corner
   still 7.
5. `PIN_EFFECTIVE_RADIUS_IN` -- tested *coupled* with its documented fall
   threshold, since the two are the same value in this model -- does reach
   nine and ten pins, but a full 49-cell grid against restitution finds
   **zero** combinations that reach nine at the pocket while keeping the
   light, corner, outside, and flush-hit controls distinguishable from it.
   In all 13 cells that reach nine, the corner control stays within two
   pins of the pocket -- that one blocker, not the thin hit, is what fails
   every cell. That is a fragile deck, not carry.

## What "the representative case" means here

The seeded end-to-end shot -- seed 17, board 28, -1.5 deg, through the same
`sample_release` -> `simulate_throw` -> `impact_state_from_result` pipeline
the API routes use -- lands at a materially different `ImpactState` than a
tidy `(-2.6 in, +1.4 deg, 17 mph)` hand-placed control: about
`-3.480 in, +1.321 deg, 16.249 mph`. They happen to knock down the same
seven pins today, which is not equivalence of input state. The tests below
derive the real impact through the real pipeline, assert its exact fields,
and prove the direct collision call on that exact state reproduces the same
fallen ids, step count, termination reason, and threshold-crossing set as
both HTTP throw routes. The tidy `(-2.6, +1.4, 17)` control is kept in the
sweep corpus below under its own name (`pocket`), explicitly as a synthetic
probe on the credible entry line -- never claimed to be the seeded shot.

## Why structurally

Real 2-4-7 carry comes from the headpin deflecting left into the 2, which
drives the 4 and the 7. Here the headpin moves only a few inches on both
the seeded shot and the synthetic control, because the ball begins the run
already several inches inside the headpin's contact radius, so that first
contact is resolved largely by positional correction rather than by an
impulse. A flat disc also cannot sweep the deck the way a 15 in pin topples
across it. Neither of those is a calibration constant.

These tests pin the *current* behaviour. If a future change alters any of
it, that is a real result to be explained, not a number to be edited.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.physics import collision
from app.physics.ball import BALL_CATALOG
from app.physics.collision import (
    FALL_DISPLACEMENT_THRESHOLD_IN,
    MAX_COLLISION_STEPS,
    _simulate_collision_detail,
    simulate_collision,
)
from app.physics.impact import ImpactState, impact_state_from_result
from app.physics.lane import LaneCondition
from app.physics.pin_deck import USBC_PIN_COEFFICIENT_OF_RESTITUTION
from app.physics.simulate import simulate_throw
from app.physics.throw import Throw, sample_release

client = TestClient(app)

# Held fixed so a case differs from its neighbours only in the release
# variables under study -- the same reference ball the rest of the collision
# tests use.
BALL_MASS_LBS = 15.0
BALL_RADIUS_IN = 4.29
LANE_CONDITION_VERSION = 1

# The USBC-published target/low/high pin-to-pin coefficient of restitution.
# Sourced from the same constant `COLLISION_RESTITUTION` is defined from, so
# this file cannot silently drift from the production default's own basis.
USBC_RESTITUTION_TARGET, USBC_RESTITUTION_LOW, USBC_RESTITUTION_HIGH = (
    USBC_PIN_COEFFICIENT_OF_RESTITUTION
)


@dataclass(frozen=True)
class CarryCase:
    """One named, hand-placed entry line and the outcome this model
    currently produces for it.

    These are synthetic controls on the credible right-handed entry range --
    convenient round numbers chosen to sit on a recognisable line (pocket,
    thin, corner, ...), never a claim to reproduce any specific seeded
    throw. See `SeededRepresentativeShot` below for the one case that is
    tied to an actual seed.
    """

    name: str
    summary: str
    lateral_position_in: float
    heading_deg: float
    speed_mph: float
    standing_ids: tuple | None
    fallen_pin_ids: tuple
    termination_reason: str

    def impact(self) -> ImpactState:
        return ImpactState(
            lateral_position_in=self.lateral_position_in,
            heading_deg=self.heading_deg,
            speed_mph=self.speed_mph,
            ball_mass_lbs=BALL_MASS_LBS,
            ball_radius_in=BALL_RADIUS_IN,
            lane_condition_version=LANE_CONDITION_VERSION,
        )

    @property
    def pins(self) -> int:
        return len(self.fallen_pin_ids)


# The corpus. `pocket` is a synthetic control on the representative
# right-handed entry line -- not the seeded shot, see `SeededRepresentativeShot`
# -- and the rest are controls that a credible calibration has to keep
# distinguishable from it.
POCKET_CARRY_CORPUS: tuple = (
    CarryCase(
        name="pocket",
        summary=(
            "A synthetic, hand-placed control on the right-hander's 1-3 "
            "pocket line -- a tidy round-number probe, not the seeded shot "
            "(see SeededRepresentativeShot for that)."
        ),
        lateral_position_in=-2.6,
        heading_deg=1.4,
        speed_mph=17.0,
        standing_ids=None,
        fallen_pin_ids=(1, 3, 5, 6, 8, 9, 10),
        termination_reason="step_cap",
    ),
    CarryCase(
        name="light",
        summary="Thin on the headpin: real contact, but only a graze.",
        lateral_position_in=-6.0,
        heading_deg=1.4,
        speed_mph=17.0,
        standing_ids=None,
        fallen_pin_ids=(3, 5, 6, 7, 9),
        termination_reason="step_cap",
    ),
    CarryCase(
        name="brooklyn",
        summary="Crossover to the 1-2 side, the opposite pocket.",
        lateral_position_in=3.0,
        heading_deg=-2.0,
        speed_mph=17.0,
        standing_ids=None,
        fallen_pin_ids=(1, 2, 4, 5, 7, 8, 9),
        termination_reason="step_cap",
    ),
    CarryCase(
        name="head_on",
        summary="Flush on the headpin, no offset and no heading.",
        lateral_position_in=0.0,
        heading_deg=0.0,
        speed_mph=17.0,
        standing_ids=None,
        fallen_pin_ids=(1, 2, 3, 5, 8, 9, 10),
        termination_reason="step_cap",
    ),
    CarryCase(
        name="corner10",
        summary="Far outside, catching the 10 corner rather than the pocket.",
        lateral_position_in=-17.0,
        heading_deg=0.0,
        speed_mph=17.0,
        standing_ids=None,
        fallen_pin_ids=(4, 6, 8, 9, 10),
        termination_reason="step_cap",
    ),
    CarryCase(
        name="outside",
        summary="Almost in the channel: clips the 10 and nothing else.",
        lateral_position_in=-20.0,
        heading_deg=0.0,
        speed_mph=17.0,
        standing_ids=None,
        fallen_pin_ids=(10,),
        termination_reason="step_cap",
    ),
    CarryCase(
        name="spare_3_6_10",
        summary="Partial rack: a converted 3-6-10 spare.",
        lateral_position_in=-8.0,
        heading_deg=-2.0,
        speed_mph=16.0,
        standing_ids=(3, 6, 10),
        fallen_pin_ids=(3, 6, 10),
        termination_reason="step_cap",
    ),
)

CASE_IDS = [case.name for case in POCKET_CARRY_CORPUS]


def _case(name: str) -> CarryCase:
    return next(c for c in POCKET_CARRY_CORPUS if c.name == name)


def _run(case_or_impact, standing_ids=None):
    """Runs either a `CarryCase` or a bare `ImpactState` through the same
    recorded collision call, so every helper below shares one code path."""
    if isinstance(case_or_impact, CarryCase):
        return _simulate_collision_detail(
            case_or_impact.impact(), standing_ids=case_or_impact.standing_ids, record_replay=True
        )
    return _simulate_collision_detail(case_or_impact, standing_ids=standing_ids, record_replay=True)


def _displacements(run) -> dict:
    first = {b.body_id: (b.x_in, b.y_in) for b in run.replay.frames[0].bodies if b.body_id}
    last = {b.body_id: (b.x_in, b.y_in) for b in run.replay.frames[-1].bodies if b.body_id}
    return {
        pin_id: math.hypot(last[pin_id][0] - x0, last[pin_id][1] - y0)
        for pin_id, (x0, y0) in first.items()
    }


# --- The corpus itself ---------------------------------------------------


@pytest.mark.parametrize("case", POCKET_CARRY_CORPUS, ids=CASE_IDS)
def test_case_produces_its_recorded_outcome(case):
    run = _run(case)

    assert run.fallen_pin_ids == case.fallen_pin_ids
    assert len(run.fallen_pin_ids) == case.pins
    assert run.replay.termination_reason == case.termination_reason


@pytest.mark.parametrize("case", POCKET_CARRY_CORPUS, ids=CASE_IDS)
def test_case_is_deterministic_and_legal(case):
    first, second = _run(case), _run(case)

    # Identical input, identical run -- pin count carries no randomness of
    # its own; randomness lives in the trajectory, before impact.
    assert first.replay == second.replay
    assert first.fallen_pin_ids == second.fallen_pin_ids

    # Legal, bounded, finite.
    assert 0 < first.steps_taken <= MAX_COLLISION_STEPS
    for frame in first.replay.frames:
        for body in frame.bodies:
            assert math.isfinite(body.x_in)
            assert math.isfinite(body.y_in)
            assert abs(body.x_in) < 500.0
            assert abs(body.y_in) < 500.0


@pytest.mark.parametrize("case", POCKET_CARRY_CORPUS, ids=CASE_IDS)
def test_recording_still_does_not_change_the_result(case):
    plain = simulate_collision(case.impact(), standing_ids=case.standing_ids)
    recorded = _run(case)

    assert recorded.fallen_pin_ids == plain[0]
    assert recorded.steps_taken == plain[1]


@pytest.mark.parametrize("case", POCKET_CARRY_CORPUS, ids=CASE_IDS)
def test_crossing_ids_equal_the_fallen_set(case):
    run = _run(case)
    crossings = run.replay.threshold_crossings

    assert tuple(sorted(c.pin_id for c in crossings)) == run.fallen_pin_ids
    assert all(0 < c.step_index <= run.steps_taken for c in crossings)


def test_the_corpus_still_distinguishes_its_controls():
    """What a calibration must not destroy: the pocket carries more than a
    thin hit, and far more than a corner or a near-channel ball."""
    outcome = {case.name: len(_run(case).fallen_pin_ids) for case in POCKET_CARRY_CORPUS}

    assert outcome["light"] < outcome["pocket"]
    assert outcome["corner10"] < outcome["pocket"]
    assert outcome["outside"] <= 2
    assert outcome["spare_3_6_10"] == 3


# --- The actual seeded representative shot --------------------------------
#
# Not a hand-placed control: the exact `ImpactState` a real seeded throw
# produces, through the same pipeline the API routes use. This is the case
# the calibration note's "structural reason" section is about.


def _seeded_representative_impact() -> ImpactState:
    """Derives the real `ImpactState` for seed 17, board 28, -1.5 deg,
    through the identical `sample_release` -> `simulate_throw` ->
    `impact_state_from_result` pipeline `create_game_throw` and the legacy
    throws route both call. No shortcuts, no rounding: this is
    `result.terminal`, re-expressed, exactly as production does it.

    Fully deterministic: a fixed seed, a fixed requested throw, and a fresh
    (never-worn) house-shot lane condition, matching a game's first ball.
    """
    ball = BALL_CATALOG["reactive_pearl"]
    requested = Throw(
        speed_mph=17.0,
        rev_rate=350.0,
        axis_rotation=45.0,
        axis_tilt=15.0,
        launch_angle=-1.5,
        launch_position=28.0,
    )
    actual_throw, _seed = sample_release(requested, 17)
    result = simulate_throw(ball, actual_throw, LaneCondition.house_shot())
    return impact_state_from_result(result, ball)


# The literal `THROW_PAYLOAD` body both HTTP tests below send -- identical to
# `_seeded_representative_impact`'s inputs, so the derived-impact path and
# the HTTP paths are provably describing the same requested throw.
_SEEDED_THROW_PAYLOAD = {
    "ball_id": "reactive_pearl",
    "seed": 17,
    "speed_mph": 17.0,
    "rev_rate": 350.0,
    "axis_rotation": 45.0,
    "axis_tilt": 15.0,
    "launch_angle": -1.5,
    "launch_position": 28.0,
}


def test_the_seeded_impact_has_these_exact_derived_fields():
    """Pins the real `ImpactState` fields against the production pipeline's
    unrounded endpoint -- not the rounded presentation fields, and not the
    tidy `(-2.6, +1.4, 17)` synthetic control, which differs from this by
    about 0.88 in laterally, 0.08 deg in heading, and 0.75 mph in speed."""
    impact = _seeded_representative_impact()

    assert impact.lateral_position_in == pytest.approx(-3.4798526717791094, abs=1e-9)
    assert impact.heading_deg == pytest.approx(1.3205589118668974, abs=1e-9)
    assert impact.speed_mph == pytest.approx(16.24928162210715, abs=1e-9)
    assert impact.ball_mass_lbs == 15.0
    assert impact.ball_radius_in == 4.29
    assert impact.lane_condition_version == 1

    # And it is genuinely not the synthetic `pocket` control -- if it were,
    # the "materially different" claim above would be false.
    synthetic = _case("pocket").impact()
    assert impact.lateral_position_in != synthetic.lateral_position_in
    assert abs(impact.lateral_position_in - synthetic.lateral_position_in) > 0.5
    assert abs(impact.speed_mph - synthetic.speed_mph) > 0.5


def test_the_seeded_impact_reproduces_exactly_through_the_game_route():
    """Not just matching fallen ids -- the *complete* recorded run: step
    count, termination reason, and the full ordered threshold-crossing set,
    each (pin_id, step_index) pair. That is what "the direct call reproduces
    the end-to-end shot exactly" actually requires."""
    direct = _run(_seeded_representative_impact())

    game_id = client.post("/api/v1/games", json={}).json()["game_id"]
    body = client.post(f"/api/v1/games/{game_id}/throws", json=_SEEDED_THROW_PAYLOAD).json()
    route_replay = body["pinfall"]["replay"]

    assert list(direct.fallen_pin_ids) == body["pinfall"]["fallen_pin_ids"]
    assert direct.steps_taken == route_replay["steps_taken"]
    assert direct.replay.termination_reason == route_replay["termination_reason"]
    assert [(c.pin_id, c.step_index) for c in direct.replay.threshold_crossings] == [
        (c["pin_id"], c["step_index"]) for c in route_replay["threshold_crossings"]
    ]

    # And this is the same seven-pin, 2-4-7-standing result the corpus and
    # the note describe.
    assert body["pins_knocked"] == 7
    assert body["game_state"]["standing_pin_ids"] == [2, 4, 7]
    assert body["entry_board"] == pytest.approx(16.686, abs=1e-3)
    assert body["entry_angle_deg"] == pytest.approx(1.32, abs=1e-2)
    assert body["speed_at_pins_mph"] == pytest.approx(16.25, abs=1e-2)


def test_the_seeded_impact_reproduces_exactly_through_the_legacy_route_too():
    """The game-scoped and legacy routes build their collision response
    through identical plumbing (`simulate_throw` then
    `impact_state_from_result` then `DEFAULT_PINFALL_MODEL.resolve`), so
    this both confirms that and guards against future divergence or any
    state leaking from the game-scoped throw above into the legacy route's
    shared process-global game."""
    client.post("/api/v1/games/legacy-default/reset")
    body = client.post("/api/v1/simulations/throws", json=_SEEDED_THROW_PAYLOAD).json()
    route_replay = body["pinfall"]["replay"]

    direct = _run(_seeded_representative_impact())

    assert list(direct.fallen_pin_ids) == body["pinfall"]["fallen_pin_ids"]
    assert direct.steps_taken == route_replay["steps_taken"]
    assert direct.replay.termination_reason == route_replay["termination_reason"]
    assert [(c.pin_id, c.step_index) for c in direct.replay.threshold_crossings] == [
        (c["pin_id"], c["step_index"]) for c in route_replay["threshold_crossings"]
    ]


def test_the_seeded_shot_survivors_are_never_contacted_at_all():
    """The core finding, on the real shot rather than the synthetic
    control. 2, 4 and 7 are not marginal -- they are untouched, so no
    threshold can reclassify them and no extra travel time can reach them."""
    run = _run(_seeded_representative_impact())
    displacement = _displacements(run)
    standing = set(range(1, 11)) - set(run.fallen_pin_ids)

    assert standing == {2, 4, 7}
    for pin_id in standing:
        assert displacement[pin_id] == 0.0, f"pin {pin_id} was expected untouched"
    assert not [d for d in displacement.values() if 0.0 < d < FALL_DISPLACEMENT_THRESHOLD_IN]


def test_the_seeded_shots_headpin_barely_moves_because_it_starts_overlapped():
    """Why the left-side chain never starts, on the real shot: the ball's
    seeded lateral position already sits inside the headpin's contact
    radius, so the first contact is mostly a positional correction rather
    than an impulse, and the headpin cannot drive the 2 into the 4 and 7."""
    impact = _seeded_representative_impact()
    run = _run(impact)
    displacement = _displacements(run)

    contact_distance = impact.ball_radius_in + collision.PIN_EFFECTIVE_RADIUS_IN
    overlap = contact_distance - abs(impact.lateral_position_in)
    assert overlap > 3.0, "the seeded shot's lateral position starts deeply overlapped"

    assert displacement[1] < 5.0
    struck = [d for pin_id, d in displacement.items() if pin_id != 1 and d > 0.0]
    assert min(struck) > 40.0


# --- Why the constants cannot fix it -------------------------------------
#
# Each of these ran before any tuning was attempted, and each is the reason
# one candidate lever was rejected. They are kept so the conclusion stays
# checkable rather than remembered.


def test_the_pocket_survivors_are_never_contacted_at_all():
    """Same finding as the seeded-shot version above, on the synthetic
    `pocket` control -- confirming the mechanism generalises across the
    credible entry line rather than being an artefact of one seed."""
    run = _run(_case("pocket"))
    displacement = _displacements(run)
    standing = set(range(1, 11)) - set(run.fallen_pin_ids)

    assert standing == {2, 4, 7}
    for pin_id in standing:
        assert displacement[pin_id] == 0.0, f"pin {pin_id} was expected untouched"
    assert not [d for d in displacement.values() if 0.0 < d < FALL_DISPLACEMENT_THRESHOLD_IN]


def test_the_headpin_barely_moves_because_the_ball_starts_overlapping_it():
    """The synthetic-control version of the mechanism above."""
    run = _run(_case("pocket"))
    displacement = _displacements(run)

    contact_distance = BALL_RADIUS_IN + collision.PIN_EFFECTIVE_RADIUS_IN
    overlap = contact_distance - abs(_case("pocket").lateral_position_in)
    assert overlap > 4.0, "the pocket line starts deeply overlapped with the headpin"

    assert displacement[1] < 5.0
    struck = [d for pin_id, d in displacement.items() if pin_id != 1 and d > 0.0]
    assert min(struck) > 40.0


def test_damping_cannot_change_the_outcome_at_any_plausible_value(monkeypatch):
    """Rejects `LINEAR_DAMPING_PER_S` as a carry lever: across a twenty-fold
    range it changes nothing, because the survivors receive no impulse."""
    baseline = {case.name: _run(case).fallen_pin_ids for case in POCKET_CARRY_CORPUS}

    for damping in (1.2, 0.9, 0.6, 0.4, 0.2, 0.05):
        monkeypatch.setattr(collision, "LINEAR_DAMPING_PER_S", damping)
        for case in POCKET_CARRY_CORPUS:
            assert _run(case).fallen_pin_ids == baseline[case.name], damping


def test_a_larger_pin_radius_inflates_the_corner_control_before_it_helps_the_pocket(
    monkeypatch,
):
    """Rejects `PIN_EFFECTIVE_RADIUS_IN` as a carry lever: it makes every
    line carry rather than making the pocket carry. Coupled with
    `FALL_DISPLACEMENT_THRESHOLD_IN` at every step, since the production
    model defines the threshold as equal to the radius -- this tests the
    configuration the note actually documents, not a radius-only variant of
    it. (The two give identical outcomes at every value tried; see
    `test_coupling_the_threshold_does_not_change_the_radius_conclusion`.)"""
    pocket, corner = _case("pocket"), _case("corner10")

    for radius in (2.6, 2.7, 2.8):
        monkeypatch.setattr(collision, "PIN_EFFECTIVE_RADIUS_IN", radius)
        monkeypatch.setattr(collision, "FALL_DISPLACEMENT_THRESHOLD_IN", radius)
        # The pocket has not improved...
        assert len(_run(pocket).fallen_pin_ids) == pocket.pins, radius
        # ...while the corner hit already knocks more down.
        assert len(_run(corner).fallen_pin_ids) > corner.pins, radius


def test_reaching_nine_at_the_pocket_flattens_the_controls(monkeypatch):
    """The threshold *is* reachable — and this is what it costs. At the
    coupled radius/threshold that first gets the pocket to nine, a thin hit
    and a corner hit both knock eight and a flush headpin hit strikes. The
    model stops telling one shot from another, which is worse than
    carrying too little."""
    monkeypatch.setattr(collision, "PIN_EFFECTIVE_RADIUS_IN", 3.6)
    monkeypatch.setattr(collision, "FALL_DISPLACEMENT_THRESHOLD_IN", 3.6)

    outcome = {case.name: len(_run(case).fallen_pin_ids) for case in POCKET_CARRY_CORPUS}

    assert outcome["pocket"] >= 9, "the coupled radius/threshold does reach the stated threshold"
    # But every control follows it up.
    assert outcome["light"] >= 8, "a graze now carries like a pocket hit"
    assert outcome["corner10"] >= 8, "a corner hit now carries like a pocket hit"
    assert outcome["head_on"] >= outcome["pocket"], "a flush hit out-carries the pocket"


def test_coupling_the_threshold_does_not_change_the_radius_conclusion(monkeypatch):
    """Direct check of the coupling claim itself: for the values used above,
    varying `PIN_EFFECTIVE_RADIUS_IN` alone (leaving the fall threshold at
    its production default) gives the identical outcome as varying both
    together. So the earlier radius-only experiment's conclusion was not
    accidentally right for the wrong model -- but this file now tests the
    actually-documented coupled rule regardless, rather than relying on
    that agreement continuing to hold."""
    pocket, corner = _case("pocket"), _case("corner10")

    for radius in (2.6, 2.7, 2.8, 3.6):
        monkeypatch.setattr(collision, "PIN_EFFECTIVE_RADIUS_IN", radius)
        monkeypatch.setattr(collision, "FALL_DISPLACEMENT_THRESHOLD_IN", radius)
        coupled_pocket = len(_run(pocket).fallen_pin_ids)
        coupled_corner = len(_run(corner).fallen_pin_ids)

        monkeypatch.setattr(collision, "PIN_EFFECTIVE_RADIUS_IN", radius)
        monkeypatch.setattr(
            collision, "FALL_DISPLACEMENT_THRESHOLD_IN", FALL_DISPLACEMENT_THRESHOLD_IN
        )
        decoupled_pocket = len(_run(pocket).fallen_pin_ids)
        decoupled_corner = len(_run(corner).fallen_pin_ids)

        assert coupled_pocket == decoupled_pocket, radius
        assert coupled_corner == decoupled_corner, radius


# --- Restitution: an executable, bounded sweep ----------------------------
#
# Replaces a one-off, unbacked prose number with a deterministic test.
# Explicit axes below; nothing here searches an unbounded range.

# The 20-line right-handed entry sweep the restitution claim is measured
# against: five lateral positions crossed with four headings, all at the
# credible house-shot speed. The same grid the original investigation used.
RESTITUTION_SWEEP_LATERALS = (-1.0, -1.8, -2.6, -3.4, -4.2)
RESTITUTION_SWEEP_HEADINGS = (1.0, 1.4, 2.0, 3.0)
RESTITUTION_SWEEP_SPEED_MPH = 17.0

# Explicit values: the USBC low/target/high, then exploratory values well
# beyond the certified range, to show that even conceding physical realism
# does not buy credible pocket carry.
RESTITUTION_SWEEP_VALUES = (
    USBC_RESTITUTION_LOW,  # 0.605
    USBC_RESTITUTION_TARGET,  # 0.670 -- the production default
    USBC_RESTITUTION_HIGH,  # 0.735
    0.75,
    0.80,
    0.85,
    0.90,
    0.95,
)


def _restitution_sweep_counts() -> list:
    """Fallen-pin counts across the full 20-line entry sweep, at whatever
    `collision.COLLISION_RESTITUTION` currently is."""
    counts = []
    for lateral in RESTITUTION_SWEEP_LATERALS:
        for heading in RESTITUTION_SWEEP_HEADINGS:
            impact = ImpactState(
                lateral_position_in=lateral,
                heading_deg=heading,
                speed_mph=RESTITUTION_SWEEP_SPEED_MPH,
                ball_mass_lbs=BALL_MASS_LBS,
                ball_radius_in=BALL_RADIUS_IN,
                lane_condition_version=LANE_CONDITION_VERSION,
            )
            run = _simulate_collision_detail(impact, record_replay=False)
            counts.append(len(run.fallen_pin_ids))
    return counts


def test_the_restitution_sweep_corpus_has_twenty_lines():
    assert len(RESTITUTION_SWEEP_LATERALS) * len(RESTITUTION_SWEEP_HEADINGS) == 20


def test_restitution_at_the_production_default_matches_the_documented_baseline():
    """Pins the exact mean/max the note quotes as "typical carry", computed
    fresh rather than remembered. `COLLISION_RESTITUTION` is the module's
    live value here -- not monkeypatched -- so this also doubles as a
    regression check that nothing else moved it."""
    assert collision.COLLISION_RESTITUTION == USBC_RESTITUTION_TARGET

    counts = _restitution_sweep_counts()

    assert len(counts) == 20
    assert sum(counts) / len(counts) == pytest.approx(6.55, abs=1e-9)
    assert max(counts) == 8
    assert min(counts) == 5


@pytest.mark.parametrize("restitution", RESTITUTION_SWEEP_VALUES)
def test_restitution_never_lifts_typical_pocket_carry_to_a_credible_strike(
    monkeypatch, restitution
):
    """The executable form of "restitution leaves typical carry essentially
    unchanged": across the full USBC range and materially beyond it, the
    20-line sweep's mean never reaches 7 (it would need to, for "typically
    eight" to be a fair description) and its max never exceeds 8. A value
    that broke either bound would be a real, credible improvement worth
    revisiting this conclusion for; none does."""
    monkeypatch.setattr(collision, "COLLISION_RESTITUTION", restitution)

    counts = _restitution_sweep_counts()
    mean = sum(counts) / len(counts)

    assert max(counts) == 8, (restitution, counts)
    assert mean < 7.0, (restitution, mean)


def test_restitution_sweep_reproduces_its_recorded_extremes(monkeypatch):
    """Pins the two extreme sweep points precisely, so a silent change in
    the solver's impulse response is caught even if it happens to keep the
    bounds above satisfied."""
    monkeypatch.setattr(collision, "COLLISION_RESTITUTION", USBC_RESTITUTION_HIGH)
    at_usbc_high = _restitution_sweep_counts()
    monkeypatch.setattr(collision, "COLLISION_RESTITUTION", 0.95)
    at_exploratory_max = _restitution_sweep_counts()

    assert sum(at_usbc_high) / len(at_usbc_high) == pytest.approx(6.6, abs=1e-9)
    assert max(at_usbc_high) == 8
    assert sum(at_exploratory_max) / len(at_exploratory_max) == pytest.approx(6.85, abs=1e-9)
    assert max(at_exploratory_max) == 8

    # And restitution beyond the USBC-published high is exploratory, not a
    # value this model could honestly claim as calibrated.
    assert 0.95 > USBC_RESTITUTION_HIGH


def test_restitution_is_restored_after_the_sweep():
    # monkeypatch reverts on its own; this asserts that rather than trusting
    # it, since a leaked value would silently corrupt every later test.
    assert collision.COLLISION_RESTITUTION == USBC_RESTITUTION_TARGET


# --- Restitution: the named controls, not just the aggregate --------------
#
# The 20-line sweep above measures a mean and max over *anonymous* entry
# lines. That is the right shape for "typical carry does not move", but it
# cannot notice a named control collapsing: a change that lifted the thin
# hit or the corner onto the pocket would leave the sweep's aggregate
# largely untouched and its bounds still satisfied. These tests close that
# gap by measuring the real seeded representative shot and every named
# corpus control at every explicit sweep value, at the production coupled
# radius/threshold.

# Pinned as a literal rather than read from the module, so this section
# cannot silently follow a production change; the two are asserted equal by
# `test_the_control_table_is_measured_at_production_geometry`.
PRODUCTION_PIN_RADIUS_IN = 2.383


def _restitution_control_readout() -> dict:
    """Fallen-pin counts for the seeded representative shot and every named
    corpus control, at whatever collision constants are currently set.

    `_seeded_representative_impact()` is re-derived here rather than cached
    across values: it is trajectory-only (about 4 ms) and reads no collision
    constant, so the monkeypatched restitution cannot affect it, and
    deriving it in place keeps this helper free of cross-test state.
    """
    counts = {"seeded": len(_run(_seeded_representative_impact()).fallen_pin_ids)}
    for case in POCKET_CARRY_CORPUS:
        counts[case.name] = len(_run(case).fallen_pin_ids)
    return counts


# The exact documented outcome at every explicit sweep value, measured at
# the production radius. Written out in full as literals -- not computed --
# so that a change to any single control at any single restitution fails
# loudly, and names which one.
RESTITUTION_CONTROL_TABLE = (
    (
        USBC_RESTITUTION_LOW,  # 0.605
        {"seeded": 7, "pocket": 7, "light": 5, "brooklyn": 7,
         "head_on": 7, "corner10": 6, "outside": 1, "spare_3_6_10": 3},
    ),
    (
        USBC_RESTITUTION_TARGET,  # 0.670 -- production
        {"seeded": 7, "pocket": 7, "light": 5, "brooklyn": 7,
         "head_on": 7, "corner10": 5, "outside": 1, "spare_3_6_10": 3},
    ),
    (
        USBC_RESTITUTION_HIGH,  # 0.735
        {"seeded": 7, "pocket": 8, "light": 5, "brooklyn": 7,
         "head_on": 7, "corner10": 5, "outside": 1, "spare_3_6_10": 3},
    ),
    (
        0.75,
        {"seeded": 7, "pocket": 8, "light": 5, "brooklyn": 7,
         "head_on": 7, "corner10": 5, "outside": 1, "spare_3_6_10": 3},
    ),
    (
        0.80,
        {"seeded": 8, "pocket": 8, "light": 5, "brooklyn": 6,
         "head_on": 7, "corner10": 5, "outside": 1, "spare_3_6_10": 3},
    ),
    (
        0.85,
        {"seeded": 8, "pocket": 8, "light": 5, "brooklyn": 6,
         "head_on": 7, "corner10": 6, "outside": 1, "spare_3_6_10": 3},
    ),
    (
        0.90,
        {"seeded": 7, "pocket": 8, "light": 5, "brooklyn": 6,
         "head_on": 7, "corner10": 6, "outside": 1, "spare_3_6_10": 3},
    ),
    (
        0.95,
        {"seeded": 9, "pocket": 8, "light": 5, "brooklyn": 7,
         "head_on": 7, "corner10": 7, "outside": 1, "spare_3_6_10": 3},
    ),
)

_CONTROL_TABLE_IDS = [f"{restitution:.3f}" for restitution, _ in RESTITUTION_CONTROL_TABLE]


def _controls_stay_separated(outcome: dict) -> bool:
    """The documented distinguishability rule for this section: the pocket
    still out-carries both the thin hit and the corner, a flush headpin hit
    never beats it, a near-channel ball stays a token result, and the
    partial rack still converts exactly. Deliberately weaker than
    `_is_discriminating` below -- it asks only that the controls stay
    *ordered*, not that they stay separated by a margin -- because the point
    here is that restitution cannot even break the ordering, let alone buy
    carry.
    """
    return (
        outcome["light"] < outcome["pocket"]
        and outcome["corner10"] < outcome["pocket"]
        and outcome["head_on"] <= outcome["pocket"]
        and outcome["outside"] <= 2
        and outcome["spare_3_6_10"] == 3
    )


def test_the_control_table_covers_exactly_the_declared_sweep_values():
    """Guards the table against drifting out of sync with the sweep axes --
    a table that silently stopped covering a value would make every
    assertion below weaker without failing."""
    covered = tuple(restitution for restitution, _ in RESTITUTION_CONTROL_TABLE)

    assert covered == RESTITUTION_SWEEP_VALUES
    for _, expected in RESTITUTION_CONTROL_TABLE:
        assert sorted(expected) == sorted(["seeded", *CASE_IDS])


def test_the_control_table_is_measured_at_production_geometry():
    assert collision.PIN_EFFECTIVE_RADIUS_IN == PRODUCTION_PIN_RADIUS_IN
    assert collision.FALL_DISPLACEMENT_THRESHOLD_IN == PRODUCTION_PIN_RADIUS_IN


@pytest.mark.parametrize(
    "restitution,expected", RESTITUTION_CONTROL_TABLE, ids=_CONTROL_TABLE_IDS
)
def test_every_named_control_matches_its_documented_outcome(monkeypatch, restitution, expected):
    """The explicit per-control regression the aggregate sweep cannot give.
    Each named control -- and the seeded representative shot, which is *not*
    one of the 20 sweep lines -- is pinned at every declared restitution."""
    monkeypatch.setattr(collision, "COLLISION_RESTITUTION", restitution)
    monkeypatch.setattr(collision, "PIN_EFFECTIVE_RADIUS_IN", PRODUCTION_PIN_RADIUS_IN)
    monkeypatch.setattr(collision, "FALL_DISPLACEMENT_THRESHOLD_IN", PRODUCTION_PIN_RADIUS_IN)

    assert _restitution_control_readout() == expected


@pytest.mark.parametrize("restitution", RESTITUTION_SWEEP_VALUES, ids=_CONTROL_TABLE_IDS)
def test_restitution_never_breaks_the_control_ordering(monkeypatch, restitution):
    """Restitution cannot flatten the corpus. Across the whole USBC range
    and far beyond it, the pocket still out-carries the thin hit and the
    corner, the flush headpin hit never beats the pocket, the near-channel
    ball stays at one pin, and the 3-6-10 still converts.

    Deliberately asserts *only* the ordering predicate, against a freshly
    measured readout. Re-asserting the exact table here would mean a single
    drifted count failed this test on the equality line, before the
    ordering claim in its name was ever evaluated; the exact counts are
    `test_every_named_control_matches_its_documented_outcome`'s job.
    """
    monkeypatch.setattr(collision, "COLLISION_RESTITUTION", restitution)
    monkeypatch.setattr(collision, "PIN_EFFECTIVE_RADIUS_IN", PRODUCTION_PIN_RADIUS_IN)
    monkeypatch.setattr(collision, "FALL_DISPLACEMENT_THRESHOLD_IN", PRODUCTION_PIN_RADIUS_IN)

    measured = _restitution_control_readout()

    assert _controls_stay_separated(measured), (restitution, measured)


def test_only_an_uncertified_restitution_reaches_nine_and_only_on_the_seeded_line():
    """The one place restitution *does* reach nine, stated exactly rather
    than left hidden behind the sweep's aggregate bound.

    The 20-line sweep's max is 8 at every value, but the seeded
    representative shot is not one of those 20 lines -- it sits at lateral
    -3.480, heading +1.321, 16.249 mph, none of which are on the sweep's
    axes. At 0.95 that line reaches nine. This is reported, not buried:
    0.95 is about 29% above the USBC-published maximum, and even there the
    thin hit is still 5 and the corner still 7, so it is not a credible
    deck -- but "restitution never reaches nine anywhere" would be false.
    """
    reaching_nine = [
        (restitution, name)
        for restitution, expected in RESTITUTION_CONTROL_TABLE
        for name, count in expected.items()
        if count >= 9
    ]

    assert reaching_nine == [(0.95, "seeded")]

    at_uncertified_max = dict(RESTITUTION_CONTROL_TABLE)[0.95]
    assert 0.95 > USBC_RESTITUTION_HIGH
    # The "about 29% above the USBC-published maximum" figure the calibration
    # note quotes, computed here rather than remembered.
    assert round((0.95 / USBC_RESTITUTION_HIGH - 1) * 100) == 29
    assert at_uncertified_max["light"] == 5
    assert at_uncertified_max["corner10"] == 7
    assert _controls_stay_separated(at_uncertified_max)


def test_the_seeded_lines_response_to_restitution_is_not_monotonic():
    """Evidence that restitution is not a carry *lever* even where it moves
    the number: raising it does not raise carry, it rearranges the deck.
    The seeded line goes 7, 7, 7, 7, 8, 8, 7, 9 as restitution climbs --
    dropping back to seven at 0.90 before reaching nine at 0.95."""
    seeded = [expected["seeded"] for _, expected in RESTITUTION_CONTROL_TABLE]

    assert seeded == [7, 7, 7, 7, 8, 8, 7, 9]
    # Explicit indexing rather than zip(..., strict=): the runtime floor is
    # Python 3.9, matching this file's other pairwise comparisons.
    assert any(seeded[i + 1] < seeded[i] for i in range(len(seeded) - 1))


# --- The restitution x radius grid ----------------------------------------
#
# A bounded, explicit, checked-in grid -- never an unbounded search -- with
# the exact discriminating-control predicate encoded as a pure function
# rather than left in prose. Radius and its fall threshold are coupled at
# every cell, matching the documented rule.

# Explicit axes. Restitution reuses the USBC low/target/high plus the same
# exploratory ceiling as the one-dimensional sweep above; radius spans from
# the production default to +1.217 in, in six even steps.
GRID_RESTITUTION_VALUES = (
    USBC_RESTITUTION_LOW,
    USBC_RESTITUTION_TARGET,
    USBC_RESTITUTION_HIGH,
    0.75,
    0.80,
    0.85,
    0.90,
)
GRID_RADIUS_VALUES = (2.383, 2.6, 2.8, 3.0, 3.2, 3.4, 3.6)

# The named cases the discriminating predicate below reads. A subset of the
# full corpus -- brooklyn is included only for a fuller readout in failures,
# not required by the predicate itself.
_GRID_CASE_NAMES = ("pocket", "light", "brooklyn", "head_on", "corner10", "outside", "spare_3_6_10")


def _grid_outcome(restitution: float, radius: float) -> dict:
    """Every named case's fallen-pin count at one grid cell. Radius and
    threshold are set to the same value, on purpose -- see the module
    docstring's "coupled" requirement."""
    collision.COLLISION_RESTITUTION = restitution
    collision.PIN_EFFECTIVE_RADIUS_IN = radius
    collision.FALL_DISPLACEMENT_THRESHOLD_IN = radius
    return {name: len(_run(_case(name)).fallen_pin_ids) for name in _GRID_CASE_NAMES}


def _is_discriminating(outcome: dict) -> bool:
    """The exact predicate a calibration must satisfy to count as credible:
    the pocket reaches a real strike-adjacent carry, and every control that
    should carry less still does, by a real margin rather than by one pin."""
    return (
        outcome["pocket"] >= 9
        and outcome["light"] <= outcome["pocket"] - 2
        and outcome["corner10"] <= outcome["pocket"] - 3
        and outcome["outside"] <= 2
        and outcome["head_on"] <= outcome["pocket"]
        and outcome["spare_3_6_10"] == 3
    )


def test_the_grid_axes_are_bounded_and_explicit():
    assert len(GRID_RESTITUTION_VALUES) == 7
    assert len(GRID_RADIUS_VALUES) == 7
    assert len(GRID_RESTITUTION_VALUES) * len(GRID_RADIUS_VALUES) == 49


def test_no_grid_cell_reaches_the_pocket_threshold_while_staying_discriminating(monkeypatch):
    """The grid claim, executed. Every cell is a fixed, finite combination
    from the explicit axes above -- never a search. Restitution and the
    coupled radius/threshold are restored by `monkeypatch` regardless of
    how the loop exits."""
    reaching_nine = []
    discriminating_hits = []

    for restitution in GRID_RESTITUTION_VALUES:
        for radius in GRID_RADIUS_VALUES:
            monkeypatch.setattr(collision, "COLLISION_RESTITUTION", restitution)
            monkeypatch.setattr(collision, "PIN_EFFECTIVE_RADIUS_IN", radius)
            monkeypatch.setattr(collision, "FALL_DISPLACEMENT_THRESHOLD_IN", radius)
            outcome = _grid_outcome(restitution, radius)

            if outcome["pocket"] >= 9:
                reaching_nine.append((restitution, radius, outcome))
            if _is_discriminating(outcome):
                discriminating_hits.append((restitution, radius, outcome))

    # The predicate is not vacuously unreachable -- some cells really do get
    # the pocket to nine, so "zero survive" below is a genuine finding
    # about the trade-off, not an artefact of the axes never reaching it.
    assert len(reaching_nine) > 0, "the grid never even reaches pocket>=9; axes may be too narrow"
    assert reaching_nine[0][2]["pocket"] >= 9

    # The exact count the calibration note publishes. Pinned rather than
    # bounded, so the note's "13 of the 49" cannot drift away from the code
    # that produces it.
    assert len(reaching_nine) == 13, [(r, radius) for r, radius, _ in reaching_nine]

    assert discriminating_hits == []


def test_3_6_is_the_lowest_declared_radius_reaching_nine_at_production_restitution(monkeypatch):
    """Scoped deliberately to production restitution, and proved rather than
    asserted.

    An unqualified "the lowest radius that reaches nine" would be false:
    higher restitution reaches nine at *smaller* radii -- see
    `test_a_higher_restitution_reaches_nine_at_a_smaller_radius`. What is
    true is the scoped claim, checked here across every declared radius:
    holding restitution at the production default, the pocket stays under
    nine at 2.383 through 3.4, and 3.6 is the first declared radius that
    reaches it. Its full readout is pinned so the "flattens the controls"
    claim has a specific, checkable example.
    """
    monkeypatch.setattr(collision, "COLLISION_RESTITUTION", USBC_RESTITUTION_TARGET)

    by_radius = {}
    for radius in GRID_RADIUS_VALUES:
        monkeypatch.setattr(collision, "PIN_EFFECTIVE_RADIUS_IN", radius)
        monkeypatch.setattr(collision, "FALL_DISPLACEMENT_THRESHOLD_IN", radius)
        by_radius[radius] = _grid_outcome(USBC_RESTITUTION_TARGET, radius)

    # The whole production-restitution radius column, pinned in order.
    assert [by_radius[radius]["pocket"] for radius in GRID_RADIUS_VALUES] == [7, 7, 7, 7, 7, 8, 9]

    # Stated as the claim itself, not only as a literal list: every declared
    # radius below 3.6 leaves the pocket short of nine.
    for radius in GRID_RADIUS_VALUES:
        if radius < 3.6:
            assert by_radius[radius]["pocket"] < 9, (radius, by_radius[radius])

    assert by_radius[3.6] == {
        "pocket": 9,
        "light": 8,
        "brooklyn": 9,
        "head_on": 10,
        "corner10": 8,
        "outside": 1,
        "spare_3_6_10": 3,
    }
    assert not _is_discriminating(by_radius[3.6])


def test_a_higher_restitution_reaches_nine_at_a_smaller_radius(monkeypatch):
    """Why the test above has to be scoped to production restitution: the
    lowest radius reaching nine is not a global property of the grid. At
    restitution 0.90 the pocket already reaches nine at radius 3.0, well
    below the 3.6 that production restitution needs."""
    monkeypatch.setattr(collision, "COLLISION_RESTITUTION", 0.90)
    monkeypatch.setattr(collision, "PIN_EFFECTIVE_RADIUS_IN", 3.0)
    monkeypatch.setattr(collision, "FALL_DISPLACEMENT_THRESHOLD_IN", 3.0)

    outcome = _grid_outcome(0.90, 3.0)

    assert outcome["pocket"] >= 9
    assert 3.0 < 3.6
    assert not _is_discriminating(outcome)


def test_every_cell_that_reaches_nine_lifts_the_corner_control_with_it(monkeypatch):
    """The grid-wide statement, replacing an overbroad one.

    An earlier version of the calibration note said that at the radii which
    reach nine, a thin hit and a corner hit *both* knock eight and a flush
    headpin hit strikes. That describes exactly one cell (0.670 / 3.6), not
    the grid: at 0.85 / 3.2 the thin hit is 7, at 0.80 / 3.6 the corner is
    9, and at 0.90 / 3.0 the flush headpin hit is 9 rather than 10.

    What is true of all thirteen cells, and asserted here, is the single
    universal blocker: the corner control is never three or more behind the
    pocket. That one fact -- not the thin hit, which does clear the margin
    in some cells -- is why no cell in the grid is discriminating.
    """
    reaching_nine = []
    for restitution in GRID_RESTITUTION_VALUES:
        for radius in GRID_RADIUS_VALUES:
            monkeypatch.setattr(collision, "COLLISION_RESTITUTION", restitution)
            monkeypatch.setattr(collision, "PIN_EFFECTIVE_RADIUS_IN", radius)
            monkeypatch.setattr(collision, "FALL_DISPLACEMENT_THRESHOLD_IN", radius)
            outcome = _grid_outcome(restitution, radius)
            if outcome["pocket"] >= 9:
                reaching_nine.append((restitution, radius, outcome))

    assert len(reaching_nine) == 13

    for restitution, radius, outcome in reaching_nine:
        where = (restitution, radius, outcome)
        # The universal blocker.
        assert outcome["corner10"] > outcome["pocket"] - 3, where
        # The weaker envelope that is genuinely grid-wide.
        assert outcome["light"] >= 7, where
        assert outcome["corner10"] >= 8, where
        assert outcome["head_on"] >= 9, where
        assert outcome["head_on"] >= outcome["pocket"], where
        assert outcome["brooklyn"] == 9, where
        assert outcome["outside"] == 1, where
        assert outcome["spare_3_6_10"] == 3, where

    # The thin control is *not* universally flattened: in 4 of the 13 it
    # still clears its own margin. This is why the note names the corner as
    # the blocker instead of claiming every control collapses.
    thin_still_separated = [
        (restitution, radius)
        for restitution, radius, outcome in reaching_nine
        if outcome["light"] <= outcome["pocket"] - 2
    ]
    assert len(thin_still_separated) == 4, thin_still_separated

    # And the three concrete counterexamples that make the old, stronger
    # wording false -- pinned so the correction cannot quietly regress.
    by_cell = {(restitution, radius): o for restitution, radius, o in reaching_nine}
    assert by_cell[(0.85, 3.2)]["light"] == 7
    assert by_cell[(0.80, 3.6)]["corner10"] == 9
    assert by_cell[(0.90, 3.0)]["head_on"] == 9


def test_the_grid_restores_production_constants_after_every_cell():
    assert collision.COLLISION_RESTITUTION == USBC_RESTITUTION_TARGET
    assert collision.PIN_EFFECTIVE_RADIUS_IN == 2.383
    assert collision.FALL_DISPLACEMENT_THRESHOLD_IN == 2.383
