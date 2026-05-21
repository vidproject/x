/**
 * sidebar.ts — UI for the persistent sidebar.
 *
 * Sidebars in Firefox stay open across tab/window switches, which is the
 * intended persistence model. This module renders state pushed from the
 * background service worker and forwards user commands back.
 */

import { ACTIVITY_TAIL_MAX } from './lib/config.js';
import type { ExtensionState, LogEvent, RuntimeMessage, TweetSighting } from './lib/types.js';

const $ = <T extends HTMLElement = HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing element #${id}`);
  return el as T;
};

const connDot = $('conn-dot');
const connText = $<HTMLSpanElement>('conn-text');
const accountList = $<HTMLUListElement>('account-list');
const recentTweetList = $<HTMLUListElement>('recent-tweet-list');
const activityList = $<HTMLUListElement>('activity-list');
const autoToggle = $<HTMLInputElement>('auto-capture');
const updateExistingToggle = $<HTMLInputElement>('update-existing');
const lowBandwidthToggle = $<HTMLInputElement>('low-bandwidth');
const captureAllBtn = $<HTMLButtonElement>('capture-all');
const captureThisBtn = $<HTMLButtonElement>('capture-this');
const flushBtn = $<HTMLButtonElement>('flush-all');
const optionsLink = $<HTMLAnchorElement>('open-options');
const viewerLink = $<HTMLAnchorElement>('open-viewer');
const clearActBtn = $<HTMLButtonElement>('clear-activity');
const extVersionEl = $<HTMLSpanElement>('ext-version');
const masterSwitch = $<HTMLInputElement>('master-switch');
const masterLabel = $<HTMLSpanElement>('master-label');
const autoScrollInterval = $<HTMLInputElement>('auto-scroll-interval');
const autoScrollSecsEl = $<HTMLSpanElement>('auto-scroll-secs');
const autoScrollStatus = $<HTMLSpanElement>('auto-scroll-status');
const autoScrollStartBtn = $<HTMLButtonElement>('auto-scroll-start');
const autoScrollCancelBtn = $<HTMLButtonElement>('auto-scroll-cancel');
const autoScrollProgress = $<HTMLSpanElement>('auto-scroll-progress');
const refetchSection = $<HTMLElement>('refetch-section');
const refetchCount = $<HTMLSpanElement>('refetch-count');
const refetchStartBtn = $<HTMLButtonElement>('refetch-start');
const refetchCancelBtn = $<HTMLButtonElement>('refetch-cancel');
const refetchProgress = $<HTMLSpanElement>('refetch-progress');
const mediaCrawlSection = $<HTMLElement>('media-crawl-section');
const mediaCrawlCount = $<HTMLSpanElement>('media-crawl-count');
const mediaCrawlStartBtn = $<HTMLButtonElement>('media-crawl-start');
const mediaCrawlCancelBtn = $<HTMLButtonElement>('media-crawl-cancel');
const mediaCrawlProgress = $<HTMLSpanElement>('media-crawl-progress');
const threadOpenSection = $<HTMLElement>('thread-open-section');
const threadOpenCount = $<HTMLSpanElement>('thread-open-count');
const threadOpenStartBtn = $<HTMLButtonElement>('thread-open-start');
const threadOpenCancelBtn = $<HTMLButtonElement>('thread-open-cancel');
const threadOpenProgress = $<HTMLSpanElement>('thread-open-progress');
const purgeLink = $<HTMLAnchorElement>('purge-unrelated');

let lastState: ExtensionState | null = null;

async function send<T>(msg: RuntimeMessage): Promise<T> {
  return browser.runtime.sendMessage(msg) as Promise<T>;
}

