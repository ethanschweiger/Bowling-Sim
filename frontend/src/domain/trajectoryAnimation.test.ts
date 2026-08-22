import { describe, expect, it } from 'vitest';
import {
  canReplay,
  decidePlaybackAction,
  easeOutCubic,
  initialAnimationProgress,
  interpolatePathPosition,
  type PlaybackState,
} from './trajectoryAnimation';

const PATH = [
  { distance_ft: 0, board: 28 },
  { distance_ft: 15, board: 27 },
  { distance_ft: 30, board: 24 },
  { distance_ft: 45, board: 19 },
  { distance_ft: 60, board: 18 },
];

const OTHER_PATH = [
  { distance_ft: 0, board: 30 },
  { distance_ft: 30, board: 26 },
  { distance_ft: 60, board: 20 },
];

function state(overrides: Partial<PlaybackState>): PlaybackState {
  return { latestThrowPath: null, isBusy: false, replayCount: 0, ...overrides };
}

describe('interpolatePathPosition', () => {
  it('returns exactly the first point at progress 0', () => {
    expect(interpolatePathPosition(PATH, 0)).toEqual({ board: 28, distanceFt: 0 });
  });

  it('returns exactly the last point at progress 1', () => {
    expect(interpolatePathPosition(PATH, 1)).toEqual({ board: 18, distanceFt: 60 });
  });

  it('lands exactly on an intermediate recorded point when progress aligns with it', () => {
    // 5 points -> 4 segments; progress 0.5 lands exactly on the middle point.
    expect(interpolatePathPosition(PATH, 0.5)).toEqual({ board: 24, distanceFt: 30 });
  });

  it('interpolates within a segment, not just snapping to an endpoint', () => {
    // progress 0.125 = halfway through the first of 4 segments.
    const result = interpolatePathPosition(PATH, 0.125);
    expect(result.distanceFt).toBeCloseTo(7.5);
    expect(result.board).toBeCloseTo(27.5);
  });

  it('clamps out-of-range progress to the nearest endpoint', () => {
    expect(interpolatePathPosition(PATH, -1)).toEqual({ board: 28, distanceFt: 0 });
    expect(interpolatePathPosition(PATH, 2)).toEqual({ board: 18, distanceFt: 60 });
  });

  it('handles an empty path without throwing', () => {
    expect(interpolatePathPosition([], 0.5)).toEqual({ board: 0, distanceFt: 0 });
  });

  it('handles a single-point path as a fixed position at any progress', () => {
    const singlePoint = [{ distance_ft: 12, board: 20 }];
    expect(interpolatePathPosition(singlePoint, 0)).toEqual({ board: 20, distanceFt: 12 });
    expect(interpolatePathPosition(singlePoint, 0.9)).toEqual({ board: 20, distanceFt: 12 });
  });
});

describe('easeOutCubic', () => {
  it('starts at 0', () => {
    expect(easeOutCubic(0)).toBe(0);
  });

  it('ends at 1', () => {
    expect(easeOutCubic(1)).toBe(1);
  });

  it('is monotonically non-decreasing across the range', () => {
    const samples = Array.from({ length: 11 }, (_, i) => easeOutCubic(i / 10));
    for (let i = 1; i < samples.length; i += 1) {
      expect(samples[i]).toBeGreaterThanOrEqual(samples[i - 1]);
    }
  });

  it('clamps out-of-range input', () => {
    expect(easeOutCubic(-1)).toBe(0);
    expect(easeOutCubic(2)).toBe(1);
  });
});

describe('initialAnimationProgress', () => {
  it('starts fully settled (the static trajectory, no autoplay) under reduced motion', () => {
    expect(initialAnimationProgress(true)).toBe(1);
  });

  it('starts at the beginning of the path otherwise', () => {
    expect(initialAnimationProgress(false)).toBe(0);
  });
});

describe('canReplay', () => {
  it('is false with no completed throw', () => {
    expect(canReplay(null, false, false)).toBe(false);
  });

  it('is false when the throw has an empty path', () => {
    expect(canReplay({ path: [] }, false, false)).toBe(false);
  });

  it('is false while a request is in flight', () => {
    expect(canReplay({ path: PATH }, true, false)).toBe(false);
  });

  it('is false while the game is confirmed stale', () => {
    expect(canReplay({ path: PATH }, false, true)).toBe(false);
  });

  it('is true for a completed throw with no request pending and a live game', () => {
    expect(canReplay({ path: PATH }, false, false)).toBe(true);
  });

  it('does not mutate the throw response it inspects (a frozen input stays intact)', () => {
    // If canReplay ever tried to write to its latestThrow argument, this
    // would throw (ES modules run in strict mode) -- a genuine guarantee,
    // not just an equality check, that gating a replay never mutates the
    // game-state object it was handed.
    const frozenThrow = Object.freeze({ path: PATH });
    expect(() => canReplay(frozenThrow, false, false)).not.toThrow();
    expect(canReplay(frozenThrow, false, false)).toBe(true);
  });
});

describe('decidePlaybackAction', () => {
  it('settles immediately the instant a request starts, even with no preceding throw', () => {
    const previous = state({ latestThrowPath: null, isBusy: false });
    const next = state({ latestThrowPath: null, isBusy: true });
    expect(decidePlaybackAction(previous, next)).toEqual({ kind: 'settle' });
  });

  it('settles a still-animating preceding throw the instant a second throw begins', () => {
    const previous = state({ latestThrowPath: PATH, isBusy: false });
    const next = state({ latestThrowPath: PATH, isBusy: true }); // same path -- request just started, not finished
    expect(decidePlaybackAction(previous, next)).toEqual({ kind: 'settle' });
  });

  it('starts exactly one new animation when a successful response brings a new path', () => {
    const previous = state({ latestThrowPath: PATH, isBusy: true });
    const next = state({ latestThrowPath: OTHER_PATH, isBusy: false });
    expect(decidePlaybackAction(previous, next)).toEqual({ kind: 'start', path: OTHER_PATH });
  });

  it('leaves the preceding result settled, with no auto-replay, when a request merely fails', () => {
    // Failure never reassigns the path -- previous and next carry the
    // *same* path reference, only isBusy returns to false.
    const previous = state({ latestThrowPath: PATH, isBusy: true });
    const next = state({ latestThrowPath: PATH, isBusy: false });
    expect(decidePlaybackAction(previous, next)).toEqual({ kind: 'none' });
  });

  it('settles (draws nothing) when a reset/new-game clears the previous throw', () => {
    const previous = state({ latestThrowPath: PATH, isBusy: true });
    const next = state({ latestThrowPath: null, isBusy: false });
    expect(decidePlaybackAction(previous, next)).toEqual({ kind: 'settle' });
  });

  it('starts a fresh animation over the same path when replay is pressed', () => {
    const previous = state({ latestThrowPath: PATH, isBusy: false, replayCount: 0 });
    const next = state({ latestThrowPath: PATH, isBusy: false, replayCount: 1 });
    expect(decidePlaybackAction(previous, next)).toEqual({ kind: 'start', path: PATH });
  });

  it('does nothing when nothing relevant changed', () => {
    const previous = state({ latestThrowPath: PATH, isBusy: false, replayCount: 2 });
    const next = state({ latestThrowPath: PATH, isBusy: false, replayCount: 2 });
    expect(decidePlaybackAction(previous, next)).toEqual({ kind: 'none' });
  });

  it('does nothing on the very first render with no throw and no request', () => {
    const previous = state({});
    const next = state({});
    expect(decidePlaybackAction(previous, next)).toEqual({ kind: 'none' });
  });
});
