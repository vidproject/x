import { DEFAULT_SETTINGS, FALLBACK_ACCOUNTS, ACTIVITY_TAIL_MAX } from './config.js';
import { decryptPat, encryptPat, isEncryptedPat } from './crypto.js';
import type {
  AccountConfig,
  AccountCounter,
  CanonicalTweet,
  ConnectionState,
  LogEvent,
  Settings,
  UnavailableTweet,
} from './types.js';

/**
 * Buffered tweets for a given account, awaiting commit. A "run" is one entry
 * in this map; flushing clears the entry. We store as Record so the structure
 * survives JSON serialization through browser.storage.
 */
export interface RunBuffer {
  run_id: string;
  account_handle: string;
  started_at: string;
  last_capture_at: string;
  tweets_by_id: Record<string, CanonicalTweet>;
  unavailable_by_id?: Record<string, UnavailableTweet>;
  tweet_ids_observed: string[];
  endpoints_seen: string[];
  source_url: string | null;
}

/** Per-tweet committed-state index, keyed by handle then tweet_id.
 * Used to skip re-capturing tweets we've already pushed with identical
 * engagement counts — avoids generating one new raw/* file every browse. */
export interface CommittedEntry {
  ts: string; // ISO timestamp of the last commit
  sig: string; // engagement signature: "like|rt|reply|quote"
}

/** Progress + inflight tracking for a queue-driven loop (refetch / media-crawl).
 * The loop persists this so a SW eviction in the middle of a run preserves
 * "X of Y processed" — and the wait-for-ingest guard knows what to expect.
 */
export interface LoopSession {
  /** Initial queue size when the user clicked "Start". */
  totalAtStart: number;
  /** Tweets processed (ingested OR dropped after retries) so far. */
  processed: number;
  /** ISO of when this session started. */
  startedAt: string;
  /** Tweet currently inflight — we navigated to it and are waiting for the
   * page-hook to capture the response before advancing. null between ticks
   * (and once an ingest has been observed). */
  inflight: { tweetId: string; navigatedAt: string } | null;
}

/** Auto-scroll session: counts of ticks and tweets ingested since the user
 * clicked Start. Persisted so SW eviction during a run keeps progress. */
export interface AutoScrollSession {
  startedAt: string;
  scrollCount: number;
  ingestedCount: number;
  expandedCount: number;
}

interface StorageShape {
  settings: Settings;
  accounts: AccountConfig[];
  /** ISO timestamp of the last successful accounts.yaml fetch from the repo.
   * Used by `refreshAccountsList` to skip re-fetching on every SW wake — the
   * file changes rarely and an authenticated raw fetch every wake adds up. */
  accountsRefreshedAt: string | null;
  connection: ConnectionState;
  counters: Record<string, AccountCounter>;
  runBuffers: Record<string, RunBuffer>;
  activity: LogEvent[];
  committedIndex: Record<string, Record<string, CommittedEntry>>;
  /** Tweets the normalizer flagged as truncated and that we haven't seen a
   * full-text version of yet. Keyed by handle → array of tweet ids. */
  refetchQueue: Record<string, string[]>;
  /** Tweet IDs seen via Media-tab / partial captures that couldn't be
   * normalized into a full canonical tweet. Keyed by hint-handle (or
   * "_unknown") → array of tweet ids. The user can drive a crawl that
   * opens each detail page to capture the real thing. */
  mediaCrawlQueue: Record<string, string[]>;
  refetchSession: LoopSession | null;
  mediaCrawlSession: LoopSession | null;
  autoScrollSession: AutoScrollSession | null;
}

const DEFAULT_CONNECTION: ConnectionState = {
  status: 'unknown',
  login: null,
  checkedAt: null,
  error: null,
  defaultBranch: null,
  configuredBranchExists: null,
  rateLimitResetAt: null,
};

const DEFAULTS: StorageShape = {
  settings: { ...DEFAULT_SETTINGS },
  accounts: [...FALLBACK_ACCOUNTS],
  accountsRefreshedAt: null,
  connection: { ...DEFAULT_CONNECTION },
  counters: {},
  runBuffers: {},
  activity: [],
  committedIndex: {},
  refetchQueue: {},
  mediaCrawlQueue: {},
  refetchSession: null,
  mediaCrawlSession: null,
  autoScrollSession: null,
};

async function getRaw<K extends keyof StorageShape>(key: K): Promise<StorageShape[K]> {
  const result = await browser.storage.local.get(key);
  const value = result[key];
  if (value === undefined) {
    return structuredClone(DEFAULTS[key]);
  }
  return value as StorageShape[K];
}

