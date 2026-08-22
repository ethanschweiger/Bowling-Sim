/**
 * Pure logic behind the lane canvas's trajectory animation and "Replay
 * last shot" action. Nothing here touches the DOM, `requestAnimationFrame`,
 * or the API client — it's testable without a browser or a timer.
 *
 * The animation is presentation of a response the server already
 * computed, not a second physics simulation: it interpolates *between*
 * the exact points `path` already contains, and never invents, decays,
 * or recalculates a trajectory of its own. `path` points are recorded at
 * fixed downlane-distance steps (`STEP_FT` in
 * `backend/app/physics/simulate.py`), not at fixed time steps — there is
 * no per-point timestamp to animate against. So "progress" here is a
 * fraction of the path's own point sequence over one fixed, documented
 * visual playback duration, not a reproduction of real ball-travel time.
 */

import type { GameThrowResponse, TrajectoryPointResponse } from '../api/types';

/** Visual playback duration only — not calibrated to any real ball speed
 * or the throw's own `speed_at_pins_mph`. See the module docstring. */
export const TRAJECTORY_ANIMATION_DURATION_MS = 900;

export interface PathPosition {
  board: number;
  distanceFt: number;
}

/** Interpolates a position along `path` at a normalized `progress` in
 * [0, 1] — 0 is exactly the first recorded point, 1 is exactly the last,
 * everything between is a straight line between the two nearest recorded
 * points. `progress` outside [0, 1] clamps to the nearer endpoint. */
export function interpolatePathPosition(path: readonly TrajectoryPointResponse[], progress: number): PathPosition {
  if (path.length === 0) {
    return { board: 0, distanceFt: 0 };
  }
  if (path.length === 1) {
    return { board: path[0].board, distanceFt: path[0].distance_ft };
  }

  const clamped = Math.max(0, Math.min(1, progress));
  const scaled = clamped * (path.length - 1);
  const lowerIndex = Math.floor(scaled);
  const upperIndex = Math.min(lowerIndex + 1, path.length - 1);
  const localT = scaled - lowerIndex;
  const lower = path[lowerIndex];
  const upper = path[upperIndex];

  return {
    board: lower.board + (upper.board - lower.board) * localT,
    distanceFt: lower.distance_ft + (upper.distance_ft - lower.distance_ft) * localT,
  };
}

/** Ease-out cubic: fast start, gentle finish. A purely visual timing
 * curve applied to elapsed-time fraction — the ball's real deceleration
 * is already baked into `path`'s own point spacing; this only shapes how
 * smoothly the on-screen marker advances through those fixed points. */
export function easeOutCubic(t: number): number {
  const clamped = Math.max(0, Math.min(1, t));
  return 1 - Math.pow(1 - clamped, 3);
}

/** The animation's starting progress for a given reduced-motion
 * preference: 1 (fully settled — the complete static trajectory, no
 * autoplay) when the user has asked for reduced motion, 0 (the very
 * start of the path) otherwise. */
export function initialAnimationProgress(reducedMotion: boolean): number {
  return reducedMotion ? 1 : 0;
}

/** Whether "Replay last shot" should be enabled right now: a completed
 * throw with a real path exists, no request is in flight, and the saved
 * game isn't in the confirmed-stale state. A pure predicate — it never
 * reads game state beyond its own three arguments and never calls the
 * API client (this module imports nothing from `api/client`), so
 * replaying can't mutate score, pins, lane condition, game id, or
 * release values; it only restarts the canvas's own local animation over
 * the exact path already stored from that throw. */
export function canReplay(
  latestThrow: Pick<GameThrowResponse, 'path'> | null,
  isBusy: boolean,
  isStale: boolean,
): boolean {
  return latestThrow !== null && latestThrow.path.length > 0 && !isBusy && !isStale;
}
