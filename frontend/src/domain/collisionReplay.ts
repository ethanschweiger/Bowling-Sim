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
 * anything else — an older v1 recording, or a future 3D solver — is
 * refused rather than reinterpreted through assumptions that no longer
 * hold. Refusal is not an error state: the canvas simply shows the static
 * server rack, the same thing it shows for a gutter ball or a
 * heuristic-model result.
 *
 * ## What playback does and doesn't claim
 *
 * The last frame is the run's *terminal* snapshot — the final state the
 * solver computed. It is not a settled scene: the solver may have stopped
 * at its step cap with bodies still in motion, in which case playback ends
 * mid-flight because there is genuinely nothing further to show.
 * `termination_reason` says which of the two happened. Neither value is
 * evidence about real pins, and nothing here may turn it into pin state or
 * scoring — the server's `fallen_pin_ids`/`standing_pin_ids` remain the
 * only authority, exactly as before this field existed.
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

import type {
  CollisionReplayResponse,
  ReplayFrameResponse,
  ThresholdCrossingResponse,
} from '../api/types';

/** The one replay shape this client understands. Must match
 * `REPLAY_MODEL_VERSION` in `backend/app/physics/replay.py`.
 *
 * Earlier versions are deliberately *not* also accepted, for reasons that
 * differ but land the same way:
 *
 * - v1 carried no `termination_reason`, so every v1 replay is genuinely
 *   ambiguous between the solver's two exits. Supplying a value on its
 *   behalf would invent the very fact the field exists to state.
 * - v2 sampled every 100 solver steps rather than every 20. Since the
 *   *complete frame schedule* is derived from the cadence and checked
 *   exactly, a v2 payload does not merely look sparse — it fails to be the
 *   contract this client validates against.
 * - v3 carried no `threshold_crossings`, so there was no honest way to show
 *   *when* a scored pin fell and every replay pin had to stay drawn as
 *   standing until the static-rack handoff. The events cannot be recovered
 *   from positions — that would be deriving the server's decision here —
 *   so a v3 payload falls back rather than being played without them.
 *
 * Both fall back to the static server rack, like any other unknown
 * version. Nothing is reinterpreted. */
export const SUPPORTED_REPLAY_MODEL_VERSION = 'planar-collision-replay-2d-v4';

/**
 * How the recorded run ended. Mirrors `TerminationReason` in
 * `backend/app/physics/replay.py`.
 *
 * Neither value is a claim about real pins. `'settled'` means every body
 * in the planar model dropped below its velocity threshold; `'step_cap'`
 * means the solver stopped at its fixed 2 s limit with bodies still
 * moving — a numerical safety stop with no physical meaning. Playback of a
 * `'step_cap'` run simply ends mid-motion, which is honest: the server has
 * no later state to show.
 *
 * This is presentation metadata only. Nothing in this client may derive
 * pin state, scoring, or collision outcomes from it — `fallen_pin_ids` and
 * `standing_pin_ids` remain the only authority on what happened.
 */
export type ReplayTerminationReason = 'settled' | 'step_cap';

/** The exact permitted set, so the validator and tests can't drift from
 * each other by restating the strings independently. */
export const REPLAY_TERMINATION_REASONS: readonly ReplayTerminationReason[] = [
  'settled',
  'step_cap',
];

/**
 * A replay that has passed `acceptReplay`.
 *
 * Structurally the payload it came from — the same object, never a copy —
 * but with `termination_reason` narrowed to a value that has actually been
 * checked. The distinction is the whole point of the firewall: an
 * unvalidated `CollisionReplayResponse` may carry any string or none, and
 * only this type says otherwise.
 */
export interface AcceptedReplay extends CollisionReplayResponse {
  termination_reason: ReplayTerminationReason;
  /** Validated: ordered by step then pin id, one per pin, every id a real
   * participating pin, every step inside the run, and the id set exactly
   * equal to the response's own `fallen_pin_ids`. */
  threshold_crossings: ThresholdCrossingResponse[];
}

/** The ball is body 0; a crossing is only ever a pin. */
const MIN_PIN_ID = 1;
const MAX_PIN_ID = 10;