async function setRaw<K extends keyof StorageShape>(key: K, value: StorageShape[K]): Promise<void> {
  await browser.storage.local.set({ [key]: value });
}

// --- Settings -------------------------------------------------------------

export async function getSettings(): Promise<Settings> {
  // Backfill any keys the user's stored settings predate — readers can rely
  // on every Settings field being present rather than `?? defaulting` at
  // every callsite. The PAT is stored encrypted-at-rest as of v0.2.0; we
  // decrypt transparently so callers continue to see plaintext.
  const stored = await getRaw('settings');
  const merged = { ...DEFAULT_SETTINGS, ...stored };
  if (merged.pat && isEncryptedPat(merged.pat)) {
    try {
      merged.pat = await decryptPat(merged.pat);
    } catch {
      merged.pat = '';
    }
  }
  return merged;
}
export async function setSettings(s: Settings): Promise<void> {
  // Encrypt the PAT before persisting; legacy plaintext PATs get upgraded
  // on the next save.
  const toWrite: Settings = { ...s };
  if (toWrite.pat && !isEncryptedPat(toWrite.pat)) {
    toWrite.pat = await encryptPat(toWrite.pat);
  }
  await setRaw('settings', toWrite);
}
export async function updateSettings(patch: Partial<Settings>): Promise<Settings> {
  const cur = await getSettings();
  const next = { ...cur, ...patch };
  await setSettings(next);
  return next;
}

// --- Accounts -------------------------------------------------------------

export async function getAccounts(): Promise<AccountConfig[]> {
  return getRaw('accounts');
}
export async function setAccounts(a: AccountConfig[]): Promise<void> {
  await setRaw('accounts', a);
}
export async function getAccountsRefreshedAt(): Promise<string | null> {
  return getRaw('accountsRefreshedAt');
}
export async function setAccountsRefreshedAt(iso: string | null): Promise<void> {
  await setRaw('accountsRefreshedAt', iso);
}

// --- Connection state ----------------------------------------------------

export async function getConnection(): Promise<ConnectionState> {
  const c = await getRaw('connection');
  // Backfill fields added after the user's storage was written.
  const out: ConnectionState = {
    ...c,
    defaultBranch: c.defaultBranch === undefined ? null : c.defaultBranch,
    configuredBranchExists:
      c.configuredBranchExists === undefined ? null : c.configuredBranchExists,
    rateLimitResetAt: c.rateLimitResetAt === undefined ? null : c.rateLimitResetAt,
  };
  return out;
}
export async function setConnection(c: ConnectionState): Promise<void> {
  await setRaw('connection', c);
}

// --- Counters -------------------------------------------------------------

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

export async function getCounters(): Promise<Record<string, AccountCounter>> {
  return getRaw('counters');
}
export async function bumpCounter(
  handle: string,
  patch: Partial<AccountCounter>
): Promise<AccountCounter> {
  const all = await getCounters();
  const cur = all[handle] ?? {
    todayCount: 0,
    todayDate: todayISO(),
    lastCaptureAt: null,
    totalCommitted: 0,
    bufferedCount: 0,
  };
  if (cur.todayDate !== todayISO()) {
    cur.todayDate = todayISO();
    cur.todayCount = 0;
  }
  const next: AccountCounter = { ...cur, ...patch };
  all[handle] = next;
  await setRaw('counters', all);
  return next;
}

export async function setBufferedCount(handle: string, count: number): Promise<void> {
  await bumpCounter(handle, { bufferedCount: count });
}

// --- Run buffers (the pending captures awaiting commit) ------------------

export async function getRunBuffers(): Promise<Record<string, RunBuffer>> {
  return getRaw('runBuffers');
}

export async function getRunBuffer(handle: string): Promise<RunBuffer | null> {
  const all = await getRunBuffers();
  return all[handle] ?? null;
}

export async function setRunBuffer(handle: string, buf: RunBuffer): Promise<void> {
  const all = await getRunBuffers();
  all[handle] = buf;
  await setRaw('runBuffers', all);
}

export async function clearRunBuffer(handle: string): Promise<void> {
  const all = await getRunBuffers();
  delete all[handle];
  await setRaw('runBuffers', all);
}

// --- Activity tail --------------------------------------------------------

export async function getActivity(): Promise<LogEvent[]> {
  return getRaw('activity');
}

export async function appendActivity(ev: LogEvent): Promise<LogEvent[]> {
  const cur = await getActivity();
  cur.unshift(ev);
  if (cur.length > ACTIVITY_TAIL_MAX) cur.length = ACTIVITY_TAIL_MAX;
  await setRaw('activity', cur);
  return cur;
}

