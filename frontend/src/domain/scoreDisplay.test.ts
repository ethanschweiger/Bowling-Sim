import { describe, expect, it } from 'vitest';
import type { FrameStateResponse } from '../api/types';
import { describeStandingPins, formatScore, frameCellSymbols } from './scoreDisplay';

function frame(overrides: Partial<FrameStateResponse>): FrameStateResponse {
  return {
    number: 1,
    rolls: [],
    is_strike: false,
    is_spare: false,
    is_complete: false,
    score: null,
    roll_symbols: [],
    ...overrides,
  };
}

describe('frameCellSymbols', () => {
  it('renders exactly the server-provided roll_symbols, in order', () => {
    expect(
      frameCellSymbols(
        frame({ rolls: [10], is_strike: true, is_complete: true, roll_symbols: ['X'] }),
      ),
    ).toEqual(['X']);
  });

  it('renders a spare pair as the server provided it', () => {
    expect(
      frameCellSymbols(
        frame({ rolls: [7, 3], is_spare: true, is_complete: true, roll_symbols: ['7', '/'] }),
      ),
    ).toEqual(['7', '/']);
  });

  it('renders a miss-then-spare as dash then slash, straight from the server', () => {
    expect(
      frameCellSymbols(
        frame({ rolls: [0, 10], is_spare: true, is_complete: true, roll_symbols: ['-', '/'] }),
      ),
    ).toEqual(['-', '/']);
  });

  it('renders an open frame as plain numbers', () => {
    expect(
      frameCellSymbols(
        frame({ rolls: [4, 3], is_complete: true, score: 7, roll_symbols: ['4', '3'] }),
      ),
    ).toEqual(['4', '3']);
  });

  it('renders a frame still waiting on ball 2 as just the first roll', () => {
    expect(frameCellSymbols(frame({ rolls: [6], roll_symbols: ['6'] }))).toEqual(['6']);
  });

  it(
    "renders the tenth frame's fresh-rack bonus strikes as X -- the case this " +
      'feature exists for',
    () => {
      // Before server-owned roll_symbols existed, a tenth-frame bonus ball
      // landing on a fresh rack after an opening strike rendered as its own
      // plain pin count, because this module derived glyphs from
      // is_strike/is_spare alone -- which can't tell a second fresh-rack
      // strike apart from an ordinary roll. It is now pure pass-through of
      // the server's own symbols.
      expect(
        frameCellSymbols(
          frame({
            number: 10,
            rolls: [10, 10, 4],
            is_strike: true,
            is_complete: true,
            roll_symbols: ['X', 'X', '4'],
          }),
        ),
      ).toEqual(['X', 'X', '4']);
    },
  );

  it('renders exactly what roll_symbols says, even if it disagreed with is_strike/is_spare', () => {
    // Proves this is pass-through, not derivation: a deliberately
    // inconsistent fixture still returns roll_symbols unchanged, never a
    // value recomputed from is_strike/is_spare.
    expect(
      frameCellSymbols(frame({ rolls: [10], is_strike: true, roll_symbols: ['7'] })),
    ).toEqual(['7']);
  });

  it('renders an empty array for a frame with no rolls yet', () => {
    expect(frameCellSymbols(frame({}))).toEqual([]);
  });
});

describe('formatScore', () => {
  it('renders null as an em dash', () => {
    expect(formatScore(null)).toBe('—');
  });

  it('renders a resolved score as a plain number', () => {
    expect(formatScore(34)).toBe('34');
  });

  it('renders zero as "0", not an em dash', () => {
    expect(formatScore(0)).toBe('0');
  });
});

describe('describeStandingPins', () => {
  it('describes a full rack', () => {
    expect(describeStandingPins([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])).toBe('All ten pins standing.');
  });

  it('describes an empty rack', () => {
    expect(describeStandingPins([])).toBe('No pins standing.');
  });

  it('lists a partial rack', () => {
    expect(describeStandingPins([1, 2, 4])).toBe('Pins standing: 1, 2, 4.');
  });
});
