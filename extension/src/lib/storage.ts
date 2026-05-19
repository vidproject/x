import { DEFAULT_SETTINGS, FALLBACK_ACCOUNTS, ACTIVITY_TAIL_MAX } from './config.js';
import type {
  AccountConfig,
  AccountCounter,
  CanonicalTweet,
  ConnectionState,
  LogEvent,
  Settings,
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
  tweet_ids_observed: string[];
  endpoints_seen: string[];
  source_url: string | null;
}

interface StorageShape {
  settings: Settings;
  accounts: AccountConfig[];
  connection: ConnectionState;
  counters: Record<string, AccountCounter>;
  runBuffers: Record<string, RunBuffer>;
  activity: LogEvent[];
}

const DEFAULT_CONNECTION: ConnectionState = {
  status: 'unknown',
  login: null,
  checkedAt: null,
  error: null,
  defaultBranch: null,
  configuredBranchExists: null,
};

const DEFAULTS: StorageShape = {
  settings: { ...DEFAULT_SETTINGS },
  accounts: [...FALLBACK_ACCOUNTS],
  connection: { ...DEFAULT_CONNECTION },
  counters: {},
  runBuffers: {},
  activity: [],
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
  return getRaw('settings');
}
export async function setSettings(s: Settings): Promise<void> {
  await setRaw('settings', s);
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

// --- Connection state ----------------------------------------------------

export async function getConnection(): Promise<ConnectionState> {
  const c = await getRaw('connection');
  // Backfill fields added after the user's storage was written.
  const out: ConnectionState = {
    ...c,
    defaultBranch: c.defaultBranch === undefined ? null : c.defaultBranch,
    configuredBranchExists:
      c.configuredBranchExists === undefined ? null : c.configuredBranchExists,
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

// --- Full reset ----------------------------------------------------------

export async function clearAll(): Promise<void> {
  await browser.storage.local.clear();
}
