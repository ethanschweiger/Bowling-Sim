import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { BallResponse } from '../api/types';
import {
  DEFAULT_BALL_ID,
  fetchBallCatalog,
  isSelectable,
  pickDefaultBallId,
  resetBallCatalogForTests,
} from './ballCatalog';

function ball(id: string, name = id): BallResponse {
  return {
    id,
    name,
    coverstock: 'reactive',
    surface: '2000-grit',
    description: `${name} description`,
    spec: { mass_lbs: 15, radius_in: 4.29, rg_in: 2.52, differential: 0.052, hook_potential: 1 },
  };
}

function jsonResponse(body: unknown, init: { status?: number } = {}) {
  const status = init.status ?? 200;
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => {
  resetBallCatalogForTests();
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetBallCatalogForTests();
});

describe('pickDefaultBallId', () => {
  it('prefers the reactive ball that matches the right-handed starter release', () => {
    const balls = [ball('house_ball'), ball('reactive_pearl', 'Reactive Pearl')];

    expect(pickDefaultBallId(balls)).toBe(DEFAULT_BALL_ID);
    expect(DEFAULT_BALL_ID).toBe('reactive_pearl');
  });

  it('falls back to the first published ball when the preferred one is absent', () => {
    const balls = [ball('house_ball'), ball('urethane_smooth')];

    expect(pickDefaultBallId(balls)).toBe('house_ball');
  });

  it('returns null for an empty catalog rather than inventing an id', () => {
    expect(pickDefaultBallId([])).toBeNull();
  });
});

describe('isSelectable', () => {
  it('accepts only ids the server published', () => {
    const balls = [ball('house_ball')];

    expect(isSelectable(balls, 'house_ball')).toBe(true);
    expect(isSelectable(balls, 'reactive_pearl')).toBe(false);
    expect(isSelectable(balls, null)).toBe(false);
    expect(isSelectable([], 'house_ball')).toBe(false);
  });
});

describe('fetchBallCatalog', () => {
  it('returns the balls array from the response body', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ balls: [ball('house_ball')] }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(fetchBallCatalog()).resolves.toEqual([ball('house_ball')]);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/balls');
  });

  it('shares one request across concurrent callers', async () => {
    // The StrictMode double-mount case: two callers, one HTTP request.
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ balls: [ball('house_ball')] }));
    vi.stubGlobal('fetch', fetchMock);

    const [first, second] = await Promise.all([fetchBallCatalog(), fetchBallCatalog()]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(first).toBe(second);
  });

  it('does not re-request after a success', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ balls: [ball('house_ball')] }));
    vi.stubGlobal('fetch', fetchMock);

    await fetchBallCatalog();
    await fetchBallCatalog();

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('lets a retry genuinely try again after a failure', async () => {
    // Without clearing the memo, Retry would replay the same rejection
    // forever and the user could never recover.
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: 'boom' }, { status: 500 }))
      .mockResolvedValueOnce(jsonResponse({ balls: [ball('house_ball')] }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(fetchBallCatalog()).rejects.toMatchObject({ status: 500 });
    await expect(fetchBallCatalog()).resolves.toEqual([ball('house_ball')]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