function setConnStatus(state: ExtensionState): void {
  const { connection, settings } = state;
  let cls = '';
  let text = '';
  // Only warn when the configured branch genuinely doesn't exist on the
  // remote. A non-default branch that exists is fine — capturing into a
  // working branch is routine — so the mere `!==` to default_branch is not
  // a problem and shouldn't shout in the header.
  const branchMissing =
    connection.status === 'ok' && settings.branch && connection.configuredBranchExists === false;
  if (!settings.patSet) {
    cls = 'warn';
    text = 'Not configured — open Settings';
  } else if (branchMissing) {
    cls = 'err';
    text = `Branch "${settings.branch}" does not exist on ${settings.owner}/${settings.repo} — commits will 422. Set branch to "${connection.defaultBranch ?? 'master'}" in Settings.`;
  } else if (connection.status === 'ok') {
    cls = 'ok';
    text = `Connected as @${connection.login ?? '?'} to ${settings.owner}/${settings.repo} · …${settings.patSuffix}`;
  } else if (connection.status === 'auth-error') {
    cls = 'err';
    text = isWriteAuthError(connection.error)
      ? 'PAT can read repo but cannot write - set Contents: Read & Write'
      : 'Auth error - check your PAT';
  } else if (connection.status === 'rate-limited') {
    cls = 'warn';
    const resets = fmtRateLimitReset(connection.rateLimitResetAt);
    text = resets
      ? `GitHub rate-limited — resets ${resets}`
      : 'GitHub rate-limited — captures will retry';
  } else if (connection.status === 'network-error') {
    cls = 'err';
    text = 'Network error — check connectivity';
  } else if (connection.status === 'not-configured') {
    cls = 'warn';
    text = 'Not configured — open Settings';
  } else {
    cls = '';
    text = 'Checking…';
  }
  connDot.className = `dot ${cls}`;
  connText.textContent = text;
  connText.title = connection.error ?? '';
}

function fmtRel(iso: string | null): string {
  if (!iso) return '—';
  const d = Date.parse(iso);
  if (!Number.isFinite(d)) return '—';
  const secs = Math.max(0, Math.round((Date.now() - d) / 1000));
  if (secs < 5) return 'just now';
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

function fmtRateLimitReset(epochSec: number | null): string {
  if (epochSec === null) return '';
  const deltaSec = epochSec - Math.floor(Date.now() / 1000);
  if (deltaSec <= 0) return 'momentarily';
  const wallClock = new Date(epochSec * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  });
  if (deltaSec < 60) return `in ${deltaSec}s (${wallClock})`;
  const mins = Math.round(deltaSec / 60);
  if (mins < 60) return `in ${mins}m (${wallClock})`;
  const hours = Math.round(mins / 60);
  return `in ${hours}h (${wallClock})`;
}

function fmtNum(n: number): string {
  return n.toLocaleString('en-US');
}

function isWriteAuthError(message: string | null): boolean {
  return (
    typeof message === 'string' &&
    /Resource not accessible by personal access token|\/git\/blobs|\/git\/refs/i.test(message)
  );
}

function renderAccounts(state: ExtensionState): void {
  accountList.replaceChildren();
  if (state.accounts.length === 0) {
    const li = document.createElement('li');
    li.className = 'empty';
    li.textContent = 'No accounts configured.';
    accountList.append(li);
    return;
  }
  for (const a of state.accounts) {
    const c = state.counters[a.handle];
    const li = document.createElement('li');
    li.className = c && c.bufferedCount > 0 ? 'acc pending' : 'acc';

    const head = document.createElement('div');
    head.className = 'acc-head';
    const handle = document.createElement('span');
    handle.className = 'acc-handle';
    handle.innerHTML = `<span class="at">@</span>${escapeHtml(a.handle)}`;
    const meta = document.createElement('span');
    meta.className = 'acc-meta';
    meta.textContent = a.label;
    head.append(handle, meta);

    const row = document.createElement('div');
    row.className = 'acc-row';
    const stats = document.createElement('span');
    stats.className = 'acc-stats';
    const today = c?.todayCount ?? 0;
    const buffered = c?.bufferedCount ?? 0;
    const last = c?.lastCaptureAt ?? null;
    stats.innerHTML =
      `<span class="num">${fmtNum(today)}</span> today` +
      (buffered > 0 ? ` · <span class="num">${fmtNum(buffered)}</span> buffered` : '') +
      ` · last ${escapeHtml(fmtRel(last))}`;

    const actions = document.createElement('span');
    actions.className = 'acc-action';
    const btn = document.createElement('button');
    btn.className = 'btn';
    btn.textContent = 'Capture now';
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        await send({ type: 'capture-now', handle: a.handle });
      } finally {
        btn.disabled = false;
      }
    });
    actions.append(btn);

    row.append(stats, actions);
    li.append(head, row);
    accountList.append(li);
  }
}

