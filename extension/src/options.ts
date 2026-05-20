/**
 * options.ts — settings page.
 *
 * Stores PAT and repo identifiers in `browser.storage.local`, verifies access
 * by calling /user and /repos/:owner/:repo, and surfaces the resulting
 * connection state.
 */

import { DEFAULT_SETTINGS } from './lib/config.js';
import {
  clearAll,
  getAccounts,
  getActivity,
  getArchiveSnapshot,
  getConnection,
  getCounters,
  getRunBuffers,
  getSettings,
  updateSettings,
} from './lib/storage.js';
import type { RuntimeMessage } from './lib/types.js';

const $ = <T extends HTMLElement = HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing element #${id}`);
  return el as T;
};

const form = $<HTMLFormElement>('settings-form');
const ownerInput = $<HTMLInputElement>('owner');
const repoInput = $<HTMLInputElement>('repo');
const branchInput = $<HTMLInputElement>('branch');
const patInput = $<HTMLInputElement>('pat');
const revealBtn = $<HTMLButtonElement>('reveal-pat');
const saveStatus = $<HTMLSpanElement>('save-status');
const connDetail = $('conn-detail');
const accountList = $<HTMLUListElement>('account-readonly');
const refreshBtn = $<HTMLButtonElement>('refresh-accounts');
const diagBox = $<HTMLTextAreaElement>('diagnostics');
const copyDiagBtn = $<HTMLButtonElement>('copy-diagnostics');
const clearStorageBtn = $<HTMLButtonElement>('clear-storage');
const diagStatus = $<HTMLSpanElement>('diag-status');

async function send<T>(msg: RuntimeMessage): Promise<T> {
  return browser.runtime.sendMessage(msg) as Promise<T>;
}

function setStatus(el: HTMLElement, kind: 'ok' | 'err' | 'warn' | '', msg: string): void {
  el.className = kind ? `status ${kind}` : 'status';
  el.textContent = msg;
}

function isWriteAuthError(message: string | null): boolean {
  return (
    typeof message === 'string' &&
    /Resource not accessible by personal access token|\/git\/blobs|\/git\/refs/i.test(message)
  );
}

async function loadSettings(): Promise<void> {
  const s = await getSettings();
  ownerInput.value = s.owner || DEFAULT_SETTINGS.owner;
  repoInput.value = s.repo || DEFAULT_SETTINGS.repo;
  branchInput.value = s.branch || DEFAULT_SETTINGS.branch;
  if (s.pat) {
    patInput.value = s.pat;
    patInput.placeholder = `…${s.pat.slice(-4)}`;
  }
  await paintConnDetail();
}

async function paintConnDetail(): Promise<void> {
  const conn = await getConnection();
  const s = await getSettings();
  const lines: string[] = [];
  lines.push(`Status: ${conn.status}`);
  if (conn.login) lines.push(`Logged in as: @${conn.login}`);
  lines.push(`Repository: ${s.owner || '?'}/${s.repo || '?'}@${s.branch || '?'}`);
  if (conn.checkedAt) lines.push(`Last checked: ${conn.checkedAt}`);
  if (conn.status === 'rate-limited' && conn.rateLimitResetAt !== null) {
    const resetIso = new Date(conn.rateLimitResetAt * 1000).toISOString();
    const deltaSec = conn.rateLimitResetAt - Math.floor(Date.now() / 1000);
    const inLabel =
      deltaSec <= 0
        ? 'momentarily'
        : deltaSec < 60
          ? `in ${deltaSec}s`
          : deltaSec < 3600
            ? `in ${Math.round(deltaSec / 60)}m`
            : `in ${Math.round(deltaSec / 3600)}h`;
    lines.push(`Rate limit resets: ${resetIso} (${inLabel})`);
  }
  if (conn.error) lines.push(`Error: ${conn.error}`);
  connDetail.textContent = lines.join('\n');
}

async function paintAccounts(): Promise<void> {
  const list = await getAccounts();
  accountList.replaceChildren();
  for (const a of list) {
    const li = document.createElement('li');
    const handle = document.createElement('span');
    handle.className = 'handle';
    handle.textContent = `@${a.handle}`;
    const label = document.createElement('span');
    label.className = 'label';
    label.textContent = a.label;
    li.append(handle, label);
    accountList.append(li);
  }
}

