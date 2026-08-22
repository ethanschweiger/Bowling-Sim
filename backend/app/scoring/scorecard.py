"""Standard ten-pin scoring rules — a pure, framework-independent domain
model.

`Scorecard` takes one roll's pinfall count at a time and knows nothing
else: no lane, no collision physics, no HTTP, no database, no
WebSockets. It doesn't simulate a rack or decide which physical pins are
still standing — a future game-session integration supplies an
already-valid pinfall count per roll (from a `PinfallResult`) and
separately decides the rack for the *next* throw. This module only turns
a sequence of pinfall counts into frame states and a score, exactly per
USBC ten-pin rules.

## Rules encoded

Frames 1-9: two balls unless the first is a strike (10 pins), which ends
the frame immediately. An open frame scores the sum of its two balls; a
spare (two balls summing to 10) scores 10 plus the next ball thrown; a
strike scores 10 plus the next two balls thrown — which may come from the
following frame, or (for a strike in frame 9) from frame 10's own rolls.

Frame 10 is self-contained: a strike or spare earns bonus ball(s) *within
frame 10 itself*, against a rack that resets after any strike or a spare.
An open tenth frame gets no bonus roll at all. See `_layout_tenth_frame`
for the exact ball-by-ball legality.

## Rejection semantics

Every illegal `add_roll` call raises `ScorecardError` and leaves the
scorecard completely unchanged — rejection is validate-then-commit, never
partial: the prospective new roll sequence is fully re-validated before
anything is written to the scorecard's own state.

## Unresolved vs. zero

A frame that needs bonus balls it hasn't received yet (an incomplete
strike or spare) reports `Frame.score` as `None`, never as a number
computed by treating a missing bonus as zero. Once any frame's score is
unresolved, every later frame's cumulative score is `None` too — a
running total can't skip past a gap.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

FRAME_COUNT = 10
MAX_PINS = 10


class ScorecardError(Exception):
    """Raised by `Scorecard.add_roll` for any illegal roll. The scorecard
    is left exactly as it was before the call."""


@dataclass(frozen=True)
class Frame:
    number: int              # 1-10
    rolls: tuple             # the pinfall values thrown in this frame (1-3 ints)
    is_strike: bool
    is_spare: bool
    is_complete: bool        # this frame has thrown every roll it owns (not necessarily scored yet)
    score: Optional[int]     # cumulative total through this frame, or None if unresolved


@dataclass(frozen=True)
class _RawFrame:
    """Internal: a frame's layout in the flat roll sequence, before scoring."""

    number: int
    rolls: tuple
    end_index: int  # index into the flat roll list right after this frame's own rolls
    is_strike: bool
    is_spare: bool
    is_complete: bool


def _layout_tenth_frame(rolls: list, i: int) -> tuple:
    """Lays out frame 10 starting at index i. Returns (_RawFrame, next_index).
    Raises ScorecardError for any illegal tenth-frame sequence."""
    n = len(rolls)
    first = rolls[i]

    if i + 1 >= n:
        return _RawFrame(10, (first,), i + 1, first == MAX_PINS, False, False), i + 1

    second = rolls[i + 1]

    if first == MAX_PINS:
        # Strike on ball 1: ball 2 is against a fresh rack — no same-rack
        # constraint against `first`.
        if i + 2 >= n:
            return _RawFrame(10, (first, second), i + 2, True, False, False), i + 2
        third = rolls[i + 2]
        if second < MAX_PINS and second + third > MAX_PINS:
            raise ScorecardError(
                f"frame 10: ball 2 ({second}) + ball 3 ({third}) exceed the {MAX_PINS} pins on that rack"
            )
        return _RawFrame(10, (first, second, third), i + 3, True, False, True), i + 3

    if first + second > MAX_PINS:
        raise ScorecardError(f"frame 10: ball 1 ({first}) + ball 2 ({second}) exceed {MAX_PINS} pins")

    if first + second < MAX_PINS:
        # Open tenth frame: exactly two balls, no bonus roll.
        if i + 2 < n:
            raise ScorecardError("frame 10: an open frame does not get a bonus roll")
        return _RawFrame(10, (first, second), i + 2, False, False, True), i + 2

    # Spare on balls 1+2: exactly one bonus ball, against a fresh rack.
    if i + 2 >= n:
        return _RawFrame(10, (first, second), i + 2, False, True, False), i + 2
    third = rolls[i + 2]
    if i + 3 < n:
        raise ScorecardError("frame 10: no rolls are legal after the spare's bonus ball")
    return _RawFrame(10, (first, second, third), i + 3, False, True, True), i + 3