function renderActivity(events: LogEvent[]): void {
  activityList.replaceChildren();
  if (events.length === 0) {
    const li = document.createElement('li');
    li.className = 'empty';
    li.textContent = 'No activity yet.';
    activityList.append(li);
    return;
  }
  for (const ev of events.slice(0, ACTIVITY_TAIL_MAX)) {
    const li = document.createElement('li');
    li.className = `ev ${ev.level}`;
    const ts = document.createElement('span');
    ts.className = 'ev-ts';
    ts.textContent = new Date(ev.ts).toLocaleTimeString('en-US', { hour12: false });
    const icon = document.createElement('span');
    icon.className = 'ev-icon';
    icon.textContent = ev.level === 'error' ? '✗' : ev.level === 'warn' ? '!' : '·';
    const body = document.createElement('span');
    const msg = document.createElement('div');
    msg.className = 'ev-msg';
    msg.textContent = ev.msg;
    body.append(msg);
    const ctxKeys = Object.keys(ev.context);
    if (ctxKeys.length > 0) {
      const ctx = document.createElement('div');
      ctx.className = 'ev-ctx';
      ctx.textContent = ctxKeys.map((k) => `${k}=${stringifyShort(ev.context[k])}`).join(' · ');
      body.append(ctx);
    }
    li.append(ts, icon, body);
    activityList.append(li);
  }
}

function stringifyShort(v: unknown): string {
  if (v === null) return 'null';
  if (typeof v === 'string') return v.length > 80 ? `${v.slice(0, 80)}…` : v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  try {
    const s = JSON.stringify(v);
    return s.length > 80 ? `${s.slice(0, 80)}…` : s;
  } catch {
    return '?';
  }
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    c === '&' ? '&amp;' : c === '<' ? '&lt;' : c === '>' ? '&gt;' : c === '"' ? '&quot;' : '&#39;'
  );
}

async function refreshState(): Promise<void> {
  try {
    lastState = await send<ExtensionState>({ type: 'get-state' });
    paint(lastState);
  } catch (err) {
    console.warn('[imm-archive] sidebar refreshState failed', err);
  }
}

function paint(state: ExtensionState): void {
  extVersionEl.textContent = state.version;
  setConnStatus(state);
  autoToggle.checked = state.settings.autoCapture;
  updateExistingToggle.checked = state.settings.updateExisting !== false;
  lowBandwidthToggle.checked = state.settings.lowBandwidthBrowsing === true;
  viewerLink.href = `https://${state.settings.owner}.github.io/${state.settings.repo}/`;
  paintMasterSwitch(state);
  renderAccounts(state);
  renderRecentTweets(state.recentTweetSightings);
  paintAutoScroll(state);
  paintMediaCrawl(state);
  paintRefetch(state);
  paintThreadOpen(state);
}

