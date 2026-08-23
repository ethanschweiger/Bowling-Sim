/**
 * Turning the server's authoritative collision replay into something the
 * canvas can draw — validation, coordinate conversion, and time-based
 * lookup between two adjacent recorded frames.
 *
 * Nothing here computes physics. The browser never decides where a pin
 * goes, whether a contact happened, or whether a pin fell; it only reads
 * positions the solver already recorded (see
 * `backend/app/physics/replay.py`) and, purely so paints look smooth,
 * straight-lines *between two adjacent authoritative frames*. That
 * interpolation is presentation, exactly as `trajectoryAnimation.ts`
 * already interpolates between recorded path points — it can never
 * produce a position outside the segment the server itself bracketed.
 *
 * ## Version gating
 *
 * Only `SUPPORTED_REPLAY_MODEL_VERSION` is played. A replay stamped with
 * anything else — a future 3D solver, say — is refused rather than
 * reinterpreted through assumptions that no longer hold. Refusal is not
 * an error state: the canvas simply shows the settled server rack, the
 * same thing it shows for a gutter ball or a heuristic-model result.
 *
 * ## Coordinates
 *
 * Backend inches -> canvas lane coordinates, using the same declared
 * constants the rest of the drawing code uses:
 *
 * - `x_in` is inches from lane center, positive toward higher boards, so
 *   `board = 20 + x_in / 1.05` (board 20 is the center of 39; 1.05 in is
 *   the declared board width).
 * - `y_in` is inches downlane from the headpin plane, so
 *   `distanceFt = 60 + y_in / 12`.
 *
 * Both directions are the backend's, unmirrored. A sign error here would
 * put the ball on the wrong side of the deck, which is exactly why
 * `collisionReplay.test.ts` checks continuity against the trajectory's
 * own endpoint at the 60 ft boundary rather than trusting the formula by
 * eye.
 */

import type { CollisionReplayResponse, ReplayFrameResponse } from '../api/types';

/** The one replay shape this client understands. Must match
 * `REPLAY_MODEL_VERSION` in `backend/app/physics/replay.py`. */
export const SUPPORTED_REPLAY_MODEL_VERSION = 'planar-collision-replay-2d-v1';

/** Defensive ceiling on accepted frames. The backend enforces its own
 * bound (`MAX_REPLAY_FRAMES`); this is deliberately a little looser and
 * independent, so a malformed or hostile payload still can't make the
 * canvas iterate an unbounded list. */
export const MAX_ACCEPTED_REPLAY_FRAMES = 256;

const BOARD_COUNT = 39; // backend/app/physics/lane.py
const LANE_CENTER_BOARD = (BOARD_COUNT + 1) / 2; // board 20
const BOARD_WIDTH_IN = 1.05; // backend/app/physics/units.py: BOARD_WIDTH_IN
const HEADPIN_DISTANCE_FT = 60.0; // backend/app/physics/pin_deck.py
const IN_PER_FT = 12;

/** `body_id` 0 is always the ball — see the backend's `BALL_BODY_ID`. */
export const BALL_BODY_ID = 0;

