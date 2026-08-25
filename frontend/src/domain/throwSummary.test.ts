import { describe, expect, it } from 'vitest';
import type { GameThrowResponse } from '../api/types';
import { describeLatestThrow } from './throwSummary';

function throwResponse(overrides: Partial<GameThrowResponse>): GameThrowResponse {
  return {
    game_id: 'g1',
    seed: 1,
    actual_release: {
      speed_mph: 17,
      rev_rate: 350,
      axis_rotation: 45,
      axis_tilt: 15,
      launch_angle: 0.5,
      launch_position: 28,
    },
    path: [],
    entry_board: 18.4,
    entry_angle_deg: 3.2,
    speed_at_pins_mph: 14.1,
    pins_knocked: 3,
    pinfall: {
      model_id: 'planar-collision-2d-v1',
      limitations: '',
      fallen_pin_ids: [1, 2, 4],
      replay: null,
    },
    lane_condition_version: 1,
    game_state: {
      standing_pin_ids: [3, 5, 6, 7, 8, 9, 10],
      frames: [],
      total_score: null,
      is_game_complete: false,
      next_frame_number: 1,
      next_ball_number: 2,
    },
    ...overrides,
  };
}

describe('describeLatestThrow', () => {
  it('describes the no-throw-yet state', () => {
    expect(describeLatestThrow(null)).toMatch(/no throw yet/i);
  });

  it('describes a leftward-drifting throw (positive entry angle)', () => {
    const text = describeLatestThrow(throwResponse({ entry_angle_deg: 3.2 }));
    expect(text).toContain('board 18.4');
    expect(text).toContain('left');
    expect(text).toContain('3.2');
    expect(text).toContain('14.1 mph');
    expect(text).toContain('3 pins');
    expect(text).toContain('1, 2, 4');
  });

  it('describes a rightward-drifting throw (negative entry angle)', () => {
    const text = describeLatestThrow(throwResponse({ entry_angle_deg: -2.5 }));
    expect(text).toContain('right');
    expect(text).toContain('2.5');
  });

  it('uses singular "pin" for exactly one pin knocked down', () => {
    const text = describeLatestThrow(
      throwResponse({
        pins_knocked: 1,
        pinfall: { model_id: 'm', limitations: '', fallen_pin_ids: [7], replay: null },
      }),
    );
    expect(text).toContain('1 pin ');
  });
});
