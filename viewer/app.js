// Orchestrator: wires the UI controls to the Store, manages URL state, theme,
// column visibility, CSV export, and lazy parquet loading.

import { exportCsv } from './csv.js';
import { loadParquetRows } from './parquet.js';
import { applyToUrl, defaults as defaultState, fromHash } from './state.js';
import { SEARCH_FIELD_OPTIONS, Store } from './store.js';
import {
  openColumnFilterPopup,
  parseVisibleColumns,
  renderColumnsMenu,
  renderTable,
  setUserLookup,
} from './table.js';
import { closeSidepanel, openSidepanel } from './sidepanel.js';

const $ = (id) => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing element #${id}`);
  return el;
};

// --- DOM handles ---
const els = {
  hdrStats: $('hdr-stats'),
  colsBtn: $('cols-btn'),
  colsMenu: $('cols-menu'),
  csvBtn: $('csv-btn'),
  tipsBtn: $('tips-btn'),
  tips: $('tips'),
  themeBtn: $('theme-btn'),
  themeIcon: $('theme-icon'),
  toolbar: $('toolbar'),
  searchField: $('search-field'),
  search: $('search'),
  accountFilter: $('account-filter'),
  categoryFilter: $('category-filter'),
  dateFrom: $('date-from'),
  dateTo: $('date-to'),
  tweetType: $('tweet-type'),
  mediaType: $('media-type'),
  pageSize: $('page-size'),
  resetBtn: $('reset-btn'),
  resultBar: $('result-bar'),
  resultCount: $('result-count'),
  spinner: $('spinner'),
  tableWrap: $('table-wrap'),
  theadRow: $('thead-row'),
  tbody: $('tbody'),
  pager: $('pager'),
  pgFirst: $('pg-first'),
  pgPrev: $('pg-prev'),
  pgLabel: $('pg-label'),
  pgNext: $('pg-next'),
  pgLast: $('pg-last'),
  empty: $('empty'),
  emptyDetail: $('empty-detail'),
  sidepanel: $('sidepanel'),
  spTitle: $('sp-title'),
  spBody: $('sp-body'),
  spClose: $('sp-close'),
  colPop: $('col-pop'),
  errbar: $('errbar'),
  loadErrorsBtn: $('load-errors-btn'),
  loadErrorsCount: $('load-errors-count'),
  loadErrors: $('load-errors'),
  loadErrorsBody: $('load-errors-body'),
  loadErrorsClose: $('load-errors-close'),
};

// Structured per-resource load failures. Surface them via the
// "⚠ N load failures" button in the result-bar so the user can see what
// actually broke (a 404 from Pages lag vs. a corrupted parquet vs.
// network).
let loadErrors = [];
/** @type {Map<string, Record<string, unknown>>} */
let users = new Map();

// --- State ---
const store = new Store();
let manifest = null;
let urlState = fromHash();
let visibleCols = parseVisibleColumns(urlState.cols);
let filteredRows = [];
let filteredThreads = [];
/** @type {Record<string, Set<string>>} */
let colFilters = {};
let selectedRowId = null;
/** @type {Set<string>} */
let expandedThreads = new Set();

// --- Theme ---
function loadTheme() {
  const saved = localStorage.getItem('imm-theme') || 'auto';
  applyTheme(saved);
}
function applyTheme(mode) {
  document.body.classList.remove('theme-auto', 'theme-light', 'theme-dark');
  document.body.classList.add(`theme-${mode}`);
  els.themeIcon.textContent = mode === 'dark' ? '☾' : mode === 'light' ? '☀' : '◐';
  els.themeBtn.title = `Theme: ${mode} (click to cycle)`;
  localStorage.setItem('imm-theme', mode);
}
els.themeBtn.addEventListener('click', () => {
  const cur = localStorage.getItem('imm-theme') || 'auto';
  const next = cur === 'auto' ? 'light' : cur === 'light' ? 'dark' : 'auto';
  applyTheme(next);
});

// --- Tips ---
els.tipsBtn.addEventListener('click', () => {
  const next = els.tips.hidden;
  els.tips.hidden = !next;
  els.tipsBtn.setAttribute('aria-pressed', next ? 'true' : 'false');
});

// --- Manifest + auto-load every account ---
//
// The viewer treats the archive as a single combined database — there is
// no per-account opt-in download. On boot we fetch every parquet listed
// in the manifest in parallel (bounded by `LOAD_CONCURRENCY`) and merge
// them into one row set. The "Accounts" dropdown becomes a read-only
// directory: clicking a handle adds it to the account-filter on the
// table; clicking again clears it.

