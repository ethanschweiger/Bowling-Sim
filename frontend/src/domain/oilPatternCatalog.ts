/**
 * Where the oil-pattern notice's data comes from.
 *
 * The backend owns the pattern catalog. `GET /api/v1/oil-patterns`
 * publishes the same supported-pattern registry `POST /api/v1/games`
 * validates `oil_pattern` against, so anything this module hands the UI
 * is a pattern a game can actually be created with.
 *
 * Only one pattern exists today, and the UI has no control to pick a
 * different one — see `BallSelect`'s non-interactive pattern notice — but
 * the *text* is server-owned rather than a local copy, so it cannot
 * disagree with what the backend actually models. There is deliberately
 * no local fallback pattern to fall back on for the same reason
 * `ballCatalog.ts` has none: a stale hardcoded description is worse than
 * no description.
 */

import { getOilPatterns } from '../api/client';
import type { OilPatternResponse } from '../api/types';

// Module-level, matching `ballCatalog.ts` and `gameLifecycle.ts`'s
// bootstrap memo and for the same reason: React StrictMode's dev-only
// double mount should share one in-flight request, and a re-render
// should never issue another.
let catalogPromise: Promise<OilPatternResponse[]> | null = null;

/** The server's oil-pattern catalog, fetched once per module lifetime. A
 * failed attempt clears the memo before rejecting, so a later call
 * (StrictMode's second pass, or the user pressing Retry) genuinely tries
 * again instead of replaying the same stale rejection. */
export function fetchOilPatternCatalog(): Promise<OilPatternResponse[]> {
  if (!catalogPromise) {
    catalogPromise = getOilPatterns()
      .then((response) => response.patterns)
      .catch((error: unknown) => {
        catalogPromise = null;
        throw error;
      });
  }
  return catalogPromise;
}

/** Test-only seam: forget any in-flight or completed fetch so one test
 * does not inherit another's memo. */
export function resetOilPatternCatalogForTests(): void {
  catalogPromise = null;
}

/** The pattern the UI's non-interactive notice describes: the first the
 * server published, since nothing lets a player choose among more than
 * one yet. Returns null for an empty catalog rather than inventing one. */
export function primaryOilPattern(patterns: readonly OilPatternResponse[]): OilPatternResponse | null {
  return patterns[0] ?? null;
}
