import { describe, expect, it } from 'vitest';
import { createLaneProjection, DEFAULT_MAX_DISTANCE_FT, distanceEase } from './laneProjection';

describe('createLaneProjection', () => {
  const projection = createLaneProjection(400, 600, 10);

  it('puts board 1 (the right gutter) at the right edge', () => {
    expect(projection.boardToX(1)).toBeGreaterThan(projection.boardToX(20));
  });

  it('puts board 39 (the left gutter) at the left edge', () => {
    expect(projection.boardToX(39)).toBeLessThan(projection.boardToX(20));
  });

  it('keeps every in-bounds board within the padded canvas width', () => {
    expect(projection.boardToX(1)).toBeLessThanOrEqual(400 - 10);
    expect(projection.boardToX(39)).toBeGreaterThanOrEqual(10);
  });

  it('puts the foul line (distance 0) at the bottom edge', () => {
    expect(projection.distanceToY(0)).toBeGreaterThan(projection.distanceToY(60));
  });

  it('puts the pin deck (60 ft) nearer the top than the foul line', () => {
    expect(projection.distanceToY(60)).toBeLessThan(projection.distanceToY(0));
  });
});

describe('distanceEase', () => {
  it('pins both endpoints exactly', () => {
    expect(distanceEase(0)).toBe(0);
    expect(distanceEase(1)).toBe(1);
  });

  it('clamps out-of-range input', () => {
    expect(distanceEase(-1)).toBe(0);
    expect(distanceEase(2)).toBe(1);
  });

  it('is strictly increasing', () => {
    let previous = -Infinity;
    for (let i = 0; i <= 200; i += 1) {
      const value = distanceEase(i / 200);
      expect(value).toBeGreaterThan(previous);
      previous = value;
    }
  });

  it('gives the pin-deck end more canvas per foot than the foul-line end', () => {
    const step = 0.001;
    const nearRate = (distanceEase(step) - distanceEase(0)) / step;
    const farRate = (distanceEase(1) - distanceEase(1 - step)) / step;
    expect(farRate).toBeGreaterThan(nearRate);
  });
});

describe('lane projection continuity', () => {
  // The previous two-band mapping handed a fixed share of the canvas to
  // the last five feet, so the scale's *derivative* stepped at 55 ft and
  // a physically smooth path picked up a visible kink there. These guard
  // against reintroducing any such special-cased distance.
  const projection = createLaneProjection(400, 600, 10);

  function sampleSlopes(step: number) {
    const slopes: number[] = [];
    for (let d = 0; d + step <= 62; d += step) {
      slopes.push((projection.distanceToY(d + step) - projection.distanceToY(d)) / step);
    }
    return slopes;
  }

  it('is monotonic in screen position across the whole lane', () => {
    let previous = Infinity; // y decreases as distance grows
    for (let d = 0; d <= 62; d += 0.25) {
      const y = projection.distanceToY(d);
      expect(y).toBeLessThan(previous);
      previous = y;
    }
  });

  it('has no derivative jump anywhere, including the old 55 ft split', () => {
    const step = 0.25;
    const slopes = sampleSlopes(step);
    const changes = slopes.slice(1).map((s, i) => Math.abs(s - slopes[i]));
    const largest = Math.max(...changes);
    const median = [...changes].sort((a, b) => a - b)[Math.floor(changes.length / 2)];

    // A step change in slope would make one sample's change dwarf the
    // rest. With a smooth map the largest is the same order as typical.
    expect(largest).toBeLessThan(median * 5 + 1e-9);
  });

  it('specifically does not single out 55 ft', () => {
    const step = 0.25;
    const slopeAt = (d: number) => (projection.distanceToY(d + step) - projection.distanceToY(d)) / step;
    const before = slopeAt(54);
    const across = slopeAt(55);
    const after = slopeAt(56);
    // Each neighbouring slope differs only gradually; no discontinuity.
    expect(Math.abs(across - before)).toBeLessThan(Math.abs(before) * 0.15);
    expect(Math.abs(after - across)).toBeLessThan(Math.abs(across) * 0.15);
  });
});


