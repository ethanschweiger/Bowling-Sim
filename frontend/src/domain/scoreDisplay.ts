/**
 * Pure display formatting for a `GameStateResponse`/`FrameStateResponse` —
 * turning already-server-decided facts into scoresheet notation. Nothing
 * here decides whether a roll is a strike or spare, what a frame is worth,
 * whether the next ball gets a fresh rack, or whether the game is over —
 * every one of those is read directly off a flag or number the server
 * already computed (`is_strike`, `is_spare`, `score`, `is_complete`,
 * `total_score`, `is_game_complete`). See the root README's "Treat FastAPI
 * as authoritative" note.
 *
 * The one deliberately incomplete corner: traditional scoresheet notation
 * marks a *later* bonus ball as "X" when it lands on a rack that a fresh
 * strike left standing (e.g. the 10th frame's second or third ball, after
 * an opening strike). Telling which bonus ball faced a fresh rack is a
 * rack rule, not a formatting one, so this module doesn't derive it — a
 * fresh-rack bonus ball is shown as its own plain pin count instead of a
 * synthesized "X". Every number shown is still exactly what the server
 * returned; only that one decorative glyph is intentionally left plain.
 */

import type { FrameStateResponse } from '../api/types';

/** A plain-language summary of which pins are standing, straight off
 * `game_state.standing_pin_ids` — no rack rule involved, just phrasing a
 * list of numbers the server already sorted. */
export function describeStandingPins(standingPinIds: readonly number[]): string {
  if (standingPinIds.length === 10) {
    return 'All ten pins standing.';
  }
  if (standingPinIds.length === 0) {
    return 'No pins standing.';
  }
  return `Pins standing: ${standingPinIds.join(', ')}.`;
}

const EMPTY_SCORE_DISPLAY = '—'; // em dash

/** A single roll's plain pin count, with the scoresheet convention of a
 * dash for a miss (0 pins), same as any printed scoresheet uses. */
export function rollSymbol(pins: number): string {
  return pins === 0 ? '-' : String(pins);
}

/** One display symbol per roll in the frame, in order. See the module
 * docstring for exactly which glyphs are (and aren't) derived. */
export function frameCellSymbols(frame: FrameStateResponse): string[] {
  const symbols = frame.rolls.map(rollSymbol);
  if (frame.is_strike) {
    symbols[0] = 'X';
  } else if (frame.is_spare && symbols.length > 1) {
    symbols[1] = '/';
  }
  return symbols;
}

/** A resolved score as a string, or an em dash while it's still null
 * (nothing resolved yet, or this frame needs a bonus ball that hasn't
 * landed) — never "0" for "unknown," which would misread as a real score. */
export function formatScore(score: number | null): string {
  return score === null ? EMPTY_SCORE_DISPLAY : String(score);
}
