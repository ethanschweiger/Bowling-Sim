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

/**
 * The path's own final sample — what the canvas draws as the entry
 * marker. Returns the exact element from `path`, never a copy or a
 * recomputed value, so the marker provably sits on the end of the
 * polyline rather than beside it.
 *
 * The response also carries an `entry_board` field. The backend derives
 * both it and this final sample from one unrounded terminal state, so
 * they agree today; drawing *this* keeps them from being able to
 * disagree tomorrow. See `backend/app/physics/simulate.py`'s
 * `TerminalState`.
 */
export function trajectoryEndpoint(
  path: readonly TrajectoryPointResponse[],
): TrajectoryPointResponse | null {
  return path.length > 0 ? path[path.length - 1] : null;
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
 * throw with a real path exists, no request is in flight, the saved game
 * isn't in the confirmed-stale state, and the most recent throw attempt
 * didn't end in a rejected/failed request. A pure predicate — it never
 * reads game state beyond its own four arguments and never calls the API
 * client (this module imports nothing from `api/client`), so replaying
 * can't mutate score, pins, lane condition, game id, or release values;
 * it only restarts the canvas's own local animation over the exact path
 * already stored from that throw.
 *
 * `throwRejected` exists so a request that fails after `isBusy` returns
 * to `false` (e.g. a 503 truncated-trajectory rejection) can't re-enable
 * replay on the *previous* completed throw's path — the display is
 * meant to stay settled and inert until an actual successful state
 * transition (a new successful throw, a reset, or a new game) supersedes
 * it, not a bare status change from loading to error. This never
 * discards or mutates `latestThrow` itself; it only withholds the
 * ability to restart its animation while the failure is unresolved. */
export function canReplay(
  latestThrow: Pick<GameThrowResponse, 'path'> | null,
  isBusy: boolean,
  isStale: boolean,
  throwRejected: boolean,
): boolean {
  return latestThrow !== null && latestThrow.path.length > 0 && !isBusy && !isStale && !throwRejected;
}

/** The two signals a playback decision depends on, snapshotted once per
 * render: the path to animate (or `null` if there's no completed throw to
 * show), whether a request is currently in flight, and how many times
 * "Replay last shot" has been pressed (an incrementing counter — a
 * *change* in this number, not its value, is what a replay press means).
 */
export interface PlaybackState {
  latestThrowPath: readonly TrajectoryPointResponse[] | null;
  isBusy: boolean;
  replayCount: number;
}

export type PlaybackAction =
  | { kind: 'none' }
  | { kind: 'settle' }
  | { kind: 'start'; path: readonly TrajectoryPointResponse[] };

/**
 * Decides what the canvas's animation should do when moving from one
 * `PlaybackState` to the next — the same signals `LaneCanvas` reacts to,
 * modeled here as plain data so the decision is testable without
 * rendering React, touching the DOM, or scheduling a real
 * `requestAnimationFrame`.
 *
 * - A request *starting* (`isBusy` false -> true) always settles
 *   immediately: any in-flight animation of the preceding result stops,
 *   with that preceding result left visible as a static image. It never
 *   starts a new animation itself — there's no new path yet.
 * - A genuinely new path (reference change, meaning a fresh successful
 *   throw or reset/new-game response actually arrived) or an explicit
 *   replay press (`replayCount` changed) starts a fresh animation over
 *   `next`'s path — or settles, if that path is empty/absent (a
 *   reset/new-game clearing the previous throw).
 * - Critically, a request merely *finishing* with the same path as
 *   before (an ordinary failed request — nothing reassigns the path on
 *   failure) is `none`: it must not auto-replay the still-settled
 *   preceding result just because `isBusy` flipped back to false.
 */
export function decidePlaybackAction(previous: PlaybackState, next: PlaybackState): PlaybackAction {
  if (!previous.isBusy && next.isBusy) {
    return { kind: 'settle' };
  }

  const pathChanged = previous.latestThrowPath !== next.latestThrowPath;
  const replayChanged = previous.replayCount !== next.replayCount;

  if (replayChanged || (pathChanged && !next.isBusy)) {
    return next.latestThrowPath && next.latestThrowPath.length > 0
      ? { kind: 'start', path: next.latestThrowPath }
      : { kind: 'settle' };
  }

  return { kind: 'none' };
}

/** The state a canvas compares against before it has seen anything: no
 * completed throw, idle, and no replay presses yet. Exported so a remount
 * can reset to exactly the same starting point the first mount used. */
export const INITIAL_PLAYBACK_STATE: PlaybackState = {
  latestThrowPath: null,
  isBusy: false,
  replayCount: 0,
};

export interface PlaybackTransition {
  action: PlaybackAction;
  /** The snapshot to compare against next time. */
  snapshot: PlaybackState;
}

/**
 * `decidePlaybackAction` plus the rule for when that decision may be
 * recorded as *made*.
 *
 * The distinction matters because the decision is a transition, not a
 * property of the current state: it is derived by comparing against the
 * previous snapshot, so advancing that snapshot consumes the decision. If
 * the caller advances it while unable to act, the action is gone — every
 * later comparison sees no change and answers `none`.
 *
 * That is exactly the defect this exists to prevent. A canvas mounting with
 * a completed throw already in hand decides `start` in its layout pass; if
 * its player is created by a *passive* effect, that player does not exist
 * yet, and recording the snapshot anyway means the throw never animates at
 * all. Passing `canAct: false` keeps the old snapshot, so the identical
 * comparison happens again on the next pass and the decision survives.
 *
 * The ordering fix (create the player in an earlier layout effect) is what
 * makes `canAct` true in practice; this is the guarantee that a future
 * reordering cannot silently lose a throw again.
 */
export function planPlaybackTransition(
  previous: PlaybackState,
  next: PlaybackState,
  canAct: boolean,
): PlaybackTransition {
  const action = decidePlaybackAction(previous, next);
  if (!canAct) {
    return { action: { kind: 'none' }, snapshot: previous };
  }
  return { action, snapshot: next };
}