function renderRecentTweets(rows: TweetSighting[]): void {
  recentTweetList.replaceChildren();
  if (rows.length === 0) {
    const li = document.createElement('li');
    li.className = 'empty';
    li.textContent = 'No tweets seen yet.';
    recentTweetList.append(li);
    return;
  }
  for (const row of rows.slice(0, 40)) {
    const li = document.createElement('li');
    li.className = `recent-tweet ${row.archive_status}`;

    const top = document.createElement('div');
    top.className = 'tweet-head';
    const link = document.createElement('a');
    link.className = 'tweet-handle';
    link.href = row.tweet_url;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = `@${row.account_handle}`;
    const status = document.createElement('span');
    status.className = `tweet-pill ${row.archive_status}`;
    status.textContent = row.archive_status === 'saved' ? 'saved' : 'new';
    top.append(link, status);

    const text = document.createElement('div');
    text.className = 'tweet-text';
    text.textContent = truncateText(row.text, 140);

    const meta = document.createElement('div');
    meta.className = 'tweet-meta';
    meta.textContent = `${formatTweetAction(row.action)} · ${row.tweet_type} · posted ${fmtRel(row.posted_at)}`;

    li.append(top, text, meta);
    recentTweetList.append(li);
  }
}

function formatTweetAction(action: TweetSighting['action']): string {
  if (action === 'buffered') return 'buffered';
  if (action === 'unchanged') return 'unchanged';
  return 'skipped';
}

function truncateText(text: string, max: number): string {
  const compact = text.replace(/\s+/g, ' ').trim();
  return compact.length <= max ? compact : `${compact.slice(0, max - 1)}…`;
}

function paintMediaCrawl(state: ExtensionState): void {
  const q = state.mediaCrawlQueue;
  const total = q.total;
  const running = q.running;
  mediaCrawlSection.hidden = total === 0 && !running;
  mediaCrawlCount.textContent =
    total === 0
      ? running
        ? 'finishing…'
        : '0 queued'
      : `${fmtNum(total)} queued${running ? ' · running' : ''}`;
  mediaCrawlStartBtn.hidden = running;
  mediaCrawlStartBtn.disabled = total === 0;
  mediaCrawlCancelBtn.hidden = !running;
  paintQueueProgress(mediaCrawlProgress, q);
}

function paintQueueProgress(
  el: HTMLSpanElement,
  q: { processed: number; total_at_start: number; running: boolean; total: number }
): void {
  if (!q.running && q.processed === 0) {
    el.hidden = true;
    return;
  }
  // Denominator: max of initial total and current processed+remaining so the
  // bar still makes sense if new entries were enqueued mid-run.
  const denom = Math.max(q.total_at_start, q.processed + q.total);
  el.hidden = false;
  el.textContent = `${fmtNum(q.processed)} / ${fmtNum(denom)} processed`;
  el.className = q.running ? 'progress on' : 'progress';
}

function paintMasterSwitch(state: ExtensionState): void {
  const on = state.settings.enabled !== false;
  masterSwitch.checked = on;
  masterLabel.textContent = on ? 'ON' : 'PAUSED';
  document.body.classList.toggle('paused', !on);
}

function paintAutoScroll(state: ExtensionState): void {
  const secs = state.settings.autoScrollIntervalSec;
  autoScrollInterval.value = String(secs);
  autoScrollSecsEl.textContent = String(secs);
  const as = state.autoScroll;
  autoScrollStartBtn.hidden = as.active;
  autoScrollCancelBtn.hidden = !as.active;
  if (as.active) {
    const n = as.tabCount;
    autoScrollStatus.textContent =
      n === 0 ? `running — no X tabs open` : `running — ${n} ${n === 1 ? 'tab' : 'tabs'}`;
    autoScrollStatus.className = 'muted on';
  } else {
    autoScrollStatus.textContent = 'idle';
    autoScrollStatus.className = 'muted';
  }
  if (as.active || as.scrollCount > 0 || as.ingestedCount > 0) {
    autoScrollProgress.hidden = false;
    const parts = [
      `${fmtNum(as.scrollCount)} scrolls`,
      `${fmtNum(as.ingestedNewCount)} new buffered`,
      `${fmtNum(as.ingestedExistingCount)} old buffered`,
    ];
    if (as.skippedOldCount > 0) parts.push(`${fmtNum(as.skippedOldCount)} old skipped`);
    parts.push(`${fmtNum(as.expandedCount)} expanded`);
    autoScrollProgress.textContent = parts.join(' · ');
    autoScrollProgress.className = as.active ? 'progress on' : 'progress';
  } else {
    autoScrollProgress.hidden = true;
  }
}