const LOAD_CONCURRENCY = 6;
// Re-render every N completed loads while loading, so the table fills in
// progressively instead of waiting for the slowest parquet.
const PROGRESSIVE_REFRESH_EVERY = 10;

let loadProgress = { completed: 0, total: 0, failed: 0 };

async function loadManifest() {
  try {
    const res = await fetch('data/manifest.json', { cache: 'no-store' });
    if (!res.ok) throw new Error(`manifest: ${res.status}`);
    manifest = await res.json();
  } catch (err) {
    manifest = { accounts: [] };
    pushLoadError({
      resource: 'data/manifest.json',
      status: null,
      kind: 'manifest',
      message: err.message ?? String(err),
    });
    els.emptyDetail.textContent =
      'No data/manifest.json found yet. Once captures land and the ingest workflow runs, accounts will appear here.';
  }
  // Push account categories into the store so the category filter can
  // resolve handle → category in O(1) at filter time. Untracked authors
  // (anyone not in the manifest) implicitly fall through to `public`.
  const catMap = new Map();
  for (const a of manifest?.accounts ?? []) {
    if (a.handle && a.category) catMap.set(a.handle, a.category);
  }
  store.setAccountCategories(catMap);
  // Best-effort users.json — totally optional, viewer still works without
  // avatars / display names.
  try {
    const res = await fetch('data/users.json', { cache: 'no-store' });
    if (res.ok) {
      const payload = await res.json();
      const map = new Map();
      for (const [handle, meta] of Object.entries(payload?.users ?? {})) {
        map.set(handle, meta);
      }
      users = map;
      setUserLookup(users);
    } else if (res.status !== 404) {
      pushLoadError({
        resource: 'data/users.json',
        status: res.status,
        kind: 'users',
        message: `HTTP ${res.status} ${res.statusText}`,
      });
    }
  } catch (err) {
    pushLoadError({
      resource: 'data/users.json',
      status: null,
      kind: 'users',
      message: err.message ?? String(err),
    });
  }
  paintAccountFilter();
  paintCategoryFilter();
  paintHdrStats();
  if ((manifest.accounts || []).length === 0) {
    els.empty.hidden = false;
  }
}

/**
 * Fetch optional sidecars and build additive per-tweet overlays. Missing
 * sidecars are normal on a fresh archive; the viewer still works without them.
 */
async function loadSidecars() {
  const [tagMap, mediaInsightMap] = await Promise.all([loadLexicalTags(), loadMediaInsights()]);
  for (const [id, insights] of mediaInsightMap.entries()) {
    for (const insight of insights) {
      if (Array.isArray(insight.tags)) mergeTags(tagMap, id, insight.tags);
    }
  }
  return { tagMap, mediaInsightMap };
}

async function loadLexicalTags() {
  const cacheKey = manifest?.generated_at ? `?v=${encodeURIComponent(manifest.generated_at)}` : '';
  const url = `data/tags/lexical.parquet${cacheKey}`;
  try {
    const rows = await loadParquetRows(url);
    const map = new Map();
    for (const r of rows) {
      const id = String(r?.tweet_id ?? '');
      if (!id) continue;
      mergeTags(map, id, Array.isArray(r.tags) ? r.tags : []);
    }
    return map;
  } catch (err) {
    const status = (err && /:\s*(\d{3})\s/.exec(err.message ?? String(err))?.[1]) || null;
    // 404 is expected before the tagger workflow has run for the first
    // time; record-but-don't-shout. Any other failure is a real error
    // and surfaces in the load-failures panel.
    if (status !== '404') {
      pushLoadError({
        resource: url,
        status: status ? Number(status) : null,
        kind: 'tags',
        message: err?.message ?? String(err),
      });
    }
    return new Map();
  }
}

async function loadMediaInsights() {
  const cacheKey = manifest?.generated_at ? `?v=${encodeURIComponent(manifest.generated_at)}` : '';
  const url = `data/tags/media_vision.parquet${cacheKey}`;
  try {
    const rows = await loadParquetRows(url);
    const map = new Map();
    for (const r of rows) {
      const id = String(r?.tweet_id ?? '');
      if (!id) continue;
      const list = map.get(id) ?? [];
      list.push(r);
      map.set(id, list);
    }
    return map;
  } catch (err) {
    const status = (err && /:\s*(\d{3})\s/.exec(err.message ?? String(err))?.[1]) || null;
    if (status !== '404') {
      pushLoadError({
        resource: url,
        status: status ? Number(status) : null,
        kind: 'media-tags',
        message: err?.message ?? String(err),
      });
    }
    return new Map();
  }
}