/**
 * Validates the v4 crossing events against the response's authoritative
 * `fallen_pin_ids`.
 *
 * The correspondence check is the important one and is why this takes the
 * fallen set at all: the events and that list come from a single server
 * decision, so if they disagree the payload is not describing one coherent
 * run and nothing here should try to reconcile them. Everything else is
 * shape and bounds.
 *
 * Deliberately no fallback to deriving a crossing from displacement. That
 * would be recomputing the server's fall rule in the browser, which is the
 * whole thing v4 exists to avoid.
 */
function acceptThresholdCrossings(
  crossings: unknown,
  stepsTaken: number,
  fallenPinIds: readonly number[],
): ThresholdCrossingResponse[] | null {
  if (!Array.isArray(crossings)) {
    return null;
  }
  if (crossings.length !== fallenPinIds.length) {
    return null;
  }

  let previousStep = 0;
  let previousPin = 0;
  const seen = new Set<number>();

  for (const crossing of crossings) {
    if (!crossing || typeof crossing !== 'object') {
      return null;
    }
    const { pin_id: pinId, step_index: stepIndex } = crossing as ThresholdCrossingResponse;

    if (!Number.isSafeInteger(pinId) || pinId < MIN_PIN_ID || pinId > MAX_PIN_ID) {
      return null;
    }
    // Step 0 is the initial placement, before any stepping, so nothing can
    // have crossed there; and nothing can cross after the run ended.
    if (!Number.isSafeInteger(stepIndex) || stepIndex <= 0 || stepIndex > stepsTaken) {
      return null;
    }
    if (seen.has(pinId)) {
      return null; // one event per pin
    }
    seen.add(pinId);

    // Ordered by step, then pin id — the order the recorder produces.
    if (stepIndex < previousStep || (stepIndex === previousStep && pinId <= previousPin)) {
      return null;
    }
    previousStep = stepIndex;
    previousPin = pinId;
  }

  // Exactly the pins the server says fell — not a subset, not a superset.
  for (const pinId of fallenPinIds) {
    if (!seen.has(pinId)) {
      return null;
    }
  }

  return crossings as ThresholdCrossingResponse[];
}

function isTerminationReason(value: unknown): value is ReplayTerminationReason {
  return (
    typeof value === 'string' &&
    (REPLAY_TERMINATION_REASONS as readonly string[]).includes(value)
  );
}

// --- The recorded contract's own documented bounds -----------------------
// Mirrors backend/app/physics/{collision,replay}.py. A payload claiming to
// be this model version but exceeding any of these is not a replay this
// client understands, however finite its numbers happen to be: JavaScript
// will happily call 1e308 a number, and a replay "ending" there would keep
// an animation loop alive effectively forever.

/** `COLLISION_DT_S` in backend/app/physics/collision.py — fixed for this
 * model version, not a free parameter a payload may choose. */
export const V3_DT_S = 0.0005;
/** `REPLAY_SAMPLE_EVERY_STEPS` in backend/app/physics/replay.py — likewise
 * fixed for v3. Together with `V3_DT_S` these pin down the exact set of
 * frames a genuine v3 recording contains.
 *
 * 20 steps at 0.0005 s is one frame per 10 ms (100 Hz), five times denser
 * than v2's 100 steps / 50 ms. That is what the version bump is for: the
 * complete schedule is validated, so the cadence is part of the contract
 * rather than a server-side detail. Denser sampling shortens how long a
 * resolved impulse can go unseen — about 4.4 in of ball travel at the
 * fastest legal release, against roughly 22 in at v2's cadence. It records
 * the same run more finely; it does not change the run. */
export const V3_SAMPLE_EVERY_STEPS = 20;

/** `MAX_REPLAY_FRAMES` in the backend recorder. A full-length run is
 * 4000 / 20 + 1 = 201 frames, so this bound clears it with headroom while
 * staying small and fixed. */
export const MAX_ACCEPTED_REPLAY_FRAMES = 256;
/** `MAX_COLLISION_STEPS` — the solver's own iteration cap. */
export const MAX_ACCEPTED_STEPS = 4000;
/** `MAX_COLLISION_SECONDS` — the solver stops here at the latest, even if
 * bodies are still moving. A terminal frame is therefore the last state the
 * solver computed, not a state in which anything came to rest. A run that
 * reaches this cap usually reports `'step_cap'`, but reports `'settled'` if
 * its bodies crossed the velocity threshold on that final step, so the
 * duration alone never determines the reason. */
