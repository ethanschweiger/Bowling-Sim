import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  bootstrapGame,
  classifyThrowFailure,
  describeLaneVersion,
  GAME_ID_STORAGE_KEY,
  isStaleGameError,
  resetBootstrapForTests,
  startNewGame,
  type GameIdStore,
} from './gameLifecycle';
import { ApiError } from '../api/client';

function jsonResponse(body: unknown, init: { status?: number; ok?: boolean } = {}) {
  const status = init.status ?? 200;
  return {
    ok: init.ok ?? (status >= 200 && status < 300),
    status,
    json: async () => body,
  } as Response;
}

function fakeStore(initial: Record<string, string> = {}): GameIdStore {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
  };
}

const CREATED_GAME = { game_id: 'fresh-game', lane_condition_version: 1, game_state: { standing_pin_ids: [] } };
const LOADED_GAME = { game_id: 'saved-game', lane_condition_version: 3, game_state: { standing_pin_ids: [] } };

beforeEach(() => {
  resetBootstrapForTests();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('bootstrapGame', () => {
  it('creates a game when the store has no saved id', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(CREATED_GAME, { status: 201 }));
    vi.stubGlobal('fetch', fetchMock);
    const store = fakeStore();

    const result = await bootstrapGame(store);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][1].method).toBe('POST');
    expect(result).toEqual({ gameId: 'fresh-game', laneConditionVersion: 1, gameState: CREATED_GAME.game_state });
    expect(store.getItem(GAME_ID_STORAGE_KEY)).toBe('fresh-game');
  });

  it('loads the saved game when the store has a valid id', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(LOADED_GAME));
    vi.stubGlobal('fetch', fetchMock);
    const store = fakeStore({ [GAME_ID_STORAGE_KEY]: 'saved-game' });

    const result = await bootstrapGame(store);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/games/saved-game');
    expect(result).toEqual({ gameId: 'saved-game', laneConditionVersion: 3, gameState: LOADED_GAME.game_state });
  });

  it('falls back to creating a game when the saved id is stale (404) and overwrites it', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: "Unknown game_id 'gone'" }, { status: 404 }))
      .mockResolvedValueOnce(jsonResponse(CREATED_GAME, { status: 201 }));
    vi.stubGlobal('fetch', fetchMock);
    const store = fakeStore({ [GAME_ID_STORAGE_KEY]: 'gone' });

    const result = await bootstrapGame(store);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.gameId).toBe('fresh-game');
    expect(store.getItem(GAME_ID_STORAGE_KEY)).toBe('fresh-game');
  });

  it('does not swallow a non-404 error loading the saved game', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: 'server exploded' }, { status: 500 }));
    vi.stubGlobal('fetch', fetchMock);
    const store = fakeStore({ [GAME_ID_STORAGE_KEY]: 'saved-game' });

    await expect(bootstrapGame(store)).rejects.toMatchObject({ status: 500 });
    expect(fetchMock).toHaveBeenCalledTimes(1); // never fell through to create
  });

  it('shares one in-flight attempt across concurrent callers (React StrictMode-safe)', async () => {
    let resolveFetch!: (value: Response) => void;
    const fetchMock = vi.fn().mockReturnValue(
      new Promise<Response>((resolve) => {
        resolveFetch = resolve;
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const store = fakeStore();

    // Two "mount effects" racing, exactly like StrictMode's deliberate
    // double-invoke of a mount effect in development.
    const first = bootstrapGame(store);
    const second = bootstrapGame(store);

    expect(fetchMock).toHaveBeenCalledTimes(1); // only one network call was actually started

    resolveFetch(jsonResponse(CREATED_GAME, { status: 201 }));
    const [firstResult, secondResult] = await Promise.all([first, second]);

    expect(firstResult).toEqual(secondResult);
    expect(fetchMock).toHaveBeenCalledTimes(1); // still just the one
  });

  it('allows a clean retry after a bootstrap failure instead of replaying the rejection', async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetchMock);
    const store = fakeStore();

    await expect(bootstrapGame(store)).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // A second call after the failure is a genuine new attempt, not the
    // same memoized rejection played back.
    fetchMock.mockResolvedValueOnce(jsonResponse(CREATED_GAME, { status: 201 }));
    const result = await bootstrapGame(store);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(result.gameId).toBe('fresh-game');
  });
});

