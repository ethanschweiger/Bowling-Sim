"""A fixed-seed corpus for planar pocket carry, and what it shows.

The question this exists to answer: a legitimate right-handed pocket entry
ends at about seven pins, and we wanted to know whether the existing 2D
collision constants can be calibrated to produce believable *typical* carry.

The answer recorded here is **no**, and the reason is structural rather than
a coefficient. The tests below are the evidence, run rather than asserted:

1. The pins a pocket hit leaves standing (2, 4, 7) have **exactly zero**
   displacement. They are never contacted at all, so they are not marginal
   calls that a threshold tweak could reclassify.
2. `LINEAR_DAMPING_PER_S` therefore has no effect on the outcome across its
   entire plausible range. Extra travel time cannot help a pin that never
   receives an impulse.
3. `FALL_DISPLACEMENT_THRESHOLD_IN` cannot help for the same reason: with
   nothing between zero and the threshold, lowering it reclassifies nothing.
4. `COLLISION_RESTITUTION` moves the representative line from 7 to 8 but
   leaves *typical* pocket carry essentially unchanged across the credible
   entry range.
5. `PIN_EFFECTIVE_RADIUS_IN` does reach nine and ten — but only by making
   every line carry. It inflates the corner-hit control before it improves
   the pocket at all, and at the radii that reach nine a thin hit and a
   corner hit both knock eight. That is a fragile deck, not carry.

Why structurally: real 2-4-7 carry comes from the headpin deflecting left
into the 2, which drives the 4 and the 7. Here the headpin moves about
3.3 in, because the ball begins the run already overlapping it — at the
pocket line by about 4.07 in — so that first contact is resolved largely by
positional correction rather than by an impulse. A flat disc also cannot
sweep the deck the way a 15 in pin topples across it. Neither of those is a
calibration constant.

These tests pin the *current* behaviour. If a future change alters any of it,
that is a real result to be explained, not a number to be edited.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.physics import collision
from app.physics.collision import (
    FALL_DISPLACEMENT_THRESHOLD_IN,
    MAX_COLLISION_STEPS,
    _simulate_collision_detail,
    simulate_collision,
)
from app.physics.impact import ImpactState

client = TestClient(app)

# Held fixed so a case differs from its neighbours only in the release
# variables under study — the same reference ball the rest of the collision
# tests use.
BALL_MASS_LBS = 15.0
BALL_RADIUS_IN = 4.29
LANE_CONDITION_VERSION = 1


@dataclass(frozen=True)
class CarryCase:
    """One named entry line and the outcome this model currently produces."""

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


# The corpus. `pocket` is the representative right-handed entry; the rest are
# controls that a credible calibration has to keep distinguishable from it.
POCKET_CARRY_CORPUS: tuple = (
    CarryCase(
        name="pocket",
        summary="Right-hander's 1-3 pocket entry — the representative case.",
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


def _run(case: CarryCase):
    return _simulate_collision_detail(
        case.impact(), standing_ids=case.standing_ids, record_replay=True
    )


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


def test_the_end_to_end_house_shot_reproduces_the_representative_case():
    """A real seeded throw through both the trajectory and the collision, so
    the corpus is anchored to something a user can actually bowl."""
    game_id = client.post("/api/v1/games", json={}).json()["game_id"]
    body = client.post(
        f"/api/v1/games/{game_id}/throws",
        json={
            "ball_id": "reactive_pearl",
            "seed": 17,
            "speed_mph": 17.0,
            "rev_rate": 350.0,
            "axis_rotation": 45.0,
            "axis_tilt": 15.0,
            "launch_angle": -1.5,
            "launch_position": 28.0,
        },
    ).json()

    assert body["entry_board"] == pytest.approx(16.686, abs=1e-3)
    assert body["entry_angle_deg"] == pytest.approx(1.32, abs=1e-2)
    assert body["speed_at_pins_mph"] == pytest.approx(16.25, abs=1e-2)
    # The same seven-pin result the direct corpus records, leaving the 2-4-7.
    assert body["pins_knocked"] == 7
    assert body["pinfall"]["fallen_pin_ids"] == [1, 3, 5, 6, 8, 9, 10]
    assert body["game_state"]["standing_pin_ids"] == [2, 4, 7]


# --- Why the constants cannot fix it -------------------------------------
#
# Each of these ran before any tuning was attempted, and each is the reason
# one candidate lever was rejected. They are kept so the conclusion stays
# checkable rather than remembered.


def test_the_pocket_survivors_are_never_contacted_at_all():
    """The core finding. 2, 4 and 7 are not marginal -- they are untouched,
    so no threshold can reclassify them and no extra travel time can reach
    them."""
    run = _run(_case("pocket"))
    displacement = _displacements(run)
    standing = set(range(1, 11)) - set(run.fallen_pin_ids)

    assert standing == {2, 4, 7}
    for pin_id in standing:
        assert displacement[pin_id] == 0.0, f"pin {pin_id} was expected untouched"

    # And nothing anywhere sits between zero and the threshold.
    assert not [d for d in displacement.values() if 0.0 < d < FALL_DISPLACEMENT_THRESHOLD_IN]


def test_the_headpin_barely_moves_because_the_ball_starts_overlapping_it():
    """Why the left-side chain never starts: the headpin receives almost no
    velocity, so it cannot drive the 2, which cannot drive the 4 and 7."""
    run = _run(_case("pocket"))
    displacement = _displacements(run)

    # The ball begins the run already overlapping pin 1, so that contact is
    # resolved mostly by positional correction rather than an impulse.
    contact_distance = BALL_RADIUS_IN + collision.PIN_EFFECTIVE_RADIUS_IN
    overlap = contact_distance - abs(_case("pocket").lateral_position_in)
    assert overlap > 4.0, "the pocket line starts deeply overlapped with the headpin"

    # The headpin moves a few inches; every pin the ball or the chain truly
    # strikes moves by tens of inches.
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
    line carry rather than making the pocket carry."""
    pocket, corner = _case("pocket"), _case("corner10")

    for radius in (2.6, 2.7, 2.8):
        monkeypatch.setattr(collision, "PIN_EFFECTIVE_RADIUS_IN", radius)
        # The pocket has not improved...
        assert len(_run(pocket).fallen_pin_ids) == pocket.pins, radius
        # ...while the corner hit already knocks more down.
        assert len(_run(corner).fallen_pin_ids) > corner.pins, radius


def test_reaching_nine_at_the_pocket_flattens_the_controls(monkeypatch):
    """The threshold *is* reachable — and this is what it costs. At the
    radius that first gets the pocket to nine, a thin hit and a corner hit
    both knock eight and a flush headpin hit strikes. The model stops
    telling one shot from another, which is worse than carrying too little."""
    monkeypatch.setattr(collision, "PIN_EFFECTIVE_RADIUS_IN", 3.6)

    outcome = {case.name: len(_run(case).fallen_pin_ids) for case in POCKET_CARRY_CORPUS}

    assert outcome["pocket"] >= 9, "the radius does reach the stated threshold"
    # But every control follows it up.
    assert outcome["light"] >= 8, "a graze now carries like a pocket hit"
    assert outcome["corner10"] >= 8, "a corner hit now carries like a pocket hit"
    assert outcome["head_on"] >= outcome["pocket"], "a flush hit out-carries the pocket"
