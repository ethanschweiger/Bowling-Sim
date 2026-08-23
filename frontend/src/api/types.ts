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

/** One body's position at one instant of a collision replay.
 *
 * Coordinates are inches in the backend's own pin-deck frame (see
 * `backend/app/physics/replay.py`): `x_in` lateral from lane center,
 * positive toward higher board numbers; `y_in` downlane from the headpin
 * plane, which is y=0. `domain/collisionReplay.ts` is the one place these
 * convert to the canvas's board/distance coordinates. */
export interface ReplayBodyResponse {
  /** 0 is the ball; 1-10 are the pins that were actually standing. */
  body_id: number;
  x_in: number;
  y_in: number;
}

export interface ReplayFrameResponse {
  /** Simulation seconds since impact — not wall-clock, not paint time. */
  t_s: number;
  bodies: ReplayBodyResponse[];
}

/** A bounded, deterministic, server-authoritative replay of one planar
 * collision run. `model_version` names the replay's own shape/semantics
 * (distinct from `PinfallInfo.model_id`, which names the model that
 * resolved the pins), so a client can refuse data it doesn't understand
 * rather than misinterpreting it.
 *
 * Every field here is typed as it arrives on the wire, not as it will be
 * once trusted: this is the *unvalidated* shape. `domain/collisionReplay.ts`
 * is what turns it into something playable, and only it may narrow these. */
export interface CollisionReplayResponse {
  model_version: string;
  dt_s: number;
  sample_every_steps: number;
  steps_taken: number;
  frames: ReplayFrameResponse[];
  /**
   * Which of the solver's two loop exits produced the final frame —
   * `'settled'` or `'step_cap'` (see `backend/app/physics/replay.py`).
   *
   * Deliberately `string | undefined` rather than that union: this
   * describes a payload, and a payload can carry anything, including
   * nothing at all — a v1 replay predates the field entirely, and an older
   * version may not match this contract at all. Narrowing it
   * here would let a consumer read an unvalidated string as if it were one
   * of the two known values. `acceptReplay` does the narrowing, after
   * checking.
   *
   * Neither value describes pin state. `'settled'` means every body fell
   * under the planar model's velocity threshold; `'step_cap'` means the
   * solver hit its 2 s limit with bodies still moving. Which pins fell is
   * `PinfallInfo.fallen_pin_ids`, and nothing else.
   */
  termination_reason?: string;
}

export interface PinfallInfo {
  model_id: string;
  limitations: string;
  fallen_pin_ids: number[];
  /** Null whenever no collision run was simulated: the heuristic model, a
   * gutter miss, a non-positive impact speed, or an empty rack. Never a
   * fabricated scene for a collision that didn't happen. */
  replay: CollisionReplayResponse | null;
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
