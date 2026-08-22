/**
 * UI configuration for the six release inputs `POST /api/v1/games/{id}/throws`
 * accepts. `min`/`max`/`default` are copied from the same two sources the
 * backend itself reads from a single place — `RELEASE_BOUNDS` and
 * `ThrowRequest`'s field defaults in `backend/app/physics/throw.py` /
 * `backend/app/models/schemas.py` — so the client can never offer a value
 * the server would reject as out of range. If those bounds ever change,
 * this is the one frontend file that needs the matching edit.
 */

export type ReleaseFieldId =
  | 'speed_mph'
  | 'rev_rate'
  | 'axis_rotation'
  | 'axis_tilt'
  | 'launch_angle'
  | 'launch_position';

export interface ReleaseFieldConfig {
  id: ReleaseFieldId;
  label: string;
  unit: string;
  min: number;
  max: number;
  step: number;
  defaultValue: number;
  help: string;
}

export const RELEASE_FIELDS: readonly ReleaseFieldConfig[] = [
  {
    id: 'speed_mph',
    label: 'Ball speed',
    unit: 'mph',
    min: 10,
    max: 25,
    step: 0.1,
    defaultValue: 17.0,
    help: 'Speed leaving the hand.',
  },
  {
    id: 'rev_rate',
    label: 'Rev rate',
    unit: 'rpm',
    min: 0,
    max: 600,
    step: 1,
    defaultValue: 350,
    help: 'Revolutions per minute.',
  },
  {
    id: 'axis_rotation',
    label: 'Axis rotation',
    unit: '°',
    min: 0,
    max: 90,
    step: 1,
    defaultValue: 45,
    help: '0° is a full roll, 90° is a full spinner.',
  },
  {
    id: 'axis_tilt',
    label: 'Axis tilt',
    unit: '°',
    min: 0,
    max: 90,
    step: 1,
    defaultValue: 15,
    help: 'Higher tilt stores up more roll for a later hook.',
  },
  {
    id: 'launch_angle',
    label: 'Launch angle',
    unit: '°',
    min: -2,
    max: 2,
    step: 0.1,
    defaultValue: 0.5,
    help: "Aim off the lane's centerline; positive points toward higher board numbers.",
  },
  {
    id: 'launch_position',
    label: 'Launch position',
    unit: 'board',
    min: 1,
    max: 39,
    step: 0.5,
    defaultValue: 28,
    help: 'The board the ball is laid down on at the foul line, 1–39 — not where the bowler stands.',
  },
];

export function defaultReleaseValues(): Record<ReleaseFieldId, number> {
  const values = {} as Record<ReleaseFieldId, number>;
  for (const field of RELEASE_FIELDS) {
    values[field.id] = field.defaultValue;
  }
  return values;
}
