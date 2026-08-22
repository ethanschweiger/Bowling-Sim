from dataclasses import fields

from app.physics.throw import _NOISE_CLAMP_STD, _RELEASE_NOISE_STD, Throw, sample_release

REQUESTED = Throw(
    speed_mph=17.0,
    rev_rate=350.0,
    axis_rotation=45.0,
    axis_tilt=15.0,
    launch_angle=2.0,
    launch_position=28.0,
)


def test_seeded_throws_repeat_exactly():
    first, seed_a = sample_release(REQUESTED, seed=42)
    second, seed_b = sample_release(REQUESTED, seed=42)

    assert seed_a == seed_b == 42
    assert first == second


def test_different_seeds_produce_bounded_variation():
    a, _ = sample_release(REQUESTED, seed=1)
    b, _ = sample_release(REQUESTED, seed=2)

    assert a != b

    for field in fields(Throw):
        std = _RELEASE_NOISE_STD[field.name]
        bound = std * _NOISE_CLAMP_STD
        requested_value = getattr(REQUESTED, field.name)
        for sampled in (a, b):
            deviation = abs(getattr(sampled, field.name) - requested_value)
            assert deviation <= bound + 1e-9


def test_omitted_seed_is_generated_and_reusable():
    sampled_once, generated_seed = sample_release(REQUESTED)
    sampled_again, reused_seed = sample_release(REQUESTED, seed=generated_seed)

    assert reused_seed == generated_seed
    assert sampled_once == sampled_again
