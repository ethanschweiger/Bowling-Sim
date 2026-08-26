import { describe, expect, it } from 'vitest';
import type { CollisionReplayResponse, TrajectoryPointResponse } from '../api/types';
import {
  acceptReplay,
  BALL_BODY_ID,
  SUPPORTED_REPLAY_MODEL_VERSION,
  V3_DT_S,
  V3_SAMPLE_EVERY_STEPS,
} from './collisionReplay';
import { buildLaneScene, type LaneScene } from './laneScene';
import { phaseAt, SETTLED, TERMINAL_HOLD_MS } from './playbackController';
import { TRAJECTORY_ANIMATION_DURATION_MS } from './trajectoryAnimation';

/**
 * Staging continuity for the lane canvas.
 *
 * The defect these guard is not a missing animation — the server's frames
 * were always moving — but a rack that changed identity under it: the
 * approach drawn against the post-throw `standing_pin_ids`, the deck against
 * the replay's pre-throw frame, and a swap back to static in the same instant
 * the last recorded positions appeared.
 */

const DECK_DURATION_S = 2.0;

/** Every step a v3 recorder must emit a frame for. */
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

/** A complete v3 replay: one frame per scheduled step, bodies placed by a
 * pure function of elapsed time. Built rather than listed because a
 * full-length run at the 10 ms cadence is 201 frames. */
function buildReplay(
  stepsTaken: number,
  bodyIds: readonly number[],
  positionAt: (bodyId: number, tS: number) => { x_in: number; y_in: number },
  crossings: { pin_id: number; step_index: number }[] = [],
): CollisionReplayResponse {
  return {
    model_version: SUPPORTED_REPLAY_MODEL_VERSION,
    termination_reason: 'step_cap',
    dt_s: V3_DT_S,
    sample_every_steps: V3_SAMPLE_EVERY_STEPS,
    steps_taken: stepsTaken,
    threshold_crossings: crossings,
    frames: scheduledSteps(stepsTaken).map((step) => {
      const tS = step * V3_DT_S;
      return {
        t_s: tS,
        bodies: bodyIds.map((body_id) => ({ body_id, ...positionAt(body_id, tS) })),
      };
    }),
  };
}

/** A full-rack replay: ball plus pins 1, 3 and 10. Pin 10 never moves, so a
 * test can assert nothing invents motion the server did not record. */
function fullRackReplay(): CollisionReplayResponse {
  return buildReplay(4000, [BALL_BODY_ID, 1, 3, 10], (id, tS) => {
    if (id === BALL_BODY_ID) return { x_in: -3 + tS * 0.5, y_in: tS * 6 };
    if (id === 1) return { x_in: tS * 2, y_in: tS * 4 };
    if (id === 3) return { x_in: -6 - tS, y_in: 10.392 + tS * 3 };
    return { x_in: -18, y_in: 31.176 };
  });
}

/** A second ball: only the three pins that were still standing. */
function partialRackReplay(): CollisionReplayResponse {
  return buildReplay(4000, [BALL_BODY_ID, 2, 4, 7], (id, tS) => {
    if (id === BALL_BODY_ID) return { x_in: 5 + tS * 0.5, y_in: tS * 6 };
    if (id === 2) return { x_in: 6, y_in: 10.392 };
    if (id === 4) return { x_in: 12 + tS, y_in: 20.784 + tS * 2 };
    return { x_in: 18, y_in: 31.176 };
  });
}

const PATH: readonly TrajectoryPointResponse[] = [
  { distance_ft: 0, board: 28, elapsed_s: 0 },
  { distance_ft: 30, board: 22, elapsed_s: 1.5 },
  { distance_ft: 60, board: 17, elapsed_s: 3 },
];

// Deliberately disagrees with every replay above: pin 1 is gone and pins 5
// and 9 appear. If any approach or deck scene reads this, the difference is
// unmissable -- which is the point.
const POST_SCORE_RACK = [5, 9, 10];

const pinIds = (scene: LaneScene): number[] =>
  scene.pins.source === 'replay' ? scene.pins.bodies.map((p) => p.pinId) : [];

function accepted(replay: CollisionReplayResponse) {
  const value = acceptReplay(replay);
  if (!value) {
    throw new Error('fixture must be an acceptable replay');
  }
  return value;
}

describe('the fixtures are replays the validator accepts', () => {
  it('accepts both, so these scenes describe data that could really arrive', () => {
    expect(acceptReplay(fullRackReplay())).not.toBeNull();
    expect(acceptReplay(partialRackReplay())).not.toBeNull();
  });
});

