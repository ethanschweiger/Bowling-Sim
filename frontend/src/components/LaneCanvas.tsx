import { useEffect, useMemo, useRef, useState } from 'react';
import type { GameThrowResponse, ThrowRequest } from '../api/types';
import { createLaneProjection, type LaneDistanceBounds } from '../domain/laneProjection';
import { LANE_ORIENTATION_DESCRIPTION, LANE_ORIENTATION_LABELS } from '../domain/laneOrientation';
import { BOARD_COUNT, LANE_LENGTH_FT, PIN_DECK_LAYOUT } from '../domain/pinDeckLayout';
import { describeStandingPins } from '../domain/scoreDisplay';
import { describeLatestThrow } from '../domain/throwSummary';
import { ShotAnalysis } from './ShotAnalysis';
import {
  acceptReplay,
  BALL_BODY_ID,
  replayDistanceExtentFt,
  replayPositionsAt,
  type ReplayLanePosition,
} from '../domain/collisionReplay';
import {
  PlaybackController,
  SETTLED,
  type PlaybackPhase,
} from '../domain/playbackController';
import {
  decidePlaybackAction,
  easeOutCubic,
  interpolatePathPosition,
  trajectoryEndpoint,
  type PlaybackState,
} from '../domain/trajectoryAnimation';
import styles from './LaneCanvas.module.css';

interface LaneCanvasProps {
  standingPinIds: readonly number[];
  latestThrow: GameThrowResponse | null;
  latestRequestedRelease: ThrowRequest | null;
  /** Whether "Replay last shot" should be enabled — see
   * `domain/trajectoryAnimation.ts`'s `canReplay`. Computed by the parent
   * (it depends on request-in-flight and stale-game state this component
   * doesn't otherwise know about); replaying itself is entirely local to
   * this component and never calls the API or touches game state. */
  replayEnabled: boolean;
  /** Whether a throw/reset/new-game request is currently in flight.
   * Starting a new one must settle any in-flight animation of the
   * *preceding* result immediately — see `decidePlaybackAction`. */
  requestPending: boolean;
}

const PIN_RADIUS_FRACTION = 0.02; // of canvas width

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false;
}

/** A lane diagram: foul line, gutters, the standard pin deck (filled =
 * standing, outlined+faded+"×" = fallen, sourced from
 * `game_state.standing_pin_ids`), and — once a throw has completed —
 * that throw's own path and pin-deck entry point. A new completed throw
 * plays a one-time two-phase sequence: the ball advancing along the
 * server's *exact* recorded `path` (interpolated between those exact
 * points, see `domain/trajectoryAnimation.ts` — never a recalculated path
 * of its own), then, if the server sent a playable collision replay, the
 * recorded pin-deck collision played from its own timestamps at 1x
 * simulation time (see `domain/collisionReplay.ts` and
 * `domain/playbackController.ts`). During that deck phase the bodies
 * drawn are the server's recorded ones — the pre-impact rack in motion,
 * including the ball — after which it settles to the response's own
 * `standing_pin_ids`. A throw with no playable replay (gutter, heuristic
 * model, unknown replay version) simply has no deck phase and settles
 * straight after the path, inventing no movement. "Replay last shot"
 * restarts the whole sequence from the same stored response; it makes no
 * request and changes no game state.
 * Submitting a new throw/reset (`requestPending` turning true) settles
 * any still-playing animation of the *preceding* result immediately,
 * before the new request's own outcome is known — an ordinary failure
 * afterward simply leaves that settled result in place, never
 * auto-replaying it (see `decidePlaybackAction`). Under
 * `prefers-reduced-motion`, throws (and replays) skip straight to the
 * settled static state — no autoplay. The canvas itself is
 * `aria-hidden`; the paragraphs beneath it are the real text
 * alternative, and are never re-announced per animation frame. */
