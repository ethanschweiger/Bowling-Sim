/**
 * Session-lifecycle logic for the frontend's one game: idempotent
 * create-or-load bootstrap, and detecting when a throw/reset's rejection
 * means the saved game no longer exists on the server. Kept out of App.tsx
 * so it's testable without rendering React or a browser tab — every
 * function here is a plain async function over the typed API client and
 * (for bootstrap/new-game) an injectable, `localStorage`-shaped store.
 */

import { ApiError, createGame, getGame } from '../api/client';
import type { GameStateResponse } from '../api/types';

export const GAME_ID_STORAGE_KEY = 'bowling-sim:game-id';

/** The subset of the Web Storage API this module needs — small enough to
 * fake in a test without a real `window`/`localStorage`. */
export type GameIdStore = Pick<Storage, 'getItem' | 'setItem'>;

function defaultStore(): GameIdStore {
  return window.localStorage;
}

export interface BootResult {
  gameId: string;
  laneConditionVersion: number;
  gameState: GameStateResponse;
}

// Module-level, not component state: this is what makes bootstrap survive
// React StrictMode's deliberate double-invoke of a mount effect. Both
// invocations call bootstrapGame() near-simultaneously; the second one
// finds this already set and shares the first one's in-flight promise
// instead of starting a second create/GET — so a StrictMode dev double-
// mount can never create two games and orphan one.
let bootstrapPromise: Promise<BootResult> | null = null;

/** Create-or-load, memoized for the lifetime of this module. A failed
 * attempt clears the memo before rejecting, so the *next* call (a mount
 * effect's second StrictMode pass, or a user pressing "Retry") genuinely
 * tries again rather than replaying the same stale rejection forever. */
export function bootstrapGame(store: GameIdStore = defaultStore()): Promise<BootResult> {
  if (!bootstrapPromise) {
    bootstrapPromise = attemptBootstrap(store).catch((error: unknown) => {
      bootstrapPromise = null;
      throw error;
    });
  }
  return bootstrapPromise;
}

/** Test-only seam: forget any in-flight/completed attempt so a fresh test
 * doesn't inherit state a previous one left in this module. */
export function resetBootstrapForTests(): void {
  bootstrapPromise = null;
}

async function attemptBootstrap(store: GameIdStore): Promise<BootResult> {
  const storedId = store.getItem(GAME_ID_STORAGE_KEY);
  if (storedId) {
    try {
      const found = await getGame(storedId);
      return { gameId: found.game_id, laneConditionVersion: found.lane_condition_version, gameState: found.game_state };
    } catch (error) {
      if (!isStaleGameError(error)) {
        throw error;
      }
      // Stored id is stale (server restarted, or it never existed) — fall
      // through and create a fresh one below.
    }
  }

  const created = await createGame();
  store.setItem(GAME_ID_STORAGE_KEY, created.game_id);
  return { gameId: created.game_id, laneConditionVersion: created.lane_condition_version, gameState: created.game_state };
}

/** True exactly when `error` is the server telling us a game_id doesn't
 * exist — the one specific condition that means "this browser's saved
 * game is gone; offer to start a new one." A network failure, a
 * validation error, or a completed-game 409 are all real, visible errors,
 * but none of them mean the game itself is gone, so none of them should
 * route through this recovery path.
 *
 * Only safe to use directly on a bootstrap GET's or a reset's own error —
 * both 404 for exactly one reason (an unknown game_id). A throw's 404 is
 * *not* one of those: `POST /games/{id}/throws` also 404s for an unknown
 * `ball_id` (see backend/app/api/routes/games.py), so a throw's own 404
 * alone can't tell "missing game" and "unrelated request error" apart.
 * Use `classifyThrowFailure` for that one, ambiguous case instead. */
export function isStaleGameError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

export type ThrowFailureClassification = { kind: 'confirmed-missing-game' } | { kind: 'other'; error: unknown };

/** Resolves what a failed throw's error actually means. A throw's 404 is
 * ambiguous (see `isStaleGameError`'s docstring) — the one authoritative
 * way to tell "missing game" from "unrelated request error" (most likely
 * an unknown `ball_id`, but this deliberately doesn't parse the error's
 * human-readable text to check) is to ask the server directly, with the
 * one endpoint that only ever 404s for a missing game_id: `GET`. If that
 * confirmation GET also 404s, the game really is gone. If it succeeds,
 * the game is fine and the throw failed for some other reason — the
 * *original* throw error is what should reach the user, not a fabricated
 * "missing game" story. If the confirmation GET itself fails for some
 * unrelated reason (network, 5xx — genuinely unknown either way), this
 * conservatively reports the original throw error and never offers
 * destructive recovery on an unconfirmed guess. Non-404 throw errors
 * (409 complete, network failure, ...) are never ambiguous in the first
 * place and pass through untouched, with no confirmation call at all. */
export async function classifyThrowFailure(gameId: string, originalError: unknown): Promise<ThrowFailureClassification> {
  if (!(originalError instanceof ApiError) || originalError.status !== 404) {
    return { kind: 'other', error: originalError };
  }
  try {
    await getGame(gameId);
    return { kind: 'other', error: originalError };
  } catch (confirmationError) {
    if (isStaleGameError(confirmationError)) {
      return { kind: 'confirmed-missing-game' };
    }
    return { kind: 'other', error: originalError };
  }
}

/** Unconditionally starts a brand new game and makes it this browser's
 * saved one — the "Start a new game" recovery action for a confirmed-
 * stale saved game_id. Never reads the old id; always overwrites it (or
 * writes one for the first time) on success.
 *
 * `oilPatternId` is the player's own selection from the oil-pattern
 * catalog (see `domain/oilPatternCatalog.ts`); omit it (or pass undefined)
 * to let the server fall back to its own default, exactly as the initial
 * bootstrap create already does. */
export async function startNewGame(
  store: GameIdStore = defaultStore(),
  oilPatternId?: string,
): Promise<BootResult> {
  const created = await createGame(oilPatternId ? { oil_pattern: oilPatternId } : {});
  store.setItem(GAME_ID_STORAGE_KEY, created.game_id);
  return { gameId: created.game_id, laneConditionVersion: created.lane_condition_version, gameState: created.game_state };
}

/** The two lane-version facts the UI can truthfully know at any moment —
 * see the root README's "Read a game without changing it" for why they
 * aren't the same number. `currentLaneVersion` comes from a create, reset,
 * or GET response (all documented as reporting the game's *current*
 * version); `lastThrowRanAgainstVersion` comes only from a throw response
 * (documented as the version that throw ran *against*, i.e. one behind
 * current once that throw's own wear has landed). */
export interface LaneVersionState {
  currentLaneVersion: number | null;
  lastThrowRanAgainstVersion: number | null;
}

/** One sentence describing lane-condition version state, labeled for
 * exactly what it is — never presenting a throw's pre-wear number as if
 * it were the game's current version. */
export function describeLaneVersion(state: LaneVersionState): string {
  if (state.lastThrowRanAgainstVersion !== null) {
    return `Last throw ran against lane condition version ${state.lastThrowRanAgainstVersion}.`;
  }
  return `Lane condition version ${state.currentLaneVersion ?? '—'}.`;
}
