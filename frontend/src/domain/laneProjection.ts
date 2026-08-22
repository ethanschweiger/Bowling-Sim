/**
 * Pure board/distance -> pixel-coordinate mapping for `LaneCanvas`. Kept
 * separate from the drawing code so the coordinate convention itself is
 * unit-testable without a real `<canvas>`.
 *
 * The lane is drawn several times narrower, physically, than it is long
 * (39 boards at 1.05 in each is ~3.4 ft across a 60+ ft lane) — drawn to
 * true scale that would be an unreadably thin sliver. Like every bowling
 * broadcast graphic and scoring-app lane view, board (lateral) spacing is
 * deliberately exaggerated relative to distance (downlane) spacing; only
 * the *ordering* and *relative* position of boards and pins is faithful,
 * not the true aspect ratio.
 *
 * Distance gets one more deliberate distortion: the pin deck (a ~2.6 ft
 * band) sits at the end of a 60 ft approach, so a single honest linear
 * scale would squeeze it into a sliver a few percent of the canvas tall —
 * exactly the part a viewer most wants to read clearly. `distanceToY` maps
 * the two spans through two different linear scales instead: the first
 * `ZOOM_SPLIT_FT` feet of approach share `1 - ZOOM_BAND_SHARE` of the
 * canvas, and everything past that (the last stretch of approach plus the
 * whole pin deck) gets the remaining `ZOOM_BAND_SHARE`. The mapping is
 * still monotonic and still order-preserving — a later point on a path is
 * always drawn further up the lane — only the *rate* of feet-per-pixel
 * changes at the split, the same trade-off already made for board spacing.
 */

import { BOARD_COUNT, LANE_LENGTH_FT, PIN_DECK_BACK_ROW_FT } from './pinDeckLayout';

// A little margin either side of the lane's own 1..39 boards and past the
// pin deck's back row, so nothing touches the canvas edge.
const MIN_BOARD = 0;
const MAX_BOARD = BOARD_COUNT + 1;
const MIN_DISTANCE_FT = 0;
const MAX_DISTANCE_FT = PIN_DECK_BACK_ROW_FT + 2;

// Where the "zoomed in" band starts, and how much of the canvas it gets.
const ZOOM_SPLIT_FT = LANE_LENGTH_FT - 5; // the last 5 ft of approach, plus the whole pin deck
const ZOOM_BAND_SHARE = 0.45;

export interface LaneProjection {
  boardToX(board: number): number;
  distanceToY(distanceFt: number): number;
}

/** Builds a projection for a `width` x `height` pixel canvas, with `padding`
 * pixels of margin on every side. Board 1 (the right gutter) maps to the
 * right edge and board 39 (the left gutter) to the left edge — a bowler's-
 * eye view standing at the foul line looking down the lane, matching
 * `simulate.py`'s documented "board 1 is the right gutter" convention.
 * Distance 0 (the foul line) maps to the bottom edge and the pin deck to
 * the top — looking away from the bowler, down the lane. */
export function createLaneProjection(width: number, height: number, padding = 12): LaneProjection {
  const innerWidth = Math.max(width - padding * 2, 1);
  const innerHeight = Math.max(height - padding * 2, 1);

  return {
    boardToX(board: number): number {
      const t = (board - MIN_BOARD) / (MAX_BOARD - MIN_BOARD);
      return padding + (1 - t) * innerWidth;
    },
    distanceToY(distanceFt: number): number {
      const clamped = Math.max(MIN_DISTANCE_FT, Math.min(MAX_DISTANCE_FT, distanceFt));
      const t =
        clamped <= ZOOM_SPLIT_FT
          ? ((clamped - MIN_DISTANCE_FT) / (ZOOM_SPLIT_FT - MIN_DISTANCE_FT)) * (1 - ZOOM_BAND_SHARE)
          : 1 - ZOOM_BAND_SHARE + ((clamped - ZOOM_SPLIT_FT) / (MAX_DISTANCE_FT - ZOOM_SPLIT_FT)) * ZOOM_BAND_SHARE;
      return padding + (1 - t) * innerHeight;
    },
  };
}
