/**
 * Pure logic behind the lane canvas's trajectory animation and "Replay
 * last shot" action. Nothing here touches the DOM, `requestAnimationFrame`,
 * or the API client — it's testable without a browser or a timer.
 *
 * The animation is presentation of a response the server already
 * computed, not a second physics simulation: it interpolates *between*
 * the exact points `path` already contains, and never invents, decays,
 * or recalculates a trajectory of its own. `path` points are recorded at
 * fixed downlane-distance steps (`PATH_SAMPLE_FT` in
 * `backend/app/physics/simulate.py`), so they are unevenly spaced in
 * *time* — the ball covers each foot more slowly as it decelerates, so
 * later samples sit further apart in `elapsed_s` than earlier ones even
 * though they're the same distance apart. Each point now carries that
 * real, server-observed `elapsed_s`, so "progress" is mapped through
 * those recorded time fractions, not through raw index position: a
 * ball's on-screen pace through the fast early samples and the slower
 * late ones matches the physics that produced them, rather than treating
 * every recorded segment as if it took the same slice of the animation.
 *
 * The path phase's own *duration* is likewise derived from the path's
 * final `elapsed_s` (see `pathAnimationDurationMs`) rather than fixed —
 * a throw the server simulated as taking longer plays back longer, one
 * that reached the pins faster plays back faster. This is a deliberate,
 * named DISPLAY SCALE of that real time (`PATH_ANIMATION_MS_PER_ELAPSED_SECOND`),
 * clamped to a usable on-screen range — never real ball-travel time, and
 * never a fabricated client clock: every millisecond of it still traces
 * back to the server's own `elapsed_s`.
 */

import type { GameThrowResponse, TrajectoryPointResponse } from '../api/types';

/** Milliseconds of on-screen path-phase animation per second of the
 * server's real, recorded `elapsed_s`. A deliberate DISPLAY scale, not a
 * claim that playback runs at real ball-travel speed — see the module
 * docstring. Chosen so the legal release envelope's real travel time
 * (roughly 1.6s for the fastest legal throw to 4.2s for the slowest,
 * house shot, per `backend/app/physics/throw.py`'s `RELEASE_BOUNDS`)
 * maps to a comfortably snappy ~500ms-1250ms of screen time — inside the
 * clamp below on every legal throw, which exists as a guard against a
 * future physics change or malformed path, not a bound ordinary throws
 * are expected to hit. */
export const PATH_ANIMATION_MS_PER_ELAPSED_SECOND = 300;

/** Floor and ceiling on the path phase's on-screen duration. */
export const MIN_PATH_ANIMATION_MS = 400;
export const MAX_PATH_ANIMATION_MS = 1600;

/** The path phase's on-screen duration for a completed throw's `path`,
 * derived from the server's own recorded `elapsed_s` at the final
 * sample — never the same fixed number for every throw. An empty path
 * (no completed throw) returns the floor; nothing schedules an
 * animation over an empty path in practice, so this only avoids a
 * degenerate zero/NaN duration rather than describing a real case. */
export function pathAnimationDurationMs(path: readonly TrajectoryPointResponse[]): number {
  const finalElapsedS = path.length > 0 ? path[path.length - 1].elapsed_s : 0;
  const scaled = finalElapsedS * PATH_ANIMATION_MS_PER_ELAPSED_SECOND;
  return Math.min(MAX_PATH_ANIMATION_MS, Math.max(MIN_PATH_ANIMATION_MS, scaled));
}

export interface PathPosition {
  board: number;
  distanceFt: number;
}

/** The index of the recorded path point that starts the segment
 * containing a normalized `progress`'s target simulation time — the
 * boundary between "fully behind the ball" and "currently interpolating"
 * samples. `progress` is scaled against the path's own final `elapsed_s`
 * to get that target time, then this scans for the two recorded points
 * whose `elapsed_s` straddle it; the path is monotonically increasing in
 * `elapsed_s` (a server invariant — see `simulate_throw`), so one forward
 * scan suffices, with no assumption of even time or index spacing.
 *
 * Exported so `LaneCanvas`'s partial-polyline draw and
 * `interpolatePathPosition`'s own segment lookup agree on exactly the
 * same boundary — computing it twice by different rules is exactly how
 * the drawn line and the interpolated ball position could disagree. */
export function pathLowerIndexAtProgress(path: readonly TrajectoryPointResponse[], progress: number): number {
  if (path.length < 2) {
    return 0;
  }
  const clamped = Math.max(0, Math.min(1, progress));
  const targetElapsedS = clamped * path[path.length - 1].elapsed_s;

  let lowerIndex = 0;
  while (lowerIndex < path.length - 2 && path[lowerIndex + 1].elapsed_s <= targetElapsedS) {
    lowerIndex += 1;
  }
  return lowerIndex;
}

/** Interpolates a position along `path` at a normalized `progress` in
 * [0, 1] — 0 is exactly the first recorded point, 1 is exactly the last.
 * `progress` is mapped through each point's real `elapsed_s` rather than
 * its index (see `pathLowerIndexAtProgress`), then linearly interpolated
 * between the two recorded points that straddle the resulting target
 * simulation time. `progress` outside [0, 1] clamps to the nearer
 * endpoint. */
export function interpolatePathPosition(path: readonly TrajectoryPointResponse[], progress: number): PathPosition {
  if (path.length === 0) {
    return { board: 0, distanceFt: 0 };
  }
  if (path.length === 1) {
    return { board: path[0].board, distanceFt: path[0].distance_ft };
  }

  const clamped = Math.max(0, Math.min(1, progress));
  const targetElapsedS = clamped * path[path.length - 1].elapsed_s;
  const lowerIndex = pathLowerIndexAtProgress(path, clamped);
  const lower = path[lowerIndex];
  const upper = path[lowerIndex + 1];

  const segmentDurationS = upper.elapsed_s - lower.elapsed_s;
  // A zero-duration segment shouldn't occur in a real server path, but
  // guards against dividing by zero rather than producing NaN.
  const localT = segmentDurationS > 0 ? (targetElapsedS - lower.elapsed_s) / segmentDurationS : 1;

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
