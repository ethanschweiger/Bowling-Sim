import { describe, expect, it } from 'vitest';
import type { GameThrowResponse, ThrowRequest } from '../api/types';
import { describeNextRoll, shotAnalysisRows } from './shotAnalysis';

const request: ThrowRequest = {
  ball_id: 'reactive_pearl',
  speed_mph: 17,
  rev_rate: 350,
  axis_rotation: 45,
  axis_tilt: 15,
  launch_angle: -1.5,
  launch_position: 28,
};

const response: GameThrowResponse = {
  game_id: 'game-1',
  seed: 17,
  actual_release: {
    speed_mph: 16.55,
    rev_rate: 347,
    axis_rotation: 46.6,
    axis_tilt: 14.6,
    launch_angle: -1.45,
    launch_position: 27.38,
  },
  path: [
    { distance_ft: 0, board: 28, elapsed_s: 0 },
    { distance_ft: 60, board: 17.2, elapsed_s: 3 },
  ],
  entry_board: 17.2,
  entry_angle_deg: 1.4,
  speed_at_pins_mph: 15.9,
  pins_knocked: 7,
  pinfall: {
    model_id: 'planar-collision-2d-v1',
    limitations: '',
    fallen_pin_ids: [1, 3, 5, 6, 8, 9, 10],
    replay: null,
  },
  lane_condition_version: 4,
  game_state: {
    oil_pattern: 'house',
    standing_pin_ids: [2, 4, 7],
    frames: [],
    total_score: null,
    is_game_complete: false,
    next_frame_number: 1,
    next_ball_number: 2,
  },
};

describe('shot analysis', () => {
  it('renders the sent request and only values carried by the response', () => {
    const rows = shotAnalysisRows(response, request);

    expect(rows).toEqual([
      { label: 'Replay seed', value: '17' },
      expect.objectContaining({ label: 'Requested release', value: expect.stringContaining('17.0 mph') }),
      expect.objectContaining({ label: 'Actual release', value: expect.stringContaining('16.6 mph') }),
      { label: 'Entry', value: 'Board 17.2, +1.4°, 15.9 mph' },
      { label: 'Lane condition', value: 'Version 4 used for this throw' },
      { label: 'Game status', value: 'Next: frame 1, ball 2' },
    ]);
  });

  it('uses the server-owned next-roll state and handles a complete game', () => {
    expect(describeNextRoll(response.game_state)).toBe('Next: frame 1, ball 2');
    expect(
      describeNextRoll({ ...response.game_state, is_game_complete: true, next_frame_number: null, next_ball_number: null }),
    ).toBe('Game complete');
  });
});
