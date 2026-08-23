/**
 * The one animation loop behind a completed throw's display, owning both
 * phases: the ball travelling the recorded lane path, then the recorded
 * collision at the deck.
 *
 * Framework-free and timer-injectable on purpose. The scheduling rules
 * that actually matter — exactly one loop ever running, cancellation on a
 * new throw/reset/unmount/rapid replay, and scheduling *nothing* under
 * reduced motion — are the easy things to get subtly wrong in a React
 * effect and the hard things to test there. Here they're plain logic over
 * an injected `requestFrame`/`cancelFrame`/`now`, so a test can drive them
 * deterministically without a browser, a real timer, or a sleep.
 *
 * ## Two phases, one clock
 *
 * - **Path** — `TRAJECTORY_ANIMATION_DURATION_MS` of eased progress
 *   through the server's recorded path points. A fixed visual duration,
 *   not real ball-travel time (see `trajectoryAnimation.ts`).
 * - **Deck** — the collision replay, played from its own `t_s` timestamps
 *   at **1x simulation time**: one second of recorded simulation takes one
 *   second on screen. Unlike the path phase this *is* a real timing
 *   relationship, because the replay carries real timestamps.
 *
 * A throw with no playable replay (gutter, heuristic model, an older or unknown
 * version, missing termination reason) simply has no deck phase; the
 * sequence ends settled after the path, exactly as it did before replays
 * existed.
 */

import type { CollisionReplayResponse } from '../api/types';
import { replayDurationS } from './collisionReplay';
import { TRAJECTORY_ANIMATION_DURATION_MS } from './trajectoryAnimation';

const MS_PER_S = 1000;

/**
 * How long the terminal replay scene is held before handing back to the
 * server's static rack, in milliseconds.
 *
 * A **presentation** interval, not a physical one. The solver's last frame
 * often arrives with bodies still moving (`termination_reason: 'step_cap'`),
 * and v3 frames carry no fall-event time, so the moment a pin "goes down"
 * is never something playback can show. What the hold does is keep the last
 * authoritative positions on screen long enough to read before the static
 * standing/fallen rack replaces them — without it, the recorded result and
 * the scored result swap in the same instant and pins appear to vanish
 * rather than to have moved.
 *
 * 400 ms is a deliberate choice on that axis alone: long enough to register
 * as a held frame at any refresh rate, short enough not to feel like the
 * animation has stalled. It asserts nothing about the simulation, and it is
 * cancelled like any other part of the sequence by a new throw, a reset, a
 * replay press, or unmount. Reduced motion never reaches it, since that path
 * schedules no frames at all.
 */
export const TERMINAL_HOLD_MS = 400;

/**
 * Where a sequence is at one instant.
 *
 * `settled` here names *this player's* resting state — full static path,
 * server's own rack, no replay bodies drawn — and nothing about physics.
 * It is unrelated to the replay's `termination_reason: 'settled'`, which
 * describes how the solver's loop exited. The player reaches `settled`
 * after every sequence, including one whose recording ended at the step
 * cap mid-motion.
 */
export type PlaybackPhase =
  | { kind: 'path'; progress: number }
  /** `isTerminal` marks the run's last recorded frame: the solver's
   * terminal snapshot, which is not necessarily a state of rest — the
   * backend stops at a two-second cap whether or not the bodies were still
   * moving (`termination_reason: 'step_cap'`). It is presented as the end
   * of the recording, never as pins having come to rest. The controller
   * paints it and holds it for a frame before handing back to the static
   * rack. */
  | { kind: 'deck'; tS: number; isTerminal: boolean }
  | { kind: 'settled' };

export const SETTLED: PlaybackPhase = { kind: 'settled' };

export interface PlaybackSequence {
  /** Null when this throw has no playable deck phase. */
  replay: CollisionReplayResponse | null;
}

/** Total sequence length in ms — path phase plus, if there is one, the
 * replay's own duration at 1x and the terminal hold that follows it. */
export function sequenceDurationMs(sequence: PlaybackSequence): number {
  const deckMs = sequence.replay
    ? replayDurationS(sequence.replay) * MS_PER_S + TERMINAL_HOLD_MS
    : 0;
  return TRAJECTORY_ANIMATION_DURATION_MS + deckMs;
}

/**
 * The phase at `elapsedMs` into a sequence. Pure: same inputs, same
 * answer, no clock of its own.
 *
 * The boundary is deliberately inclusive-at-the-end for the path phase —
 * at exactly `TRAJECTORY_ANIMATION_DURATION_MS` the ball has just reached
 * the headpin plane, which is also the deck replay's own `t_s = 0`. Both
 * describe the same instant, so the handoff has no gap and no jump.
 */
