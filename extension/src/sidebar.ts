/**
 * sidebar.ts — UI for the persistent sidebar.
 *
 * Sidebars in Firefox stay open across tab/window switches, which is the
 * intended persistence model. This module renders state pushed from the
 * background service worker and forwards user commands back.
 */

import { ACTIVITY_TAIL_MAX } from './lib/config.js';
import type { ExtensionState, LogEvent, RuntimeMessage } from './lib/types.js';

const $ = <T extends HTMLElement = HTMLElement>(id: string): T => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing element #${id}`);
  return el as T;
};

const connDot = $('conn-dot');
const connText = $<HTMLSpanElement>('conn-text');
const accountList = $<HTMLUListElement>('account-list');
const activityList = $<HTMLUListElement>('activity-list');
const autoToggle = $<HTMLInputElement>('auto-capture');
const captureAllBtn = $<HTMLButtonElement>('capture-all');
const flushBtn = $<HTMLButtonElement>('flush-all');
const optionsLink = $<HTMLAnchorElement>('open-options');
const viewerLink = $<HTMLAnchorElement>('open-viewer');
const clearActBtn = $<HTMLButtonElement>('clear-activity');
const extVersionEl = $<HTMLSpanElement>('ext-version');

let lastState: ExtensionState | null = null;

async function send<T>(msg: RuntimeMessage): Promise<T> {
  return browser.runtime.sendMessage(msg) as Promise<T>;
}

function setConnStatus(state: ExtensionState): void {
  const { connection, settings } = state;
  let cls = '';
  let text = '';
  if (!settings.patSet) {
    cls = 'warn';
    text = 'Not configured — open Settings';
  } else if (connection.status === 'ok') {
    cls = 'ok';
    text = `Connected as @${connection.login ?? '?'} to ${settings.owner}/${settings.repo} · …${settings.patSuffix}`;
  } else if (connection.status === 'auth-error') {
    cls = 'err';
    text = `Auth error — check your PAT`;
  } else if (connection.status === 'rate-limited') {
    cls = 'warn';
    text = 'GitHub rate-limited — captures will retry';
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

function fmtNum(n: number): string {
  return n.toLocaleString('en-US');
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
  viewerLink.href = `https://${state.settings.owner}.github.io/${state.settings.repo}/`;
  renderAccounts(state);
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