describe('buildLaneScene — the approach', () => {
  it('draws the pre-throw rack from replay frame 0, not the post-score rack', () => {
    const replay = accepted(fullRackReplay());
    const scene = buildLaneScene({ kind: 'path', progress: 0.4 }, replay, PATH, POST_SCORE_RACK);

    expect(scene.pins.source).toBe('replay');
    expect(pinIds(scene)).toEqual([1, 3, 10]);
    // The post-score rack's own membership never shows through.
    expect(pinIds(scene)).not.toContain(5);
    expect(pinIds(scene)).not.toContain(9);
  });

  it('holds frame 0 for the whole approach rather than advancing the pins', () => {
    const replay = accepted(fullRackReplay());
    const early = buildLaneScene({ kind: 'path', progress: 0.05 }, replay, PATH, POST_SCORE_RACK);
    const late = buildLaneScene({ kind: 'path', progress: 0.95 }, replay, PATH, POST_SCORE_RACK);

    // Pins are the recorded pre-impact rack, motionless until impact.
    expect(late.pins).toEqual(early.pins);
    // Only the ball has moved.
    expect(late.ball).not.toEqual(early.ball);
  });

  it('excludes the replay ball from the pins and keeps the trajectory ball', () => {
    const replay = accepted(fullRackReplay());
    const scene = buildLaneScene({ kind: 'path', progress: 0.5 }, replay, PATH, POST_SCORE_RACK);

    expect(pinIds(scene)).not.toContain(BALL_BODY_ID);
    expect(scene.ball).not.toBeNull();
    // Mid-approach the ball is on the recorded path, not at the replay
    // ball's own deck position.
    expect(scene.ball!.distanceFt).toBeLessThan(60);
    expect(scene.showEntryMarker).toBe(false);
  });

  it('falls back to the static rack when there is no playable replay', () => {
    const scene = buildLaneScene({ kind: 'path', progress: 0.5 }, null, PATH, POST_SCORE_RACK);

    expect(scene.pins).toEqual({ source: 'rack', standingPinIds: POST_SCORE_RACK });
    expect(scene.ball).not.toBeNull();
  });
});

describe('buildLaneScene — the path-to-deck boundary', () => {
  it('changes exactly one thing at deck t=0: the ball', () => {
    const replay = accepted(fullRackReplay());
    // The last approach instant and deck t_s = 0 are the same moment.
    const approach = buildLaneScene({ kind: 'path', progress: 1 }, replay, PATH, POST_SCORE_RACK);
    const deckZero = buildLaneScene(
      { kind: 'deck', tS: 0, isTerminal: false },
      replay,
      PATH,
      POST_SCORE_RACK,
    );

    // Same bodies, same recorded coordinates -- no reset, no flash.
    expect(deckZero.pins).toEqual(approach.pins);
    // And the ball is the only difference.
    expect(deckZero.ball).not.toEqual(approach.ball);
    expect(approach.ball).not.toBeNull();
    expect(deckZero.ball).not.toBeNull();
  });

  it('carries the same continuity through the real phase sequence', () => {
    // Driven by phaseAt rather than hand-built phases, so the boundary under
    // test is the one the controller actually produces.
    const replay = accepted(fullRackReplay());
    const sequence = { replay };
    const sceneAt = (elapsed: number) =>
      buildLaneScene(phaseAt(sequence, elapsed), replay, PATH, POST_SCORE_RACK);

    const lastApproach = sceneAt(TRAJECTORY_ANIMATION_DURATION_MS - 1);
    const firstDeck = sceneAt(TRAJECTORY_ANIMATION_DURATION_MS);

    expect(lastApproach.pins).toEqual(firstDeck.pins);
    expect(firstDeck.pins.source).toBe('replay');
  });

  it('never shows two balls at any point in the sequence', () => {
    const replay = accepted(fullRackReplay());
    const sequence = { replay };
    const end = TRAJECTORY_ANIMATION_DURATION_MS + DECK_DURATION_S * 1000;

    for (let elapsed = 0; elapsed <= end + TERMINAL_HOLD_MS + 200; elapsed += 25) {
      const scene = buildLaneScene(phaseAt(sequence, elapsed), replay, PATH, POST_SCORE_RACK);
      const ballCount = (scene.ball ? 1 : 0) + (scene.showEntryMarker ? 1 : 0);

      expect(ballCount, `at ${elapsed}ms`).toBe(1);
      expect(pinIds(scene), `at ${elapsed}ms`).not.toContain(BALL_BODY_ID);
    }
  });
});