export function phaseAt(sequence: PlaybackSequence, elapsedMs: number): PlaybackPhase {
  if (elapsedMs < TRAJECTORY_ANIMATION_DURATION_MS) {
    return { kind: 'path', progress: Math.max(0, elapsedMs) / TRAJECTORY_ANIMATION_DURATION_MS };
  }
  if (!sequence.replay) {
    return SETTLED;
  }
  const deckMs = elapsedMs - TRAJECTORY_ANIMATION_DURATION_MS;
  const durationS = replayDurationS(sequence.replay);
  const tS = deckMs / MS_PER_S;
  if (tS < durationS) {
    return { kind: 'deck', tS, isTerminal: false };
  }
  // Past the last recorded timestamp. The terminal frame is *held* for
  // TERMINAL_HOLD_MS rather than handed straight back to the static rack,
  // so the final authoritative positions stay readable instead of being
  // replaced in the instant they appear. Clamping (rather than skipping)
  // also means the exact final frame is what gets shown: real frame times
  // almost never coincide with the duration, so without this the terminal
  // frame would be jumped over on essentially every run.
  const heldMs = deckMs - durationS * MS_PER_S;
  return heldMs < TERMINAL_HOLD_MS
    ? { kind: 'deck', tS: durationS, isTerminal: true }
    : SETTLED;
}

export interface FrameScheduler {
  requestFrame: (callback: (now: number) => void) => number;
  cancelFrame: (handle: number) => void;
  now: () => number;
}

/**
 * Owns at most one in-flight animation at a time.
 *
 * Every entry point (`start`, `settle`, `dispose`) cancels whatever was
 * running first, so a rapid second replay press, a new throw arriving
 * mid-animation, or an unmount can never leave an older loop painting
 * stale bodies over a newer rack. `dispose` additionally latches the
 * controller off, so a frame callback already queued by the browser can't
 * resurrect it.
 */
export class PlaybackController {
  private readonly scheduler: FrameScheduler;
  private readonly onPhase: (phase: PlaybackPhase) => void;
  private handle: number | null = null;
  private disposed = false;

  constructor(scheduler: FrameScheduler, onPhase: (phase: PlaybackPhase) => void) {
    this.scheduler = scheduler;
    this.onPhase = onPhase;
  }

  /** True while a loop is scheduled — the property lifecycle tests assert
   * against, rather than reaching into private state. */
  get isRunning(): boolean {
    return this.handle !== null;
  }

  /**
   * Play `sequence` from the beginning.
   *
   * Under `reducedMotion` this schedules **no** frames at all: it reports
   * the settled state once and returns. That's stronger than starting a
   * loop that finishes immediately — a user who asked for reduced motion
   * gets no animation frames, not a brief one.
   */
  start(sequence: PlaybackSequence, reducedMotion: boolean): void {
    this.cancel();
    if (this.disposed) {
      return;
    }
    if (reducedMotion) {
      this.onPhase(SETTLED);
      return;
    }

    const startedAt = this.scheduler.now();

    // Emitted synchronously, before any frame is requested. Without this the
    // caller would paint once at whatever phase it was already in — for a
    // freshly arrived response, the post-score static rack — and only switch
    // to the approach on the first animation frame. That single stale paint
    // is the post-score flash; staging phase zero up front removes it.
    this.onPhase(phaseAt(sequence, 0));

    let terminalPainted = false;
    const tick = (now: number): void => {
      if (this.disposed) {
        return;
      }
      const elapsed = now - startedAt;
      const phase = phaseAt(sequence, elapsed);

      if (phase.kind === 'deck' && phase.isTerminal) {
        terminalPainted = true;
      }

      if (phase.kind === 'settled' && !terminalPainted && sequence.replay) {
        // Only reachable when one frame gap swallowed the entire terminal
        // hold — a backgrounded tab, a stalled main thread. The last
        // recorded positions still get a paint before the static rack, then
        // exactly one more frame hands over. The hold is a presentation
        // interval, so losing its *duration* here is acceptable; losing the
        // frame itself is not.
        this.onPhase({ kind: 'deck', tS: replayDurationS(sequence.replay), isTerminal: true });
        this.handle = this.scheduler.requestFrame(() => {
          if (this.disposed) {
            return;
          }
          this.onPhase(SETTLED);
          this.handle = null;
        });
        return;
      }

      this.onPhase(phase);
      if (phase.kind === 'settled') {
        this.handle = null;
        return;
      }
      this.handle = this.scheduler.requestFrame(tick);
    };

    this.handle = this.scheduler.requestFrame(tick);
  }

  /** Stop any loop and show the resting state. */
  settle(): void {
    this.cancel();
    if (!this.disposed) {
      this.onPhase(SETTLED);
    }
  }

  /** Stop any loop without reporting a phase — for unmount, where setting
   * state on a gone component is exactly what we're avoiding. */
  dispose(): void {
    this.cancel();
    this.disposed = true;
  }

  private cancel(): void {
    if (this.handle !== null) {
      this.scheduler.cancelFrame(this.handle);
      this.handle = null;
    }
  }
}