export function LaneCanvas({
  standingPinIds,
  latestThrow,
  latestRequestedRelease,
  replayEnabled,
  requestPending,
}: LaneCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [phase, setPhase] = useState<PlaybackPhase>(SETTLED);
  const [replayCount, setReplayCount] = useState(0);
  const controllerRef = useRef<PlaybackController | null>(null);
  const latestThrowPath = latestThrow && latestThrow.path.length > 0 ? latestThrow.path : null;
  const previousPlaybackStateRef = useRef<PlaybackState>({ latestThrowPath: null, isBusy: false, replayCount: 0 });

  // Validated once per response, not per frame: an unknown version or a
  // malformed payload becomes `null` here and the deck phase simply
  // doesn't exist for this throw. Never throws — see `acceptReplay`.
  const replay = useMemo(() => acceptReplay(latestThrow?.pinfall?.replay), [latestThrow]);

  // One viewport for this throw's entire sequence, spanning both ends of
  // the accepted replay's recorded extent. Held for the whole sequence —
  // path, initial rack, intermediate frames, terminal frame, and the
  // static rack afterwards — so nothing rescales mid-playback and no
  // recorded position gets pinned to either edge at a distance it doesn't
  // actually hold. Undefined (the default lane geometry) whenever there's
  // no playable replay, so ordinary static drawing is pixel-identical to
  // what it has always been.
  const viewportBounds = useMemo(() => {
    if (!replay) {
      return undefined;
    }
    const extent = replayDistanceExtentFt(replay);
    return { minDistanceFt: extent.minFt, maxDistanceFt: extent.maxFt };
  }, [replay]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) {
        return;
      }
      const { width, height } = entry.contentRect;
      setSize({ width, height });
    });
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  // One controller for this component's whole lifetime, owning at most one
  // in-flight loop across *both* phases. Disposed on unmount so a frame
  // the browser already queued can't set state on a gone component.
  useEffect(() => {
    const controller = new PlaybackController(
      {
        requestFrame: (callback) => requestAnimationFrame(callback),
        cancelFrame: (handle) => cancelAnimationFrame(handle),
        now: () => performance.now(),
      },
      setPhase,
    );
    controllerRef.current = controller;
    return () => {
      controller.dispose();
      controllerRef.current = null;
    };
  }, []);

  // Drives playback from decidePlaybackAction's decision, never on resize
  // (the draw effect below only redraws at whatever phase this one already
  // reached). Comparing against the *previous* snapshot (not just reacting
  // to current props) is what lets "settle" and "start" be told apart from
  // "do nothing" — see decidePlaybackAction's docstring for why a request
  // merely finishing must not auto-replay.
  useEffect(() => {
    const nextState: PlaybackState = { latestThrowPath, isBusy: requestPending, replayCount };
    const action = decidePlaybackAction(previousPlaybackStateRef.current, nextState);
    previousPlaybackStateRef.current = nextState;

    const controller = controllerRef.current;
    if (action.kind === 'none' || !controller) {
      return;
    }
    if (action.kind === 'settle') {
      controller.settle();
      return;
    }
    // action.kind === 'start' — the path itself isn't needed here; the
    // draw effect below reads it straight from latestThrow, driven by the
    // phase this loop advances. Cancelling any previous loop is the
    // controller's own job, so a rapid second replay can't double up.
    controller.start({ replay }, prefersReducedMotion());
  }, [latestThrowPath, requestPending, replayCount, replay]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || size.width === 0 || size.height === 0) {
      return;
    }
    // Deck bodies are computed here, not inside `draw`, so the drawing
    // function stays a pure function of already-resolved positions.
    const deckBodies =
      phase.kind === 'deck' && replay ? replayPositionsAt(replay, phase.tS) : null;
    draw(canvas, size, standingPinIds, latestThrow, phase, deckBodies, viewportBounds);
  }, [size, standingPinIds, latestThrow, phase, replay, viewportBounds]);

  const standingSummary = describeStandingPins(standingPinIds);
  const throwSummary = describeLatestThrow(latestThrow);

  return (
    <div className={styles.wrapper}>
      <div className={styles.canvasFrame} ref={containerRef}>
        {/* Decorative: the paragraphs below are this canvas's real text alternative. */}
        <canvas className={styles.canvas} ref={canvasRef} aria-hidden="true" />
      </div>
      <div className={styles.orientation} role="note" aria-label={LANE_ORIENTATION_DESCRIPTION}>
        <span>Foul line</span>
        {LANE_ORIENTATION_LABELS.map(({ board, label }) => (
          <span key={board}>{board}: {label}</span>
        ))}
      </div>
      <p className={styles.resultText} aria-live="polite">{throwSummary}</p>
      <p className={styles.standingText}>{standingSummary}</p>
      <ShotAnalysis latestThrow={latestThrow} requestedRelease={latestRequestedRelease} />
      <button
        type="button"
        className={styles.replayButton}
        onClick={() => setReplayCount((count) => count + 1)}
        disabled={!replayEnabled}
      >
        Replay last shot
      </button>
    </div>
  );
}

