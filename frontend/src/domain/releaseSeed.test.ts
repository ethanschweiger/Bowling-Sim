import { describe, expect, it } from 'vitest';
import { MAX_RELEASE_SEED, parseReleaseSeed } from './releaseSeed';

describe('release seed parsing', () => {
  it('omits a blank seed so the backend can create one', () => {
    expect(parseReleaseSeed('   ')).toEqual({ kind: 'empty' });
  });

  it('accepts the generated-seed range exactly', () => {
    expect(parseReleaseSeed('0')).toEqual({ kind: 'valid', seed: 0 });
    expect(parseReleaseSeed(String(MAX_RELEASE_SEED))).toEqual({ kind: 'valid', seed: MAX_RELEASE_SEED });
  });

  it('rejects fractions, negative values, and values outside the bounded range', () => {
    for (const value of ['1.5', '-1', String(MAX_RELEASE_SEED + 1), 'not-a-number']) {
      expect(parseReleaseSeed(value)).toMatchObject({ kind: 'invalid' });
    }
  });
});
