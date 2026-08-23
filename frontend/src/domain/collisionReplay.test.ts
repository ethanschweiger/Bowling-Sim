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
  replayDistanceExtentFt,
  replayDurationS,
  replayMaxDistanceFt,
  replayPositionsAt,
  REPLAY_TERMINATION_REASONS,
  SUPPORTED_REPLAY_MODEL_VERSION,
  V3_DT_S,
  V3_SAMPLE_EVERY_STEPS,
} from './collisionReplay';

function body(body_id: number, x_in: number, y_in: number) {
  return { body_id, x_in, y_in };
}

// Fixtures below obey the recorded contract the backend actually emits:
// dt_s 0.0005, cadence every 20 steps (so on-cadence frames land on
// multiples of 0.01 s), a first frame exactly at impact, and a final frame
// at exactly dt_s * steps_taken.
//
// v3 raised the cadence from every 100 steps (50 ms) to every 20 (10 ms),
// which is why these are built programmatically rather than listed by hand:
// a full-length run is 201 frames, and a fixture that merely *looked* dense
// enough would be exactly the sparse payload the validator must reject.
//
// Each fixture carries a `termination_reason` the real solver could actually
// have recorded alongside its step count. Only one implication constrains
// that pairing: stopping short of the 4,000-step cap is reachable only
// through the settle branch, so a fixture with `steps_taken < 4000` must say
// `settled`. The converse does NOT hold -- a run whose bodies cross the
// velocity threshold on the final permitted step records `settled` with
// `steps_taken === 4000` -- so at the cap either reason describes a
// producible run. See "The reason is not a function of steps_taken" in
// backend/app/physics/replay.py.
//
// None of this is something the client may reason from: `acceptReplay`
// validates the reason as one of two strings and never checks it against the
// step count. These notes are about keeping the fixtures honest.

/** Every step a v3 recorder must emit a frame for: step 0, each cadence
 * tick, and the final step when it is not itself a tick. */
function scheduledSteps(stepsTaken: number): number[] {
  const steps: number[] = [];
  for (let step = 0; step <= stepsTaken; step += V3_SAMPLE_EVERY_STEPS) {
    steps.push(step);
  }
  if (steps[steps.length - 1] !== stepsTaken) {
    steps.push(stepsTaken);
  }
  return steps;
}

/** Builds a complete v3 replay: one frame per scheduled step, with each
 * body placed by `positionAt` as a pure function of elapsed time. */
function buildReplay(
  stepsTaken: number,
  terminationReason: 'settled' | 'step_cap',
  bodyIds: readonly number[],
  positionAt: (bodyId: number, tS: number) => { x_in: number; y_in: number },
): CollisionReplayResponse {
  return {
    model_version: SUPPORTED_REPLAY_MODEL_VERSION,
    termination_reason: terminationReason,
    dt_s: V3_DT_S,
    sample_every_steps: V3_SAMPLE_EVERY_STEPS,
    steps_taken: stepsTaken,
    frames: scheduledSteps(stepsTaken).map((step) => {
      const tS = step * V3_DT_S;
      return {
        t_s: tS,
        bodies: bodyIds.map((id) => {
          const { x_in, y_in } = positionAt(id, tS);
          return body(id, x_in, y_in);
        }),
      };
    }),
  };
}

/** A pocket-shaped replay: ball arriving left of center, headpin ahead.
 * 200 steps at 0.0005 s = 0.1 s, so 11 frames on the 0.01 s cadence. */
function pocketReplay(): CollisionReplayResponse {
  return buildReplay(200, 'settled', [BALL_BODY_ID, 1, 3], (id, tS) => {
    if (id === BALL_BODY_ID) return { x_in: -3.2 + tS * 4, y_in: tS * 40 };
    if (id === 1) return { x_in: tS * 3, y_in: tS * 12 };
    return { x_in: -6 + tS * 4, y_in: 10.392 + tS * 4 };
  });
}

/** A second-ball spare attempt: only the pins that were still standing.
 * 100 steps = 0.05 s, which is five v3 cadence intervals (5 x 20 steps), so
 * the final step is itself a cadence tick and no terminal frame is appended.
 * (100 here is the run length, not the stride -- the v3 stride is 20.) */
