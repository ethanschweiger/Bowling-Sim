/**
 * Where the selectable ball list comes from, and which ball starts
 * selected.
 *
 * The backend owns the catalog. `GET /api/v1/balls` publishes
 * `backend/app/physics/ball.py`'s `BALL_CATALOG`, which is the same dict
 * the throw routes validate `ball_id` against, so anything this module
 * hands the UI is throwable by construction. Nothing here hardcodes a
 * ball id except the *preference* below, and that preference is checked
 * against the fetched list before it is used.
 *
 * There is deliberately no local copy of the catalog to fall back on. A
 * stale hardcoded list is worse than no list: it can disagree with the
 * server and offer an id a throw would reject with a 404.
 */

import { getBalls } from '../api/client';
import type { BallResponse } from '../api/types';

/** The ball the starter release is tuned around: a reactive ball makes
 * the displayed skid-to-hook shape legible. Only a preference. If the
 * server stops publishing it, `pickDefaultBallId` falls back rather than
 * selecting an id the server does not offer. */
export const DEFAULT_BALL_ID = 'reactive_pearl';

// Module-level, matching `gameLifecycle.ts`'s bootstrap memo and for the
// same reason: React StrictMode invokes a mount effect twice in dev, and
// both passes should share one in-flight request instead of firing two.
// It also means a re-render never re-requests the catalog.
let catalogPromise: Promise<BallResponse[]> | null = null;

/** The server's catalog, fetched once per module lifetime. A failed
 * attempt clears the memo before rejecting, so a later call (StrictMode's
 * second pass, or the user pressing Retry) genuinely tries again instead
 * of replaying the same stale rejection. */
export function fetchBallCatalog(): Promise<BallResponse[]> {
  if (!catalogPromise) {
    catalogPromise = getBalls()
      .then((response) => response.balls)
      .catch((error: unknown) => {
        catalogPromise = null;
        throw error;
      });
  }
  return catalogPromise;
}

/** Test-only seam: forget any in-flight or completed fetch so one test
 * does not inherit another's memo. */
export function resetBallCatalogForTests(): void {
  catalogPromise = null;
}

/** Which ball to select when the catalog arrives: the preferred one when
 * the server actually returned it, otherwise the first ball it did
 * return. Returns null for an empty catalog, which leaves the UI with
 * nothing selectable rather than inventing an id. */
export function pickDefaultBallId(balls: readonly BallResponse[]): string | null {
  if (balls.some((ball) => ball.id === DEFAULT_BALL_ID)) {
    return DEFAULT_BALL_ID;
  }
  return balls[0]?.id ?? null;
}

/** True when `ballId` is one the server published. The throw path checks
 * this before submitting, so a stale selection can never be sent. */
export function isSelectable(balls: readonly BallResponse[], ballId: string | null): boolean {
  return ballId !== null && balls.some((ball) => ball.id === ballId);
}
