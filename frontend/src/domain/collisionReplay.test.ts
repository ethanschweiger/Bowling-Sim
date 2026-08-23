import { describe, expect, it } from 'vitest';
import type { CollisionReplayResponse } from '../api/types';
import {
  acceptReplay,
  BALL_BODY_ID,
  MAX_ABS_COORDINATE_IN,
  MAX_ACCEPTED_DURATION_S,
  MAX_ACCEPTED_REPLAY_FRAMES,
  MAX_ACCEPTED_STEPS,
  replayBodyToLanePosition,
  replayDurationS,
  replayMaxDistanceFt,
  replayPositionsAt,
  SUPPORTED_REPLAY_MODEL_VERSION,
} from './collisionReplay';

function body(body_id: number, x_in: number, y_in: number) {
  return { body_id, x_in, y_in };
}

// Fixtures below obey the recorded contract the backend actually emits:
// dt_s 0.0005, cadence every 100 steps (so on-cadence frames land on
// multiples of 0.05 s), a first frame exactly at impact, and a final
// frame at exactly dt_s * steps_taken. An earlier version of this file
// used 4,000 steps ending at 0.1 s, which no real recorder could produce
// -- and which the strengthened validator now correctly rejects.

/** A pocket-shaped replay: ball arriving left of center, headpin ahead.
 * 200 steps at 0.0005 s = 0.1 s, with frames on the 0.05 s cadence. */
function pocketReplay(): CollisionReplayResponse {
  return {
    model_version: SUPPORTED_REPLAY_MODEL_VERSION,
    dt_s: 0.0005,
    sample_every_steps: 100,
    steps_taken: 200,
    frames: [
      { t_s: 0, bodies: [body(BALL_BODY_ID, -3.2, 0), body(1, 0, 0), body(3, -6, 10.392)] },
      { t_s: 0.05, bodies: [body(BALL_BODY_ID, -3.0, 2), body(1, 0.1, 0.4), body(3, -6, 10.392)] },
      { t_s: 0.1, bodies: [body(BALL_BODY_ID, -2.8, 4), body(1, 0.3, 1.2), body(3, -5.6, 10.8)] },
    ],
  };
}

/** A second-ball spare attempt: only the pins that were still standing.
 * 100 steps = 0.05 s, so the final frame is also the first cadence tick. */
function partialRackReplay(): CollisionReplayResponse {
  return {
    model_version: SUPPORTED_REPLAY_MODEL_VERSION,
    dt_s: 0.0005,
    sample_every_steps: 100,
    steps_taken: 100,
    frames: [
      { t_s: 0, bodies: [body(BALL_BODY_ID, 5, 0), body(7, 18, 31.18), body(10, -18, 31.18)] },
      { t_s: 0.05, bodies: [body(BALL_BODY_ID, 5.2, 3), body(7, 18, 31.18), body(10, -18, 31.18)] },
    ],
  };
}

/** A full-length run: the solver's own 4,000-step / 2 s cap, 41 frames. */
function fullLengthReplay(): CollisionReplayResponse {
  const frames = [];
  for (let i = 0; i <= 40; i += 1) {
    frames.push({
      t_s: i * 0.05,
      bodies: [body(BALL_BODY_ID, -3.2 + i * 0.1, i * 4), body(1, 0, 0)],
    });
  }
  return {
    model_version: SUPPORTED_REPLAY_MODEL_VERSION,
    dt_s: 0.0005,
    sample_every_steps: 100,
    steps_taken: 4000,
    frames,
  };
}

