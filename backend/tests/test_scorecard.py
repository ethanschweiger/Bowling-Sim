"""Pure ten-pin scorecard rules: perfect game, bonus attribution, tenth
frame variants, illegal-roll rejection (state-preserving), unresolved
bonuses staying unresolved (not zero), and bounded totals.
"""

import pytest

from app.scoring.scorecard import Scorecard, ScorecardError


def _roll_all(card: Scorecard, pins_sequence) -> None:
    for pins in pins_sequence:
        card.add_roll(pins)


def test_perfect_game_totals_300_with_ten_frames_correctly_marked():
    card = Scorecard()
    _roll_all(card, [10] * 12)  # 10 strikes + 2 bonus balls in frame 10

    assert card.is_game_complete
    assert card.total_score == 300
    assert len(card.frames) == 10

    for frame in card.frames[:9]:
        assert frame.is_strike
        assert frame.rolls == (10,)

    tenth = card.frames[9]
    assert tenth.rolls == (10, 10, 10)
    assert tenth.is_strike
    assert tenth.score == 300

    # Running cumulative total climbs by 30 each frame (10 + next two strikes).
    expected_cumulative = [30 * n for n in range(1, 10)] + [300]
    assert [f.score for f in card.frames] == expected_cumulative


def test_representative_sequence_attributes_bonuses_to_the_correct_frame():
    # Frame 1: strike -> needs next two balls (frame 2's 7, 3) => 10+7+3=20
    # Frame 2: spare (7,3) -> needs next one ball (frame 3's 4) => 10+4=14
    # Frame 3: open (4,2) => 6
    # Frames 4-10: all gutters (0 pins) -> 0 each, no bonuses.
    rolls = [10, 7, 3, 4, 2] + [0] * 14  # frames 4-9 = 12 rolls of 0, frame 10 = 2 rolls of 0
    card = Scorecard()
    _roll_all(card, rolls)

    assert card.is_game_complete
    f1, f2, f3 = card.frames[0], card.frames[1], card.frames[2]
    assert f1.is_strike and f1.score == 20
    assert f2.is_spare and f2.rolls == (7, 3) and f2.score == 20 + 14
    assert f3.rolls == (4, 2) and f3.score == 20 + 14 + 6
    for frame in card.frames[3:]:
        assert frame.rolls == (0, 0)
    assert card.total_score == 20 + 14 + 6


def test_tenth_frame_spare_permits_exactly_one_bonus_roll():
    card = Scorecard()
    _roll_all(card, [0] * 18)  # frames 1-9 all gutters
    card.add_roll(6)
    card.add_roll(4)  # spare in frame 10
    card.add_roll(5)  # the one legal bonus ball

    assert card.is_game_complete
    tenth = card.frames[9]
    assert tenth.is_spare
    assert tenth.rolls == (6, 4, 5)
    assert tenth.score == 15  # frame 10 is self-contained: 6+4+5

    with pytest.raises(ScorecardError):
        card.add_roll(0)  # no further rolls are legal


def test_tenth_frame_strike_permits_exactly_two_bonus_rolls():
    card = Scorecard()
    _roll_all(card, [0] * 18)
    card.add_roll(10)  # strike in frame 10
    card.add_roll(5)
    card.add_roll(5)  # legal: ball 2 didn't clear the fresh rack by itself, but this is ball 3 not exceeding it

    assert card.is_game_complete
    tenth = card.frames[9]
    assert tenth.rolls == (10, 5, 5)
    assert tenth.score == 20

    with pytest.raises(ScorecardError):
        card.add_roll(0)


def test_tenth_frame_open_permits_no_bonus_roll():
    card = Scorecard()
    _roll_all(card, [0] * 18)
    card.add_roll(3)
    card.add_roll(4)  # open — 7 total, no spare

    assert card.is_game_complete
    tenth = card.frames[9]
    assert not tenth.is_strike and not tenth.is_spare
    assert tenth.score == 7

    with pytest.raises(ScorecardError):
        card.add_roll(0)  # open frame 10 gets no bonus ball at all