function mergeTags(map, id, tags) {
  if (!Array.isArray(tags) || tags.length === 0) return;
  const list = map.get(id) ?? [];
  list.push(...tags);
  map.set(id, list);
}

function pushLoadError(entry) {
  loadErrors.push({
    when: new Date().toISOString(),
    ...entry,
  });
  paintLoadErrorsButton();
}

function paintLoadErrorsButton() {
  if (loadErrors.length === 0) {
    els.loadErrorsBtn.hidden = true;
    return;
  }
  els.loadErrorsBtn.hidden = false;
  els.loadErrorsCount.textContent = String(loadErrors.length);
}

function paintLoadErrorsPanel() {
  els.loadErrorsBody.replaceChildren();
  for (const err of loadErrors) {
    const tr = document.createElement('tr');
    const when = new Date(err.when).toLocaleTimeString('en-US', { hour12: false });
    const httpCell = err.status === null ? '—' : String(err.status);
    tr.innerHTML =
      `<td>${escapeHtml(when)}</td>` +
      `<td><code>${escapeHtml(err.resource)}</code></td>` +
      `<td class="cell-num">${escapeHtml(httpCell)}</td>` +
      `<td>${escapeHtml(err.message)}</td>`;
    els.loadErrorsBody.append(tr);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    c === '&' ? '&amp;' : c === '<' ? '&lt;' : c === '>' ? '&gt;' : c === '"' ? '&quot;' : '&#39;'
  );
}

function paintHdrStats() {
  const accounts = manifest?.accounts ?? [];
  const loadedRows = Array.isArray(store.allRows) ? store.allRows : [];
  const loadedTotal = loadedRows.length;
  const loadedReplies = loadedRows.filter((r) => r.tweet_type === 'reply').length;
  const manifestReplies = accounts.reduce((s, a) => s + (a.reply_count || 0), 0);
  const manifestPosts = accounts.reduce(
    (s, a) => s + (a.post_count ?? Math.max(0, (a.row_count || 0) - (a.reply_count || 0))),
    0
  );
  const totalPosts = loadedTotal > 0 ? loadedTotal - loadedReplies : manifestPosts;
  const totalReplies = loadedTotal > 0 ? loadedReplies : manifestReplies;
  const totalMedia = accounts.reduce((s, a) => s + (a.media_count || 0), 0);
  if (accounts.length === 0) {
    els.hdrStats.textContent = '';
    return;
  }
  const loading =
    loadProgress.total > 0 && loadProgress.completed < loadProgress.total
      ? ` · loading ${fmtNum(loadProgress.completed)} / ${fmtNum(loadProgress.total)}`
      : '';
  const failed = loadProgress.failed > 0 ? ` · ${loadProgress.failed} failed` : '';
  els.hdrStats.textContent =
    `${fmtNum(totalPosts)} tweets · ${fmtNum(totalReplies)} replies · ${fmtNum(totalMedia)} media · ` +
    `${accounts.length} account${accounts.length === 1 ? '' : 's'}${loading}${failed}`;
}

function _paintDlMenu() {
  els.dlMenu.replaceChildren();
  const accounts = manifest?.accounts ?? [];
  if (accounts.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'muted';
    empty.style.padding = '6px';
    empty.textContent = 'No accounts yet.';
    els.dlMenu.append(empty);
    return;
  }
  const heading = document.createElement('div');
  heading.className = 'muted';
  heading.style.padding = '4px 6px';
  heading.style.fontSize = '11px';
  heading.textContent = 'Click a handle to filter to it; click again to clear.';
  els.dlMenu.append(heading);
  for (const a of accounts) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'dl-row dl-row-btn';
    row.dataset.handle = a.handle;
    const handle = document.createElement('span');
    handle.className = 'handle';
    handle.textContent = `@${a.handle}`;
    const label = document.createElement('span');
    label.className = 'label';
    label.textContent = a.label;
    const count = document.createElement('span');
    count.className = 'count';
    count.textContent = fmtNum(a.row_count);
    row.append(handle, label, count);
    row.addEventListener('click', () => _toggleAccountFilter(a.handle));
    if (urlState.accounts.includes(a.handle)) row.classList.add('active');
    els.dlMenu.append(row);
  }
}

