import { describe, expect, it } from 'vitest';
import type { FrameStateResponse } from '../api/types';
import { describeStandingPins, formatScore, frameCellSymbols, rollSymbol } from './scoreDisplay';

function frame(overrides: Partial<FrameStateResponse>): FrameStateResponse {
  return {
    number: 1,
    rolls: [],
    is_strike: false,
    is_spare: false,
    is_complete: false,
    score: null,
    ...overrides,
  };
}

describe('rollSymbol', () => {
  it('shows a dash for a miss', () => {
    expect(rollSymbol(0)).toBe('-');
  });

  it('shows the plain pin count otherwise', () => {
    expect(rollSymbol(7)).toBe('7');
  });
});

describe('frameCellSymbols', () => {
  it('marks a strike as a single X', () => {
    expect(frameCellSymbols(frame({ rolls: [10], is_strike: true, is_complete: true }))).toEqual(['X']);
  });

  it('marks a spare on the second roll', () => {
    expect(frameCellSymbols(frame({ rolls: [7, 3], is_spare: true, is_complete: true }))).toEqual(['7', '/']);
  });

  it('shows a miss-then-spare as dash then slash', () => {
    expect(frameCellSymbols(frame({ rolls: [0, 10], is_spare: true, is_complete: true }))).toEqual(['-', '/']);
  });

  it('leaves an open frame as plain numbers', () => {
    expect(frameCellSymbols(frame({ rolls: [4, 3], is_complete: true, score: 7 }))).toEqual(['4', '3']);
  });

  it('shows a frame still waiting on ball 2 as just the first roll', () => {
    expect(frameCellSymbols(frame({ rolls: [6] }))).toEqual(['6']);
  });

  it('marks only the first roll of a tenth-frame strike sequence as X', () => {
    // The bonus balls' own fresh-rack status is a rack rule this module
    // deliberately doesn't re-derive — see the module docstring.
    expect(
      frameCellSymbols(frame({ number: 10, rolls: [10, 7, 2], is_strike: true, is_complete: true })),
    ).toEqual(['X', '7', '2']);
  });

  it("marks the tenth frame's spare bonus ball as its own plain number", () => {
    expect(
      frameCellSymbols(frame({ number: 10, rolls: [6, 4, 8], is_spare: true, is_complete: true })),
    ).toEqual(['6', '/', '8']);
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
