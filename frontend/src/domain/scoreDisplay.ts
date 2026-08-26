/**
 * Pure display formatting for a `GameStateResponse`/`FrameStateResponse` —
 * turning already-server-decided facts into scoresheet notation. Nothing
 * here decides whether a roll is a strike or spare, what a frame is worth,
 * whether the next ball gets a fresh rack, or whether the game is over —
 * every one of those is read directly off a flag or number the server
 * already computed (`is_strike`, `is_spare`, `score`, `is_complete`,
 * `total_score`, `is_game_complete`, `roll_symbols`). See the root
 * README's "Treat FastAPI as authoritative" note.
 *
 * Traditional scoresheet notation marks a *later* bonus ball as "X" when
 * it lands on a rack that a fresh strike left standing (e.g. the 10th
 * frame's second or third ball, after an opening strike) — telling which
 * bonus ball faced a fresh rack is a rack rule, not a formatting one, so
 * this module doesn't derive it itself. `roll_symbols` is the server's own
 * answer to exactly that, one symbol per roll in `rolls`, and
 * `frameCellSymbols` renders it directly rather than reconstructing
 * strike/spare glyphs from `rolls`/`is_strike`/`is_spare`.
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

/** One display symbol per roll in the frame, in order — the server's own
 * `roll_symbols`, rendered as-is. Not a derivation: this module never
 * reconstructs `X`/`/` from `rolls`/`is_strike`/`is_spare` itself, so a
 * bonus ball landing on a fresh rack (e.g. the 10th frame's second or
 * third ball, after an opening strike) shows the server's own `X` rather
 * than a plain pin count. */
export function frameCellSymbols(frame: FrameStateResponse): string[] {
  return frame.roll_symbols;
}

/** A resolved score as a string, or an em dash while it's still null
 * (nothing resolved yet, or this frame needs a bonus ball that hasn't
 * landed) — never "0" for "unknown," which would misread as a real score. */
export function formatScore(score: number | null): string {
  return score === null ? EMPTY_SCORE_DISPLAY : String(score);
}
