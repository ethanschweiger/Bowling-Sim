/**
 * Where the selectable oil-pattern list comes from, and which pattern a
 * new game starts with.
 *
 * The backend owns the pattern catalog. `GET /api/v1/oil-patterns`
 * publishes the same supported-pattern registry `POST /api/v1/games`
 * validates `oil_pattern` against, so anything this module hands the UI
 * is a pattern a game can actually be created with. Mirrors
 * `ballCatalog.ts`'s shape exactly, for the same reasons: there is
 * deliberately no local fallback catalog or hardcoded pattern id except
 * the *preference* below, and that preference is checked against the
 * fetched list before it is used. A stale hardcoded list or description
 * is worse than none: it can disagree with the server and offer an id a
 * create-game request would reject with a 422.
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

/** The pattern a new game starts with unless the player picks otherwise:
 * the forgiving house shot, the same default `POST /api/v1/games` itself
 * falls back to when `oil_pattern` is omitted. Only a preference — if the
 * server ever stops publishing it, `pickDefaultOilPatternId` falls back
 * rather than selecting an id the server does not offer. */
export const DEFAULT_OIL_PATTERN_ID = 'house';

/** Which pattern id to select when the catalog arrives: the preferred one
 * when the server actually returned it, otherwise the first pattern it
 * did return. Returns null for an empty catalog, which leaves the UI with
 * nothing selectable rather than inventing an id. */
export function pickDefaultOilPatternId(patterns: readonly OilPatternResponse[]): string | null {
  if (patterns.some((pattern) => pattern.id === DEFAULT_OIL_PATTERN_ID)) {
    return DEFAULT_OIL_PATTERN_ID;
  }
  return patterns[0]?.id ?? null;
}

/** True when `patternId` is one the server published. The new-game path
 * checks this before submitting, so a stale selection can never be sent. */
export function isOilPatternSelectable(
  patterns: readonly OilPatternResponse[],
  patternId: string | null,
): boolean {
  return patternId !== null && patterns.some((pattern) => pattern.id === patternId);
}

/**
 * Whether the main gameplay surface (lane, throw/reset controls,
 * scoreboard, stale-game recovery) can render.
 *
 * Deliberately a function of the game and the *ball* catalog only. A
 * throw must name a `ball_id` the server published, so gameplay genuinely
 * depends on that catalog — but the oil-pattern catalog feeds only the
 * optional next-game selector, so its failure must never take the loaded
 * game's own controls down with it. An earlier version of this gate also
 * required the oil-pattern catalog, which turned one optional selector's
 * fetch failure into a total app block; `oilPatternCatalog` is
 * intentionally absent from this signature so that cannot recur.
 */
export function canPlayLoadedGame(hasGame: boolean, hasBallCatalog: boolean): boolean {
  return hasGame && hasBallCatalog;
}

/**
 * The `oil_pattern` id a new-game request should carry, or `undefined` to
 * omit the field entirely and let the server apply its own `house`
 * default.
 *
 * Deliberately total over a *failed* catalog load, not just an empty one:
 * `patterns` is null whenever `fetchOilPatternCatalog()` rejected, and
 * that case must still produce a usable new-game request rather than
 * blocking recovery. An id the server never published is likewise
 * dropped rather than sent, since it would only earn a 422.
 */
export function oilPatternIdForNewGame(
  patterns: readonly OilPatternResponse[] | null,
  patternId: string | null,
): string | undefined {
  if (!patterns || !isOilPatternSelectable(patterns, patternId)) {
    return undefined;
  }
  return patternId ?? undefined;
}