def test_illegal_frame_total_raises_and_leaves_state_unchanged():
    card = Scorecard()
    card.add_roll(6)
    snapshot_rolls = card.frames

    with pytest.raises(ScorecardError):
        card.add_roll(5)  # 6 + 5 = 11 > 10 pins available

    assert card.frames == snapshot_rolls
    assert card.frames[0].rolls == (6,)
    assert card.frames[0].is_complete is False


def test_illegal_tenth_frame_sequence_raises_and_leaves_state_unchanged():
    card = Scorecard()
    _roll_all(card, [0] * 18)
    card.add_roll(4)
    card.add_roll(3)  # open tenth (7 total) — no bonus allowed
    snapshot = card.frames

    with pytest.raises(ScorecardError):
        card.add_roll(2)

    assert card.frames == snapshot
    assert card.is_game_complete  # the open tenth frame was already a complete, legal game


def test_roll_after_game_completion_raises_and_leaves_state_unchanged():
    card = Scorecard()
    _roll_all(card, [0] * 20)  # a full, legal (if unimpressive) game
    assert card.is_game_complete
    snapshot = card.frames

    with pytest.raises(ScorecardError):
        card.add_roll(0)

    assert card.frames == snapshot


def test_out_of_range_pins_raise_and_leave_state_unchanged():
    card = Scorecard()
    card.add_roll(5)
    snapshot = card.frames

    with pytest.raises(ScorecardError):
        card.add_roll(11)
    with pytest.raises(ScorecardError):
        card.add_roll(-1)

    assert card.frames == snapshot


def test_incomplete_bonuses_remain_unresolved_not_zero():
    card = Scorecard()
    card.add_roll(10)  # frame 1: strike, needs 2 bonus balls not yet thrown

    assert card.frames[0].is_strike
    assert card.frames[0].score is None  # not 10, not 10+0+0 — genuinely unknown
    assert card.total_score is None

    card.add_roll(4)  # one of two bonus balls in — still unresolved
    assert card.frames[0].score is None

    card.add_roll(3)  # second bonus ball in — now resolvable
    assert card.frames[0].score == 17


def test_a_gap_in_resolution_keeps_that_frames_own_score_none():
    card = Scorecard()
    card.add_roll(10)  # frame 1 strike, unresolved until 2 more balls land
    card.add_roll(0)
    card.add_roll(0)  # frame 2: open, 0 — this alone doesn't unblock frame 1's bonus need

    # Frame 1 needed rolls[1] and rolls[2] as bonus — both now thrown (0, 0) — resolved.
    assert card.frames[0].score == 10
    assert card.frames[1].score == 10  # cumulative: 10 (frame1) + 0 (frame2)

    card.add_roll(10)  # frame 3: strike, immediately unresolved again
    assert card.frames[2].score is None
    # total_score reports the last *resolved* frame's cumulative — what a
    # scorekeeper could currently state — not None just because the frame
    # in progress hasn't resolved yet.
    assert card.total_score == 10


def test_total_score_is_none_only_before_anything_has_resolved():
    card = Scorecard()
    card.add_roll(10)  # frame 1: strike, no bonus balls thrown yet — nothing has resolved
    assert card.frames[0].score is None
    assert card.total_score is None


def test_complete_legal_games_stay_within_0_and_300():
    all_gutters = Scorecard()
    _roll_all(all_gutters, [0] * 20)
    assert all_gutters.is_game_complete
    assert all_gutters.total_score == 0

    all_strikes = Scorecard()
    _roll_all(all_strikes, [10] * 12)
    assert all_strikes.is_game_complete
    assert all_strikes.total_score == 300

    mixed = Scorecard()
    _roll_all(mixed, [3, 4, 10, 2, 3, 10, 10, 4, 6, 10, 0, 0, 10, 10, 10, 10])
    assert mixed.is_game_complete
    assert 0 <= mixed.total_score <= 300
    assert mixed.total_score == 161  # hand-verified: cumulative bonus attribution frame by frame