function paintRefetch(state: ExtensionState): void {
  const q = state.refetchQueue;
  const total = q.total;
  const running = q.running;
  refetchSection.hidden = total === 0 && !running;
  refetchCount.textContent =
    total === 0
      ? running
        ? 'finishing…'
        : '0 queued'
      : `${fmtNum(total)} queued${running ? ' · running' : ''}`;
  refetchStartBtn.hidden = running;
  refetchStartBtn.disabled = total === 0;
  refetchCancelBtn.hidden = !running;
  paintQueueProgress(refetchProgress, q);
}

function paintThreadOpen(state: ExtensionState): void {
  const q = state.threadOpenQueue;
  const total = q.total;
  const running = q.running;
  threadOpenSection.hidden = total === 0 && !running;
  threadOpenCount.textContent =
    total === 0
      ? running
        ? 'finishing...'
        : '0 queued'
      : `${fmtNum(total)} queued${running ? ' · running' : ''}`;
  threadOpenStartBtn.hidden = running;
  threadOpenStartBtn.disabled = total === 0;
  threadOpenCancelBtn.hidden = !running;
  paintQueueProgress(threadOpenProgress, q);
}

async function refreshActivity(): Promise<void> {
  // Pull straight from storage on first render; live updates come via
  // `log-event` broadcasts.
  const stored = await browser.storage.local.get('activity');
  const events = (stored.activity as LogEvent[] | undefined) ?? [];
  renderActivity(events);
}

// --- Wire up --------------------------------------------------------------

captureAllBtn.addEventListener('click', async () => {
  captureAllBtn.disabled = true;
  try {
    await send({ type: 'capture-all' });
  } finally {
    captureAllBtn.disabled = false;
  }
});

captureThisBtn.addEventListener('click', async () => {
  captureThisBtn.disabled = true;
  try {
    await send({ type: 'capture-this-page' });
  } finally {
    captureThisBtn.disabled = false;
  }
});

flushBtn.addEventListener('click', async () => {
  flushBtn.disabled = true;
  try {
    await send({ type: 'flush-all' });
  } finally {
    flushBtn.disabled = false;
  }
});

autoToggle.addEventListener('change', () => {
  void send({ type: 'toggle-auto-capture', on: autoToggle.checked });
});

updateExistingToggle.addEventListener('change', () => {
  void send({ type: 'toggle-update-existing', on: updateExistingToggle.checked });
});

lowBandwidthToggle.addEventListener('change', () => {
  void send({ type: 'toggle-low-bandwidth', on: lowBandwidthToggle.checked });
});

masterSwitch.addEventListener('change', () => {
  void send({ type: 'toggle-enabled', on: masterSwitch.checked });
});

autoScrollStartBtn.addEventListener('click', () => {
  autoScrollStartBtn.disabled = true;
  void send({ type: 'start-auto-scroll' }).finally(() => {
    autoScrollStartBtn.disabled = false;
  });
});
autoScrollCancelBtn.addEventListener('click', () => {
  autoScrollCancelBtn.disabled = true;
  void send({ type: 'cancel-auto-scroll' }).finally(() => {
    autoScrollCancelBtn.disabled = false;
  });
});

autoScrollInterval.addEventListener('input', () => {
  autoScrollSecsEl.textContent = autoScrollInterval.value;
});
autoScrollInterval.addEventListener('change', () => {
  const seconds = Number(autoScrollInterval.value);
  if (Number.isFinite(seconds)) {
    void send({ type: 'set-auto-scroll-interval', seconds });
  }
});

purgeLink.addEventListener('click', (e: Event) => {
  e.preventDefault();
  const ok = window.confirm(
    'Drop every counter, run buffer, refetch / media-crawl / thread-opening queue entry for accounts not in your tracked list?\n\n' +
      'This only touches local extension state. Already-committed files in the GitHub repo are not affected — ' +
      'run scripts/purge_unrelated.py separately if you also want to scrub the repo.'
  );
  if (!ok) return;
  void send({ type: 'purge-unrelated' });
});

