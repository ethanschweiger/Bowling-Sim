/**
 * What the lane canvas should show at one instant — decided here, once,
 * instead of by phase conditionals scattered through the drawing code.
 *
 * ## The defect this exists to fix
 *
 * Drawing used to mix two different rack sources within a single throw.
 * The whole approach was drawn against `game_state.standing_pin_ids` — the
 * rack *after* this throw resolved — then at deck `t=0` it swapped to the
 * replay's pre-throw frame, and one frame after the terminal frame it
 * swapped back. Three visible lies came out of that:
 *
 * - the ball appeared to reach the deck and nothing happened, because the
 *   pins it was approaching were already in their post-throw state;
 * - on a second ball the rack visibly *reset* just before contact, since
 *   the pre-throw frame restored pins the post-score rack had removed;
 * - pins recorded as knocked down appeared to vanish rather than move,
 *   because the swap back to the static rack happened in the same instant
 *   the last recorded positions were drawn.
 *
 * None of that was missing backend motion. The server's frames were always
 * moving and authoritative; they were being staged against the wrong rack
 * at the wrong times.
 *
 * ## The rule
 *
 * For a throw with a playable accepted replay, the pins come from the
 * replay for the *entire* sequence — approach and deck alike — starting at
 * frame 0 and ending at the terminal frame. `standing_pin_ids` is not read
 * at all until the sequence hands back to the static rack. Since the
 * approach draws frame 0's pins and deck `t=0` resolves to that same frame,
 * the handoff changes exactly one thing: the ball. No pin moves, appears,
 * or disappears at the boundary, because it is the same set of bodies at
 * the same recorded coordinates on both sides of it.
 *
 * With no playable replay nothing changes from before: the static rack is
 * used throughout and no deck phase exists. This module invents no
 * pre-throw rack in that case — reconstructing one would mean re-deriving
 * the fresh-rack-on-frame-completion rule that deliberately lives on the
 * server.
 *
 * ## What this is not
 *
 * No physics. Every position here is either a server-recorded replay
 * coordinate or a point on the server's own recorded path. Nothing infers a
 * pin pose, a contact, a fall, or a rack transition, and nothing decides
 * *when* a pin fell — see `FALL_TIMING_LIMITATION` below.
 */

import type { CollisionReplayResponse, TrajectoryPointResponse } from '../api/types';
import { BALL_BODY_ID, replayPositionsAt, type ReplayLanePosition } from './collisionReplay';
import type { PlaybackPhase } from './playbackController';
import { easeOutCubic, interpolatePathPosition } from './trajectoryAnimation';

/**
 * The v2 replay records *positions over time*, not fall events. A pin's
 * `fallen` status comes from `fallen_pin_ids`, decided server-side by a
 * displacement threshold, and the frames never say at which timestamp that
 * threshold was crossed.
 *
 * So this module deliberately does not colour, fade, or cross out a pin
 * mid-playback: doing so would require inventing a fall time from
 * displacement, which is client-side physics wearing a costume. Every pin
 * is drawn as a body at its recorded position for the whole sequence, and
 * the standing/fallen distinction appears only when the static rack takes
 * over — where it is the server's answer.
 *
 * Making that moment legible is what the terminal hold is for. Finer fall
 * timing needs either a versioned backend fall-event or a denser sampling
 * decision, and is out of scope here.
 */
export const FALL_TIMING_LIMITATION =
  'v2 replay frames carry positions, not fall-event times; no fall timing is inferred here.';

export interface ScenePin {
  pinId: number;
  board: number;
  distanceFt: number;
}

export interface SceneBall {
  board: number;
  distanceFt: number;
}

/** Where this scene's pins come from. The two are never mixed within one
 * scene — that mixing was the original defect. */
export type ScenePins =
  | { source: 'replay'; bodies: readonly ScenePin[] }
  | { source: 'rack'; standingPinIds: readonly number[] };

export interface LaneScene {
  pins: ScenePins;
  /** The single ball to draw as a circle, or null when the static
   * entry-point marker stands in for it. Exactly one of `ball` and
   * `showEntryMarker` is ever active, so no scene shows two balls. */
  ball: SceneBall | null;
  /** How much of the server's recorded path polyline to draw, 0..1. */
  pathProgress: number;
  /** Draw the static entry-point dot (the resting stand-in for the ball). */
  showEntryMarker: boolean;
}

function pinsFromPositions(positions: readonly ReplayLanePosition[]): ScenePin[] {
  // The ball is excluded here and re-added as `ball`, so a scene can never
  // end up drawing it twice or labelling it as a pin.
  return positions
    .filter((position) => position.bodyId !== BALL_BODY_ID)
    .map((position) => ({
      pinId: position.bodyId,
      board: position.board,
      distanceFt: position.distanceFt,
    }));
}

function ballFromPositions(positions: readonly ReplayLanePosition[]): SceneBall | null {
  const ball = positions.find((position) => position.bodyId === BALL_BODY_ID);
  return ball ? { board: ball.board, distanceFt: ball.distanceFt } : null;
}

/**
 * The scene for one instant.
 *
 * Pure — no clock, no canvas, no React. `phase` comes from the playback
 * controller, `replay` is an already-accepted payload (or null), `path` is
 * the server's recorded polyline, and `standingPinIds` is the post-score
 * rack.
 *
 * `standingPinIds` is read **only** on the static branch. That is the
 * property the continuity fix rests on, and `laneScene.test.ts` asserts it
 * by passing a rack that disagrees with the replay and checking it does not
 * show through during the approach or the deck.
 */
export function buildLaneScene(
  phase: PlaybackPhase,
  replay: CollisionReplayResponse | null,
  path: readonly TrajectoryPointResponse[] | null,
  standingPinIds: readonly number[],
): LaneScene {
  const hasPath = path !== null && path.length > 0;

  if (phase.kind === 'deck' && replay) {
    // Only what the solver recorded, at the timestamp asked for.
    const positions = replayPositionsAt(replay, phase.tS);
    return {
      pins: { source: 'replay', bodies: pinsFromPositions(positions) },
      ball: ballFromPositions(positions),
      pathProgress: 1,
      showEntryMarker: false,
    };
  }

  if (phase.kind === 'path' && hasPath) {
    const progress = easeOutCubic(phase.progress);
    const position = interpolatePathPosition(path, progress);
    return {
      // With a playable replay the approach already shows the rack this
      // throw is about to hit — frame 0, the same bodies deck `t=0` will
      // resolve to. Without one, the static rack, exactly as before.
      pins: replay
        ? { source: 'replay', bodies: pinsFromPositions(replayPositionsAt(replay, 0)) }
        : { source: 'rack', standingPinIds },
      ball: { board: position.board, distanceFt: position.distanceFt },
      pathProgress: progress,
      showEntryMarker: false,
    };
  }

  // Settled, or a phase with no path to animate: the server's own rack.
  return {
    pins: { source: 'rack', standingPinIds },
    ball: null,
    pathProgress: 1,
    showEntryMarker: hasPath,
  };
}
