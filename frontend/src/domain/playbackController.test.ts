import { describe, expect, it } from 'vitest';
import type { CollisionReplayResponse } from '../api/types';
import { acceptReplay, SUPPORTED_REPLAY_MODEL_VERSION } from './collisionReplay';
import {
  PlaybackController,
  phaseAt,
  sequenceDurationMs,
  type FrameScheduler,
  type PlaybackPhase,
} from './playbackController';
import { TRAJECTORY_ANIMATION_DURATION_MS } from './trajectoryAnimation';

/**
 * A deterministic stand-in for requestAnimationFrame. Nothing here sleeps
 * or reads a real clock: the test advances time itself and decides when a
 * frame fires, so scheduling behavior is observed exactly rather than
 * raced against.
 */
function fakeScheduler() {
  let handle = 0;
  let currentTime = 0;
  const pending = new Map<number, (now: number) => void>();
  const cancelled: number[] = [];

  const scheduler: FrameScheduler = {
    requestFrame(callback) {
      handle += 1;
      pending.set(handle, callback);
      return handle;
    },
    cancelFrame(h) {
      cancelled.push(h);
      pending.delete(h);
    },
    now: () => currentTime,
  };

  return {
    scheduler,
    cancelled,
    get pendingCount() {
      return pending.size;
    },
    /** Fire every currently-queued frame at `time`. */
    advanceTo(time: number) {
      currentTime = time;
      const due = [...pending.entries()];
      pending.clear();
      for (const [, callback] of due) {
        callback(time);
      }
    },
    setNow(time: number) {
      currentTime = time;
    },
  };
}

const DECK_DURATION_S = 2.0;

function replay(): CollisionReplayResponse {
  // A genuinely complete v2 recording: 4,000 steps x 0.0005 s = the 2 s
  // cap, with all 41 frames on the recorder's own 0.05 s cadence. Built in
  // full rather than sampled down to three frames, so this fixture is one
  // `acceptReplay` would actually accept -- a sparse stand-in would quietly
  // describe data the client is now required to reject.
  const frames = [];
  for (let i = 0; i <= 40; i += 1) {
    frames.push({ t_s: i * 0.05, bodies: [{ body_id: 0, x_in: -3 + i * 0.05, y_in: i * 0.6 }] });
  }
  return {
    model_version: SUPPORTED_REPLAY_MODEL_VERSION,
    // Reaching the cap is what `step_cap` means, and it is the reason
    // every real seeded throw currently reports — so the lifecycle tests
    // below exercise the terminal-frame handoff for a run that ended
    // mid-motion, which is the case that most needs to behave.
    termination_reason: 'step_cap',
    dt_s: 0.0005,
    sample_every_steps: 100,
    steps_taken: 4000,
    frames,
  };
}

function collect() {
  const phases: PlaybackPhase[] = [];
  return { phases, onPhase: (phase: PlaybackPhase) => phases.push(phase) };
}

describe('the shared fixture', () => {
  it('is a replay the validator actually accepts', () => {
    // Keeps these lifecycle tests honest: they exercise data that could
    // really reach the controller, not a shape acceptReplay would reject.
    const r = replay();
    expect(acceptReplay(r)).toBe(r);
    expect(r.frames.length).toBe(41);
  });
});

describe('sequenceDurationMs', () => {
  it('is the path phase alone when there is no replay', () => {
    expect(sequenceDurationMs({ replay: null })).toBe(TRAJECTORY_ANIMATION_DURATION_MS);
  });

  it('adds the replay duration at one-times simulation time', () => {
    expect(sequenceDurationMs({ replay: replay() })).toBe(
      TRAJECTORY_ANIMATION_DURATION_MS + DECK_DURATION_S * 1000,
    );
  });
});

