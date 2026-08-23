import { describe, expect, it } from 'vitest';
import { RELEASE_FIELDS } from './releaseFields';

describe('release field guidance', () => {
  it('explains the signed launch direction and calls the board a laydown point', () => {
    const launchAngle = RELEASE_FIELDS.find((field) => field.id === 'launch_angle');
    const laydown = RELEASE_FIELDS.find((field) => field.id === 'launch_position');

    expect(launchAngle?.help).toContain('lower/right');
    expect(launchAngle?.help).toContain('higher/left');
    expect(laydown?.label).toBe('Ball laydown board');
    expect(laydown?.help).toContain('not the bowler');
  });
});
