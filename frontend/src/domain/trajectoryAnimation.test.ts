import { describe, expect, it } from 'vitest';
import {
  canReplay,
  decidePlaybackAction,
  easeOutCubic,
  INITIAL_PLAYBACK_STATE,
  planPlaybackTransition,
  initialAnimationProgress,
  interpolatePathPosition,
  trajectoryEndpoint,
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
    expect(canReplay(null, false, false, false)).toBe(false);
  });

  it('is false when the throw has an empty path', () => {
    expect(canReplay({ path: [] }, false, false, false)).toBe(false);
  });

  it('is false while a request is in flight', () => {
    expect(canReplay({ path: PATH }, true, false, false)).toBe(false);
  });

  it('is false while the game is confirmed stale', () => {
    expect(canReplay({ path: PATH }, false, true, false)).toBe(false);
  });

  it('is true for a completed throw with no request pending and a live game', () => {
    expect(canReplay({ path: PATH }, false, false, false)).toBe(true);
  });

  it('does not mutate the throw response it inspects (a frozen input stays intact)', () => {
    // If canReplay ever tried to write to its latestThrow argument, this
    // would throw (ES modules run in strict mode) -- a genuine guarantee,
    // not just an equality check, that gating a replay never mutates the
    // game-state object it was handed.
    const frozenThrow = Object.freeze({ path: PATH });
    expect(() => canReplay(frozenThrow, false, false, false)).not.toThrow();
    expect(canReplay(frozenThrow, false, false, false)).toBe(true);
  });

  // The defect this corrective milestone fixes: once a request finishes
  // (isBusy back to false) a rejected throw would otherwise look
  // replayable again on the *previous*, still-displayed completed throw
  // -- exactly the moment a 503 truncated-trajectory rejection settles.
  // Every other argument here is identical to the "is true" case above;
  // `throwRejected` alone is what must flip the result to false, proving
  // it is load-bearing and not redundant with `isBusy`/`isStale`.
  it('is false once a throw has been rejected, even though the request is no longer in flight', () => {
    expect(canReplay({ path: PATH }, false, false, true)).toBe(false);
  });

  it('is true again once a rejection clears via a genuine successful transition', () => {
    // App.tsx clears `throwRejected` only on a new successful throw, a
    // reset, or a new game -- never on a bare status change or a timer.
    // From canReplay's perspective that transition is just this argument
    // going back to false while the rest of the state is unchanged.
    expect(canReplay({ path: PATH }, false, false, false)).toBe(true);
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

  it('is indifferent to why a throw was rejected — a 503 settles exactly like any other failure', () => {
    // This function never sees an HTTP status: App.tsx's catch block for a
    // rejected throw (404, 409, 503, network failure, ...) never calls
    // setLatestThrow on failure, so `latestThrowPath` here is unchanged no
    // matter which of those it was. The case above already proves the
    // general rule; this one exists so the new 503 contract has its own
    // named regression tying it to the settled-display guarantee, not just
    // an inference from an unrelated failure mode.
    const previous = state({ latestThrowPath: PATH, isBusy: true });
    const next = state({ latestThrowPath: PATH, isBusy: false });
    expect(decidePlaybackAction(previous, next)).toEqual({ kind: 'none' });
    // And a replay stays exactly as disabled/enabled as it already was —
    // this function issues no new animation, so nothing here re-arms replay.
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

describe('trajectoryEndpoint', () => {
  it('returns the path’s own final sample, by reference', () => {
    const endpoint = trajectoryEndpoint(PATH);
    // Reference identity, not deep equality: proves the client hands the
    // canvas the server's own sample rather than a recomputed or
    // transformed copy of it.
    expect(endpoint).toBe(PATH[PATH.length - 1]);
    expect(endpoint).toEqual({ distance_ft: 60, board: 18 });
  });

  it('returns null for an empty path', () => {
    expect(trajectoryEndpoint([])).toBeNull();
  });

  it('returns the single sample of a one-point path', () => {
    const single = [{ distance_ft: 12, board: 20 }];
    expect(trajectoryEndpoint(single)).toBe(single[0]);
  });

  it('does not mutate or reorder the server path it was given', () => {
    const frozen = Object.freeze([...PATH].map((p) => Object.freeze({ ...p })));
    const before = JSON.stringify(frozen);
    expect(() => trajectoryEndpoint(frozen)).not.toThrow();
    expect(JSON.stringify(frozen)).toBe(before);
  });

  it('agrees with a full-progress interpolation of the same path', () => {
    // The animation's settled position and the drawn entry marker must be
    // the same place; otherwise the marker sits off the end of the line.
    const endpoint = trajectoryEndpoint(PATH)!;
    const settled = interpolatePathPosition(PATH, 1);
    expect(settled.board).toBe(endpoint.board);
    expect(settled.distanceFt).toBe(endpoint.distance_ft);
  });
});

describe('planPlaybackTransition', () => {
  // The mount-lifecycle defect this guards, precisely: the decision is a
  // *transition*, so advancing the snapshot consumes it. A canvas that
  // mounts with a completed throw already in hand decides `start` during its
  // layout pass; if its player is created by a passive effect it does not
  // exist yet, and recording the snapshot regardless means every later
  // comparison sees no change and the throw never animates at all.

  const PRELOADED: PlaybackState = { latestThrowPath: PATH, isBusy: false, replayCount: 0 };

  it('would decide start for a canvas mounting with a completed throw', () => {
    expect(decidePlaybackAction(INITIAL_PLAYBACK_STATE, PRELOADED)).toEqual({
      kind: 'start',
      path: PATH,
    });
  });

  it('does not consume that decision when nothing can act on it', () => {
    const { action, snapshot } = planPlaybackTransition(
      INITIAL_PLAYBACK_STATE,
      PRELOADED,
      false,
    );

    // Nothing may be started yet.
    expect(action).toEqual({ kind: 'none' });
    // The critical half: the baseline is untouched, so the decision is still
    // pending rather than spent.
    expect(snapshot).toBe(INITIAL_PLAYBACK_STATE);
  });

  it('still yields exactly one start once something can act', () => {
    // Pass one: no player. Pass two: the identical props, now with a player.
    const first = planPlaybackTransition(INITIAL_PLAYBACK_STATE, PRELOADED, false);
    const second = planPlaybackTransition(first.snapshot, PRELOADED, true);

    expect(second.action).toEqual({ kind: 'start', path: PATH });
    expect(second.snapshot).toBe(PRELOADED);

    // And exactly one: a third pass over the same props starts nothing more,
    // so a retry can never become an auto-replay.
    const third = planPlaybackTransition(second.snapshot, PRELOADED, true);
    expect(third.action).toEqual({ kind: 'none' });
  });

  it('consumes the decision normally when a player is present', () => {
    const { action, snapshot } = planPlaybackTransition(
      INITIAL_PLAYBACK_STATE,
      PRELOADED,
      true,
    );

    expect(action).toEqual({ kind: 'start', path: PATH });
    expect(snapshot).toBe(PRELOADED);
  });

  it('holds back a settle just as it holds back a start', () => {
    const busy: PlaybackState = { latestThrowPath: null, isBusy: true, replayCount: 0 };
    const withoutPlayer = planPlaybackTransition(INITIAL_PLAYBACK_STATE, busy, false);

    expect(withoutPlayer.action).toEqual({ kind: 'none' });
    expect(withoutPlayer.snapshot).toBe(INITIAL_PLAYBACK_STATE);
    expect(planPlaybackTransition(withoutPlayer.snapshot, busy, true).action).toEqual({
      kind: 'settle',
    });
  });

  it('resets cleanly for a remount, the StrictMode setup/cleanup/remount case', () => {
    // StrictMode mounts, cleans up, and mounts again with the same refs. The
    // cleanup restores INITIAL_PLAYBACK_STATE, so the second mount decides
    // from exactly where a first mount would -- without that reset it would
    // compare against its own first-mount snapshot and conclude `none`,
    // leaving a preloaded throw unplayed by a different route.
    const firstMount = planPlaybackTransition(INITIAL_PLAYBACK_STATE, PRELOADED, true);
    expect(firstMount.action).toEqual({ kind: 'start', path: PATH });

    // Without the reset: the stale snapshot swallows the second decision.
    expect(planPlaybackTransition(firstMount.snapshot, PRELOADED, true).action).toEqual({
      kind: 'none',
    });

    // With it: the remount starts the sequence exactly once, as it should.
    const afterCleanup = INITIAL_PLAYBACK_STATE;
    expect(planPlaybackTransition(afterCleanup, PRELOADED, true).action).toEqual({
      kind: 'start',
      path: PATH,
    });
  });

  it('leaves every ordinary decision unchanged when a player is present', () => {
    // planPlaybackTransition must add a gate, not new behaviour: with a
    // player it has to agree with decidePlaybackAction on every input.
    const states: PlaybackState[] = [
      INITIAL_PLAYBACK_STATE,
      PRELOADED,
      { latestThrowPath: PATH, isBusy: true, replayCount: 0 },
      { latestThrowPath: PATH, isBusy: false, replayCount: 1 },
      { latestThrowPath: null, isBusy: false, replayCount: 2 },
    ];

    for (const previous of states) {
      for (const next of states) {
        expect(planPlaybackTransition(previous, next, true).action).toEqual(
          decidePlaybackAction(previous, next),
        );
      }
    }
  });
});