describe('phaseAt', () => {
  const withDeck = { replay: replay() };
  const withoutDeck = { replay: null };

  it('runs the path phase for its full documented duration', () => {
    expect(phaseAt(withDeck, 0)).toEqual({ kind: 'path', progress: 0 });
    expect(phaseAt(withDeck, TRAJECTORY_ANIMATION_DURATION_MS / 2)).toEqual({
      kind: 'path',
      progress: 0.5,
    });
  });

  it('hands off to the deck phase exactly at the headpin plane, with no gap', () => {
    // The last path instant and the deck's own t_s = 0 describe the same
    // moment; the handoff must not skip or repeat time.
    const boundary = phaseAt(withDeck, TRAJECTORY_ANIMATION_DURATION_MS);
    expect(boundary).toEqual({ kind: 'deck', tS: 0, isTerminal: false });
  });

  it('advances deck time at one-times simulation scale', () => {
    const half = phaseAt(withDeck, TRAJECTORY_ANIMATION_DURATION_MS + 1000);
    expect(half).toEqual({ kind: 'deck', tS: 1, isTerminal: false });
  });

  it('resolves to the exact terminal frame at and past the end, never skipping it', () => {
    // The defect this replaces: returning `settled` here meant the last
    // authoritative frame was never rendered at all. Real frame times
    // rarely land exactly on the duration, so past-the-end must clamp back
    // onto the terminal frame rather than jump over it.
    const atEnd = phaseAt(withDeck, TRAJECTORY_ANIMATION_DURATION_MS + DECK_DURATION_S * 1000);
    expect(atEnd).toEqual({ kind: 'deck', tS: DECK_DURATION_S, isTerminal: true });

    const pastEnd = phaseAt(withDeck, TRAJECTORY_ANIMATION_DURATION_MS + DECK_DURATION_S * 1000 + 250);
    expect(pastEnd).toEqual({ kind: 'deck', tS: DECK_DURATION_S, isTerminal: true });
  });

  it('settles straight after the path when there is no replay', () => {
    expect(phaseAt(withoutDeck, TRAJECTORY_ANIMATION_DURATION_MS)).toEqual({ kind: 'settled' });
  });
});

