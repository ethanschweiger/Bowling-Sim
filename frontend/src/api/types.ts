/**
 * Request/response shapes for the FastAPI backend's game API.
 *
 * Mirrors `backend/app/models/schemas.py` field-for-field and name-for-name.
 * Nothing here re-derives a rule the server already computed (score, next
 * roll, strike/spare, rack transitions) — every field is exactly what the
 * response body contains, typed. If the backend contract changes, this file
 * is meant to need the matching edit and nothing else.
 */

export interface ThrowRequest {
  ball_id: string;
  /** Reuse a seed to reproduce a throw's release exactly. Omit for a random one. */
  seed?: number;
  speed_mph: number;
  rev_rate: number;
  axis_rotation: number;
  axis_tilt: number;
  launch_angle: number;
  launch_position: number;
}

export interface ReleaseValues {
  speed_mph: number;
  rev_rate: number;
  axis_rotation: number;
  axis_tilt: number;
  launch_angle: number;
  launch_position: number;
}

export interface TrajectoryPointResponse {
  distance_ft: number;
  board: number;
}

export interface PinfallInfo {
  model_id: string;
  limitations: string;
  fallen_pin_ids: number[];
}

export interface FrameStateResponse {
  number: number;
  rolls: number[];
  is_strike: boolean;
  is_spare: boolean;
  is_complete: boolean;
  /** Cumulative score through this frame; null if a bonus it needs hasn't landed yet. */
  score: number | null;
}

export interface GameStateResponse {
  standing_pin_ids: number[];
  frames: FrameStateResponse[];
  /** Cumulative through the most recently resolved frame; null if nothing resolved yet. */
  total_score: number | null;
  is_game_complete: boolean;
  /** Both null exactly when is_game_complete is true. */
  next_frame_number: number | null;
  next_ball_number: number | null;
}

export interface CreateGameRequest {
  /** Only "house" is selectable this milestone. */
  oil_pattern?: 'house';
}

export interface CreateGameResponse {
  game_id: string;
  lane_condition_version: number;
  game_state: GameStateResponse;
}

export interface GameThrowResponse {
  game_id: string;
  seed: number;
  actual_release: ReleaseValues;
  path: TrajectoryPointResponse[];
  entry_board: number;
  entry_angle_deg: number;
  speed_at_pins_mph: number;
  pins_knocked: number;
  pinfall: PinfallInfo;
  /** The lane-condition version this throw ran against (pre-wear). */
  lane_condition_version: number;
  game_state: GameStateResponse;
}

export interface GameResetResponse {
  game_id: string;
  lane_condition_version: number;
  game_state: GameStateResponse;
}

export interface GameStatusResponse {
  game_id: string;
  /** The game's current version (post-wear from its last throw, if any). */
  lane_condition_version: number;
  game_state: GameStateResponse;
}
