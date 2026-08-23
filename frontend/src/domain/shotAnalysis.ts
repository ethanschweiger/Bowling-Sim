import type { GameStateResponse, GameThrowResponse, ReleaseValues, ThrowRequest } from '../api/types';

export interface ShotAnalysisRow {
  label: string;
  value: string;
}

function formatNumber(value: number, decimals = 1): string {
  return value.toFixed(decimals);
}

export function formatRelease(release: ReleaseValues): string {
  return [
    `${formatNumber(release.speed_mph)} mph`,
    `${Math.round(release.rev_rate)} rpm`,
    `${formatNumber(release.axis_rotation)}° rotation`,
    `${formatNumber(release.axis_tilt)}° tilt`,
    `${formatNumber(release.launch_angle)}° angle`,
    `board ${formatNumber(release.launch_position)}`,
  ].join(' · ');
}

export function requestedReleaseValues(request: ThrowRequest): ReleaseValues {
  const { speed_mph, rev_rate, axis_rotation, axis_tilt, launch_angle, launch_position } = request;
  return { speed_mph, rev_rate, axis_rotation, axis_tilt, launch_angle, launch_position };
}

export function describeNextRoll(gameState: GameStateResponse): string {
  if (gameState.is_game_complete || gameState.next_frame_number === null || gameState.next_ball_number === null) {
    return 'Game complete';
  }
  return `Next: frame ${gameState.next_frame_number}, ball ${gameState.next_ball_number}`;
}

export function shotAnalysisRows(response: GameThrowResponse, requested: ThrowRequest): ShotAnalysisRow[] {
  const entryAngle = `${response.entry_angle_deg >= 0 ? '+' : ''}${formatNumber(response.entry_angle_deg)}°`;
  return [
    { label: 'Replay seed', value: String(response.seed) },
    { label: 'Requested release', value: formatRelease(requestedReleaseValues(requested)) },
    { label: 'Actual release', value: formatRelease(response.actual_release) },
    {
      label: 'Entry',
      value: `Board ${formatNumber(response.entry_board)}, ${entryAngle}, ${formatNumber(response.speed_at_pins_mph)} mph`,
    },
    { label: 'Lane condition', value: `Version ${response.lane_condition_version} used for this throw` },
    { label: 'Game status', value: describeNextRoll(response.game_state) },
  ];
}