describe('acceptReplay', () => {
  it('accepts a well-formed pocket replay and returns it unchanged', () => {
    const replay = pocketReplay();
    // Reference identity: accepting must not copy or rewrite the server's
    // own data on its way to the canvas.
    expect(acceptReplay(replay)).toBe(replay);
  });

  it('accepts a partial-rack replay carrying only the standing pins', () => {
    const replay = partialRackReplay();
    expect(acceptReplay(replay)).toBe(replay);
    const ids = replay.frames[0].bodies.map((b) => b.body_id);
    expect(ids).toEqual([BALL_BODY_ID, 7, 10]);
  });

  it('refuses a null replay — the gutter / no-run case', () => {
    expect(acceptReplay(null)).toBeNull();
    expect(acceptReplay(undefined)).toBeNull();
  });

  it('refuses an unknown or future model version rather than reinterpreting it', () => {
    const future = { ...pocketReplay(), model_version: 'planar-collision-replay-3d-v2' };
    expect(acceptReplay(future)).toBeNull();
  });

  it('refuses non-finite coordinates', () => {
    const nan = pocketReplay();
    nan.frames[1].bodies[0].x_in = Number.NaN;
    expect(acceptReplay(nan)).toBeNull();

    const infinite = pocketReplay();
    infinite.frames[1].bodies[0].y_in = Number.POSITIVE_INFINITY;
    expect(acceptReplay(infinite)).toBeNull();
  });

  it('refuses timestamps that do not strictly increase', () => {
    const duplicate = pocketReplay();
    duplicate.frames[1].t_s = duplicate.frames[0].t_s;
    expect(acceptReplay(duplicate)).toBeNull();

    const backwards = pocketReplay();
    backwards.frames[2].t_s = 0.01;
    expect(acceptReplay(backwards)).toBeNull();
  });

  it('refuses unsorted or duplicated body ids', () => {
    const unsorted = pocketReplay();
    unsorted.frames[0].bodies = [body(3, -6, 10.392), body(BALL_BODY_ID, -3.2, 0), body(1, 0, 0)];
    expect(acceptReplay(unsorted)).toBeNull();

    const duplicated = pocketReplay();
    duplicated.frames[0].bodies = [body(BALL_BODY_ID, -3.2, 0), body(1, 0, 0), body(1, 0, 0)];
    expect(acceptReplay(duplicated)).toBeNull();
  });

  it('refuses a frame set whose membership changes mid-replay', () => {
    const shifting = pocketReplay();
    shifting.frames[2].bodies = [body(BALL_BODY_ID, -2.8, 4), body(1, 0.3, 1.2)];
    expect(acceptReplay(shifting)).toBeNull();
  });

  it('refuses a replay with no ball body', () => {
    const noBall = pocketReplay();
    noBall.frames = noBall.frames.map((frame) => ({
      ...frame,
      bodies: frame.bodies.filter((b) => b.body_id !== BALL_BODY_ID),
    }));
    expect(acceptReplay(noBall)).toBeNull();
  });

  it('refuses an empty frame list', () => {
    expect(acceptReplay({ ...pocketReplay(), frames: [] })).toBeNull();
  });

  it('accepts a full-length run at the solver caps', () => {
    const replay = fullLengthReplay();
    expect(acceptReplay(replay)).toBe(replay);
    expect(replay.frames.length).toBe(MAX_ACCEPTED_REPLAY_FRAMES - 23);
    expect(replay.steps_taken).toBe(MAX_ACCEPTED_STEPS);
  });

  // --- Metadata firewall ------------------------------------------------
  // The defect these guard: a payload can carry the right model_version
  // and nothing but finite JavaScript numbers while still describing a run
  // no solver could have produced. Accepting one lets it drive playback.

  it('refuses a terminal time that would run the loop effectively forever', () => {
    // The concrete reported case: finite, known-version, and absurd.
    const hostile = pocketReplay();
    hostile.frames[hostile.frames.length - 1].t_s = 1e308;
    expect(acceptReplay(hostile)).toBeNull();
  });

  it('refuses a duration past the documented two-second solver cap', () => {
    const tooLong = pocketReplay();
    tooLong.steps_taken = MAX_ACCEPTED_STEPS + 1000;
    tooLong.frames[tooLong.frames.length - 1].t_s = MAX_ACCEPTED_DURATION_S + 0.5;
    expect(acceptReplay(tooLong)).toBeNull();

    // Even at a legal step count, a final timestamp beyond the cap is out.
    const overCap = pocketReplay();
    overCap.dt_s = 0.001;
    overCap.steps_taken = 4000; // 4 s
    overCap.frames[overCap.frames.length - 1].t_s = 4;
    expect(acceptReplay(overCap)).toBeNull();
  });

  it('refuses non-integer, zero, negative, or oversized step metadata', () => {
    for (const patch of [
      { sample_every_steps: 0 },
      { sample_every_steps: -100 },
      { sample_every_steps: 12.5 },
      { steps_taken: 0 },
      { steps_taken: -200 },
      { steps_taken: 200.5 },
      { steps_taken: MAX_ACCEPTED_STEPS + 1 },
      { dt_s: 0 },
      { dt_s: -0.0005 },
    ]) {
      expect(acceptReplay({ ...pocketReplay(), ...patch })).toBeNull();
    }
  });

  it('refuses a first frame that is not exactly impact', () => {
    const late = pocketReplay();
    late.frames[0].t_s = 0.05;
    expect(acceptReplay(late)).toBeNull();

    const negative = pocketReplay();
    negative.frames[0].t_s = -0.01;
    expect(acceptReplay(negative)).toBeNull();
  });

  it('refuses a final timestamp inconsistent with dt_s * steps_taken', () => {
    const mismatch = pocketReplay();
    // Metadata says 200 steps * 0.0005 = 0.1 s; claim 0.15 s instead.
    mismatch.frames[mismatch.frames.length - 1].t_s = 0.15;
    expect(acceptReplay(mismatch)).toBeNull();
  });

  it('refuses intermediate frames off the fixed sampling cadence', () => {
    const offCadence = pocketReplay();
    // 0.037 s is not a whole number of 0.05 s sampling intervals.
    offCadence.frames[1].t_s = 0.037;
    expect(acceptReplay(offCadence)).toBeNull();
  });

  it('refuses more frames than the recorder can emit', () => {
    const flood = pocketReplay();
    const one = flood.frames[0];
    flood.frames = Array.from({ length: MAX_ACCEPTED_REPLAY_FRAMES + 1 }, (_, i) => ({
      t_s: i * 0.05,
      bodies: one.bodies,
    }));
    expect(acceptReplay(flood)).toBeNull();
  });

  it('refuses more bodies than a ball plus a full rack', () => {
    const crowded = pocketReplay();
    const bodies = [body(BALL_BODY_ID, 0, 0)];
    for (let id = 1; id <= 10; id += 1) bodies.push(body(id, id, id));
    bodies.push(body(11, 0, 0)); // 12 bodies, and 11 is not a pin
    crowded.frames = crowded.frames.map((f) => ({ ...f, bodies }));
    expect(acceptReplay(crowded)).toBeNull();
  });

  it('refuses body ids outside the ball plus pins 1..10', () => {
    for (const badId of [11, 99, -1, 999]) {
      const bad = pocketReplay();
      bad.frames = bad.frames.map((f) => ({
        ...f,
        bodies: [body(BALL_BODY_ID, 0, 0), body(badId, 1, 1)],
      }));
      expect(acceptReplay(bad)).toBeNull();
    }
  });

  it('refuses finite-but-impossible coordinates', () => {
    for (const patch of [
      { x_in: MAX_ABS_COORDINATE_IN + 1 },
      { x_in: -(MAX_ABS_COORDINATE_IN + 1) },
      { y_in: MAX_ABS_COORDINATE_IN + 1 },
      { y_in: 1e12 },
    ]) {
      const bad = pocketReplay();
      Object.assign(bad.frames[1].bodies[0], patch);
      expect(acceptReplay(bad)).toBeNull();
    }
  });

  it('still accepts the real coordinate range a two-second run produces', () => {
    // Measured extremes across the legal release space reach about 271 in
    // laterally and 404 in downlane; the bound must not reject those.
    const wide = pocketReplay();
    wide.frames[1].bodies[0].x_in = -271;
    wide.frames[1].bodies[0].y_in = 405;
    expect(acceptReplay(wide)).toBe(wide);
  });

  it('never throws on structurally hostile input', () => {
    for (const bad of [
      {},
      { model_version: SUPPORTED_REPLAY_MODEL_VERSION },
      { ...pocketReplay(), frames: 'not-an-array' },
      { ...pocketReplay(), dt_s: 0 },
      { ...pocketReplay(), frames: [{ t_s: 0, bodies: [] }] },
      { ...pocketReplay(), frames: [{ t_s: 0, bodies: [{ body_id: 1.5, x_in: 0, y_in: 0 }] }] },
    ]) {
      expect(() => acceptReplay(bad as never)).not.toThrow();
      expect(acceptReplay(bad as never)).toBeNull();
    }
  });

  it('does not mutate the replay it inspects', () => {
    const replay = pocketReplay();
    const before = JSON.stringify(replay);
    acceptReplay(replay);
    expect(JSON.stringify(replay)).toBe(before);
  });
});

