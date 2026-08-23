export const MAX_RELEASE_SEED = 2 ** 31 - 1;

export type ReleaseSeedParseResult =
  | { kind: 'empty' }
  | { kind: 'valid'; seed: number }
  | { kind: 'invalid'; message: string };

export function parseReleaseSeed(text: string): ReleaseSeedParseResult {
  const trimmed = text.trim();
  if (trimmed === '') {
    return { kind: 'empty' };
  }

  const seed = Number(trimmed);
  if (!Number.isSafeInteger(seed) || seed < 0 || seed > MAX_RELEASE_SEED) {
    return {
      kind: 'invalid',
      message: `Release seed must be a whole number from 0 through ${MAX_RELEASE_SEED}.`,
    };
  }
  return { kind: 'valid', seed };
}