export async function clearActivity(): Promise<void> {
  await setRaw('activity', []);
}

// --- Committed-tweets dedup index ----------------------------------------

export async function getCommittedIndex(): Promise<Record<string, Record<string, CommittedEntry>>> {
  return getRaw('committedIndex');
}

export async function setCommittedIndex(
  idx: Record<string, Record<string, CommittedEntry>>
): Promise<void> {
  await setRaw('committedIndex', idx);
}

/** Compute the engagement signature we use to decide whether a re-captured
 * tweet is "materially different" from what we last committed. We omit
 * view_count because it ticks up frequently on active tweets and would
 * defeat the dedup. Likes / RTs / replies / quotes change rarely enough that
 * each change is worth a re-commit (and an engagement_history snapshot).
 * `is_truncated` is folded in so a refetch that swaps the 280-char head for
 * the full `note_tweet` body bypasses the dedup even when engagement counts
 * haven't moved — otherwise the new body would be silently dropped. */
export function engagementSig(
  likes: number,
  retweets: number,
  replies: number,
  quotes: number,
  isTruncated: boolean = false
): string {
  return `${likes}|${retweets}|${replies}|${quotes}|${isTruncated ? 't' : 'f'}`;
}

/** Drop entries older than `maxAgeMs`. Returns the number pruned. */
export async function pruneCommittedIndex(maxAgeMs: number): Promise<number> {
  const idx = await getCommittedIndex();
  const cutoff = new Date(Date.now() - maxAgeMs).toISOString();
  let pruned = 0;
  for (const handle of Object.keys(idx)) {
    const inner = idx[handle];
    if (!inner) continue;
    for (const tid of Object.keys(inner)) {
      const e = inner[tid];
      if (e && e.ts < cutoff) {
        delete inner[tid];
        pruned += 1;
      }
    }
    if (Object.keys(inner).length === 0) delete idx[handle];
  }
  if (pruned > 0) await setCommittedIndex(idx);
  return pruned;
}

// --- Refetch queue (tweets needing full-text recapture) -----------------

export async function getRefetchQueue(): Promise<Record<string, string[]>> {
  return getRaw('refetchQueue');
}

export async function refetchQueueTotal(): Promise<number> {
  const q = await getRefetchQueue();
  let total = 0;
  for (const ids of Object.values(q)) total += ids.length;
  return total;
}

export async function enqueueRefetch(handle: string, tweetId: string): Promise<void> {
  const q = await getRefetchQueue();
  const ids = q[handle] ?? [];
  if (!ids.includes(tweetId)) {
    ids.push(tweetId);
    q[handle] = ids;
    await setRaw('refetchQueue', q);
  }
}

export async function dequeueRefetch(handle: string, tweetId: string): Promise<void> {
  const q = await getRefetchQueue();
  const ids = q[handle];
  if (!ids) return;
  const filtered = ids.filter((id) => id !== tweetId);
  if (filtered.length === 0) delete q[handle];
  else q[handle] = filtered;
  await setRaw('refetchQueue', q);
}

export async function nextRefetchTarget(): Promise<{
  handle: string;
  tweetId: string;
} | null> {
  const q = await getRefetchQueue();
  for (const [handle, ids] of Object.entries(q)) {
    if (ids.length > 0) return { handle, tweetId: ids[0]! };
  }
  return null;
}

// --- Media-crawl queue (partial captures awaiting detail-page fetch) ----

export async function getMediaCrawlQueue(): Promise<Record<string, string[]>> {
  return getRaw('mediaCrawlQueue');
}

export async function mediaCrawlQueueTotal(): Promise<number> {
  const q = await getMediaCrawlQueue();
  let total = 0;
  for (const ids of Object.values(q)) total += ids.length;
  return total;
}

export async function enqueueMediaCrawl(hintHandle: string | null, tweetId: string): Promise<void> {
  const q = await getMediaCrawlQueue();
  const bucket = hintHandle ?? '_unknown';
  const ids = q[bucket] ?? [];
  if (!ids.includes(tweetId)) {
    ids.push(tweetId);
    q[bucket] = ids;
    await setRaw('mediaCrawlQueue', q);
  }
}

export async function dequeueMediaCrawl(tweetId: string): Promise<void> {
  const q = await getMediaCrawlQueue();
  let changed = false;
  for (const bucket of Object.keys(q)) {
    const ids = q[bucket];
    if (!ids) continue;
    const filtered = ids.filter((id) => id !== tweetId);
    if (filtered.length !== ids.length) {
      changed = true;
      if (filtered.length === 0) delete q[bucket];
      else q[bucket] = filtered;
    }
  }
  if (changed) await setRaw('mediaCrawlQueue', q);
}