function _toggleAccountFilter(handle) {
  const idx = urlState.accounts.indexOf(handle);
  if (idx === -1) urlState.accounts.push(handle);
  else urlState.accounts.splice(idx, 1);
  urlState.page = 1;
  applyToUrl(urlState);
  // Refresh menu highlight + table.
  for (const el of els.dlMenu.querySelectorAll('.dl-row-btn')) {
    if (el.dataset.handle === handle) el.classList.toggle('active');
  }
  refresh();
}

// ----- Account category filter --------------------------------------
//
// Categories come from `manifest.accounts[].category`. Untracked
// authors are implicitly `public` and not surfaced here; filtering by
// `public` includes everyone in `_misc.parquet` plus any tracked
// account explicitly labelled public.

const CATEGORY_LABELS = {
  core: 'Core (DHS / ICE / WH / …)',
  government: 'Other federal agencies',
  officials: 'Federal officials',
  public_figures: 'Public figures (senators, governors, …)',
  public: 'Public (replies, quotes, RTs)',
};
const CATEGORY_ORDER = ['core', 'government', 'officials', 'public_figures', 'public'];

function _paintCategoryMenu() {
  if (!els.catsMenu) return;
  els.catsMenu.replaceChildren();
  const accounts = manifest?.accounts ?? [];
  const counts = new Map();
  for (const a of accounts) {
    const c = a.category || 'core';
    counts.set(c, (counts.get(c) ?? 0) + (a.row_count || 0));
  }
  // `_misc.parquet` shows up in manifest with category=public; that already
  // covers the bulk of public-bucket counts.
  if (counts.size === 0) {
    const empty = document.createElement('div');
    empty.className = 'muted';
    empty.style.padding = '6px';
    empty.textContent = 'No accounts loaded yet.';
    els.catsMenu.append(empty);
    return;
  }
  const heading = document.createElement('div');
  heading.className = 'muted';
  heading.style.padding = '4px 6px';
  heading.style.fontSize = '11px';
  heading.textContent = 'Show only tweets from these account categories.';
  els.catsMenu.append(heading);
  for (const cat of CATEGORY_ORDER) {
    if (!counts.has(cat)) continue;
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'dl-row dl-row-btn';
    row.dataset.category = cat;
    const label = document.createElement('span');
    label.className = 'label';
    label.textContent = CATEGORY_LABELS[cat] || cat;
    const count = document.createElement('span');
    count.className = 'count';
    count.textContent = fmtNum(counts.get(cat));
    row.append(label, count);
    if (urlState.categories.includes(cat)) row.classList.add('active');
    row.addEventListener('click', () => _toggleCategoryFilter(cat));
    els.catsMenu.append(row);
  }
}

function _toggleCategoryFilter(cat) {
  const idx = urlState.categories.indexOf(cat);
  if (idx === -1) urlState.categories.push(cat);
  else urlState.categories.splice(idx, 1);
  urlState.page = 1;
  applyToUrl(urlState);
  for (const el of els.catsMenu.querySelectorAll('.dl-row-btn')) {
    if (el.dataset.category === cat) el.classList.toggle('active');
  }
  refresh();
}

function paintSearchFieldMenu() {
  els.searchField.replaceChildren();
  for (const field of SEARCH_FIELD_OPTIONS) {
    const option = document.createElement('option');
    option.value = field.value;
    option.textContent = field.label;
    els.searchField.append(option);
  }
  els.searchField.value = urlState.qfield || 'all';
  updateSearchPlaceholder();
}

function paintAccountFilter() {
  els.accountFilter.replaceChildren(optionEl('', 'All accounts'));
  const accounts = manifest?.accounts ?? [];
  for (const account of accounts) {
    const label = account.label ? `@${account.handle} - ${account.label}` : `@${account.handle}`;
    els.accountFilter.append(optionEl(account.handle, label));
  }
  els.accountFilter.value = urlState.accounts.length === 1 ? urlState.accounts[0] : '';
}

