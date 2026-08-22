/**
 * Static lane and pin-deck geometry for the `<canvas>` drawing only — never
 * consulted for scoring or physics. Every constant is copied from the
 * backend's own documented geometry (`backend/app/physics/lane.py`,
 * `backend/app/physics/units.py`, and `backend/app/physics/pin_deck.py`'s
 * `_build_standard_deck`, itself cited to the USBC equipment manual), not
 * invented for the UI. Which pins are *standing* is never read from here —
 * that always comes from a `game_state.standing_pin_ids` response.
 */

export const BOARD_COUNT = 39; // backend/app/physics/lane.py: BOARD_COUNT
export const LANE_LENGTH_FT = 60.0; // backend/app/physics/lane.py: LANE_LENGTH_FT (foul line to headpin)

const BOARD_WIDTH_IN = 1.05; // backend/app/physics/units.py: BOARD_WIDTH_IN
const PIN_SPACING_IN = 12.0; // backend/app/physics/pin_deck.py: PIN_SPACING_IN
const ROW_SPACING_IN = (PIN_SPACING_IN * Math.sqrt(3)) / 2; // equilateral triangle row height, ~10.392 in
const HEADPIN_DISTANCE_FT = 60.0; // backend/app/physics/pin_deck.py: HEADPIN_DISTANCE_FT
const LANE_CENTER_BOARD = (BOARD_COUNT + 1) / 2; // board 20 of 39

export interface PinLayout {
  id: number;
  /** 0 (headpin) .. 3 (back row). */
  row: number;
  /** Fractional board position, 1-39. */
  board: number;
  distanceFt: number;
}

// (pin_id, row, lateral position in units of half a pin-spacing) — the same
// layout pin_deck.py's `_build_standard_deck` lays out, viewed from the
// foul line:
//     7  8  9  10
//       4  5  6
//         2  3
//           1
const RAW_LAYOUT: readonly (readonly [number, number, number])[] = [
  [1, 0, 0],
  [2, 1, 1],
  [3, 1, -1],
  [4, 2, 2],
  [5, 2, 0],
  [6, 2, -2],
  [7, 3, 3],
  [8, 3, 1],
  [9, 3, -1],
  [10, 3, -3],
];

export const PIN_DECK_LAYOUT: readonly PinLayout[] = RAW_LAYOUT.map(([id, row, halfSpacings]) => ({
  id,
  row,
  board: LANE_CENTER_BOARD + (halfSpacings * (PIN_SPACING_IN / 2)) / BOARD_WIDTH_IN,
  distanceFt: HEADPIN_DISTANCE_FT + (row * (ROW_SPACING_IN / 12)) / 1,
}));

/** How far downlane the pin deck's back row sits — the canvas needs to draw
 * a little past `LANE_LENGTH_FT` to fit the whole triangle. */
export const PIN_DECK_BACK_ROW_FT = Math.max(...PIN_DECK_LAYOUT.map((pin) => pin.distanceFt));