export async function nextMediaCrawlTarget(): Promise<{
  bucket: string;
  tweetId: string;
} | null> {
  const q = await getMediaCrawlQueue();
  for (const [bucket, ids] of Object.entries(q)) {
    if (ids.length > 0) return { bucket, tweetId: ids[0]! };
  }
  return null;
}

/** Has this tweet id already been committed (any handle)? Used to skip
 * enqueueing media-crawl targets we've already archived. */
export async function isCommitted(tweetId: string): Promise<boolean> {
  const idx = await getCommittedIndex();
  for (const inner of Object.values(idx)) {
    if (inner && tweetId in inner) return true;
  }
  return false;
}

// --- Loop session state (progress + inflight gating) ---------------------

export async function getRefetchSession(): Promise<LoopSession | null> {
  return getRaw('refetchSession');
}
export async function setRefetchSession(s: LoopSession | null): Promise<void> {
  await setRaw('refetchSession', s);
}
export async function getMediaCrawlSession(): Promise<LoopSession | null> {
  return getRaw('mediaCrawlSession');
}
export async function setMediaCrawlSession(s: LoopSession | null): Promise<void> {
  await setRaw('mediaCrawlSession', s);
}
export async function getAutoScrollSession(): Promise<AutoScrollSession | null> {
  return getRaw('autoScrollSession');
}
export async function setAutoScrollSession(s: AutoScrollSession | null): Promise<void> {
  await setRaw('autoScrollSession', s);
}

// --- Purge: unrelated buffers, counters, queue entries -------------------

/**
 * Drop every per-handle bit of state (counters, run buffer, committed-index
 * entries, refetch / media-crawl queue entries) that doesn't correspond to a
 * tracked account. Returns a summary describing what was cleared so the
 * caller can log it.
 *
 * Tracked accounts are passed in (caller resolves from the live config).
 */
export async function purgeUnrelatedState(targeted: ReadonlySet<string>): Promise<{
  countersRemoved: number;
  buffersRemoved: number;
  committedHandlesRemoved: number;
  refetchHandlesRemoved: number;
  mediaCrawlIdsRemoved: number;
}> {
  const targLower = new Set([...targeted].map((h) => h.toLowerCase()));
  const isTargeted = (h: string): boolean => targLower.has(h.toLowerCase());

  // Counters
  const counters = await getCounters();
  let countersRemoved = 0;
  for (const h of Object.keys(counters)) {
    if (!isTargeted(h)) {
      delete counters[h];
      countersRemoved += 1;
    }
  }
  await setRaw('counters', counters);

  // Run buffers
  const buffers = await getRunBuffers();
  let buffersRemoved = 0;
  for (const h of Object.keys(buffers)) {
    if (!isTargeted(h)) {
      delete buffers[h];
      buffersRemoved += 1;
    }
  }
  await setRaw('runBuffers', buffers);

  // Committed dedup index
  const idx = await getCommittedIndex();
  let committedHandlesRemoved = 0;
  for (const h of Object.keys(idx)) {
    if (!isTargeted(h)) {
      delete idx[h];
      committedHandlesRemoved += 1;
    }
  }
  await setCommittedIndex(idx);

  // Refetch queue: buckets are keyed by handle.
  const rq = await getRefetchQueue();
  let refetchHandlesRemoved = 0;
  for (const h of Object.keys(rq)) {
    if (!isTargeted(h)) {
      delete rq[h];
      refetchHandlesRemoved += 1;
    }
  }
  await setRaw('refetchQueue', rq);

  // Media-crawl queue: buckets are hint-handles (or "_unknown"). Drop
  // entries whose bucket isn't targeted — including "_unknown" since we
  // can't verify relation.
  const mq = await getMediaCrawlQueue();
  let mediaCrawlIdsRemoved = 0;
  for (const h of Object.keys(mq)) {
    if (!isTargeted(h)) {
      mediaCrawlIdsRemoved += (mq[h] ?? []).length;
      delete mq[h];
    }
  }
  await setRaw('mediaCrawlQueue', mq);

  return {
    countersRemoved,
    buffersRemoved,
    committedHandlesRemoved,
    refetchHandlesRemoved,
    mediaCrawlIdsRemoved,
  };
}

// --- Full reset ----------------------------------------------------------

export async function clearAll(): Promise<void> {
  await browser.storage.local.clear();
}
