import { describe, expect, it } from 'vitest';
import type { CollisionReplayResponse, TrajectoryPointResponse } from '../api/types';
import { acceptReplay, BALL_BODY_ID, SUPPORTED_REPLAY_MODEL_VERSION } from './collisionReplay';
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

/** A full-rack replay: ball plus pins 1, 3 and 10, all moving. */
function fullRackReplay(): CollisionReplayResponse {
  const frames = [];
  for (let i = 0; i <= 40; i += 1) {
    frames.push({
      t_s: i * 0.05,
      bodies: [
        { body_id: BALL_BODY_ID, x_in: -3 + i * 0.05, y_in: i * 0.6 },
        { body_id: 1, x_in: 0 + i * 0.2, y_in: 0 + i * 0.4 },
        { body_id: 3, x_in: -6 - i * 0.1, y_in: 10.392 + i * 0.3 },
        { body_id: 10, x_in: -18, y_in: 31.176 },
      ],
    });
  }
  return {
    model_version: SUPPORTED_REPLAY_MODEL_VERSION,
    termination_reason: 'step_cap',
    dt_s: 0.0005,
    sample_every_steps: 100,
    steps_taken: 4000,
    frames,
  };
}

/** A second ball: only the three pins that were still standing. */
function partialRackReplay(): CollisionReplayResponse {
  const frames = [];
  for (let i = 0; i <= 40; i += 1) {
    frames.push({
      t_s: i * 0.05,
      bodies: [
        { body_id: BALL_BODY_ID, x_in: 5 + i * 0.05, y_in: i * 0.6 },
        { body_id: 2, x_in: 6, y_in: 10.392 },
        { body_id: 4, x_in: 12 + i * 0.1, y_in: 20.784 + i * 0.2 },
        { body_id: 7, x_in: 18, y_in: 31.176 },
      ],
    });
  }
  return {
    model_version: SUPPORTED_REPLAY_MODEL_VERSION,
    termination_reason: 'step_cap',
    dt_s: 0.0005,
    sample_every_steps: 100,
    steps_taken: 4000,
    frames,
  };
}

const PATH: readonly TrajectoryPointResponse[] = [
  { distance_ft: 0, board: 28 },
  { distance_ft: 30, board: 22 },
  { distance_ft: 60, board: 17 },
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