describe('buildLaneScene — the deck', () => {
  it('draws only the positions the server recorded', () => {
    const replay = accepted(fullRackReplay());
    const mid = buildLaneScene(
      { kind: 'deck', tS: 1.0, isTerminal: false },
      replay,
      PATH,
      POST_SCORE_RACK,
    );

    expect(mid.pins.source).toBe('replay');
    expect(pinIds(mid)).toEqual([1, 3, 10]);
    expect(mid.ball).not.toBeNull();
  });

  it('moves the pins as the recorded frames move them', () => {
    const replay = accepted(fullRackReplay());
    const at = (tS: number) =>
      buildLaneScene({ kind: 'deck', tS, isTerminal: false }, replay, PATH, POST_SCORE_RACK);

    const early = at(0);
    const late = at(1.5);
    expect(late.pins).not.toEqual(early.pins);
    // Pin 10 is stationary in this fixture and must stay exactly put --
    // nothing invents motion the server did not record.
    const pinTen = (scene: LaneScene) =>
      scene.pins.source === 'replay' ? scene.pins.bodies.find((p) => p.pinId === 10) : undefined;
    expect(pinTen(late)).toEqual(pinTen(early));
  });

  it('keeps the terminal positions visible for the whole hold', () => {
    const replay = accepted(fullRackReplay());
    const sequence = { replay };
    const end = TRAJECTORY_ANIMATION_DURATION_MS + DECK_DURATION_S * 1000;

    const atTerminal = buildLaneScene(phaseAt(sequence, end), replay, PATH, POST_SCORE_RACK);
    const midHold = buildLaneScene(
      phaseAt(sequence, end + TERMINAL_HOLD_MS / 2),
      replay,
      PATH,
      POST_SCORE_RACK,
    );
    const lateHold = buildLaneScene(
      phaseAt(sequence, end + TERMINAL_HOLD_MS - 1),
      replay,
      PATH,
      POST_SCORE_RACK,
    );

    expect(atTerminal.pins.source).toBe('replay');
    expect(midHold).toEqual(atTerminal);
    expect(lateHold).toEqual(atTerminal);

    // Only after the hold does the server's own rack take over.
    const after = buildLaneScene(
      phaseAt(sequence, end + TERMINAL_HOLD_MS),
      replay,
      PATH,
      POST_SCORE_RACK,
    );
    expect(after.pins).toEqual({ source: 'rack', standingPinIds: POST_SCORE_RACK });
  });
});

describe('buildLaneScene — the static post-score rack', () => {
  it('shows the server rack and the entry marker once settled', () => {
    const replay = accepted(fullRackReplay());
    const scene = buildLaneScene(SETTLED, replay, PATH, POST_SCORE_RACK);

    expect(scene.pins).toEqual({ source: 'rack', standingPinIds: POST_SCORE_RACK });
    expect(scene.ball).toBeNull();
    expect(scene.showEntryMarker).toBe(true);
    expect(scene.pathProgress).toBe(1);
  });

  it('shows no entry marker before any throw has a path', () => {
    const scene = buildLaneScene(SETTLED, null, null, POST_SCORE_RACK);

    expect(scene.pins).toEqual({ source: 'rack', standingPinIds: POST_SCORE_RACK });
    expect(scene.showEntryMarker).toBe(false);
    expect(scene.ball).toBeNull();
  });

  it('invents no pre-throw rack for a rejected or absent replay', () => {
    // The static fallback must stay exactly what it was: the server's rack,
    // no deck phase, no reconstructed pre-throw membership.
    for (const phase of [
      { kind: 'path', progress: 0.5 } as const,
      SETTLED,
    ]) {
      const scene = buildLaneScene(phase, null, PATH, POST_SCORE_RACK);
      expect(scene.pins).toEqual({ source: 'rack', standingPinIds: POST_SCORE_RACK });
    }
  });
});

