import { describe, expect, it } from 'vitest';
import { LANE_ORIENTATION_DESCRIPTION, LANE_ORIENTATION_LABELS } from './laneOrientation';

describe('lane orientation labels', () => {
  it('states the documented right-handed board convention at the foul line', () => {
    expect(LANE_ORIENTATION_LABELS).toEqual([
      { board: 39, label: 'Left' },
      { board: 20, label: 'Center' },
      { board: 1, label: 'Right' },
    ]);
    expect(LANE_ORIENTATION_DESCRIPTION).toContain('board 39 is left');
    expect(LANE_ORIENTATION_DESCRIPTION).toContain('board 1 is right');
  });
});