export interface ReplayLanePosition {
  bodyId: number;
  /** Fractional board, 1-39, in the same coordinate the canvas projects. */
  board: number;
  distanceFt: number;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/**
 * Accepts a replay only if it is the known version and structurally sound
 * enough to animate: a bounded non-empty frame list, strictly increasing
 * finite timestamps, and every frame carrying the same sorted, unique body
 * IDs including the ball.
 *
 * Returns the replay unchanged on success (never a copy — the caller's
 * data stays the server's), or `null` for anything missing, malformed, or
 * of an unknown version. Never throws: a canvas that threw on a surprising
 * payload would take the whole panel down over a decoration.
 */
export function acceptReplay(replay: CollisionReplayResponse | null | undefined): CollisionReplayResponse | null {
  if (!replay || typeof replay !== 'object') {
    return null;
  }
  if (replay.model_version !== SUPPORTED_REPLAY_MODEL_VERSION) {
    return null;
  }
  if (!isFiniteNumber(replay.dt_s) || replay.dt_s <= 0) {
    return null;
  }

  const frames = replay.frames;
  if (!Array.isArray(frames) || frames.length === 0 || frames.length > MAX_ACCEPTED_REPLAY_FRAMES) {
    return null;
  }

  let expectedIds: string | null = null;
  let previousT = -Infinity;

  for (const frame of frames) {
    if (!frame || typeof frame !== 'object' || !isFiniteNumber(frame.t_s)) {
      return null;
    }
    // Strictly increasing: equal timestamps would make "which frame is
    // current" ambiguous, and a decreasing one means the data isn't the
    // ordered sequence this player assumes.
    if (frame.t_s <= previousT) {
      return null;
    }
    previousT = frame.t_s;

    const bodies = frame.bodies;
    if (!Array.isArray(bodies) || bodies.length === 0) {
      return null;
    }

    const ids: number[] = [];
    for (const body of bodies) {
      if (!body || typeof body !== 'object') {
        return null;
      }
      if (!Number.isInteger(body.body_id) || !isFiniteNumber(body.x_in) || !isFiniteNumber(body.y_in)) {
        return null;
      }
      ids.push(body.body_id);
    }

    // Sorted and unique, and the same membership every frame — a body
    // appearing or vanishing mid-replay would mean the sequence isn't
    // describing one continuous run.
    for (let i = 1; i < ids.length; i += 1) {
      if (ids[i] <= ids[i - 1]) {
        return null;
      }
    }
    if (ids[0] !== BALL_BODY_ID) {
      return null;
    }

    const signature = ids.join(',');
    if (expectedIds === null) {
      expectedIds = signature;
    } else if (signature !== expectedIds) {
      return null;
    }
  }

  return replay;
}

/** How long the deck phase lasts, in simulation seconds: the last frame's
 * own timestamp. Played back at 1x simulation time (see
 * `playbackController.ts`), so this is also its real duration. */
export function replayDurationS(replay: CollisionReplayResponse): number {
  return replay.frames[replay.frames.length - 1].t_s;
}

/** One body's recorded inches converted to the canvas's lane coordinates.
 * Pure arithmetic over the declared constants — see the module docstring
 * for the two conversions and why their direction matters. */
export function replayBodyToLanePosition(body: {
  body_id: number;
  x_in: number;
  y_in: number;
}): ReplayLanePosition {
  return {
    bodyId: body.body_id,
    board: LANE_CENTER_BOARD + body.x_in / BOARD_WIDTH_IN,
    distanceFt: HEADPIN_DISTANCE_FT + body.y_in / IN_PER_FT,
  };
}

function lanePositionsAt(frame: ReplayFrameResponse): ReplayLanePosition[] {
  return frame.bodies.map(replayBodyToLanePosition);
}

/**
 * Every body's position at simulation time `tS`, straight-lined between
 * the two adjacent recorded frames that bracket it.
 *
 * Before the first frame's time, returns the first frame exactly; at or
 * after the last, the last frame exactly. Both bracketing frames are the
 * server's own, so an interpolated point always lies on the segment
 * between two authoritative positions — never beyond either.
 *
 * Does not mutate `replay` or any frame/body in it.
 */
export function replayPositionsAt(replay: CollisionReplayResponse, tS: number): ReplayLanePosition[] {
  const frames = replay.frames;
  if (tS <= frames[0].t_s) {
    return lanePositionsAt(frames[0]);
  }
  const last = frames[frames.length - 1];
  if (tS >= last.t_s) {
    return lanePositionsAt(last);
  }

  let upperIndex = 1;
  while (upperIndex < frames.length - 1 && frames[upperIndex].t_s < tS) {
    upperIndex += 1;
  }
  const lower = frames[upperIndex - 1];
  const upper = frames[upperIndex];
  const span = upper.t_s - lower.t_s;
  // `acceptReplay` guarantees strictly increasing timestamps, so span > 0;
  // the guard is belt-and-braces against an unvalidated caller.
  const localT = span > 0 ? (tS - lower.t_s) / span : 0;

  const upperById = new Map(upper.bodies.map((body) => [body.body_id, body]));
  return lower.bodies.map((lowerBody) => {
    const upperBody = upperById.get(lowerBody.body_id) ?? lowerBody;
    return replayBodyToLanePosition({
      body_id: lowerBody.body_id,
      x_in: lowerBody.x_in + (upperBody.x_in - lowerBody.x_in) * localT,
      y_in: lowerBody.y_in + (upperBody.y_in - lowerBody.y_in) * localT,
    });
  });
}
