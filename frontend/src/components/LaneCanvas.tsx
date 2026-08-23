import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { GameThrowResponse, ThrowRequest } from '../api/types';
import { createLaneProjection, type LaneDistanceBounds } from '../domain/laneProjection';
import { LANE_ORIENTATION_DESCRIPTION, LANE_ORIENTATION_LABELS } from '../domain/laneOrientation';
import { BOARD_COUNT, LANE_LENGTH_FT, PIN_DECK_LAYOUT } from '../domain/pinDeckLayout';
import { describeStandingPins } from '../domain/scoreDisplay';
import { describeLatestThrow } from '../domain/throwSummary';
import { ShotAnalysis } from './ShotAnalysis';
import {
  acceptReplay,
  replayDistanceExtentFt,
} from '../domain/collisionReplay';
import { buildLaneScene, type LaneScene } from '../domain/laneScene';
import {
  PlaybackController,
  SETTLED,
  type PlaybackPhase,
} from '../domain/playbackController';
import {
  decidePlaybackAction,
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
 * `domain/playbackController.ts`).
 *
 * Which pins to draw, and from where, is decided in one place —
 * `domain/laneScene.ts` — rather than by phase conditionals here. For a
 * throw with a playable replay the pins come from the replay for the
 * *entire* sequence: frame 0 during the approach, the recorded frames
 * during the deck, then the terminal frame held briefly before the
 * server's own `standing_pin_ids` takes over. So the only thing that
 * changes at the path-to-deck boundary is the ball, and the rack never
 * resets, flashes, or swaps identity mid-throw. A throw with no playable
 * replay (gutter, heuristic model, unknown or v1 version) simply has no
 * deck phase, keeps the static rack throughout, and settles straight
 * after the path, inventing no movement. "Replay last shot"
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
  // The same phase, mirrored synchronously. `setPhase` only schedules a
  // re-render, so within the pass where the controller stages phase zero the
  // `phase` *variable* still holds the previous value — and the draw effect
  // below would render one stale scene (the post-score rack over a brand-new
  // response) before the re-render replaced it. Reading the ref instead makes
  // the staged scene the only one drawn. State is still what re-runs the
  // effect on later frames; the ref only supplies its current value.
  const phaseRef = useRef<PlaybackPhase>(SETTLED);
  const publishPhase = useCallback((next: PlaybackPhase) => {
    phaseRef.current = next;
    setPhase(next);
  }, []);
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
      publishPhase,
    );
    controllerRef.current = controller;
    return () => {
      controller.dispose();
      controllerRef.current = null;
    };
    // `publishPhase` has an empty dependency list of its own, so it is
    // stable and this effect still runs exactly once per lifetime.
  }, [publishPhase]);

  // Drives playback from decidePlaybackAction's decision, never on resize
  // (the draw effect below only redraws at whatever phase this one already
  // reached). Comparing against the *previous* snapshot (not just reacting
  // to current props) is what lets "settle" and "start" be told apart from
  // "do nothing" — see decidePlaybackAction's docstring for why a request
  // merely finishing must not auto-replay.
  // `useLayoutEffect`, not `useEffect`, and paired with the controller
  // emitting phase zero synchronously: together they stage the approach
  // *before* the browser paints. With a plain effect a freshly arrived
  // response paints once at the previous settled phase — the post-score
  // rack — and only switches to the approach one frame later, which is the
  // flash this replaces. Every existing guarantee is unchanged: cancelling
  // a previous loop is still the controller's job, a request starting still
  // settles the preceding result, and a stale callback still cannot revive
  // an old scene (the controller latches off on dispose).
  useLayoutEffect(() => {
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
    controller.start({ replay }, prefersReducedMotion());
  }, [latestThrowPath, requestPending, replayCount, replay]);

  // What to draw is decided by `buildLaneScene` — one place, testable
  // without a canvas — so `draw` below renders a scene and makes no phase
  // decisions of its own. Also a layout effect, so the staged scene reaches
  // the canvas in the same pre-paint pass that chose it.
  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || size.width === 0 || size.height === 0) {
      return;
    }
    const scene = buildLaneScene(
      phaseRef.current,
      replay,
      latestThrow?.path ?? null,
      standingPinIds,
    );
    draw(canvas, size, latestThrow, scene, viewportBounds);
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
  latestThrow: GameThrowResponse | null,
  scene: LaneScene,
  viewportBounds: LaneDistanceBounds | undefined,
): void {
  const progress = scene.pathProgress;
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

  // This throw's own recorded path. Only the polyline and the ball marker
  // are decided here; which pins to draw, and from where, is the scene's
  // job — see `domain/laneScene.ts`.
  if (latestThrow && latestThrow.path.length > 0) {
    // The same lowerIndex interpolatePathPosition computes internally —
    // recomputed here (not returned from it) only because drawing the
    // partial line needs the index boundary, not just the interpolated
    // point. path[0..lowerIndex] are fully "behind" the ball; the line
    // extends from there to the interpolated point below, never past it.
    const clampedProgress = Math.max(0, Math.min(1, progress));
    const isComplete = clampedProgress >= 1;
    const lowerIndex = Math.floor(clampedProgress * (latestThrow.path.length - 1));
    const shownPath = isComplete ? latestThrow.path : latestThrow.path.slice(0, lowerIndex + 1);

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
    const lineEnd = interpolatePathPosition(latestThrow.path, clampedProgress);
    ctx.lineTo(projection.boardToX(lineEnd.board), projection.distanceToY(lineEnd.distanceFt));
    ctx.stroke();

    if (scene.showEntryMarker) {
      // The resting stand-in for the ball. The marker is the last point of
      // the server's own polyline, not the separately-rounded `entry_board`
      // field — see `trajectoryEndpoint`. Never drawn alongside `scene.ball`,
      // so no scene shows two balls.
      const endpoint = trajectoryEndpoint(latestThrow.path);
      if (endpoint) {
        ctx.fillStyle = colors.trajectory;
        ctx.beginPath();
        ctx.arc(
          projection.boardToX(endpoint.board),
          projection.distanceToY(endpoint.distance_ft),
          pinRadius * 0.5,
          0,
          Math.PI * 2,
        );
        ctx.fill();
      }
    }
  }

  // The one ball: the trajectory ball during the approach, the replay's own
  // recorded ball during the deck. Swapping between them is the *only*
  // change at the path-to-deck boundary — the pins either side of it are
  // the same bodies at the same coordinates.
  if (scene.ball) {
    const isReplayBall = scene.pins.source === 'replay' && progress >= 1;
    ctx.fillStyle = colors.trajectory;
    ctx.beginPath();
    ctx.arc(
      projection.boardToX(scene.ball.board),
      projection.distanceToY(scene.ball.distanceFt),
      pinRadius * (isReplayBall ? 0.9 : 0.45),
      0,
      Math.PI * 2,
    );
    ctx.fill();
    if (isReplayBall) {
      ctx.strokeStyle = colors.pinOutline;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
  }

  // Pins recorded by the server, at their recorded positions — the rack this
  // throw is actually hitting, held from the approach through the terminal
  // frame. No standing/fallen glyph here: v2 frames carry no fall-event
  // time, and inventing one from displacement would be client-side physics.
  // See `laneScene.FALL_TIMING_LIMITATION`.
  if (scene.pins.source === 'replay') {
    for (const pin of scene.pins.bodies) {
      const x = projection.boardToX(pin.board);
      const y = projection.distanceToY(pin.distanceFt);

      ctx.beginPath();
      ctx.arc(x, y, pinRadius, 0, Math.PI * 2);
      ctx.fillStyle = colors.pinStanding;
      ctx.fill();
      ctx.strokeStyle = colors.pinOutline;
      ctx.lineWidth = 1.5;
      ctx.stroke();

      ctx.fillStyle = colors.pinOutline;
      ctx.font = `${Math.max(8, pinRadius)}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(String(pin.pinId), x, y);
    }
    return;
  }

  // Static rack: filled + labeled if standing, outlined + faded + "×" if
  // fallen, straight from the server's own `standing_pin_ids`.
  const standing = new Set(scene.pins.standingPinIds);
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