describe('startNewGame', () => {
  it('always creates a fresh game and overwrites whatever id was saved', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(CREATED_GAME, { status: 201 }));
    vi.stubGlobal('fetch', fetchMock);
    const store = fakeStore({ [GAME_ID_STORAGE_KEY]: 'some-other-game' });

    const result = await startNewGame(store);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][1].method).toBe('POST');
    expect(result.gameId).toBe('fresh-game');
    expect(store.getItem(GAME_ID_STORAGE_KEY)).toBe('fresh-game');
  });
});

describe('isStaleGameError', () => {
  it('is true for a 404 ApiError', () => {
    expect(isStaleGameError(new ApiError(404, "Unknown game_id 'x'"))).toBe(true);
  });

  it('is false for other ApiError statuses', () => {
    expect(isStaleGameError(new ApiError(409, 'already complete'))).toBe(false);
    expect(isStaleGameError(new ApiError(422, 'validation error'))).toBe(false);
    expect(isStaleGameError(new ApiError(0, 'network failure'))).toBe(false);
  });

  it('is false for a non-ApiError', () => {
    expect(isStaleGameError(new TypeError('boom'))).toBe(false);
    expect(isStaleGameError('not even an error')).toBe(false);
  });
});

describe('classifyThrowFailure', () => {
  it('confirms a missing game when the confirmation GET also 404s', async () => {
    const originalError = new ApiError(404, "Unknown game_id 'g1'");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: "Unknown game_id 'g1'" }, { status: 404 }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await classifyThrowFailure('g1', originalError);

    expect(result).toEqual({ kind: 'confirmed-missing-game' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/games/g1');
  });

  it('keeps the live game and the original error when the confirmation GET succeeds (e.g. an unknown ball_id)', async () => {
    const originalError = new ApiError(404, "Unknown ball_id 'nope'");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ game_id: 'g1', lane_condition_version: 2, game_state: {} }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await classifyThrowFailure('g1', originalError);

    expect(result).toEqual({ kind: 'other', error: originalError });
  });

  it('does not offer recovery when the confirmation GET itself fails for an unrelated reason', async () => {
    const originalError = new ApiError(404, "Unknown ball_id 'nope'");
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: 'server exploded' }, { status: 500 }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await classifyThrowFailure('g1', originalError);

    expect(result).toEqual({ kind: 'other', error: originalError });
  });

  it('passes a non-404 throw error straight through without confirming anything', async () => {
    const originalError = new ApiError(409, "game 'g1' is already complete");
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const result = await classifyThrowFailure('g1', originalError);

    expect(result).toEqual({ kind: 'other', error: originalError });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('treats a rejected (503) solver throw as an ordinary failure, never stale-game recovery', async () => {
    // Codex's truncated-trajectory milestone (backend/app/api/routes/games.py)
    // added this status. It is exactly as unambiguous as a 409: the game_id
    // was never in question, so — unlike a throw's 404, which is genuinely
    // ambiguous between "missing game" and "unknown ball_id" — this must
    // never trigger a confirmation GET or the stale-game recovery path.
    const originalError = new ApiError(
      503,
      'the simulated trajectory did not reach the pin deck; this throw was not recorded and game state is unchanged. Retrying is expected to work.',
    );
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const result = await classifyThrowFailure('g1', originalError);

    // The original error — status and readable message intact — is what
    // must reach the status area, not a synthesized "missing game" story.
    expect(result).toEqual({ kind: 'other', error: originalError });
    expect((result as { kind: 'other'; error: unknown }).error).toBe(originalError);
    // No confirmation GET: the network was never touched.
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('passes a non-ApiError straight through without confirming anything', async () => {
    const originalError = new TypeError('Failed to fetch');
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const result = await classifyThrowFailure('g1', originalError);

    expect(result).toEqual({ kind: 'other', error: originalError });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('describeLaneVersion', () => {
  it('labels a throw-only state as what it ran against, not as current', () => {
    expect(describeLaneVersion({ currentLaneVersion: 1, lastThrowRanAgainstVersion: 1 })).toBe(
      'Last throw ran against lane condition version 1.',
    );
  });

  it('labels a create/reset/GET-only state as current', () => {
    expect(describeLaneVersion({ currentLaneVersion: 4, lastThrowRanAgainstVersion: null })).toBe(
      'Lane condition version 4.',
    );
  });

  it('prefers the throw label once a throw has happened, even if a current version is also known', () => {
    expect(describeLaneVersion({ currentLaneVersion: 1, lastThrowRanAgainstVersion: 2 })).toBe(
      'Last throw ran against lane condition version 2.',
    );
  });

  it('falls back to an em dash before anything is known', () => {
    expect(describeLaneVersion({ currentLaneVersion: null, lastThrowRanAgainstVersion: null })).toBe(
      'Lane condition version —.',
    );
  });
});
