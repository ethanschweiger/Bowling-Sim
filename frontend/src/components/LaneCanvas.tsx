import { useEffect, useRef, useState } from 'react';
import type { GameThrowResponse } from '../api/types';
import { createLaneProjection } from '../domain/laneProjection';
import { BOARD_COUNT, LANE_LENGTH_FT, PIN_DECK_LAYOUT } from '../domain/pinDeckLayout';
import { describeStandingPins } from '../domain/scoreDisplay';
import { describeLatestThrow } from '../domain/throwSummary';
import styles from './LaneCanvas.module.css';

interface LaneCanvasProps {
  standingPinIds: readonly number[];
  latestThrow: GameThrowResponse | null;
}

const PIN_RADIUS_FRACTION = 0.02; // of canvas width

/** A static, post-throw lane diagram: foul line, gutters, the standard pin
 * deck (filled = standing, outlined+faded+"×" = fallen, sourced from
 * `game_state.standing_pin_ids`), and — once a throw has completed — that
 * throw's own path and pin-deck entry point. This is a single rendered
 * frame, not real-time animation: it redraws once whenever the game state
 * or the container's size changes, and does not run its own animation
 * loop. The canvas itself is `aria-hidden`; the two paragraphs beneath it
 * are the real text alternative for what it shows. */
export function LaneCanvas({ standingPinIds, latestThrow }: LaneCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

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

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || size.width === 0 || size.height === 0) {
      return;
    }
    draw(canvas, size, standingPinIds, latestThrow);
  }, [size, standingPinIds, latestThrow]);

  const standingSummary = describeStandingPins(standingPinIds);
  const throwSummary = describeLatestThrow(latestThrow);

  return (
    <div className={styles.wrapper}>
      <div className={styles.canvasFrame} ref={containerRef}>
        {/* Decorative: the paragraphs below are this canvas's real text alternative. */}
        <canvas className={styles.canvas} ref={canvasRef} aria-hidden="true" />
      </div>
      <p className={styles.resultText}>{throwSummary}</p>
      <p className={styles.standingText}>{standingSummary}</p>
    </div>
  );
}

function draw(
  canvas: HTMLCanvasElement,
  cssSize: { width: number; height: number },
  standingPinIds: readonly number[],
  latestThrow: GameThrowResponse | null,
): void {
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

  const projection = createLaneProjection(cssSize.width, cssSize.height);
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
  if (latestThrow && latestThrow.path.length > 0) {
    ctx.strokeStyle = colors.trajectory;
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    latestThrow.path.forEach((point, index) => {
      const x = projection.boardToX(point.board);
      const y = projection.distanceToY(point.distance_ft);
      if (index === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();

    const entryX = projection.boardToX(latestThrow.entry_board);
    const entryY = projection.distanceToY(LANE_LENGTH_FT);
    ctx.fillStyle = colors.trajectory;
    ctx.beginPath();
    ctx.arc(entryX, entryY, pinRadius * 0.5, 0, Math.PI * 2);
    ctx.fill();
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