form.addEventListener('submit', async (e: Event) => {
  e.preventDefault();
  const pat = patInput.value.trim();
  const owner = ownerInput.value.trim();
  const repo = repoInput.value.trim();
  const branch = branchInput.value.trim() || 'main';
  if (!pat || !owner || !repo) {
    setStatus(saveStatus, 'err', 'All fields are required.');
    return;
  }
  setStatus(saveStatus, '', 'Saving…');
  await updateSettings({ pat, owner, repo, branch, configuredAt: Date.now() });
  setStatus(saveStatus, '', 'Verifying…');
  await send({ type: 'verify-connection' });
  await send({ type: 'refresh-accounts' });
  const conn = await getConnection();
  if (conn.status === 'ok') {
    setStatus(saveStatus, 'ok', `Connected as @${conn.login ?? '?'}.`);
  } else if (conn.status === 'auth-error') {
    setStatus(
      saveStatus,
      'err',
      isWriteAuthError(conn.error)
        ? 'PAT can read this repo but cannot write. Set Contents: Read & Write.'
        : 'Auth failed - check the PAT and its repo scope.'
    );
  } else if (conn.status === 'rate-limited') {
    setStatus(saveStatus, 'warn', 'Rate-limited — token is valid but GitHub is throttling.');
  } else if (conn.status === 'network-error') {
    setStatus(saveStatus, 'err', 'Network error — check connectivity.');
  } else {
    setStatus(saveStatus, 'err', `Verification: ${conn.status}`);
  }
  await paintConnDetail();
  await paintAccounts();
  await refreshDiag();
});

revealBtn.addEventListener('click', () => {
  if (patInput.type === 'password') {
    patInput.type = 'text';
    revealBtn.textContent = 'Hide';
  } else {
    patInput.type = 'password';
    revealBtn.textContent = 'Show';
  }
});

refreshBtn.addEventListener('click', async () => {
  refreshBtn.disabled = true;
  try {
    await send({ type: 'refresh-accounts' });
    await paintAccounts();
    setStatus(diagStatus, 'ok', 'Accounts refreshed.');
  } catch (err) {
    setStatus(diagStatus, 'err', `Refresh failed: ${(err as Error).message}`);
  } finally {
    refreshBtn.disabled = false;
  }
});

async function refreshDiag(): Promise<void> {
  const [settings, conn, accounts, counters, buffers, activity, archiveSnapshot] =
    await Promise.all([
      getSettings(),
      getConnection(),
      getAccounts(),
      getCounters(),
      getRunBuffers(),
      getActivity(),
      getArchiveSnapshot(),
    ]);
  const redactedBuffers = Object.fromEntries(
    Object.entries(buffers).map(([k, b]) => [
      k,
      {
        run_id: b.run_id,
        started_at: b.started_at,
        last_capture_at: b.last_capture_at,
        tweets_count: Object.keys(b.tweets_by_id).length,
        observed_count: b.tweet_ids_observed.length,
        endpoints_seen: b.endpoints_seen,
        source_url: b.source_url,
      },
    ])
  );
  const dump = {
    version: browser.runtime.getManifest().version,
    user_agent: navigator.userAgent,
    settings: {
      owner: settings.owner,
      repo: settings.repo,
      branch: settings.branch,
      autoCapture: settings.autoCapture,
      updateExisting: settings.updateExisting,
      configuredAt: settings.configuredAt,
      patSet: settings.pat.length > 0,
      patSuffix: settings.pat.length >= 4 ? settings.pat.slice(-4) : '',
    },
    connection: conn,
    accounts,
    counters,
    archiveSnapshot: archiveSnapshot
      ? {
          generated_at: archiveSnapshot.generated_at,
          fetched_at: archiveSnapshot.fetched_at,
          accounts: Object.keys(archiveSnapshot.accounts).length,
        }
      : null,
    runBuffers: redactedBuffers,
    activity_tail_size: activity.length,
    activity_recent: activity.slice(0, 30),
  };
  diagBox.value = JSON.stringify(dump, null, 2);
}

copyDiagBtn.addEventListener('click', async () => {
  await navigator.clipboard.writeText(diagBox.value);
  setStatus(diagStatus, 'ok', 'Copied diagnostics to clipboard.');
});

clearStorageBtn.addEventListener('click', async () => {
  const ok = window.confirm(
    'Clear extension storage? This erases your PAT, settings, counters, buffered captures, and activity tail. There is no undo.'
  );
  if (!ok) return;
  await clearAll();
  setStatus(diagStatus, 'warn', 'Storage cleared.');
  await loadSettings();
  await paintAccounts();
  await refreshDiag();
});

void loadSettings();
void paintAccounts();
void refreshDiag();
