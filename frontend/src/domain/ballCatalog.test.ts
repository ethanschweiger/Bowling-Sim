import { describe, expect, it } from 'vitest';
import { BALL_CATALOG, DEFAULT_BALL_ID } from './ballCatalog';

describe('starter ball', () => {
  it('uses the reactive ball that matches the right-handed starter release', () => {
    expect(DEFAULT_BALL_ID).toBe('reactive_pearl');
    expect(BALL_CATALOG.find((ball) => ball.id === DEFAULT_BALL_ID)?.name).toBe('Reactive Pearl');
  });
});