describe('replayBodyToLanePosition', () => {
  it('maps lane center to board 20 and the headpin plane to 60 ft', () => {
    const at = replayBodyToLanePosition(body(1, 0, 0));
    expect(at.board).toBeCloseTo(20, 10);
    expect(at.distanceFt).toBeCloseTo(60, 10);
  });

  it('maps positive x_in toward higher boards, matching the backend convention', () => {
    // 1.05 in is one board, so +1.05 in must be exactly one board higher.
    expect(replayBodyToLanePosition(body(1, 1.05, 0)).board).toBeCloseTo(21, 10);
    expect(replayBodyToLanePosition(body(1, -1.05, 0)).board).toBeCloseTo(19, 10);
  });

  it('maps y_in downlane in feet past the headpin plane', () => {
    expect(replayBodyToLanePosition(body(1, 0, 12)).distanceFt).toBeCloseTo(61, 10);
    expect(replayBodyToLanePosition(body(1, 0, 10.392)).distanceFt).toBeCloseTo(60.866, 3);
  });

  it('preserves the body id', () => {
    expect(replayBodyToLanePosition(body(7, 1, 2)).bodyId).toBe(7);
  });
});

describe('boundary continuity with the trajectory path', () => {
  it('places the replay ball at the same board the path ends on, within a small tolerance', () => {
    // The trajectory path ends at a board; the replay's own first frame
    // records the ball's lateral inches at that same instant. Converting
    // the latter must land on the former -- a reversed x axis would put
    // it symmetrically on the wrong side, which is exactly what this
    // catches. Board 17.0 <-> (17.0 - 20) * 1.05 = -3.15 in.
    const pathEndBoard = 17.0;
    const replayBallXIn = (pathEndBoard - 20) * 1.05;

    const at = replayBodyToLanePosition(body(BALL_BODY_ID, replayBallXIn, 0));
    expect(Math.abs(at.board - pathEndBoard)).toBeLessThan(0.01);
    expect(Math.abs(at.distanceFt - 60)).toBeLessThan(0.01);
  });

  it('would fail loudly if the lateral axis were mirrored', () => {
    // Guards the test above from being satisfied by a sign error: a
    // mirrored conversion of the same input lands on board 23, not 17.
    const mirrored = 20 - (17.0 - 20);
    expect(mirrored).toBe(23);
    expect(replayBodyToLanePosition(body(BALL_BODY_ID, (17.0 - 20) * 1.05, 0)).board).not.toBeCloseTo(
      mirrored,
      1,
    );
  });
});