export const MAX_ACCEPTED_DURATION_S = 2.0;
/** The ball plus at most a full rack. */
export const MAX_ACCEPTED_BODIES = 11;

/**
 * Documented coordinate bound, in inches, for any recorded body position.
 *
 * Derived rather than guessed: the fastest legal release is 25 mph
 * (`RELEASE_BOUNDS` in the backend), or about 440 in/s, and the solver
 * stops at a 2 s cap — so no body can travel more than roughly 880 in
 * from where it started, and every body starts within a few feet of the
 * deck. 1,000 in leaves real runs comfortable room (measured extremes
 * across the legal release space reach about 271 in laterally and 404 in
 * downlane) while rejecting values that could only come from a malformed
 * or deceptive payload.
 */
export const MAX_ABS_COORDINATE_IN = 1000;

/** Float slack for comparing recorded timestamps against the cadence they
 * should have been generated from. */
const TIME_TOLERANCE_S = 1e-6;

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

function isPositiveSafeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
}

function isInBounds(value: number): boolean {
  return Math.abs(value) <= MAX_ABS_COORDINATE_IN;
}

/**
 * Accepts a replay only if it is the known version and structurally sound
 * enough to animate: a bounded non-empty frame list, strictly increasing
 * finite timestamps, and every frame carrying the same sorted, unique body
 * IDs including the ball.
 *
 * Returns the replay unchanged on success (never a copy — the caller's
 * data stays the server's), typed as `AcceptedReplay` so its
 * `termination_reason` is a value that has actually been checked. Returns
 * `null` for anything missing, malformed, or of an unknown version. Never
 * throws: a canvas that threw on a surprising payload would take the whole
 * panel down over a decoration.
 */
