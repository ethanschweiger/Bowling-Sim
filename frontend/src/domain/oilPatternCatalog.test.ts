import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { OilPatternResponse } from '../api/types';
import {
  canPlayLoadedGame,
  DEFAULT_OIL_PATTERN_ID,
  fetchOilPatternCatalog,
  isOilPatternSelectable,
  oilPatternIdForNewGame,
  pickDefaultOilPatternId,
  resetOilPatternCatalogForTests,
} from './oilPatternCatalog';

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

describe('pickDefaultOilPatternId', () => {
  it('prefers the house pattern when the server published it', () => {
    expect(pickDefaultOilPatternId([pattern('challenge'), pattern('house')])).toBe(DEFAULT_OIL_PATTERN_ID);
  });

  it('falls back to the first published pattern when house is absent', () => {
    expect(pickDefaultOilPatternId([pattern('challenge'), pattern('sport')])).toBe('challenge');
  });

  it('returns null for an empty catalog rather than inventing one', () => {
    expect(pickDefaultOilPatternId([])).toBeNull();
  });
});

describe('isOilPatternSelectable', () => {
  const catalog = [pattern('house'), pattern('challenge')];

  it('is true for a published id', () => {
    expect(isOilPatternSelectable(catalog, 'challenge')).toBe(true);
  });

  it('is false for an id the server did not publish', () => {
    expect(isOilPatternSelectable(catalog, 'sport')).toBe(false);
  });

  it('is false for null', () => {
    expect(isOilPatternSelectable(catalog, null)).toBe(false);
  });
});

describe('canPlayLoadedGame', () => {
  // The rejected-attempt regression, stated directly: an oil-pattern
  // catalog failure must not gate the loaded game's own surface. This
  // function takes no oil-pattern argument at all, so a future edit
  // cannot quietly reintroduce that coupling without changing its
  // signature and failing to compile here.
  it('is true once the game and ball catalog are loaded', () => {
    expect(canPlayLoadedGame(true, true)).toBe(true);
  });

  it('stays true regardless of oil-pattern catalog state (it takes no such argument)', () => {
    // Whatever happened to the oil-pattern catalog -- loaded, empty, or
    // failed outright -- these are the only two inputs that decide
    // whether throws, reset, the lane, and the scoreboard render.
    expect(canPlayLoadedGame(true, true)).toBe(true);
    expect(canPlayLoadedGame.length).toBe(2);
  });

  it('is false without a game', () => {
    expect(canPlayLoadedGame(false, true)).toBe(false);
  });

  it('is false without the ball catalog, which a throw genuinely needs', () => {
    expect(canPlayLoadedGame(true, false)).toBe(false);
  });
});

describe('oilPatternIdForNewGame', () => {
  const catalog = [pattern('house'), pattern('challenge')];

  it('sends a selected id the server published', () => {
    expect(oilPatternIdForNewGame(catalog, 'challenge')).toBe('challenge');
  });

  // The rejected-attempt regression: a failed oil-pattern catalog load
  // (patterns === null) must still yield a usable new-game request rather
  // than blocking recovery. `undefined` here means the field is omitted
  // entirely, so the server applies its own "house" default.
  it('falls back to omitting oil_pattern when the catalog failed to load', () => {
    expect(oilPatternIdForNewGame(null, 'challenge')).toBeUndefined();
    expect(oilPatternIdForNewGame(null, null)).toBeUndefined();
  });

  it('omits an id the server never published rather than sending a 422', () => {
    expect(oilPatternIdForNewGame(catalog, 'sport')).toBeUndefined();
  });

  it('omits when nothing is selected yet', () => {
    expect(oilPatternIdForNewGame(catalog, null)).toBeUndefined();
  });

  it('omits for an empty catalog', () => {
    expect(oilPatternIdForNewGame([], 'house')).toBeUndefined();
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