describe('PlaybackController lifecycle', () => {
  it('runs exactly one loop that carries both phases through the terminal frame to settled', () => {
    const fake = fakeScheduler();
    const { phases, onPhase } = collect();
    const controller = new PlaybackController(fake.scheduler, onPhase);
    const END_MS = TRAJECTORY_ANIMATION_DURATION_MS + DECK_DURATION_S * 1000;

    controller.start({ replay: replay() }, false);
    expect(fake.pendingCount).toBe(1);

    fake.advanceTo(TRAJECTORY_ANIMATION_DURATION_MS / 2);
    expect(fake.pendingCount).toBe(1); // still exactly one, never two

    fake.advanceTo(TRAJECTORY_ANIMATION_DURATION_MS + 500);
    expect(fake.pendingCount).toBe(1);

    // Reaching the end emits the terminal frame and keeps exactly one
    // queued frame -- the handoff -- rather than settling in the same tick.
    fake.advanceTo(END_MS);
    expect(fake.pendingCount).toBe(1);
    expect(controller.isRunning).toBe(true);
    const terminal = phases[phases.length - 1];
    expect(terminal).toEqual({ kind: 'deck', tS: DECK_DURATION_S, isTerminal: true });

    // Only the following frame hands back to the static rack.
    fake.advanceTo(END_MS + 16);
    expect(fake.pendingCount).toBe(0);
    expect(controller.isRunning).toBe(false);

    expect(phases.map((p) => p.kind)).toEqual(['path', 'deck', 'deck', 'settled']);
  });

  it('holds the exact final authoritative frame for a paint before the static rack', () => {
    // The defect this guards: skipping straight from a pre-terminal frame
    // to the static rack meant the server's last recorded positions were
    // never visible at all.
    const fake = fakeScheduler();
    const { phases, onPhase } = collect();
    const controller = new PlaybackController(fake.scheduler, onPhase);
    const END_MS = TRAJECTORY_ANIMATION_DURATION_MS + DECK_DURATION_S * 1000;

    controller.start({ replay: replay() }, false);
    fake.advanceTo(END_MS + 5000); // land well past the end

    const last = phases[phases.length - 1];
    expect(last).toEqual({ kind: 'deck', tS: DECK_DURATION_S, isTerminal: true });
    expect(phases.some((p) => p.kind === 'settled')).toBe(false);

    // It is still the terminal frame on screen until one more frame runs.
    fake.advanceTo(END_MS + 5016);
    expect(phases[phases.length - 1]).toEqual({ kind: 'settled' });
  });

  it('cancels cleanly if interrupted while holding the terminal frame', () => {
    const fake = fakeScheduler();
    const { phases, onPhase } = collect();
    const controller = new PlaybackController(fake.scheduler, onPhase);
    const END_MS = TRAJECTORY_ANIMATION_DURATION_MS + DECK_DURATION_S * 1000;

    controller.start({ replay: replay() }, false);
    fake.advanceTo(END_MS);
    expect(phases[phases.length - 1]).toMatchObject({ isTerminal: true });

    controller.settle();
    expect(fake.pendingCount).toBe(0);
    expect(controller.isRunning).toBe(false);
    expect(phases[phases.length - 1]).toEqual({ kind: 'settled' });
  });

  it('schedules no frames at all under reduced motion', () => {
    const fake = fakeScheduler();
    const { phases, onPhase } = collect();
    const controller = new PlaybackController(fake.scheduler, onPhase);

    controller.start({ replay: replay() }, true);

    expect(fake.pendingCount).toBe(0);
    expect(controller.isRunning).toBe(false);
    expect(phases).toEqual([{ kind: 'settled' }]);
  });

  it('cancels the previous loop when a rapid second replay starts', () => {
    const fake = fakeScheduler();
    const { onPhase } = collect();
    const controller = new PlaybackController(fake.scheduler, onPhase);

    controller.start({ replay: replay() }, false);
    fake.advanceTo(100);
    expect(fake.pendingCount).toBe(1);

    controller.start({ replay: replay() }, false);
    // Still exactly one queued frame -- the old one was cancelled, not
    // left running alongside the new one.
    expect(fake.pendingCount).toBe(1);
    expect(fake.cancelled.length).toBe(1);
  });

  it('cancels and settles on a new throw, reset, or new game', () => {
    const fake = fakeScheduler();
    const { phases, onPhase } = collect();
    const controller = new PlaybackController(fake.scheduler, onPhase);

    controller.start({ replay: replay() }, false);
    fake.advanceTo(200);
    controller.settle();

    expect(fake.pendingCount).toBe(0);
    expect(controller.isRunning).toBe(false);
    expect(phases[phases.length - 1]).toEqual({ kind: 'settled' });
  });

  it('settles immediately when interrupted mid-deck, leaving no pin frames over a newer rack', () => {
    // The dangerous moment: a new throw or reset lands while pins are
    // still moving. Those bodies belong to the *previous* rack, so they
    // must stop being drawn at once rather than finishing their arc over
    // whatever the server racked next.
    const fake = fakeScheduler();
    const { phases, onPhase } = collect();
    const controller = new PlaybackController(fake.scheduler, onPhase);

    controller.start({ replay: replay() }, false);
    fake.advanceTo(TRAJECTORY_ANIMATION_DURATION_MS + 500);
    expect(phases[phases.length - 1].kind).toBe('deck');

    controller.settle();

    expect(fake.pendingCount).toBe(0);
    expect(controller.isRunning).toBe(false);
    expect(phases[phases.length - 1]).toEqual({ kind: 'settled' });
  });

  it('cannot resume a stale sequence after being settled by a failed throw', () => {
    // A 503 settles the display (isBusy false->true settles; the failure
    // itself is then `none`). Nothing re-enters the loop on its own, so
    // the previous throw's collision data is never re-animated without a
    // deliberate new start.
    const fake = fakeScheduler();
    const { phases, onPhase } = collect();
    const controller = new PlaybackController(fake.scheduler, onPhase);

    controller.start({ replay: replay() }, false);
    fake.advanceTo(TRAJECTORY_ANIMATION_DURATION_MS + 500);
    controller.settle();
    phases.length = 0;

    // Time keeps passing; no frames are queued, so nothing draws.
    fake.advanceTo(TRAJECTORY_ANIMATION_DURATION_MS + 1500);
    expect(fake.pendingCount).toBe(0);
    expect(phases).toEqual([]);
  });

  it('stops on dispose and reports nothing further, even if a frame was already queued', () => {
    const fake = fakeScheduler();
    const { phases, onPhase } = collect();
    const controller = new PlaybackController(fake.scheduler, onPhase);

    controller.start({ replay: replay() }, false);
    fake.advanceTo(100);
    const beforeDispose = phases.length;

    controller.dispose();
    expect(fake.pendingCount).toBe(0);

    // A start after dispose must also stay inert -- the unmounted
    // component can never be driven again.
    controller.start({ replay: replay() }, false);
    expect(fake.pendingCount).toBe(0);
    expect(phases.length).toBe(beforeDispose);
  });

  it('settle after dispose reports nothing (no state set on a gone component)', () => {
    const fake = fakeScheduler();
    const { phases, onPhase } = collect();
    const controller = new PlaybackController(fake.scheduler, onPhase);

    controller.dispose();
    controller.settle();
    expect(phases).toEqual([]);
  });

  it('plays a throw with no replay as a path phase that settles at the boundary', () => {
    const fake = fakeScheduler();
    const { phases, onPhase } = collect();
    const controller = new PlaybackController(fake.scheduler, onPhase);

    // The gutter / heuristic / unknown-version case: no deck phase exists,
    // so nothing invents ball or pin movement past the path.
    controller.start({ replay: null }, false);
    fake.advanceTo(TRAJECTORY_ANIMATION_DURATION_MS / 2);
    fake.advanceTo(TRAJECTORY_ANIMATION_DURATION_MS);

    expect(phases.map((p) => p.kind)).toEqual(['path', 'settled']);
    expect(fake.pendingCount).toBe(0);
  });

  it.each([
    ['a v1 payload', { model_version: 'planar-collision-replay-2d-v1' }],
    ['an unknown termination reason', { termination_reason: 'timeout' }],
    ['no termination reason at all', { termination_reason: undefined }],
  ])('runs no deck loop for %s', (_label, override) => {
    // End to end through the firewall rather than passing `null` directly:
    // this is what actually happens to a payload the client cannot vouch
    // for, and the point is that it costs no animation frames.
    const rejected = acceptReplay({ ...replay(), ...override } as CollisionReplayResponse);
    expect(rejected).toBeNull();

    const fake = fakeScheduler();
    const { phases, onPhase } = collect();
    const controller = new PlaybackController(fake.scheduler, onPhase);

    controller.start({ replay: rejected }, false);
    fake.advanceTo(TRAJECTORY_ANIMATION_DURATION_MS / 2);
    fake.advanceTo(TRAJECTORY_ANIMATION_DURATION_MS);
    // Well past where a deck phase would have run, had one been started.
    fake.advanceTo(TRAJECTORY_ANIMATION_DURATION_MS + DECK_DURATION_S * 1000 + 100);

    expect(phases.map((p) => p.kind)).toEqual(['path', 'settled']);
    expect(phases.some((p) => p.kind === 'deck')).toBe(false);
    expect(fake.pendingCount).toBe(0);
  });

  it('replays the complete sequence again without any new data', () => {
    const fake = fakeScheduler();
    const { phases, onPhase } = collect();
    const controller = new PlaybackController(fake.scheduler, onPhase);
    const sequence = { replay: replay() };

    const playOnce = (base: number) => {
      fake.setNow(base);
      controller.start(sequence, false);
      fake.advanceTo(base + 10);
      fake.advanceTo(base + TRAJECTORY_ANIMATION_DURATION_MS + 10);
      fake.advanceTo(base + TRAJECTORY_ANIMATION_DURATION_MS + DECK_DURATION_S * 1000);
      fake.advanceTo(base + TRAJECTORY_ANIMATION_DURATION_MS + DECK_DURATION_S * 1000 + 16);
    };

    playOnce(0);
    const afterFirst = phases.map((p) => p.kind);
    phases.length = 0;
    playOnce(10_000);

    // Path, an intermediate deck frame, the terminal deck frame, then the
    // static rack -- identically both times, with no new data fetched.
    expect(afterFirst).toEqual(['path', 'deck', 'deck', 'settled']);
    expect(phases.map((p) => p.kind)).toEqual(['path', 'deck', 'deck', 'settled']);
  });
});