describe('buildLaneScene — standing_pin_ids is not read until the handoff', () => {
  it('produces identical approach and deck scenes for any post-score rack', () => {
    // The strongest form of the claim: vary the rack wildly and every scene
    // before the terminal-to-static transition must be byte-identical.
    const replay = accepted(fullRackReplay());
    const sequence = { replay };
    const end = TRAJECTORY_ANIMATION_DURATION_MS + DECK_DURATION_S * 1000;
    const racks = [[], [1, 2, 3], [5, 9, 10], [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]];

    for (let elapsed = 0; elapsed < end + TERMINAL_HOLD_MS; elapsed += 50) {
      const phase = phaseAt(sequence, elapsed);
      const scenes = racks.map((rack) => buildLaneScene(phase, replay, PATH, rack));

      for (const scene of scenes) {
        expect(scene, `at ${elapsed}ms`).toEqual(scenes[0]);
      }
    }
  });

  it('does read it immediately after the hold ends', () => {
    const replay = accepted(fullRackReplay());
    const sequence = { replay };
    const after = TRAJECTORY_ANIMATION_DURATION_MS + DECK_DURATION_S * 1000 + TERMINAL_HOLD_MS;
    const phase = phaseAt(sequence, after);

    expect(buildLaneScene(phase, replay, PATH, [1, 2, 3]).pins).toEqual({
      source: 'rack',
      standingPinIds: [1, 2, 3],
    });
    expect(buildLaneScene(phase, replay, PATH, [7]).pins).toEqual({
      source: 'rack',
      standingPinIds: [7],
    });
  });
});

describe('buildLaneScene — second ball and fresh rack', () => {
  it('shows only the pins that were standing for a partial-rack second ball', () => {
    // Scoring may already have racked a fresh ten; the approach must still
    // show the three pins this ball is actually about to hit.
    const replay = accepted(partialRackReplay());
    const freshRack = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
    const approach = buildLaneScene(
      { kind: 'path', progress: 0.5 },
      replay,
      PATH,
      freshRack,
    );

    expect(pinIds(approach)).toEqual([2, 4, 7]);
    expect(pinIds(approach)).toHaveLength(3);
  });

  it('hands back to the fresh ten only after the sequence completes', () => {
    const replay = accepted(partialRackReplay());
    const freshRack = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
    const sequence = { replay };
    const end = TRAJECTORY_ANIMATION_DURATION_MS + DECK_DURATION_S * 1000;

    const duringHold = buildLaneScene(
      phaseAt(sequence, end + TERMINAL_HOLD_MS / 2),
      replay,
      PATH,
      freshRack,
    );
    expect(pinIds(duringHold)).toEqual([2, 4, 7]);

    const afterHold = buildLaneScene(
      phaseAt(sequence, end + TERMINAL_HOLD_MS),
      replay,
      PATH,
      freshRack,
    );
    expect(afterHold.pins).toEqual({ source: 'rack', standingPinIds: freshRack });
  });

  it('handles a strike leaving an empty standing rack', () => {
    const replay = accepted(fullRackReplay());
    const scene = buildLaneScene(SETTLED, replay, PATH, []);

    expect(scene.pins).toEqual({ source: 'rack', standingPinIds: [] });
    expect(scene.showEntryMarker).toBe(true);
  });
});


