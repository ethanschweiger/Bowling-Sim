import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { OilPatternResponse } from '../api/types';
import { fetchOilPatternCatalog, primaryOilPattern, resetOilPatternCatalogForTests } from './oilPatternCatalog';

function pattern(id: string, name = id): OilPatternResponse {
  return {
    id,
    name,
    description: `${name} description`,
    spec: {
      length_ft: 40,
      taper_ft: 6,
      center_boards: [8, 32],
      total_boards: [3, 37],
      pattern_ratio: 3,
      total_volume_ml: 22,
    },
  };
}

function jsonResponse(body: unknown, init: { status?: number } = {}) {
  const status = init.status ?? 200;
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

beforeEach(() => {
  resetOilPatternCatalogForTests();
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetOilPatternCatalogForTests();
});

describe('primaryOilPattern', () => {
  it('returns the first published pattern', () => {
    expect(primaryOilPattern([pattern('house', 'House Shot'), pattern('sport')])).toEqual(
      pattern('house', 'House Shot'),
    );
  });

  it('returns null for an empty catalog rather than inventing one', () => {
    expect(primaryOilPattern([])).toBeNull();
  });
});

describe('fetchOilPatternCatalog', () => {
  it('returns the patterns array from the response body', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ patterns: [pattern('house')] }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(fetchOilPatternCatalog()).resolves.toEqual([pattern('house')]);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/oil-patterns');
  });

  it('shares one request across concurrent callers', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ patterns: [pattern('house')] }));
    vi.stubGlobal('fetch', fetchMock);

    const [first, second] = await Promise.all([fetchOilPatternCatalog(), fetchOilPatternCatalog()]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(first).toBe(second);
  });

  it('does not re-request after a success', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ patterns: [pattern('house')] }));
    vi.stubGlobal('fetch', fetchMock);

    await fetchOilPatternCatalog();
    await fetchOilPatternCatalog();

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('lets a retry genuinely try again after a failure', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: 'boom' }, { status: 500 }))
      .mockResolvedValueOnce(jsonResponse({ patterns: [pattern('house')] }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(fetchOilPatternCatalog()).rejects.toMatchObject({ status: 500 });
    await expect(fetchOilPatternCatalog()).resolves.toEqual([pattern('house')]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