describe('replayPositionsAt', () => {
  it('returns the first frame exactly at or before its timestamp', () => {
    const replay = pocketReplay();
    for (const t of [-1, 0]) {
      const at = replayPositionsAt(replay, t);
      expect(at.map((b) => b.bodyId)).toEqual([BALL_BODY_ID, 1, 3]);
      expect(at[0].board).toBeCloseTo(20 + -3.2 / 1.05, 10);
    }
  });

  it('returns the last frame exactly at or after its timestamp', () => {
    const replay = pocketReplay();
    for (const t of [0.1, 5]) {
      const at = replayPositionsAt(replay, t);
      expect(at[0].board).toBeCloseTo(20 + -2.8 / 1.05, 10);
    }
  });

  it('interpolates linearly between the two adjacent authoritative frames', () => {
    const replay = pocketReplay();
    // Exactly halfway between t_s 0 and 0.05: the ball's x_in should be
    // the midpoint of -3.2 and -3.0.
    const at = replayPositionsAt(replay, 0.025);
    expect(at[0].board).toBeCloseTo(20 + -3.1 / 1.05, 10);
    expect(at[0].distanceFt).toBeCloseTo(60 + 1 / 12, 10);
  });

  it('never produces a position outside the bracketing frames', () => {
    const replay = pocketReplay();
    for (let t = 0; t <= 0.1; t += 0.005) {
      const at = replayPositionsAt(replay, t);
      const ball = at.find((b) => b.bodyId === BALL_BODY_ID);
      // The ball only moves downlane and rightward-to-left across these
      // frames, so every interpolated point must sit inside that span.
      expect(ball?.board).toBeGreaterThanOrEqual(20 + -3.2 / 1.05 - 1e-9);
      expect(ball?.board).toBeLessThanOrEqual(20 + -2.8 / 1.05 + 1e-9);
      expect(ball?.distanceFt).toBeGreaterThanOrEqual(60 - 1e-9);
      expect(ball?.distanceFt).toBeLessThanOrEqual(60 + 4 / 12 + 1e-9);
    }
  });

  it('lands exactly on a recorded frame when the time matches one', () => {
    const replay = pocketReplay();
    const at = replayPositionsAt(replay, 0.05);
    expect(at[0].board).toBeCloseTo(20 + -3.0 / 1.05, 10);
  });

  it('does not mutate the replay', () => {
    const replay = pocketReplay();
    const before = JSON.stringify(replay);
    replayPositionsAt(replay, 0.03);
    expect(JSON.stringify(replay)).toBe(before);
  });
});