function paintCategoryFilter() {
  els.categoryFilter.replaceChildren(optionEl('', 'All categories'));
  const accounts = manifest?.accounts ?? [];
  const counts = new Map();
  for (const account of accounts) {
    const category = account.category || 'core';
    counts.set(category, (counts.get(category) ?? 0) + (account.row_count || 0));
  }
  for (const category of CATEGORY_ORDER) {
    if (!counts.has(category)) continue;
    const label = `${CATEGORY_LABELS[category] || category} (${fmtNum(counts.get(category))})`;
    els.categoryFilter.append(optionEl(category, label));
  }
  els.categoryFilter.value = urlState.categories.length === 1 ? urlState.categories[0] : '';
}

function optionEl(value, label) {
  const option = document.createElement('option');
  option.value = value;
  option.textContent = label;
  return option;
}

function updateSearchPlaceholder() {
  const value = els.searchField.value || 'all';
  const label = SEARCH_FIELD_OPTIONS.find((field) => field.value === value)?.label || 'All fields';
  const scope = value === 'all' ? 'all fields' : label.toLocaleLowerCase();
  els.search.placeholder = `Search ${scope}... (use * and ? wildcards)`;
}

async function loadAllAccounts(sidecarsPromise) {
  const accounts = manifest?.accounts ?? [];
  if (accounts.length === 0) return;

  loadProgress = { completed: 0, total: accounts.length, failed: 0 };
  els.emptyDetail.textContent = `Loading 0 / ${accounts.length} accounts…`;
  setSpinner(true);
  paintHdrStats();
  // Resolve the tag overlay alongside the first parquet batch so the
  // user sees pills immediately, not after every account has loaded.
  /** @type {Map<string, any[]>} */
  let tagMap = new Map();
  /** @type {Map<string, any[]>} */
  let mediaInsightMap = new Map();
  let sidecarsResolved = false;
  sidecarsPromise.then((sidecars) => {
    tagMap = sidecars.tagMap;
    mediaInsightMap = sidecars.mediaInsightMap;
    sidecarsResolved = true;
    applySidecars();
    refresh();
  });

  const queue = [...accounts];
  const inFlight = new Set();

  // Cache-bust per-parquet using manifest.generated_at, so a manifest
  // update reliably invalidates the CDN-cached parquet body / 404.
  const cacheKey = manifest?.generated_at ? `?v=${encodeURIComponent(manifest.generated_at)}` : '';

  async function fetchOnce(resource, suffix) {
    return loadParquetRows(resource + suffix);
  }

  async function loadOne(account) {
    const resource = `data/${account.handle}.parquet`;
    try {
      const rows = await fetchOnce(resource, cacheKey);
      store.byHandle.set(account.handle, rows);
      return;
    } catch (err) {
      // Retry exactly once on a 404 after a 30s pause — Pages CDN takes a
      // little while to catch up after a manifest+data commit pair.
      const status = extractStatus(err);
      if (status === 404) {
        await new Promise((r) => setTimeout(r, 30_000));
        try {
          const rows = await fetchOnce(resource, `?v=retry-${Date.now()}`);
          store.byHandle.set(account.handle, rows);
          return;
        } catch (retryErr) {
          recordLoadFailure(resource, account.handle, retryErr, true);
          return;
        }
      }
      recordLoadFailure(resource, account.handle, err, false);
    }
  }

  function extractStatus(err) {
    const m = /:\s*(\d{3})\s*(.*)$/.exec(err?.message ?? String(err));
    return m ? Number(m[1]) : null;
  }

  function recordLoadFailure(resource, handle, err, retried) {
    loadProgress.failed += 1;
    const status = extractStatus(err);
    const raw = err?.message ?? String(err);
    const friendly =
      status === 404
        ? `${resource} is in the manifest but hasn't been deployed to Pages yet (manifest commit beat the parquet commit through the CDN${retried ? '; still missing after a 30s retry' : ''}). Refresh in ~60s.`
        : raw;
    pushLoadError({
      resource,
      status,
      kind: 'parquet',
      handle,
      message: friendly,
    });
    console.warn(`[viewer] failed to load ${handle}:`, err);
  }

  async function loadOneOuter(account) {
    try {
      await loadOne(account);
    } finally {
      loadProgress.completed += 1;
      els.emptyDetail.textContent = `Loading ${loadProgress.completed} / ${loadProgress.total} accounts…`;
      paintHdrStats();
      if (
        loadProgress.completed % PROGRESSIVE_REFRESH_EVERY === 0 ||
        loadProgress.completed === loadProgress.total
      ) {
        store.rebuild();
        if (sidecarsResolved) applySidecars();
        revealUi();
        refresh();
      }
    }
  }

  while (queue.length > 0 || inFlight.size > 0) {
    while (inFlight.size < LOAD_CONCURRENCY && queue.length > 0) {
      const account = queue.shift();
      const p = loadOneOuter(account).finally(() => inFlight.delete(p));
      inFlight.add(p);
    }
    if (inFlight.size > 0) await Promise.race(inFlight);
  }

  setSpinner(false);
  if (loadProgress.failed > 0) {
    showError(
      `Loaded ${loadProgress.total - loadProgress.failed} of ${loadProgress.total} accounts; ${loadProgress.failed} failed. Click "⚠ load failures" above for details.`,
      15000
    );
  }
  paintHdrStats();

  function applySidecars() {
    store.applyTags(tagMap);
    store.applyMediaInsights(mediaInsightMap);
  }
}

