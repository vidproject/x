/**
 * crypto.ts — at-rest encryption for sensitive settings (the PAT).
 *
 * Threat model: an attacker who reads `browser.storage.local` (devtools, a
 * forgotten backup, a misconfigured profile sync) should not see a usable
 * GitHub PAT in plaintext. A determined attacker with the extension's source
 * can still derive the key — we make no claim of "real" cryptographic
 * confidentiality. The aim is to raise the bar so the PAT isn't a copyable
 * string sitting next to its key name in extension storage.
 *
 * Scheme:
 *   - On first encryption we generate a 32-byte salt and persist it in
 *     `browser.storage.local` under `__imm_archive_salt__`.
 *   - We derive an AES-GCM key by SHA-256'ing `salt || runtime.id || version
 *     || "imm-archive-pat-v1"`. The salt being per-install means two copies
 *     of the extension don't share a key. The runtime.id is the extension's
 *     UUID — stable for a given install but unknown to a remote attacker.
 *   - Plaintext is encrypted with a fresh 12-byte IV. The envelope format is
 *     `v1:<iv-b64>:<ciphertext-b64>` and is what gets stored.
 *
 * Backwards compatibility: an unprefixed value is treated as legacy
 * plaintext, decrypted to itself, and re-encrypted on the next write.
 */

const SALT_STORAGE_KEY = '__imm_archive_salt__';
const ENVELOPE_PREFIX = 'v1:';

let derivedKeyCache: CryptoKey | null = null;
let derivedKeyCacheSalt: string | null = null;

function toBase64(buf: Uint8Array): string {
  let s = '';
  for (const b of buf) s += String.fromCharCode(b);
  return btoa(s);
}

function fromBase64(s: string): Uint8Array {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function loadOrCreateSalt(): Promise<{ saltB64: string; salt: Uint8Array }> {
  const stored = await browser.storage.local.get(SALT_STORAGE_KEY);
  const v = stored[SALT_STORAGE_KEY];
  if (typeof v === 'string' && v.length > 0) {
    return { saltB64: v, salt: fromBase64(v) };
  }
  const salt = crypto.getRandomValues(new Uint8Array(32));
  const saltB64 = toBase64(salt);
  await browser.storage.local.set({ [SALT_STORAGE_KEY]: saltB64 });
  return { saltB64, salt };
}

async function deriveKey(): Promise<CryptoKey> {
  const { saltB64, salt } = await loadOrCreateSalt();
  if (derivedKeyCache !== null && derivedKeyCacheSalt === saltB64) {
    return derivedKeyCache;
  }
  const seed = new TextEncoder().encode(
    `${browser.runtime.id}|${browser.runtime.getManifest().version}|imm-archive-pat-v1`
  );
  const combined = new Uint8Array(seed.length + salt.length);
  combined.set(seed, 0);
  combined.set(salt, seed.length);
  const hash = await crypto.subtle.digest('SHA-256', combined);
  const key = await crypto.subtle.importKey('raw', hash, { name: 'AES-GCM' }, false, [
    'encrypt',
    'decrypt',
  ]);
  derivedKeyCache = key;
  derivedKeyCacheSalt = saltB64;
  return key;
}

export function isEncryptedPat(v: string): boolean {
  return v.startsWith(ENVELOPE_PREFIX);
}

export async function encryptPat(plaintext: string): Promise<string> {
  if (!plaintext) return '';
  const key = await deriveKey();
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv },
    key,
    new TextEncoder().encode(plaintext)
  );
  return `${ENVELOPE_PREFIX}${toBase64(iv)}:${toBase64(new Uint8Array(ct))}`;
}

export async function decryptPat(envelope: string): Promise<string> {
  if (!envelope) return '';
  // Legacy plaintext (pre-encryption installs): pass through. The next save
  // re-encrypts it via the storage layer.
  if (!envelope.startsWith(ENVELOPE_PREFIX)) return envelope;
  const parts = envelope.slice(ENVELOPE_PREFIX.length).split(':');
  if (parts.length !== 2) return '';
  const [ivB64, ctB64] = parts;
  if (!ivB64 || !ctB64) return '';
  try {
    const key = await deriveKey();
    const iv = fromBase64(ivB64);
    const ct = fromBase64(ctB64);
    const pt = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: iv as BufferSource },
      key,
      ct as BufferSource
    );
    return new TextDecoder().decode(pt);
  } catch {
    return '';
  }
}
