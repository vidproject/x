/**
 * Lightweight ULID-like id generator: 26-character Crockford base32 string
 * of (timestamp_ms || randomness). Good enough for run identifiers without
 * pulling in a real ULID dependency.
 */

const ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'; // Crockford

export function newRunId(): string {
  const ts = Date.now();
  let tsPart = '';
  let t = ts;
  for (let i = 0; i < 10; i++) {
    tsPart = (ALPHABET[t % 32] ?? '0') + tsPart;
    t = Math.floor(t / 32);
  }
  const rand = crypto.getRandomValues(new Uint8Array(10));
  let randPart = '';
  for (const b of rand) randPart += ALPHABET[b % 32] ?? '0';
  return tsPart + randPart;
}

export function shortRunId(id: string): string {
  return id.slice(-8);
}