// Wire the load-errors panel: button toggles, X closes.
els.loadErrorsBtn.addEventListener('click', () => {
  paintLoadErrorsPanel();
  els.loadErrors.hidden = !els.loadErrors.hidden;
});
els.loadErrorsClose.addEventListener('click', () => {
  els.loadErrors.hidden = true;
});

function revealUi() {
  els.empty.hidden = true;
  els.toolbar.hidden = false;
  els.resultBar.hidden = false;
  els.tableWrap.hidden = false;
  els.pager.hidden = false;
}

// --- Toolbar wiring ---
paintSearchFieldMenu();
els.searchField.value = urlState.qfield || 'all';
els.search.value = urlState.q;
els.accountFilter.value = urlState.accounts.length === 1 ? urlState.accounts[0] : '';
els.categoryFilter.value = urlState.categories.length === 1 ? urlState.categories[0] : '';
els.dateFrom.value = urlState.from;
els.dateTo.value = urlState.to;
els.tweetType.value = urlState.type;
els.mediaType.value = urlState.media;
els.pageSize.value = String(urlState.size);

let searchDebounce;
els.searchField.addEventListener('change', () => {
  urlState.qfield = els.searchField.value || 'all';
  updateSearchPlaceholder();
  urlState.page = 1;
  applyToUrl(urlState);
  refresh();
});
els.search.addEventListener('input', () => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => {
    urlState.q = els.search.value;
    urlState.page = 1;
    applyToUrl(urlState);
    refresh();
  }, 150);
});
els.accountFilter.addEventListener('change', () => {
  urlState.accounts = els.accountFilter.value ? [els.accountFilter.value] : [];
  urlState.page = 1;
  applyToUrl(urlState);
  refresh();
});
els.categoryFilter.addEventListener('change', () => {
  urlState.categories = els.categoryFilter.value ? [els.categoryFilter.value] : [];
  urlState.page = 1;
  applyToUrl(urlState);
  refresh();
});
els.dateFrom.addEventListener('change', () => {
  urlState.from = els.dateFrom.value;
  urlState.page = 1;
  applyToUrl(urlState);
  refresh();
});
els.dateTo.addEventListener('change', () => {
  urlState.to = els.dateTo.value;
  urlState.page = 1;
  applyToUrl(urlState);
  refresh();
});
els.tweetType.addEventListener('change', () => {
  urlState.type = els.tweetType.value;
  urlState.page = 1;
  applyToUrl(urlState);
  refresh();
});
els.mediaType.addEventListener('change', () => {
  urlState.media = els.mediaType.value;
  urlState.page = 1;
  applyToUrl(urlState);
  refresh();
});
els.pageSize.addEventListener('change', () => {
  urlState.size = Number(els.pageSize.value) || 100;
  urlState.page = 1;
  applyToUrl(urlState);
  refresh();
});
els.resetBtn.addEventListener('click', () => {
  urlState = defaultState();
  colFilters = {};
  expandedThreads = new Set();
  els.searchField.value = 'all';
  updateSearchPlaceholder();
  els.search.value = '';
  els.accountFilter.value = '';
  els.categoryFilter.value = '';
  els.dateFrom.value = '';
  els.dateTo.value = '';
  els.tweetType.value = '';
  els.mediaType.value = '';
  els.pageSize.value = '100';
  applyToUrl(urlState);
  paintAccountFilter();
  paintCategoryFilter();
  refresh();
});

