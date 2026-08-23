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
 * exactly the part a viewer most wants to read clearly. `distanceToY`
 * therefore gives progressively more pixels per foot toward the pin deck.
 *
 * It does that with one smooth exponential map rather than two linear
 * bands meeting at a split point. An earlier version handed a fixed share
 * of the canvas to the last five feet, which put a step change in the
 * scale's *derivative* at 55 ft: the physical path was smooth, but its
 * drawn form acquired a visible kink there purely from the projection.
 * `distanceEase` below is monotonic and smooth everywhere (its derivative
 * is continuous, so no distance is a special case), which keeps the deck
 * readable without inventing a bend the simulation never produced.
 *
 * This is presentation only. It never alters the server's path samples —
 * it just chooses where on the canvas each one lands.
 */

import { BOARD_COUNT, PIN_DECK_BACK_ROW_FT } from './pinDeckLayout';

// A little margin either side of the lane's own 1..39 boards and past the
// pin deck's back row, so nothing touches the canvas edge.
const MIN_BOARD = 0;
const MAX_BOARD = BOARD_COUNT + 1;
/** The default near edge: the foul line. */
export const DEFAULT_MIN_DISTANCE_FT = 0;
/** The default far edge: just past the pin deck's back row. Enough for the
 * lane itself and a settled rack, and the exact geometry every no-replay
 * drawing has always used. */
export const DEFAULT_MAX_DISTANCE_FT = PIN_DECK_BACK_ROW_FT + 2;

/**
 * The downlane span a projection covers, in feet.
 *
 * Explicit and two-sided rather than a bare "max" argument: a replay can
 * push bodies past the deck *and* back behind the foul line, and clamping
 * at either edge would paint a body somewhere it never was. Callers pass
 * the span they actually intend to draw; the projection then never has to
 * clamp an accepted position.
 */
export interface LaneDistanceBounds {
  minDistanceFt: number;
  maxDistanceFt: number;
}

export const DEFAULT_DISTANCE_BOUNDS: LaneDistanceBounds = {
  minDistanceFt: DEFAULT_MIN_DISTANCE_FT,
  maxDistanceFt: DEFAULT_MAX_DISTANCE_FT,
};

/** How strongly the far end of the lane is emphasized. 0 would be a plain
 * linear scale; this gives the pin deck roughly five times the pixels per
 * foot of the foul-line end, with no discontinuity anywhere between. */
export const DISTANCE_EMPHASIS = 1.6;

/**
 * Maps a normalized downlane fraction to a normalized canvas fraction,
 * expanding the far end. Smooth (C-infinity) and strictly increasing on
 * [0, 1], with `distanceEase(0) === 0` and `distanceEase(1) === 1`.
 */
export function distanceEase(t: number): number {
  const clamped = Math.max(0, Math.min(1, t));
  return (Math.exp(DISTANCE_EMPHASIS * clamped) - 1) / (Math.exp(DISTANCE_EMPHASIS) - 1);
}

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
export function createLaneProjection(
  width: number,
  height: number,
  padding = 12,
  bounds: LaneDistanceBounds = DEFAULT_DISTANCE_BOUNDS,
): LaneProjection {
  const innerWidth = Math.max(width - padding * 2, 1);
  const innerHeight = Math.max(height - padding * 2, 1);
  // Only ever widened, never narrowed: the lane and its settled rack must
  // stay exactly where they have always been drawn, so a caller can add
  // room at either end but cannot pull an edge inward.
  const nearEdgeFt = Math.min(bounds.minDistanceFt, DEFAULT_MIN_DISTANCE_FT);
  const farEdgeFt = Math.max(bounds.maxDistanceFt, DEFAULT_MAX_DISTANCE_FT);
  const span = farEdgeFt - nearEdgeFt;

  return {
    boardToX(board: number): number {
      const t = (board - MIN_BOARD) / (MAX_BOARD - MIN_BOARD);
      return padding + (1 - t) * innerWidth;
    },
    distanceToY(distanceFt: number): number {
      // Still clamped, but to a span the caller has already sized to
      // contain everything it intends to draw — so for an accepted replay
      // this never fires at either end, and no body is pinned to an edge
      // at a position it doesn't actually hold. See
      // `replayDistanceExtentFt`.
      const clamped = Math.max(nearEdgeFt, Math.min(farEdgeFt, distanceFt));
      const linear = (clamped - nearEdgeFt) / span;
      return padding + (1 - distanceEase(linear)) * innerHeight;
    },
  };
}
