import { describe, expect, it } from 'vitest';
import { createLaneProjection } from './laneProjection';

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
