/**
 * A small, typed client for the FastAPI game API. Every function here maps
 * one to one onto a route in `backend/app/api/routes/games.py` and returns
 * exactly the JSON body the server sent, typed — nothing here re-derives a
 * score, rack, or completion rule; that stays server-side (see the
 * `## Architecture` note in the root README).
 *
 * `API_BASE_URL` is empty by default, so requests go to a *relative*
 * `/api/v1/...` path. In development that's what `vite.config.ts`'s dev
 * server proxy forwards to the FastAPI backend; set `VITE_API_BASE_URL`
 * (see `.env.example`) to point at a different origin instead.
 */

import type {
  BallCatalogResponse,
  CreateGameRequest,
  OilPatternCatalogResponse,
  CreateGameResponse,
  GameResetResponse,
  GameStatusResponse,
  GameThrowResponse,
  ThrowRequest,
} from './types';

const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? '';

/**
 * Thrown for any non-2xx response and for network-level failures (the
 * backend unreachable, DNS failure, etc.) so callers can handle both with
 * one `catch`. `status` is the real HTTP status for a response the server
 * sent, or `0` as a sentinel for a request that never got a response at
 * all — no real HTTP status uses 0, so `error.status === 0` reliably means
 * "couldn't reach the API," distinct from any server-returned rejection.
 */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function extractErrorMessage(body: unknown, status: number): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === 'string') {
      return detail;
    }
    // FastAPI's 422 validation-error shape: detail is a list of
    // {loc, msg, type} objects. Join their messages into one readable line.
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) =>
          item && typeof item === 'object' && 'msg' in item ? String((item as { msg: unknown }).msg) : null,
        )
        .filter((message): message is string => message !== null);
      if (messages.length > 0) {
        return messages.join('; ');
      }
    }
  }
  return `Request failed with status ${status}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    });
  } catch {
    throw new ApiError(0, 'Could not reach the Bowling-Sim API. Is the backend running?');
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(response.status, extractErrorMessage(body, response.status));
  }

  return (await response.json()) as T;
}

/** The server's ball catalog. Read-only, and the only source of legal
 * `ball_id` values the UI is allowed to offer. */
export function getBalls(): Promise<BallCatalogResponse> {
  return request<BallCatalogResponse>('/api/v1/balls');
}

/** The server's oil-pattern catalog. Read-only, and the only source of
 * legal `oil_pattern` values `POST /api/v1/games` accepts. */
export function getOilPatterns(): Promise<OilPatternCatalogResponse> {
  return request<OilPatternCatalogResponse>('/api/v1/oil-patterns');
}

export function createGame(body: CreateGameRequest = {}): Promise<CreateGameResponse> {
  return request<CreateGameResponse>('/api/v1/games', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function getGame(gameId: string): Promise<GameStatusResponse> {
  return request<GameStatusResponse>(`/api/v1/games/${encodeURIComponent(gameId)}`);
}

export function throwBall(gameId: string, body: ThrowRequest): Promise<GameThrowResponse> {
  return request<GameThrowResponse>(`/api/v1/games/${encodeURIComponent(gameId)}/throws`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function resetGame(gameId: string): Promise<GameResetResponse> {
  return request<GameResetResponse>(`/api/v1/games/${encodeURIComponent(gameId)}/reset`, {
    method: 'POST',
  });
}
