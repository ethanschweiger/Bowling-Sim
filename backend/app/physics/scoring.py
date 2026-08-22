"""Turns a simulated throw's pin-deck entry into a pin count."""

from app.physics.simulate import SimulationResult

# Board 17.5 is the pocket for a right-handed bowler (the 1-3 pocket).
# Left-handers play the mirror (1-2 pocket); v1 doesn't distinguish handedness yet.
POCKET_BOARD = 17.5
POCKET_WINDOW = 1.5          # boards of slack that still count as "in the pocket"
IDEAL_ENTRY_ANGLE_DEG = 5.0  # angle that carries best through the pins
GUTTER_BOARDS = (0.0, 40.0)  # off the near-side or far-side edge of the lane


def pins_from_entry(result: SimulationResult) -> int:
    """A deterministic, simplified carry model.

    Real carry depends on pin action we aren't modeling yet (v2+). For now:
    dead center of the pocket at a good angle carries all ten; drift away
    from the pocket, or come in too square or too sharp, and pins stay up.
    """
    board = result.entry_board
    if board <= GUTTER_BOARDS[0] or board >= GUTTER_BOARDS[1]:
        return 0

    board_miss = abs(board - POCKET_BOARD)
    angle_miss = abs(result.entry_angle_deg - IDEAL_ENTRY_ANGLE_DEG)

    if board_miss <= POCKET_WINDOW and angle_miss <= 4.0:
        return 10

    # Carry falls off with distance from the pocket and from the ideal angle.
    miss_score = board_miss * 1.1 + angle_miss * 0.6
    pins = round(10 - miss_score)
    return max(0, min(10, pins))