refetchStartBtn.addEventListener('click', () => {
  refetchStartBtn.disabled = true;
  void send({ type: 'start-refetch' }).finally(() => {
    refetchStartBtn.disabled = false;
  });
});

refetchCancelBtn.addEventListener('click', () => {
  refetchCancelBtn.disabled = true;
  void send({ type: 'cancel-refetch' }).finally(() => {
    refetchCancelBtn.disabled = false;
  });
});

mediaCrawlStartBtn.addEventListener('click', () => {
  mediaCrawlStartBtn.disabled = true;
  void send({ type: 'start-media-crawl' }).finally(() => {
    mediaCrawlStartBtn.disabled = false;
  });
});

mediaCrawlCancelBtn.addEventListener('click', () => {
  mediaCrawlCancelBtn.disabled = true;
  void send({ type: 'cancel-media-crawl' }).finally(() => {
    mediaCrawlCancelBtn.disabled = false;
  });
});

threadOpenStartBtn.addEventListener('click', () => {
  threadOpenStartBtn.disabled = true;
  void send({ type: 'start-thread-open' }).finally(() => {
    threadOpenStartBtn.disabled = false;
  });
});

threadOpenCancelBtn.addEventListener('click', () => {
  threadOpenCancelBtn.disabled = true;
  void send({ type: 'cancel-thread-open' }).finally(() => {
    threadOpenCancelBtn.disabled = false;
  });
});

optionsLink.addEventListener('click', (e: Event) => {
  e.preventDefault();
  void send({ type: 'open-options' });
});

viewerLink.addEventListener('click', (e: Event) => {
  e.preventDefault();
  void send({ type: 'open-viewer' });
});

clearActBtn.addEventListener('click', async () => {
  await send({ type: 'clear-activity' });
  renderActivity([]);
});

browser.runtime.onMessage.addListener((msg: unknown) => {
  if (!msg || typeof msg !== 'object') return;
  const m = msg as { type?: string; state?: ExtensionState; event?: LogEvent };
  if (m.type === 'state-changed' && m.state) {
    lastState = m.state;
    paint(m.state);
  } else if (m.type === 'log-event' && m.event) {
    prependEvent(m.event);
  }
});

function prependEvent(ev: LogEvent): void {
  // If the list is showing the "empty" placeholder, clear it.
  if (activityList.firstElementChild?.classList.contains('empty')) {
    activityList.replaceChildren();
  }
  const li = document.createElement('li');
  li.className = `ev ${ev.level}`;
  const ts = document.createElement('span');
  ts.className = 'ev-ts';
  ts.textContent = new Date(ev.ts).toLocaleTimeString('en-US', { hour12: false });
  const icon = document.createElement('span');
  icon.className = 'ev-icon';
  icon.textContent = ev.level === 'error' ? '✗' : ev.level === 'warn' ? '!' : '·';
  const body = document.createElement('span');
  const msg = document.createElement('div');
  msg.className = 'ev-msg';
  msg.textContent = ev.msg;
  body.append(msg);
  const ctxKeys = Object.keys(ev.context);
  if (ctxKeys.length > 0) {
    const ctx = document.createElement('div');
    ctx.className = 'ev-ctx';
    ctx.textContent = ctxKeys.map((k) => `${k}=${stringifyShort(ev.context[k])}`).join(' · ');
    body.append(ctx);
  }
  li.append(ts, icon, body);
  activityList.prepend(li);
  while (activityList.childElementCount > ACTIVITY_TAIL_MAX) {
    activityList.lastElementChild?.remove();
  }
}

// Initial paint.
void refreshState();
void refreshActivity();

// Periodically re-fetch state so "last capture" times stay fresh.
setInterval(() => {
  void refreshState();
}, 15_000);