// --- Columns menu ---
function onColumnsChange(next) {
  visibleCols = next;
  urlState.cols = next.join(',');
  applyToUrl(urlState);
  renderColumnsMenu(els.colsMenu, visibleCols, onColumnsChange);
  refresh();
}
renderColumnsMenu(els.colsMenu, visibleCols, onColumnsChange);

// --- Dropdown toggles ---
const dropdownMenus = [els.colsMenu];
const dropdownButtons = [els.colsBtn];
wireDropdown(els.colsBtn, els.colsMenu);
function wireDropdown(btn, menu) {
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const willOpen = menu.hidden;
    // Close any other dropdowns first.
    for (const m of dropdownMenus) {
      if (m !== menu) m.hidden = true;
    }
    menu.hidden = !willOpen;
    btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
  });
}
document.addEventListener('mousedown', (e) => {
  for (const m of dropdownMenus) {
    if (m.hidden) continue;
    if (m.contains(e.target)) continue;
    if (dropdownButtons.includes(e.target)) continue;
    m.hidden = true;
  }
});

// --- CSV ---
els.csvBtn.addEventListener('click', () => {
  if (filteredRows.length === 0) {
    showError('Nothing to export — no rows match the current filters.', 3000);
    return;
  }
  exportCsv(filteredRows);
});

// --- Sidepanel ---
els.spClose.addEventListener('click', () => {
  selectedRowId = null;
  if (urlState.tweet) {
    urlState.tweet = '';
    applyToUrl(urlState);
  }
  closeSidepanel(els.sidepanel);
  refreshSelectionHighlight();
});

// --- Pager ---
els.pgFirst.addEventListener('click', () => goto(1));
els.pgPrev.addEventListener('click', () => goto(urlState.page - 1));
els.pgNext.addEventListener('click', () => goto(urlState.page + 1));
els.pgLast.addEventListener('click', () => goto(lastPage()));

function lastPage() {
  return Math.max(1, Math.ceil(filteredThreads.length / urlState.size));
}
function goto(p) {
  const np = Math.min(Math.max(1, p), lastPage());
  if (np === urlState.page) return;
  urlState.page = np;
  applyToUrl(urlState);
  refresh();
}

// --- Hash sync ---
window.addEventListener('hashchange', () => {
  const next = fromHash();
  if (JSON.stringify(next) === JSON.stringify(urlState)) return;
  urlState = next;
  els.searchField.value = urlState.qfield || 'all';
  updateSearchPlaceholder();
  els.search.value = urlState.q;
  els.accountFilter.value = urlState.accounts.length === 1 ? urlState.accounts[0] : '';
  els.categoryFilter.value = urlState.categories.length === 1 ? urlState.categories[0] : '';
  els.dateFrom.value = urlState.from;
  els.dateTo.value = urlState.to;
  els.tweetType.value = urlState.type;
  els.mediaType.value = urlState.media;
  els.pageSize.value = String(urlState.size);
  visibleCols = parseVisibleColumns(urlState.cols);
  paintAccountFilter();
  paintCategoryFilter();
  refresh();
});