describe('replayMaxDistanceFt', () => {
  it('reports the furthest downlane position any body reaches', () => {
    // pocketReplay's largest y_in is 10.8 (pin 3 in the last frame).
    expect(replayMaxDistanceFt(pocketReplay())).toBeCloseTo(60 + 10.8 / 12, 10);
  });

  it('accounts for bodies that travel well past the pin deck', () => {
    // The reason the canvas needs this at all: a two-second run pushes
    // bodies far beyond the deck's back row (~62.6 ft), so a viewport
    // sized to the default lane geometry would have to clamp them.
    const far = pocketReplay();
    far.frames[2].bodies[1].y_in = 176.1; // the measured seed-17 extent
    expect(replayMaxDistanceFt(far)).toBeCloseTo(74.675, 3);
    expect(replayMaxDistanceFt(far)).toBeGreaterThan(64.6);
  });

  it('does not mutate the replay it measures', () => {
    const replay = pocketReplay();
    const before = JSON.stringify(replay);
    replayMaxDistanceFt(replay);
    expect(JSON.stringify(replay)).toBe(before);
  });
});

describe('replayDurationS', () => {
  it('is the last frame timestamp', () => {
    expect(replayDurationS(pocketReplay())).toBe(0.1);
    expect(replayDurationS(partialRackReplay())).toBe(0.05);
  });
});
