/**
 * The lane canvas's required "concise text alternative describing the
 * latest result." Pure formatting over a `GameThrowResponse` — every
 * number quoted is exactly what the server returned; the only judgment
 * call made here is turning `entry_angle_deg`'s sign into the word "left"
 * or "right", using the exact sign convention `simulate.py` documents
 * (positive = drifting toward higher board numbers = left, since board 1
 * is the right gutter and board 39 the left).
 */

import type { GameThrowResponse } from '../api/types';

export function describeLatestThrow(latestThrow: GameThrowResponse | null): string {
  if (!latestThrow) {
    return 'No throw yet. Set your release below and press Throw.';
  }

  const { pins_knocked, entry_board, entry_angle_deg, speed_at_pins_mph, pinfall } = latestThrow;

  const direction = entry_angle_deg > 0 ? 'left' : entry_angle_deg < 0 ? 'right' : 'straight ahead';
  const heading =
    direction === 'straight ahead' ? 'moving straight ahead' : `drifting ${direction} at ${Math.abs(entry_angle_deg).toFixed(1)}°`;

  const pinsText = pins_knocked === 1 ? '1 pin' : `${pins_knocked} pins`;
  const fallenIds = pinfall.fallen_pin_ids.slice().sort((a, b) => a - b);
  const fallenText = fallenIds.length > 0 ? ` (pins ${fallenIds.join(', ')})` : '';

  return (
    `Ball entered the pin deck at board ${entry_board.toFixed(1)}, ${heading}, ` +
    `at ${speed_at_pins_mph.toFixed(1)} mph. Knocked down ${pinsText}${fallenText}.`
  );
}