function draw(
  canvas: HTMLCanvasElement,
  cssSize: { width: number; height: number },
  standingPinIds: readonly number[],
  latestThrow: GameThrowResponse | null,
  phase: PlaybackPhase,
  deckBodies: readonly ReplayLanePosition[] | null,
  viewportBounds: LaneDistanceBounds | undefined,
): void {
  // The path phase's own progress; the deck phase and the settled state
  // both show the complete static path behind the action.
  const progress = phase.kind === 'path' ? easeOutCubic(phase.progress) : 1;
  const ctx = canvas.getContext('2d');
  if (!ctx) {
    return;
  }

  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(cssSize.width * dpr);
  canvas.height = Math.round(cssSize.height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssSize.width, cssSize.height);

  const style = getComputedStyle(canvas);
  const colors = {
    lane: style.getPropertyValue('--color-lane').trim(),
    gutter: style.getPropertyValue('--color-gutter').trim(),
    line: style.getPropertyValue('--color-lane-line').trim(),
    pinStanding: style.getPropertyValue('--color-pin-standing').trim(),
    pinFallen: style.getPropertyValue('--color-pin-fallen').trim(),
    pinOutline: style.getPropertyValue('--color-pin-outline').trim(),
    trajectory: style.getPropertyValue('--color-trajectory').trim(),
  };

  const projection = createLaneProjection(cssSize.width, cssSize.height, undefined, viewportBounds);
  const pinRadius = Math.max(5, cssSize.width * PIN_RADIUS_FRACTION);

  // Lane surface (boards 1..39) and gutters (either side) as two rects.
  const laneLeftX = projection.boardToX(BOARD_COUNT);
  const laneRightX = projection.boardToX(1);
  const foulLineY = projection.distanceToY(0);
  const topY = projection.distanceToY(LANE_LENGTH_FT + 10); // a hair past the pin deck's own extent

  ctx.fillStyle = colors.gutter;
  ctx.fillRect(0, 0, cssSize.width, cssSize.height);

  ctx.fillStyle = colors.lane;
  ctx.fillRect(laneLeftX, topY, laneRightX - laneLeftX, foulLineY - topY);

  // Centerline (board 20) and foul line, both faint reference marks.
  ctx.strokeStyle = colors.line;
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 4]);
  const centerX = projection.boardToX((BOARD_COUNT + 1) / 2);
  ctx.beginPath();
  ctx.moveTo(centerX, foulLineY);
  ctx.lineTo(centerX, topY);
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(laneLeftX, foulLineY);
  ctx.lineTo(laneRightX, foulLineY);
  ctx.stroke();

  // This throw's own path and pin-deck entry point, if one has completed.
  // Pins are always drawn in their final, already-server-confirmed state
  // (see standingPinIds below) even mid-animation: reconstructing what the
  // rack looked like *before* this throw would mean re-deriving the same
  // fresh-rack-on-frame-completion rule this project deliberately keeps
  // server-side (see scoreDisplay.ts's module docstring for the same
  // principle applied to scoresheet glyphs). Only the ball's own motion
  // along the already-recorded path animates.
  if (latestThrow && latestThrow.path.length > 0) {
    const isSettled = progress >= 1;
    // The same lowerIndex interpolatePathPosition computes internally —
    // recomputed here (not returned from it) only because drawing the
    // partial line needs the index boundary, not just the interpolated
    // point. path[0..lowerIndex] are fully "behind" the ball; the line
    // extends from there to the interpolated point below, never past it.
    const clampedProgress = Math.max(0, Math.min(1, progress));
    const lowerIndex = Math.floor(clampedProgress * (latestThrow.path.length - 1));
    const shownPath = isSettled ? latestThrow.path : latestThrow.path.slice(0, lowerIndex + 1);

    ctx.strokeStyle = colors.trajectory;
    ctx.lineWidth = 2.5;
    // The route remains the server's exact polyline. Rounded joins/caps only
    // remove the mechanical-looking corners between its dense samples; they
    // do not interpolate, bend, or otherwise replace any physics point.
    ctx.lineJoin = 'round';
    ctx.lineCap = 'round';
    ctx.beginPath();
    shownPath.forEach((point, index) => {
      const x = projection.boardToX(point.board);
      const y = projection.distanceToY(point.distance_ft);
      if (index === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    });

    const ballPosition = interpolatePathPosition(latestThrow.path, progress);
    const ballX = projection.boardToX(ballPosition.board);
    const ballY = projection.distanceToY(ballPosition.distanceFt);
    ctx.lineTo(ballX, ballY);
    ctx.stroke();

    if (isSettled) {
      // During the deck phase the ball is one of the replay bodies below,
      // drawn at its own recorded position — so the static entry marker
      // would be a second, stale ball sitting on the boundary.
      if (!deckBodies) {
        // The marker is the last point of the server's own polyline, not the
        // separately-rounded `entry_board` field — see `trajectoryEndpoint`.
        const endpoint = trajectoryEndpoint(latestThrow.path);
        if (endpoint) {
          const entryX = projection.boardToX(endpoint.board);
          const entryY = projection.distanceToY(endpoint.distance_ft);
          ctx.fillStyle = colors.trajectory;
          ctx.beginPath();
          ctx.arc(entryX, entryY, pinRadius * 0.5, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    } else {
      // The moving ball marker itself, mid-flight only — at rest, the
      // entry-point dot above stands in for it.
      ctx.fillStyle = colors.trajectory;
      ctx.beginPath();
      ctx.arc(ballX, ballY, pinRadius * 0.45, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // Deck phase: draw exactly the bodies the server recorded, at their
  // recorded positions — the pre-impact rack in motion, including the ball
  // itself. Deliberately *instead of* the static post-score rack: that
  // rack is the state after this throw resolved (and may already be a
  // fresh one, if scoring completed the frame), so showing it here would
  // contradict the collision being played. Nothing is inferred about
  // pinsetter behavior; when the phase ends the draw below resumes and
  // settles to the response's own standing_pin_ids.
  if (deckBodies) {
    for (const body of deckBodies) {
      const x = projection.boardToX(body.board);
      const y = projection.distanceToY(body.distanceFt);
      const isBall = body.bodyId === BALL_BODY_ID;

      ctx.beginPath();
      ctx.arc(x, y, isBall ? pinRadius * 0.9 : pinRadius, 0, Math.PI * 2);
      ctx.fillStyle = isBall ? colors.trajectory : colors.pinStanding;
      ctx.fill();
      ctx.strokeStyle = colors.pinOutline;
      ctx.lineWidth = 1.5;
      ctx.stroke();

      if (!isBall) {
        ctx.fillStyle = colors.pinOutline;
        ctx.font = `${Math.max(8, pinRadius)}px sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(String(body.bodyId), x, y);
      }
    }
    return;
  }

  // Pin deck: filled + labeled if standing, outlined + faded + "×" if fallen.
  const standing = new Set(standingPinIds);
  for (const pin of PIN_DECK_LAYOUT) {
    const x = projection.boardToX(pin.board);
    const y = projection.distanceToY(pin.distanceFt);
    const isStanding = standing.has(pin.id);

    ctx.beginPath();
    ctx.arc(x, y, pinRadius, 0, Math.PI * 2);
    if (isStanding) {
      ctx.fillStyle = colors.pinStanding;
      ctx.fill();
      ctx.strokeStyle = colors.pinOutline;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.fillStyle = colors.pinOutline;
      ctx.font = `${Math.max(8, pinRadius)}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(String(pin.id), x, y);
    } else {
      ctx.globalAlpha = 0.5;
      ctx.strokeStyle = colors.pinFallen;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.beginPath();
      const offset = pinRadius * 0.5;
      ctx.moveTo(x - offset, y - offset);
      ctx.lineTo(x + offset, y + offset);
      ctx.moveTo(x + offset, y - offset);
      ctx.lineTo(x - offset, y + offset);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
  }
}