// --- Render pipeline ---
function refresh() {
  filteredRows = store.apply({
    accounts: urlState.accounts,
    accountCategories: urlState.categories,
    tags: urlState.tags,
    q: urlState.q,
    qfield: urlState.qfield,
    from: urlState.from,
    to: urlState.to,
    type: urlState.type,
    media: urlState.media,
    sort: urlState.sort,
    dir: urlState.dir,
    colFilters,
  });
  filteredThreads = store.groupIntoThreads(filteredRows);

  // Pagination counts threads (which collapse multi-row reply chains
  // into a single visible row). When no threading kicks in (the common
  // case), thread count == row count, so the math is unchanged.
  const total = filteredThreads.length;
  const page = Math.min(urlState.page, lastPage());
  if (page !== urlState.page) urlState.page = page;
  const start = (page - 1) * urlState.size;
  const end = Math.min(total, start + urlState.size);
  const rowCount = filteredRows.length;
  const threadNote = rowCount > total ? ` · ${fmtNum(rowCount)} including replies` : '';
  els.resultCount.textContent =
    total === 0
      ? 'No matches.'
      : `Showing ${fmtNum(start + 1)}–${fmtNum(end)} of ${fmtNum(total)} thread${total === 1 ? '' : 's'}${threadNote}.`;
  els.pgLabel.textContent = `Page ${fmtNum(page)} of ${fmtNum(lastPage())}`;
  els.pgFirst.disabled = page === 1;
  els.pgPrev.disabled = page === 1;
  els.pgNext.disabled = page === lastPage();
  els.pgLast.disabled = page === lastPage();

  const visibleColFilters =
    urlState.tags && urlState.tags.length > 0
      ? { ...colFilters, tags: new Set(urlState.tags) }
      : colFilters;
  renderTable({
    theadEl: els.theadRow,
    tbodyEl: els.tbody,
    rows: filteredRows,
    threads: filteredThreads,
    visible: visibleCols,
    page: urlState.page,
    pageSize: urlState.size,
    sort: urlState.sort,
    dir: urlState.dir,
    colFilters: visibleColFilters,
    expandedThreads,
    onRowClick: (r) => {
      selectedRowId = r.tweet_id;
      urlState.tweet = String(r.tweet_id || '');
      applyToUrl(urlState);
      // When the clicked row is a master that owns non-self replies,
      // hand the thread along so the sidepanel can render its "Other
      // replies" section. Lookup is O(threads) but called only on
      // click, so it stays cheap.
      const thread = filteredThreads.find((t) => t.master === r) || null;
      openSidepanel(els.sidepanel, els.spTitle, els.spBody, r, thread);
      refreshSelectionHighlight();
    },
    onSortToggle: (key) => {
      if (urlState.sort === key) {
        urlState.dir = urlState.dir === 'desc' ? 'asc' : 'desc';
      } else {
        urlState.sort = key;
        urlState.dir = 'desc';
      }
      applyToUrl(urlState);
      refresh();
    },
    onOpenColPop: (key, btn) =>
      openColumnFilterPopup({
        popEl: els.colPop,
        anchorBtn: btn,
        colKey: key,
        allRows: store.allRows,
        activeFilters: visibleColFilters,
        onChange: (col, set) => {
          if (col === 'tags') {
            // Tags filter rides on urlState so it survives reloads and
            // can be deep-linked, unlike the other col filters which
            // are session-local. Mirror the selection out.
            urlState.tags = [...set];
            applyToUrl(urlState);
          } else if (set.size === 0) {
            delete colFilters[col];
          } else {
            colFilters[col] = set;
          }
          urlState.page = 1;
          refresh();
        },
        onSort: (dir) => {
          urlState.sort = key;
          urlState.dir = dir;
          applyToUrl(urlState);
          refresh();
        },
      }),
    onToggleThread: (threadId) => {
      if (expandedThreads.has(threadId)) expandedThreads.delete(threadId);
      else expandedThreads.add(threadId);
      refresh();
    },
  });

  openSharedEntryFromUrl();
  refreshSelectionHighlight();
}

function openSharedEntryFromUrl() {
  const tweetId = String(urlState.tweet || '');
  if (!tweetId) return;
  if (selectedRowId === tweetId && !els.sidepanel.hidden) return;
  const row = store.getById(tweetId);
  if (!row) {
    if (loadProgress.total > 0 && loadProgress.completed >= loadProgress.total) {
      showError(`No archived entry found for tweet ${tweetId}.`, 4000);
    }
    return;
  }
  selectedRowId = tweetId;
  const thread = store.groupIntoThreads([row])[0] || null;
  openSidepanel(els.sidepanel, els.spTitle, els.spBody, row, thread);
}

function refreshSelectionHighlight() {
  for (const tr of els.tbody.children) {
    if (tr.dataset && tr.dataset.tweetId === String(selectedRowId)) {
      tr.classList.add('selected');
    } else {
      tr.classList.remove?.('selected');
    }
  }
}

// --- Misc helpers ---
function setSpinner(on) {
  els.spinner.hidden = !on;
}
function showError(msg, timeoutMs) {
  if (!msg) {
    els.errbar.hidden = true;
    els.errbar.textContent = '';
    return;
  }
  els.errbar.hidden = false;
  els.errbar.textContent = msg;
  if (timeoutMs) {
    setTimeout(() => {
      els.errbar.hidden = true;
      els.errbar.textContent = '';
    }, timeoutMs);
  }
}
function fmtNum(v) {
  if (v == null) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString('en-US');
}

// --- Boot ---
loadTheme();
loadManifest().then(async () => {
  // Kick off sidecar and account loads in parallel; the sidecar merge
  // happens in `loadAllAccounts` after each progressive parquet flush so
  // tags and media descriptions appear in lock-step with their rows.
  const sidecarsPromise = loadSidecars();
  await loadAllAccounts(sidecarsPromise);
});
