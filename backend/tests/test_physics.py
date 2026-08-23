
from app.physics.ball import Ball, Coverstock
from app.physics.lane import LaneCondition, apply_wear
from app.physics.simulate import TrajectoryPoint, simulate_throw
from app.physics.throw import Throw

BASE_BALL_SPECS = dict(rg_in=2.52, differential=0.052, surface="2000-grit")
THROW = Throw(
    speed_mph=17.0, rev_rate=380.0, axis_rotation=55.0,
    axis_tilt=10.0, launch_angle=0.4, launch_position=20.0,
)


def test_coverstock_keeps_its_str_enum_mixin_string_behavior():
    """Pins the `str, Enum` behavior `app/physics/ball.py` deliberately keeps
    instead of `enum.StrEnum` (see its `# noqa: UP042`): `str()` uses Enum's
    own default (`"Coverstock.REACTIVE"`, not the plain value) while
    equality/`.value`/`isinstance` against a plain string still hold.
    `enum.StrEnum` would change `str()` silently -- this is what would catch
    that regression, not just a comment.

    Deliberately does NOT assert `format()`/f-string output: CPython 3.11
    changed how a plain (non-ReprEnum) `str, Enum` mixin formats -- 'reactive'
    on 3.9, 'Coverstock.REACTIVE' on 3.11 -- for any such class, independent
    of this file's choices. Nothing in this codebase formats a Coverstock
    value, so that difference has no production effect; asserting a specific
    side of it here would make this test fail on whichever Python version
    it wasn't written against, in a project that now has to run both."""
    assert str(Coverstock.REACTIVE) == "Coverstock.REACTIVE"
    assert Coverstock.REACTIVE == "reactive"
    assert Coverstock.REACTIVE.value == "reactive"
    assert isinstance(Coverstock.REACTIVE, str)


def test_plastic_ball_hooks_less_than_a_comparable_reactive_ball():
    plastic = Ball(id="p", name="plastic-test", coverstock=Coverstock.PLASTIC, **BASE_BALL_SPECS)
    reactive = Ball(id="r", name="reactive-test", coverstock=Coverstock.REACTIVE, **BASE_BALL_SPECS)

    lane = LaneCondition.house_shot()
    plastic_result = simulate_throw(plastic, THROW, lane)
    reactive_result = simulate_throw(reactive, THROW, lane)

    plastic_drift = abs(plastic_result.entry_board - THROW.launch_position)
    reactive_drift = abs(reactive_result.entry_board - THROW.launch_position)

    assert reactive.hook_potential > plastic.hook_potential
    assert reactive_drift > plastic_drift


def test_friction_stays_in_bounds_for_out_of_range_positions():
    from app.physics.lane import DRY_FRICTION, OILED_FRICTION

    lane = LaneCondition.house_shot()

    for distance_ft, board in [(-10.0, 20.0), (10_000.0, 20.0), (10.0, -50.0), (10.0, 1_000.0)]:
        friction = lane.friction_at(distance_ft, board)
        assert OILED_FRICTION <= friction <= DRY_FRICTION


def test_repeated_wear_never_drives_oil_negative():
    lane = LaneCondition.house_shot()
    path = [TrajectoryPoint(distance_ft=float(ft), board=20.0) for ft in range(0, 33)]

    for _ in range(500):
        lane = apply_wear(lane, path)

    assert lane.version == 501
    for row in lane.oil_grid:
        for amount in row:
            assert amount >= 0.0