describe('buildLaneScene — v4 threshold crossings', () => {
  // A crossing changes one thing: the pin's glyph. It keeps its recorded
  // position, keeps moving with the run, and is never removed or animated
  // falling -- the model has no pose to animate.

  const CROSS_STEP = 900;
  const CROSS_T_S = CROSS_STEP * V3_DT_S; // 0.45 s

  function crossingReplay(): CollisionReplayResponse {
    return buildReplay(
      4000,
      [BALL_BODY_ID, 1, 3],
      // Deliberately inverted: pin 1 barely moves and *does* have an event,
      // pin 3 travels far and has none. A scene deriving crossings from
      // displacement would get both backwards.
      (id, tS) => ({
        x_in: id === BALL_BODY_ID ? -3 + tS : id === 1 ? tS * 0.5 : -6 - tS * 8,
        y_in: id === BALL_BODY_ID ? tS * 6 : id === 1 ? tS * 0.5 : tS * 8,
      }),
      [{ pin_id: 1, step_index: CROSS_STEP }],
    );
  }

  const acceptedCrossing = () => {
    const value = acceptReplay(crossingReplay(), [1]);
    if (!value) {
      throw new Error('fixture must be acceptable');
    }
    return value;
  };

  const pin = (scene: LaneScene, pinId: number) =>
    scene.pins.source === 'replay'
      ? scene.pins.bodies.find((b) => b.pinId === pinId)
      : undefined;

  it('draws a pin as standing right up to its crossing', () => {
    const replay = acceptedCrossing();
    const justBefore = buildLaneScene(
      { kind: 'deck', tS: CROSS_T_S - V3_DT_S, isTerminal: false },
      replay,
      PATH,
      POST_SCORE_RACK,
    );

    expect(pin(justBefore, 1)!.thresholdCrossed).toBe(false);
  });

  it('marks it threshold-crossed exactly at its crossing and after', () => {
    const replay = acceptedCrossing();
    const at = buildLaneScene(
      { kind: 'deck', tS: CROSS_T_S, isTerminal: false },
      replay,
      PATH,
      POST_SCORE_RACK,
    );
    const after = buildLaneScene(
      { kind: 'deck', tS: CROSS_T_S + 0.5, isTerminal: false },
      replay,
      PATH,
      POST_SCORE_RACK,
    );

    expect(pin(at, 1)!.thresholdCrossed).toBe(true);
    expect(pin(after, 1)!.thresholdCrossed).toBe(true);
  });

  it('keeps the same recorded body when it crosses', () => {
    // Only the flag changes: the position is still the server's, and the
    // pin is still present.
    const replay = acceptedCrossing();
    const before = pin(
      buildLaneScene(
        { kind: 'deck', tS: CROSS_T_S - V3_DT_S, isTerminal: false },
        replay,
        PATH,
        POST_SCORE_RACK,
      ),
      1,
    )!;
    const at = pin(
      buildLaneScene({ kind: 'deck', tS: CROSS_T_S, isTerminal: false }, replay, PATH, POST_SCORE_RACK),
      1,
    )!;

    expect(at.pinId).toBe(before.pinId);
    expect(at.thresholdCrossed).toBe(true);
    expect(before.thresholdCrossed).toBe(false);
    // Still moving with the run -- not frozen, not removed.
    expect(at.board).not.toBe(before.board);
  });

  it('leaves a pin with no crossing standing for the whole run', () => {
    const replay = acceptedCrossing();
    for (const tS of [0, 0.5, 1.0, 1.5, 2.0]) {
      const scene = buildLaneScene({ kind: 'deck', tS, isTerminal: tS >= 2 }, replay, PATH, POST_SCORE_RACK);
      expect(pin(scene, 3)!.thresholdCrossed, `pin 3 at ${tS}s`).toBe(false);
    }
  });

  it('shows nothing crossed during the approach, which is frame zero', () => {
    const replay = acceptedCrossing();
    for (const progress of [0, 0.5, 1]) {
      const scene = buildLaneScene({ kind: 'path', progress }, replay, PATH, POST_SCORE_RACK);
      expect(pin(scene, 1)!.thresholdCrossed).toBe(false);
      expect(pin(scene, 3)!.thresholdCrossed).toBe(false);
    }
  });

  it('still holds the crossed pin through the terminal hold', () => {
    const replay = acceptedCrossing();
    const sequence = { replay };
    const end = TRAJECTORY_ANIMATION_DURATION_MS + DECK_DURATION_S * 1000;
    const held = buildLaneScene(
      phaseAt(sequence, end + TERMINAL_HOLD_MS / 2),
      replay,
      PATH,
      POST_SCORE_RACK,
    );

    // Present, crossed, and still a replay body -- not handed to the rack yet.
    expect(held.pins.source).toBe('replay');
    expect(pin(held, 1)!.thresholdCrossed).toBe(true);
    expect(pin(held, 3)!.thresholdCrossed).toBe(false);
  });

  it('is driven only by the event, never by displacement', () => {
    // Pin 3 travels much further than pin 1 in this fixture, yet only pin 1
    // has an event -- so a scene that derived crossings from movement would
    // get this exactly backwards.
    const replay = acceptedCrossing();
    const late = buildLaneScene(
      { kind: 'deck', tS: 1.9, isTerminal: false },
      replay,
      PATH,
      POST_SCORE_RACK,
    );
    const one = pin(late, 1)!;
    const three = pin(late, 3)!;

    const moved = (p: typeof one, startBoard: number) => Math.abs(p.board - startBoard);
    const zero = buildLaneScene({ kind: 'deck', tS: 0, isTerminal: false }, replay, PATH, POST_SCORE_RACK);
    expect(moved(three, pin(zero, 3)!.board)).toBeGreaterThan(moved(one, pin(zero, 1)!.board));
    expect(three.thresholdCrossed).toBe(false);
    expect(one.thresholdCrossed).toBe(true);
  });
});