export function acceptReplay(
  replay: CollisionReplayResponse | null | undefined,
  fallenPinIds: readonly number[] = [],
): AcceptedReplay | null {
  if (!replay || typeof replay !== 'object') {
    return null;
  }
  if (replay.model_version !== SUPPORTED_REPLAY_MODEL_VERSION) {
    return null;
  }
  // Required in v2, and required to be one of exactly two values. A
  // missing or unrecognized reason means this payload does not describe a
  // run whose ending this client can state, so it is refused outright
  // rather than played with the question left open.
  if (!isTerminationReason(replay.termination_reason)) {
    return null;
  }

  // --- Metadata must describe a run this solver could actually have done.
  if (!isFiniteNumber(replay.dt_s) || replay.dt_s <= 0) {
    return null;
  }
  // v3's cadence and timestep are fixed constants, not payload-chosen
  // values. Pinning them is what makes the *complete* frame schedule below
  // derivable — without it, a payload could pick a stride that makes any
  // sparse frame list look "on cadence".
  if (replay.dt_s !== V3_DT_S || replay.sample_every_steps !== V3_SAMPLE_EVERY_STEPS) {
    return null;
  }
  if (!isPositiveSafeInteger(replay.steps_taken) || replay.steps_taken > MAX_ACCEPTED_STEPS) {
    return null;
  }
  // The claimed run length has to fit the solver's own wall of 2 s.
  const claimedDurationS = replay.dt_s * replay.steps_taken;
  if (!isFiniteNumber(claimedDurationS) || claimedDurationS > MAX_ACCEPTED_DURATION_S + TIME_TOLERANCE_S) {
    return null;
  }

  const frames = replay.frames;
  if (!Array.isArray(frames) || frames.length === 0 || frames.length > MAX_ACCEPTED_REPLAY_FRAMES) {
    return null;
  }

  // The *complete* schedule a v3 recorder produces, derived with integer
  // step arithmetic: the initial frame at step 0, every cadence tick
  // through the run, and one terminal frame at the final step when that
  // step isn't itself a tick. Requiring the whole list — not merely that
  // whatever arrived happens to sit on the cadence — is what stops a
  // sparse payload (say a 4,000-step run carrying only t=0 and t=2) from
  // being accepted and then having two seconds of collision motion
  // invented by interpolating across the frames it omitted.
  const expectedSteps: number[] = [];
  for (let step = 0; step <= replay.steps_taken; step += V3_SAMPLE_EVERY_STEPS) {
    expectedSteps.push(step);
  }
  if (expectedSteps[expectedSteps.length - 1] !== replay.steps_taken) {
    expectedSteps.push(replay.steps_taken);
  }
  // Exact length match rejects missing, duplicated, and extra frames alike.
  if (frames.length !== expectedSteps.length) {
    return null;
  }

  let expectedIds: string | null = null;
  let previousT = -Infinity;

  for (let index = 0; index < frames.length; index += 1) {
    const frame = frames[index];
    if (!frame || typeof frame !== 'object' || !isFiniteNumber(frame.t_s)) {
      return null;
    }
    // Each frame must sit at its own scheduled step — computed from the
    // integer step index, so float drift never accumulates across frames.
    const expectedT = expectedSteps[index] * V3_DT_S;
    if (Math.abs(frame.t_s - expectedT) > TIME_TOLERANCE_S) {
      return null;
    }
    // Impact is t=0 by definition; anything else is not this contract.
    if (index === 0 && frame.t_s !== 0) {
      return null;
    }
    // Strictly increasing: equal timestamps would make "which frame is
    // current" ambiguous, and a decreasing one means the data isn't the
    // ordered sequence this player assumes. (The schedule check above
    // already implies this; kept as an explicit, independent guarantee.)
    if (index > 0 && frame.t_s <= previousT) {
      return null;
    }
    if (frame.t_s > MAX_ACCEPTED_DURATION_S + TIME_TOLERANCE_S) {
      return null;
    }
    previousT = frame.t_s;

    const bodies = frame.bodies;
    if (!Array.isArray(bodies) || bodies.length === 0 || bodies.length > MAX_ACCEPTED_BODIES) {
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
      // Only the ball and real pin IDs exist on a deck.
      if (body.body_id !== BALL_BODY_ID && (body.body_id < 1 || body.body_id > 10)) {
        return null;
      }
      if (!isInBounds(body.x_in) || !isInBounds(body.y_in)) {
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

  // v4's crossing events, checked against the response's own fallen set.
  // Last because it is the only check that needs data from outside the
  // replay itself.
  if (acceptThresholdCrossings(replay.threshold_crossings, replay.steps_taken, fallenPinIds) === null) {
    return null;
  }

  // The very same object the caller passed in — the assertion only records
  // the narrowings checked above, and copies or rewrites nothing. The
  // server's data stays the server's.
  return replay as AcceptedReplay;
}

/**
 * How far downlane, in feet, this replay's furthest recorded body reaches.
 *
 * The canvas needs it to choose one viewport that contains the whole
 * accepted sequence — path, initial rack, intermediate frames, and the
 * terminal frame — so no recorded position has to be clamped into a
 * different on-screen coordinate than the one it actually describes.
 * Bodies routinely end up well past the pin deck: the solver runs for two
 * seconds and pins slide into the pit.
 */
export function replayMaxDistanceFt(replay: CollisionReplayResponse): number {
  return replayDistanceExtentFt(replay).maxFt;
}

/** The downlane span, in feet, of every body this replay records. */
export interface ReplayDistanceExtent {
  minFt: number;
  maxFt: number;
}

/**
 * Both ends of this replay's downlane extent.
 *
 * Two-sided on purpose. Bodies can be driven *back* toward the bowler by a
 * collision — recorded `y_in` goes negative — and a viewport that only
 * grew at its far edge would clamp such a body onto the foul-line edge,
 * painting it at a coordinate it never held. That is the same class of
 * error as the far-edge clamp, just at the other end, so the canvas sizes
 * its viewport from both values.
 */
export function replayDistanceExtentFt(replay: CollisionReplayResponse): ReplayDistanceExtent {
  let minY = Infinity;
  let maxY = -Infinity;
  for (const frame of replay.frames) {
    for (const body of frame.bodies) {
      if (body.y_in < minY) {
        minY = body.y_in;
      }
      if (body.y_in > maxY) {
        maxY = body.y_in;
      }
    }
  }
  return {
    minFt: HEADPIN_DISTANCE_FT + minY / IN_PER_FT,
    maxFt: HEADPIN_DISTANCE_FT + maxY / IN_PER_FT,
  };
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