def _layout(rolls: list) -> list:
    """Walks the flat roll list into up to 10 _RawFrames. Raises
    ScorecardError for any illegal roll — out of range, exceeding the pins
    available in that frame/ball, an illegal tenth-frame sequence, or a
    roll thrown after the game is already complete."""
    for pins in rolls:
        if not (0 <= pins <= MAX_PINS):
            raise ScorecardError(f"pins must be 0-{MAX_PINS}, got {pins}")

    frames = []
    i = 0
    n = len(rolls)
    for frame_number in range(1, FRAME_COUNT + 1):
        if i >= n:
            break

        if frame_number < FRAME_COUNT:
            first = rolls[i]
            if first == MAX_PINS:
                frames.append(_RawFrame(frame_number, (first,), i + 1, True, False, True))
                i += 1
                continue

            if i + 1 >= n:
                frames.append(_RawFrame(frame_number, (first,), i + 1, False, False, False))
                i += 1
                break

            second = rolls[i + 1]
            if first + second > MAX_PINS:
                raise ScorecardError(
                    f"frame {frame_number}: ball 1 ({first}) + ball 2 ({second}) exceed {MAX_PINS} pins"
                )
            is_spare = first + second == MAX_PINS
            frames.append(_RawFrame(frame_number, (first, second), i + 2, False, is_spare, True))
            i += 2
        else:
            raw, i = _layout_tenth_frame(rolls, i)
            frames.append(raw)

    if i < n:
        raise ScorecardError("a roll was thrown after the game was already complete")

    return frames


def _own_points(raw: "_RawFrame", rolls: list, is_last_frame: bool) -> Optional[int]:
    """This frame's own point contribution (not cumulative), or None if a
    strike/spare bonus it needs hasn't been thrown yet. Frame 10 is
    self-contained — its bonus balls are already inside raw.rolls, so it
    never depends on rolls outside itself."""
    if not raw.is_complete:
        return None

    base = sum(raw.rolls)
    if is_last_frame:
        return base

    if raw.is_strike:
        bonus = rolls[raw.end_index : raw.end_index + 2]
        return base + sum(bonus) if len(bonus) == 2 else None
    if raw.is_spare:
        bonus = rolls[raw.end_index : raw.end_index + 1]
        return base + sum(bonus) if len(bonus) == 1 else None
    return base


def _build_frames(rolls: list) -> tuple:
    raw_frames = _layout(rolls)  # raises ScorecardError; no partial frames list escapes on failure

    frames = []
    cumulative = 0
    unresolved = False
    for raw in raw_frames:
        points = _own_points(raw, rolls, is_last_frame=(raw.number == FRAME_COUNT))
        if points is None:
            unresolved = True
        else:
            cumulative += points
        score = None if unresolved else cumulative
        frames.append(
            Frame(
                number=raw.number,
                rolls=raw.rolls,
                is_strike=raw.is_strike,
                is_spare=raw.is_spare,
                is_complete=raw.is_complete,
                score=score,
            )
        )
    return tuple(frames)


