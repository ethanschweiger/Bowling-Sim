import { describe, expect, it } from 'vitest';
import { createLaneProjection, distanceEase } from './laneProjection';

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
