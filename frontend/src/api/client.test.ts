import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, createGame, getBalls, getGame, resetGame, throwBall } from './client';

function jsonResponse(body: unknown, init: { status?: number; ok?: boolean } = {}) {
  const status = init.status ?? 200;
  return {
    ok: init.ok ?? (status >= 200 && status < 300),
    status,
    json: async () => body,
  } as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('getBalls', () => {
  it('GETs /api/v1/balls', async () => {
    const body = { balls: [{ id: 'house_ball', name: 'House Ball' }] };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(body));
    vi.stubGlobal('fetch', fetchMock);

    const result = await getBalls();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/balls');
    expect(init?.method ?? undefined).toBeUndefined(); // GET is fetch's default method
    expect(result.balls[0].id).toBe('house_ball');
  });
});

describe('createGame', () => {
  it('POSTs to /api/v1/games with the given body', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ game_id: 'g1', lane_condition_version: 1, game_state: {} }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await createGame({ oil_pattern: 'house' });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/games');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ oil_pattern: 'house' });
    expect(result.game_id).toBe('g1');
  });
});

describe('getGame', () => {
  it('GETs /api/v1/games/{id} with the id URL-encoded', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ game_id: 'a b', lane_condition_version: 2, game_state: {} }));
    vi.stubGlobal('fetch', fetchMock);

    await getGame('a b');

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/games/a%20b');
    expect(init?.method ?? undefined).toBeUndefined(); // GET is fetch's default method
  });

  it('throws a 404 ApiError for an unknown game', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: "Unknown game_id 'x'" }, { status: 404 }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(getGame('x')).rejects.toMatchObject({ status: 404, message: "Unknown game_id 'x'" });
  });
});

describe('throwBall', () => {
  it('POSTs the release values to /api/v1/games/{id}/throws', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ game_id: 'g1' }));
    vi.stubGlobal('fetch', fetchMock);

    const body = {
      ball_id: 'reactive_pearl',
      speed_mph: 17,
      rev_rate: 350,
      axis_rotation: 45,
      axis_tilt: 15,
      launch_angle: 0.5,
      launch_position: 28,
    };
    await throwBall('g1', body);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/games/g1/throws');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual(body);
  });

  it('joins a 422 validation error into one readable message', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        { detail: [{ loc: ['body', 'speed_mph'], msg: 'Input should be greater than or equal to 10', type: 'greater_than_equal' }] },
        { status: 422 },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(throwBall('g1', {} as never)).rejects.toMatchObject({
      status: 422,
      message: 'Input should be greater than or equal to 10',
    });
  });

  it('surfaces a 409 for a throw against a completed game', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail: "game 'g1' is already complete" }, { status: 409 }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(throwBall('g1', {} as never)).rejects.toMatchObject({ status: 409 });
  });

  it('surfaces a 503 for a rejected (truncated-trajectory) solver throw', async () => {
    // Deliberately arbitrary fixture text, not a copy of the backend's own
    // TRUNCATED_TRAJECTORY_DETAIL constant (app/api/routes/games.py): the
    // client must relay whatever `detail` string the server actually
    // sends, generically, not one it happens to recognize or duplicate.
    const detail = 'server-fixture-only: throw rejected, nothing was saved, try again';
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail }, { status: 503 }));
    vi.stubGlobal('fetch', fetchMock);

    const error = await throwBall('g1', {} as never).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(503);
    expect((error as ApiError).message).toBe(detail);
  });

  it('wraps a network failure in a distinct, recognizable ApiError', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetchMock);

    const error = await throwBall('g1', {} as never).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(0);
  });
});

describe('resetGame', () => {
  it('POSTs to /api/v1/games/{id}/reset with no body', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ game_id: 'g1' }));
    vi.stubGlobal('fetch', fetchMock);

    await resetGame('g1');

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/games/g1/reset');
    expect(init.method).toBe('POST');
    expect(init.body).toBeUndefined();
  });
});