class Scorecard:
    """One ten-pin game. Deterministic, no random input, no external state."""

    def __init__(self) -> None:
        self._rolls: list = []
        self._frames: tuple = ()

    def add_roll(self, pins: int) -> None:
        """Records one ball's pinfall count (0-10). Raises ScorecardError,
        leaving the scorecard completely unchanged, if this roll would be
        illegal anywhere in the game: out of range, more pins than remain
        standing in that ball of the frame, an illegal tenth-frame bonus
        sequence, or a roll thrown after the game is already complete.
        """
        if not isinstance(pins, int) or not (0 <= pins <= MAX_PINS):
            raise ScorecardError(f"pins must be an integer 0-{MAX_PINS}, got {pins!r}")

        candidate_rolls = self._rolls + [pins]
        candidate_frames = _build_frames(candidate_rolls)  # raises ScorecardError on any violation

        self._rolls = candidate_rolls
        self._frames = candidate_frames

    @property
    def frames(self) -> tuple:
        """Every frame started so far (0-10 of them), each a `Frame`."""
        return self._frames

    @property
    def is_game_complete(self) -> bool:
        return len(self._frames) == FRAME_COUNT and self._frames[-1].is_complete

    @property
    def total_score(self) -> Optional[int]:
        """The cumulative score through the most recent *resolved* frame —
        what a scorekeeper could currently state as "the score so far,"
        the same way a real scorecard reads even while the next frame is
        still in progress. None only if no frame has resolved at all yet
        (e.g. the very first roll was a strike still awaiting its bonus).
        A complete game's last frame is always resolved (frame 10 is
        self-contained), so this equals frames[-1].score once
        `is_game_complete` is True.
        """
        for frame in reversed(self._frames):
            if frame.score is not None:
                return frame.score
        return None

    def next_ball_starts_fresh_rack(self) -> bool:
        """Whether the next legal roll — if the game isn't already
        complete — would be thrown against a fresh, full rack, as opposed
        to whatever's left standing from the previous ball in the same
        frame. True for: the very first roll of the game; the first ball
        of any frame; any ball immediately following a strike (frames
        1-9, or either strike ball in frame 10); and frame 10's bonus
        ball after a spare. False only when the next ball continues on
        the remainder left by the previous ball in the same frame.

        A pure read of the frames already computed by `add_roll` — this
        doesn't re-derive or duplicate the ten-pin rules that produced
        them, it only reads `is_strike`/`is_spare`/`rolls`/`is_complete`
        off the existing `Frame` objects. Meaningless (but still returns
        a bool rather than raising) once `is_game_complete` is True —
        callers that care should check that first.
        """
        if not self._frames:
            return True  # frame 1, ball 1
        last = self._frames[-1]
        if last.is_complete:
            return True  # next roll starts a new frame

        if last.number < FRAME_COUNT:
            # Frames 1-9 are only ever incomplete after one non-strike
            # ball (a strike frame is complete immediately) — ball 2
            # continues on that ball's remainder.
            return False

        # Frame 10, incomplete.
        if len(last.rolls) == 1:
            return last.is_strike  # ball 1 strike -> ball 2 fresh; else ball 2 continues
        # Two rolls thrown, frame 10 still incomplete: reachable only via
        # a ball-1 strike (ball 3's freshness depends on ball 2) or a
        # ball-1+ball-2 spare (ball 3 is always fresh).
        if last.is_spare:
            return True
        return last.rolls[1] == MAX_PINS

    def next_roll_position(self) -> Tuple[Optional[int], Optional[int]]:
        """(frame_number, ball_number) the next legal roll would belong
        to — both 1-based — or (None, None) if the game is already
        complete. Another pure read of the existing `frames`; no rule
        duplication."""
        if self.is_game_complete:
            return None, None
        if not self._frames or self._frames[-1].is_complete:
            next_frame = self._frames[-1].number + 1 if self._frames else 1
            return next_frame, 1
        last = self._frames[-1]
        return last.number, len(last.rolls) + 1