function partialRackReplay(): CollisionReplayResponse {
  return buildReplay(100, 'settled', [BALL_BODY_ID, 7, 10], (id, tS) => {
    if (id === BALL_BODY_ID) return { x_in: 5 + tS * 4, y_in: tS * 60 };
    if (id === 7) return { x_in: 18, y_in: 31.18 };
    return { x_in: -18, y_in: 31.18 };
  });
}

/** A full-length run: the solver's own 4,000-step / 2 s cap, 201 frames. */
function fullLengthReplay(): CollisionReplayResponse {
  // A full-length run that never settled. `settled` at 4,000 steps would be
  // equally producible; this fixture is the common case, not the only legal
  // pairing.
  return buildReplay(4000, 'step_cap', [BALL_BODY_ID, 1], (id, tS) =>
    id === BALL_BODY_ID ? { x_in: -3.2 + tS * 2, y_in: tS * 80 } : { x_in: 0, y_in: 0 },
  );
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

  // --- The version and termination contract -------------------------------
  //
  // v2 introduced the required `termination_reason`; v3 additionally moved
  // the sampling cadence to every 20 steps. These pin the ways a payload can
  // fail to be this contract -- an older version, a missing reason, an
  // unrecognized value, or v2's sparser schedule -- since each would
  // otherwise leave the client guessing or interpolating over gaps.

  it('is pinned to the v3 model version', () => {
    // The literal string, not the constant: every other assertion in this
    // file follows a bump automatically, so without this one a version
    // change would pass unnoticed on both sides of the contract.
    expect(SUPPORTED_REPLAY_MODEL_VERSION).toBe('planar-collision-replay-2d-v3');
  });

  it('refuses a v1 payload rather than assuming how its run ended', () => {
    // v1 recorded no distinction between the solver's two exits, so there
    // is no correct value to supply on its behalf. Even with an otherwise
    // perfect frame schedule it must fall back.
    const v1 = { ...pocketReplay(), model_version: 'planar-collision-replay-2d-v1' };
    expect(acceptReplay(v1)).toBeNull();

    // And a v1 payload that *also* omits the field -- the shape a real v1
    // backend actually sent.
    const { termination_reason: _omitted, ...withoutReason } = v1;
    expect(acceptReplay(withoutReason as CollisionReplayResponse)).toBeNull();
  });

  it('refuses a genuine v2 payload — right shape, wrong contract', () => {
    // A well-formed v2 recording: correct version string for v2, its own
    // 100-step cadence, and a complete schedule *for that cadence*. It is
    // perfectly good data and still must not play, because v3 validates a
    // different schedule and interpolating across v2's wider gaps is exactly
    // the visible-latency problem the bump exists to fix.
    const v2Frames = [];
    for (let step = 0; step <= 4000; step += 100) {
      v2Frames.push({
        t_s: step * 0.0005,
        bodies: [body(BALL_BODY_ID, -3.2 + step * 0.001, step * 0.04)],
      });
    }
    const v2: CollisionReplayResponse = {
      model_version: 'planar-collision-replay-2d-v2',
      termination_reason: 'step_cap',
      dt_s: 0.0005,
      sample_every_steps: 100,
      steps_taken: 4000,
      frames: v2Frames,
    };

    expect(v2Frames.length).toBe(41); // a complete v2 schedule, not a stub
    expect(acceptReplay(v2)).toBeNull();

    // And relabelling it v3 does not rescue it: the cadence and the frame
    // count are both still v2's.
    expect(acceptReplay({ ...v2, model_version: SUPPORTED_REPLAY_MODEL_VERSION })).toBeNull();
  });

  it('refuses a v3-labelled payload carrying only the old 50 ms samples', () => {
    // The sparse case stated precisely: correct version, correct cadence
    // metadata, but only every fifth scheduled frame present.
    const dense = fullLengthReplay();
    const sparse = {
      ...dense,
      frames: dense.frames.filter((_frame, index) => index % 5 === 0),
    };

    // Asserted, not assumed: the metadata is v3 and declares the 20-step
    // stride, so the rejection can only be about the missing samples --
    // this is not the legacy-stride case, which is covered separately.
    expect(sparse.model_version).toBe(SUPPORTED_REPLAY_MODEL_VERSION);
    expect(sparse.sample_every_steps).toBe(V3_SAMPLE_EVERY_STEPS);
    expect(sparse.dt_s).toBe(V3_DT_S);
    expect(dense.frames.length).toBe(201);
    expect(sparse.frames.length).toBe(41);
    expect(acceptReplay(dense)).toBe(dense);
    expect(acceptReplay(sparse)).toBeNull();
  });

  it('refuses a v3 payload missing a single interior frame', () => {
    const dense = fullLengthReplay();
    const missingOne = {
      ...dense,
      frames: dense.frames.filter((_frame, index) => index !== 100),
    };

    expect(missingOne.frames.length).toBe(200);
    expect(acceptReplay(missingOne)).toBeNull();
  });

  it('refuses a v3 payload with one extra frame the recorder would not emit', () => {
    const dense = fullLengthReplay();
    const extra = {
      ...dense,
      frames: [
        ...dense.frames.slice(0, 100),
        { t_s: 100 * V3_SAMPLE_EVERY_STEPS * V3_DT_S + V3_DT_S, bodies: dense.frames[100].bodies },
        ...dense.frames.slice(100),
      ],
    };

    expect(extra.frames.length).toBe(202);
    expect(acceptReplay(extra)).toBeNull();
  });

  it('refuses a v3 payload whose frame bodies are malformed', () => {
    const missingBody = fullLengthReplay();
    missingBody.frames[50] = { t_s: missingBody.frames[50].t_s, bodies: [] };
    expect(acceptReplay(missingBody)).toBeNull();

    const wrongMembership = fullLengthReplay();
    wrongMembership.frames[50] = {
      t_s: wrongMembership.frames[50].t_s,
      bodies: [body(BALL_BODY_ID, 0, 0), body(9, 0, 0)],
    };
    expect(acceptReplay(wrongMembership)).toBeNull();

    const notFinite = fullLengthReplay();
    notFinite.frames[50].bodies[0].x_in = Number.NaN;
    expect(acceptReplay(notFinite)).toBeNull();
  });

  it('refuses a payload with no termination reason', () => {
    const { termination_reason: _omitted, ...missing } = pocketReplay();
    expect(acceptReplay(missing as CollisionReplayResponse)).toBeNull();
  });

  it('refuses an unrecognized termination reason', () => {
    for (const bad of ['SETTLED', 'settled ', 'cap', 'unknown', '', 'timeout']) {
      expect(acceptReplay({ ...pocketReplay(), termination_reason: bad })).toBeNull();
    }
  });

  it('refuses a termination reason that is not a string at all', () => {
    for (const bad of [0, 1, true, null, {}, ['settled']]) {
      const forged = { ...pocketReplay(), termination_reason: bad } as CollisionReplayResponse;
      expect(acceptReplay(forged)).toBeNull();
    }
  });

  it('accepts both real termination reasons and reports them unchanged', () => {
    // Both values must survive the firewall -- a validator that only ever
    // admitted the common one would quietly drop every settled run.
    for (const reason of REPLAY_TERMINATION_REASONS) {
      const replay = { ...pocketReplay(), termination_reason: reason };
      const accepted = acceptReplay(replay);
      expect(accepted).toBe(replay);
      expect(accepted?.termination_reason).toBe(reason);
    }
  });

  it('allows exactly the two reasons the backend can record', () => {
    expect([...REPLAY_TERMINATION_REASONS]).toEqual(['settled', 'step_cap']);
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
    // Derived, not a magic number: 4000 / 20 + 1 = 201 scheduled frames,
    // comfortably inside the 256 cap.
    expect(replay.frames.length).toBe(MAX_ACCEPTED_STEPS / V3_SAMPLE_EVERY_STEPS + 1);
    expect(replay.frames.length).toBe(201);
    expect(replay.frames.length).toBeLessThanOrEqual(MAX_ACCEPTED_REPLAY_FRAMES);
    expect(replay.steps_taken).toBe(MAX_ACCEPTED_STEPS);
  });

  // --- Complete-schedule firewall ---------------------------------------
  // Being *on* the cadence is not the same as being the *whole* recording.
  // A sparse payload that skips frames would be silently reconstructed by
  // interpolation, so the client must require every sample the recorder
  // would have produced.

  it('refuses a sparse run that is cadence-aligned but missing its middle frames', () => {
    // The reported case, as a payload that is valid v3 in every other
    // respect: correct version, a real termination reason, the 0.0005 s
    // timestep, the 20-step stride, and authoritative first and last
    // bodies. Only the interior required frames are gone.
    //
    // Both surviving timestamps sit exactly on the cadence, so a per-frame
    // check passes them -- and the browser would then invent two seconds of
    // collision motion between them. Only requiring the *whole* schedule
    // catches it.
    const complete = fullLengthReplay();
    const sparse: CollisionReplayResponse = {
      ...complete,
      frames: [complete.frames[0], complete.frames[complete.frames.length - 1]],
    };

    // The control first: this exact payload plays when its schedule is whole.
    expect(acceptReplay(complete)).toBe(complete);
    expect(complete.frames.length).toBe(201);

    // Nothing else differs -- same metadata, same endpoint bodies.
    expect(sparse.model_version).toBe(SUPPORTED_REPLAY_MODEL_VERSION);
    expect(sparse.termination_reason).toBe(complete.termination_reason);
    expect(sparse.dt_s).toBe(V3_DT_S);
    expect(sparse.sample_every_steps).toBe(V3_SAMPLE_EVERY_STEPS);
    expect(sparse.steps_taken).toBe(complete.steps_taken);
    expect(sparse.frames[0]).toBe(complete.frames[0]);
    expect(sparse.frames[1]).toBe(complete.frames[complete.frames.length - 1]);
    expect(sparse.frames[0].t_s).toBe(0);
    expect(sparse.frames[1].t_s).toBeCloseTo(2.0, 12);

    // So the rejection can only be the 199 absent interior frames.
    expect(sparse.frames.length).toBe(2);
    expect(acceptReplay(sparse)).toBeNull();
  });

  it('refuses a run missing one middle cadence tick', () => {
    const gap = fullLengthReplay();
    gap.frames.splice(20, 1); // drop t = 1.0 s
    expect(acceptReplay(gap)).toBeNull();
  });

  it('refuses an extra frame the recorder would not have emitted', () => {
    const extra = fullLengthReplay();
    extra.frames.splice(20, 0, { t_s: 0.975, bodies: extra.frames[20].bodies });
    expect(acceptReplay(extra)).toBeNull();
  });

  it('refuses frames delivered out of order', () => {
    const shuffled = fullLengthReplay();
    const swap = shuffled.frames[5];
    shuffled.frames[5] = shuffled.frames[6];
    shuffled.frames[6] = swap;
    expect(acceptReplay(shuffled)).toBeNull();
  });

  it('refuses an altered fixed v3 timestep or sample stride', () => {
    // dt_s and sample_every_steps are constants of this model version, not
    // payload-chosen values. Letting a payload pick them would let it
    // define a cadence that makes any sparse frame list look valid.
    const otherDt = fullLengthReplay();
    otherDt.dt_s = 0.001;
    expect(acceptReplay(otherDt)).toBeNull();

    const otherStride = fullLengthReplay();
    otherStride.sample_every_steps = 100; // v2's cadence -- valid data, wrong contract
    expect(acceptReplay(otherStride)).toBeNull();
  });

  it('accepts a run whose final step is not itself a cadence tick', () => {
    // 250 steps at the v3 cadence -> ticks at 0, 20, ... 240, plus one
    // terminal frame at 250. Stopping there means the settle branch fired,
    // which is the shape a real low-energy run produces: settling usually
    // lands on an arbitrary step rather than a cadence tick. (Not always —
    // a crossing on step 4,000 settles exactly on one.)
    const nonCadenceTerminal = buildReplay(250, 'settled', [BALL_BODY_ID], (_id, tS) => ({
      x_in: -3 + tS * 20,
      y_in: tS * 40,
    }));

    // Ticks 0, 20, ... 240 is 13 frames; the terminal frame at 250 makes 14.
    expect(nonCadenceTerminal.frames.length).toBe(
      Math.floor(250 / V3_SAMPLE_EVERY_STEPS) + 1 + 1,
    );
    expect(nonCadenceTerminal.frames.length).toBe(14);
    expect(nonCadenceTerminal.frames[nonCadenceTerminal.frames.length - 1].t_s).toBeCloseTo(
      250 * V3_DT_S,
      12,
    );
    expect(acceptReplay(nonCadenceTerminal)).toBe(nonCadenceTerminal);
  });

  it('refuses a non-cadence terminal step whose extra frame is missing', () => {
    // Everything about this payload is correct v3 *except* the one required
    // terminal frame: v3 metadata, the full 20-step tick sequence 0..240,
    // and only step 250 absent. So it can fail for exactly one reason.
    const complete = buildReplay(250, 'settled', [BALL_BODY_ID], (_id, tS) => ({
      x_in: -3 + tS * 20,
      y_in: tS * 40,
    }));
    const missingTerminal: CollisionReplayResponse = {
      ...complete,
      frames: complete.frames.slice(0, -1),
    };

    // The control: with the terminal frame present this same payload plays.
    expect(acceptReplay(complete)).toBe(complete);

    // The defect under test, stated precisely.
    expect(missingTerminal.sample_every_steps).toBe(V3_SAMPLE_EVERY_STEPS);
    expect(missingTerminal.model_version).toBe(SUPPORTED_REPLAY_MODEL_VERSION);
    const times = missingTerminal.frames.map((f) => f.t_s);
    expect(times).toEqual(
      Array.from({ length: 13 }, (_unused, tick) => tick * V3_SAMPLE_EVERY_STEPS * V3_DT_S),
    );
    expect(times[times.length - 1]).toBeCloseTo(240 * V3_DT_S, 12);
    expect(acceptReplay(missingTerminal)).toBeNull();
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

  it('only ever interpolates between two adjacent required samples', () => {
    // With the complete schedule enforced, every pair of neighbouring frames
    // is one v3 cadence interval (0.01 s) apart, so an interpolated point can
    // never span more than one recorder interval -- there is no omitted frame
    // to bridge. That interval is five times shorter than v2's, which is the
    // whole point of the version bump.
    const cadenceS = V3_SAMPLE_EVERY_STEPS * V3_DT_S;
    expect(cadenceS).toBeCloseTo(0.01, 12);

    const replay = fullLengthReplay();
    expect(acceptReplay(replay)).toBe(replay);
    for (let i = 1; i < replay.frames.length; i += 1) {
      expect(replay.frames[i].t_s - replay.frames[i - 1].t_s).toBeCloseTo(cadenceS, 9);
    }
    // A sample midway between two adjacent frames stays inside that pair.
    const mid = replayPositionsAt(replay, cadenceS * 1.5);
    const lower = replay.frames[1].bodies[0];
    const upper = replay.frames[2].bodies[0];
    const ball = mid.find((b) => b.bodyId === BALL_BODY_ID)!;
    const lowerBoard = 20 + lower.x_in / 1.05;
    const upperBoard = 20 + upper.x_in / 1.05;
    expect(ball.board).toBeGreaterThanOrEqual(Math.min(lowerBoard, upperBoard) - 1e-9);
    expect(ball.board).toBeLessThanOrEqual(Math.max(lowerBoard, upperBoard) + 1e-9);
  });

  it('does not mutate the replay', () => {
    const replay = pocketReplay();
    const before = JSON.stringify(replay);
    replayPositionsAt(replay, 0.03);
    expect(JSON.stringify(replay)).toBe(before);
  });
});

describe('replayDistanceExtentFt', () => {
  it('reports both ends of the recorded downlane span', () => {
    const extent = replayDistanceExtentFt(pocketReplay());
    // Smallest y_in is 0 (the ball on the impact plane at t=0); largest is
    // pin 3 at the final frame, 10.392 + 0.1 s * 4 in/s = 10.792 in.
    expect(extent.minFt).toBeCloseTo(60, 10);
    expect(extent.maxFt).toBeCloseTo(60 + 10.792 / 12, 10);
  });

  it('reports a negative-y body behind the headpin plane', () => {
    // A collision can drive a body back toward the bowler. A viewport that
    // only grew at the far edge would clamp such a body onto the foul
    // line -- the same error as the far-edge clamp, at the other end.
    const backwards = pocketReplay();
    backwards.frames[1].bodies[1].y_in = -240; // 20 ft back from the deck
    const extent = replayDistanceExtentFt(backwards);
    expect(extent.minFt).toBeCloseTo(40, 10);
    expect(extent.maxFt).toBeGreaterThan(extent.minFt);
  });

  it('does not mutate the replay it measures', () => {
    const replay = pocketReplay();
    const before = JSON.stringify(replay);
    replayDistanceExtentFt(replay);
    expect(JSON.stringify(replay)).toBe(before);
  });
});

describe('replayMaxDistanceFt', () => {
  it('reports the furthest downlane position any body reaches', () => {
    // pocketReplay's largest y_in is 10.792 (pin 3 in the last frame).
    expect(replayMaxDistanceFt(pocketReplay())).toBeCloseTo(60 + 10.792 / 12, 10);
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