describe('replay viewport', () => {
  // Grounded in a real accepted response rather than invented numbers: the
  // seed-17 reactive-pearl throw enters the deck at board 16.96
  // (x_in = -3.191484159185615) and its recorded bodies reach roughly
  // 69.73 ft downlane, well past the default far edge of ~64.6 ft.
  const ENTRY_BOARD = 16.96;
  const ENTRY_DISTANCE_FT = 60;
  const TERMINAL_DISTANCE_FT = 69.73;
  const WIDTH = 400;
  const HEIGHT = 600;
  const PADDING = 12;

  it('reproduces the legacy geometry exactly when no replay extent is given', () => {
    const legacy = createLaneProjection(WIDTH, HEIGHT);
    const explicit = createLaneProjection(WIDTH, HEIGHT, PADDING, DEFAULT_MAX_DISTANCE_FT);
    for (const d of [0, 15, 30, 45, 60, DEFAULT_MAX_DISTANCE_FT]) {
      expect(legacy.distanceToY(d)).toBe(explicit.distanceToY(d));
    }
    for (const b of [1, 10, 20, 30, 39]) {
      expect(legacy.boardToX(b)).toBe(explicit.boardToX(b));
    }
  });

  it('collapsed everything past the default far edge onto one pixel — the defect this fixes', () => {
    const legacy = createLaneProjection(WIDTH, HEIGHT);
    // Documents the old behavior precisely: the terminal position and the
    // far edge were indistinguishable, so recorded motion was drawn at a
    // place it never occupied.
    expect(legacy.distanceToY(TERMINAL_DISTANCE_FT)).toBe(
      legacy.distanceToY(DEFAULT_MAX_DISTANCE_FT),
    );
  });

  it('gives the entry and terminal positions distinct, in-viewport pixels', () => {
    const projection = createLaneProjection(WIDTH, HEIGHT, PADDING, TERMINAL_DISTANCE_FT);
    const entryY = projection.distanceToY(ENTRY_DISTANCE_FT);
    const terminalY = projection.distanceToY(TERMINAL_DISTANCE_FT);

    expect(entryY).not.toBe(terminalY);
    // Downlane is up-screen, so the terminal position sits above the entry.
    expect(terminalY).toBeLessThan(entryY);
    // Both inside the drawable area, not pinned to an edge.
    for (const y of [entryY, terminalY]) {
      expect(y).toBeGreaterThanOrEqual(PADDING);
      expect(y).toBeLessThanOrEqual(HEIGHT - PADDING);
    }
  });

  it('keeps the whole sequence on one scale — no jump between path end and deck', () => {
    const projection = createLaneProjection(WIDTH, HEIGHT, PADDING, TERMINAL_DISTANCE_FT);
    // Distances across the handoff advance monotonically with no
    // discontinuity, so the ball does not visibly teleport at 60 ft.
    let previous = Infinity;
    for (let d = 55; d <= TERMINAL_DISTANCE_FT; d += 0.5) {
      const y = projection.distanceToY(d);
      expect(y).toBeLessThan(previous);
      previous = y;
    }
  });

  it('never clamps a position inside the extent it was sized for', () => {
    const projection = createLaneProjection(WIDTH, HEIGHT, PADDING, TERMINAL_DISTANCE_FT);
    // Every sampled distance up to the extent maps to its own pixel.
    const seen = new Set<number>();
    for (let d = 60; d <= TERMINAL_DISTANCE_FT; d += 0.5) {
      seen.add(projection.distanceToY(d));
    }
    // ~20 distinct samples; any clamping would collapse the tail.
    expect(seen.size).toBeGreaterThan(15);
  });

  it('still puts board 1 right of board 39 (bowler-eye view) under a replay viewport', () => {
    const projection = createLaneProjection(WIDTH, HEIGHT, PADDING, TERMINAL_DISTANCE_FT);
    expect(projection.boardToX(1)).toBeGreaterThan(projection.boardToX(39));
    // And the entry board lands between them, not mirrored to the far side.
    const entryX = projection.boardToX(ENTRY_BOARD);
    expect(entryX).toBeGreaterThan(projection.boardToX(39));
    expect(entryX).toBeLessThan(projection.boardToX(1));
    // Board 16.96 is right of center (board 20) in this view.
    expect(entryX).toBeGreaterThan(projection.boardToX(20));
  });

  it('refuses to shrink below the default lane geometry', () => {
    // A caller passing something smaller must not move the lane itself.
    const shrunk = createLaneProjection(WIDTH, HEIGHT, PADDING, 30);
    const legacy = createLaneProjection(WIDTH, HEIGHT, PADDING);
    expect(shrunk.distanceToY(60)).toBe(legacy.distanceToY(60));
  });
});
