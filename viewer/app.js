// Orchestrator: wires the UI controls to the Store, manages URL state, theme,
// column visibility, CSV export, and lazy parquet loading.

import { exportCsv } from './csv.js';
import { loadParquetRows } from './parquet.js';
import { applyToUrl, defaults as defaultState, fromHash } from './state.js';
import { SEARCH_FIELD_OPTIONS, Store } from './store.js';
import { initChartsPanel, updateChartsPanel } from './charts.js?v=e677463';
import {
  openColumnFilterPopup,
  parseVisibleColumns,
  renderColumnsMenu,
  renderTable,
  setMediaColumnConfig,
  setUserLookup,
} from './table.js?v=9587694';
import { closeSidepanel, openSidepanel } from './sidepanel.js';

const $ = (id) => {
  const el = document.getElementById(id);
  if (!el) throw new Error(`missing element #${id}`);
  return el;
};

// --- DOM handles ---
const els = {
  hdrStats: $('hdr-stats'),
  filtersBtn: $('filters-btn'),
  colsBtn: $('cols-btn'),
  colsMenu: $('cols-menu'),
  csvBtn: $('csv-btn'),
  chartsBtn: $('charts-btn'),
  chartsPanel: $('chartpanel'),
  chartsClose: $('charts-close'),
  chartsSummary: $('charts-summary'),
  chartsStatus: $('charts-status'),
  chartsCanvas: $('charts-canvas'),
  tipsBtn: $('tips-btn'),
  tips: $('tips'),
  themeBtn: $('theme-btn'),
  themeIcon: $('theme-icon'),
  toolbar: $('toolbar'),
  filterbar: $('filterbar'),
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
  timelineLabel: $('timeline-label'),
  timelineBars: $('timeline-bars'),
  timelineClear: $('timeline-clear'),
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
let uiRevealed = false;
let filterbarVisible = localStorage.getItem('imm-filterbar-visible') !== 'false';
let mediaSettings = null;
let mediaPosterBySha = new Map();

initChartsPanel({
  button: els.chartsBtn,
  panel: els.chartsPanel,
  closeBtn: els.chartsClose,
  canvas: els.chartsCanvas,
  summary: els.chartsSummary,
  status: els.chartsStatus,
  getRows: () => filteredRows,
  getAllRows: () => store.allRows,
  categoryOf: (row) => store.categoryOf(row),
});

const MEDIA_SETTINGS_KEY = 'imm-media-column-settings-v2';
const MEDIA_THUMB_DEFAULT = 22;
const MEDIA_THUMB_MIN = 16;
const MEDIA_THUMB_MAX = 48;

function loadMediaSettings() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(MEDIA_SETTINGS_KEY) || 'null');
  } catch {
    saved = null;
  }
  return normalizeMediaSettings(saved);
}

function normalizeMediaSettings(value) {
  const thumbWidth = Number(value?.thumbWidth);
  const fit = value?.fit === 'vertical' ? 'vertical' : 'horizontal';
  const previews =
    value && Object.prototype.hasOwnProperty.call(value, 'previews')
      ? Boolean(value.previews)
      : true;
  return {
    previews,
    thumbWidth: clampThumbWidth(thumbWidth || MEDIA_THUMB_DEFAULT),
    fit,
  };
}

function clampThumbWidth(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return MEDIA_THUMB_DEFAULT;
  return Math.min(MEDIA_THUMB_MAX, Math.max(MEDIA_THUMB_MIN, Math.round(n)));
}

function applyMediaSettings({ persist = true, rerender = false } = {}) {
  mediaSettings = normalizeMediaSettings(mediaSettings);
  document.documentElement.style.setProperty('--media-thumb-size', `${mediaSettings.thumbWidth}px`);
  document.documentElement.dataset.mediaPreview = mediaSettings.previews ? 'on' : 'off';
  document.documentElement.dataset.mediaFit = mediaSettings.fit;
  setMediaColumnConfig({
    previews: mediaSettings.previews,
    posterBySha: mediaPosterBySha,
  });
  syncMediaControls();
  if (persist) localStorage.setItem(MEDIA_SETTINGS_KEY, JSON.stringify(mediaSettings));
  if (rerender) refresh();
}

function syncMediaControls() {
  document.documentElement.dataset.mediaPreview = mediaSettings.previews ? 'on' : 'off';
}

function setFilterbarVisible(next, { persist = true } = {}) {
  filterbarVisible = Boolean(next);
  syncFilterbarVisibility();
  if (persist) {
    localStorage.setItem('imm-filterbar-visible', filterbarVisible ? 'true' : 'false');
  }
}

function syncFilterbarVisibility() {
  els.filterbar.hidden = !uiRevealed || !filterbarVisible;
  els.filtersBtn.setAttribute('aria-expanded', filterbarVisible ? 'true' : 'false');
  els.filtersBtn.setAttribute('aria-pressed', filterbarVisible ? 'true' : 'false');
}

mediaSettings = loadMediaSettings();
applyMediaSettings({ persist: false });
syncFilterbarVisibility();

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
  await loadAccountCategorySidecar(catMap);
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

async function loadAccountCategorySidecar(catMap) {
  const cacheKey = manifest?.generated_at ? `?v=${encodeURIComponent(manifest.generated_at)}` : '';
  const url = `data/account_categories.json${cacheKey}`;
  try {
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) {
      if (res.status !== 404) throw new Error(`HTTP ${res.status} ${res.statusText}`);
      return;
    }
    const payload = await res.json();
    for (const [handle, meta] of Object.entries(payload?.categories ?? {})) {
      if (!handle || !meta?.category) continue;
      catMap.set(handle, meta.category);
    }
  } catch (err) {
    pushLoadError({
      resource: url,
      status: null,
      kind: 'account-categories',
      message: err?.message ?? String(err),
    });
  }
}

/**
 * Fetch optional sidecars and build additive per-tweet overlays. Missing
 * sidecars are normal on a fresh archive; the viewer still works without them.
 */
async function loadSidecars() {
  const [tagMap, audioTagMap, newsMentions, mediaInsightMap, posterBySha] = await Promise.all([
    loadLexicalTags(),
    loadAudioMusicTags(),
    loadNewsMentions(),
    loadMediaInsights(),
    loadKeyframePosters(),
  ]);
  for (const [id, tags] of audioTagMap.entries()) {
    mergeTags(tagMap, id, tags);
  }
  for (const [id, tags] of newsMentions.tagMap.entries()) {
    mergeTags(tagMap, id, tags);
  }
  for (const [id, insights] of mediaInsightMap.entries()) {
    for (const insight of insights) {
      if (Array.isArray(insight.tags)) mergeTags(tagMap, id, insight.tags);
    }
  }
  return { tagMap, mediaInsightMap, newsMentionMap: newsMentions.mentionMap, posterBySha };
}

async function loadLexicalTags() {
  const cacheKey = tagLayerCacheKey('lexical');
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
  const map = new Map();
  for (const layerName of ['media_vision', 'media_llm']) {
    const layerMap = await loadMediaInsightLayer(layerName);
    for (const [id, insights] of layerMap.entries()) {
      const list = map.get(id) ?? [];
      list.push(...insights);
      map.set(id, list);
    }
  }
  return map;
}

async function loadMediaInsightLayer(layerName) {
  const cacheKey = tagLayerCacheKey(layerName);
  const url = `data/tags/${layerName}.parquet${cacheKey}`;
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
        kind: layerName,
        message: err?.message ?? String(err),
      });
    }
    return new Map();
  }
}

async function loadAudioMusicTags() {
  const cacheKey = tagLayerCacheKey('audio_music');
  const url = `data/tags/audio_music.parquet${cacheKey}`;
  try {
    const rows = await loadParquetRows(url);
    const map = new Map();
    for (const r of rows) {
      const id = String(r?.tweet_id ?? '');
      if (!id) continue;
      const tags = Array.isArray(r.tags) ? r.tags : [];
      if (tags.length > 0) mergeTags(map, id, tags);
    }
    return map;
  } catch (err) {
    const status = (err && /:\s*(\d{3})\s/.exec(err.message ?? String(err))?.[1]) || null;
    if (status !== '404') {
      pushLoadError({
        resource: url,
        status: status ? Number(status) : null,
        kind: 'audio-tags',
        message: err?.message ?? String(err),
      });
    }
    return new Map();
  }
}

async function loadNewsMentions() {
  const cacheKey = tagLayerCacheKey('news_mentions');
  const url = `data/tags/news_mentions.parquet${cacheKey}`;
  const tagMap = new Map();
  const mentionMap = new Map();
  try {
    const rows = await loadParquetRows(url);
    for (const r of rows) {
      const id = String(r?.tweet_id ?? '');
      if (!id || Number(r?.mention_count ?? 0) <= 0) continue;
      mergeTags(tagMap, id, Array.isArray(r.tags) ? r.tags : []);
      mentionMap.set(id, {
        mention_count: Number(r?.mention_count ?? 0),
        status: String(r?.status || 'mentioned'),
        detector: String(r?.detector || ''),
        detector_version: String(r?.detector_version || ''),
        generated_at: String(r?.generated_at || ''),
        articles: Array.isArray(r?.articles) ? r.articles : [],
      });
    }
    return { tagMap, mentionMap };
  } catch (err) {
    const status = (err && /:\s*(\d{3})\s/.exec(err.message ?? String(err))?.[1]) || null;
    if (status !== '404') {
      pushLoadError({
        resource: url,
        status: status ? Number(status) : null,
        kind: 'news-tags',
        message: err?.message ?? String(err),
      });
    }
    return { tagMap, mentionMap };
  }
}

async function loadKeyframePosters() {
  const cacheKey = tagLayerCacheKey('keyframes');
  const url = `data/tags/keyframes.parquet${cacheKey}`;
  try {
    const rows = await loadParquetRows(url);
    const map = new Map();
    for (const row of rows) {
      if (String(row?.status || '') !== 'ok') continue;
      const sha = String(row?.media_sha256 || '');
      if (!sha || map.has(sha)) continue;
      const poster = stringOrNull(row?.thumbnail_path) || posterPathFromFrames(row?.frames);
      if (poster) map.set(sha, poster);
    }
    return map;
  } catch (err) {
    const status = (err && /:\s*(\d{3})\s/.exec(err.message ?? String(err))?.[1]) || null;
    if (status !== '404') {
      pushLoadError({
        resource: url,
        status: status ? Number(status) : null,
        kind: 'keyframes',
        message: err?.message ?? String(err),
      });
    }
    return new Map();
  }
}

function tagLayerCacheKey(layerName) {
  const layerGeneratedAt = manifest?.layers?.[layerName]?.generated_at;
  const version = layerGeneratedAt || manifest?.generated_at || '';
  return version ? `?v=${encodeURIComponent(version)}` : '';
}

function stringOrNull(value) {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

function posterPathFromFrames(frames) {
  if (!Array.isArray(frames) || frames.length === 0) return null;
  const usable = frames.filter((frame) => typeof frame?.path === 'string' && frame.path.length > 0);
  if (usable.length === 0) return null;
  const frame = usable[Math.floor(usable.length / 2)];
  return frame.path.replace(/\\/g, '/').replace(/^\.\//, '');
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
  const counts = categoryCounts();
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
  const counts = categoryCounts();
  for (const category of CATEGORY_ORDER) {
    if (!counts.has(category)) continue;
    const label = `${CATEGORY_LABELS[category] || category} (${fmtNum(counts.get(category))})`;
    els.categoryFilter.append(optionEl(category, label));
  }
  els.categoryFilter.value = urlState.categories.length === 1 ? urlState.categories[0] : '';
}

function categoryCounts() {
  const counts = new Map();
  if (store.allRows.length > 0) {
    for (const row of store.allRows) {
      const category = store.categoryOf(row);
      counts.set(category, (counts.get(category) ?? 0) + 1);
    }
    return counts;
  }
  for (const account of manifest?.accounts ?? []) {
    const category = account.category || 'core';
    counts.set(category, (counts.get(category) ?? 0) + (account.row_count || 0));
  }
  return counts;
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
  /** @type {Map<string, any>} */
  let newsMentionMap = new Map();
  let sidecarsResolved = false;
  sidecarsPromise.then((sidecars) => {
    tagMap = sidecars.tagMap;
    mediaInsightMap = sidecars.mediaInsightMap;
    newsMentionMap = sidecars.newsMentionMap;
    mediaPosterBySha = sidecars.posterBySha;
    sidecarsResolved = true;
    applyMediaSettings({ persist: false });
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
        paintCategoryFilter();
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
  paintCategoryFilter();

  function applySidecars() {
    store.applyTags(tagMap);
    store.applyMediaInsights(mediaInsightMap);
    store.applyNewsMentions(newsMentionMap);
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
  uiRevealed = true;
  els.empty.hidden = true;
  els.toolbar.hidden = false;
  syncFilterbarVisibility();
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

els.filtersBtn.addEventListener('click', () => {
  setFilterbarVisible(!filterbarVisible);
});

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
  urlState.size = Math.min(Number(els.pageSize.value) || 20, 200);
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
  els.pageSize.value = '20';
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
  if (urlState.tweet || urlState.profile) {
    urlState.tweet = '';
    urlState.profile = '';
    applyToUrl(urlState);
  }
  closeSidepanel(els.sidepanel);
  refreshSelectionHighlight();
});

function openProfileForHandle(handle, { updateUrl = true } = {}) {
  const cleanHandle = String(handle ?? '').replace(/^@/, '').trim();
  if (!cleanHandle) return;
  selectedRowId = null;
  if (updateUrl) {
    urlState.profile = cleanHandle;
    urlState.tweet = '';
    applyToUrl(urlState);
  }
  const rows = store.allRows
    .filter((row) => row.account_handle === cleanHandle)
    .slice()
    .sort((a, b) => String(b.posted_at ?? '').localeCompare(String(a.posted_at ?? '')));
  const manifestAccount = (manifest?.accounts ?? []).find((account) => account.handle === cleanHandle);
  const user = users.get(cleanHandle) ?? {};
  els.spTitle.textContent = `@${cleanHandle}`;
  const shareEl = els.sidepanel.querySelector('#sp-share');
  if (shareEl) shareEl.hidden = true;
  els.spBody.replaceChildren(profileView(cleanHandle, rows, user, manifestAccount));
  els.sidepanel.hidden = false;
  els.sidepanel.setAttribute('aria-hidden', 'false');
  refreshSelectionHighlight();
}

function openSharedProfileFromUrl() {
  const handle = String(urlState.profile || '');
  if (!handle) return false;
  openProfileForHandle(handle, { updateUrl: false });
  return true;
}

function profileView(handle, rows, user, manifestAccount) {
  const wrap = document.createElement('div');
  wrap.className = 'profile-view';
  wrap.append(
    profileHeader(handle, rows, user, manifestAccount),
    profileStats(rows, user, manifestAccount),
    profileActivity(rows),
    profileTweetList(rows)
  );
  return wrap;
}

function profileHeader(handle, rows, user, manifestAccount) {
  const head = document.createElement('section');
  head.className = 'profile-head';
  const avatar = document.createElement(user.avatar_url ? 'img' : 'span');
  avatar.className = user.avatar_url ? 'profile-avatar' : 'profile-avatar profile-avatar-placeholder';
  if (user.avatar_url) {
    avatar.src = user.avatar_url;
    avatar.alt = '';
    avatar.loading = 'lazy';
  } else {
    avatar.textContent = '@';
  }
  const body = document.createElement('div');
  body.className = 'profile-head-body';
  const name = document.createElement('div');
  name.className = 'profile-name';
  name.textContent = user.display_name || manifestAccount?.label || `@${handle}`;
  const meta = document.createElement('div');
  meta.className = 'profile-meta';
  meta.textContent = [
    `@${handle}`,
    manifestAccount?.category,
    user.location,
    user.verified || user.is_blue_verified ? 'verified' : '',
  ]
    .filter(Boolean)
    .join(' · ');
  body.append(name, meta);
  if (user.description) {
    const desc = document.createElement('div');
    desc.className = 'profile-description';
    desc.textContent = user.description;
    body.append(desc);
  }
  const actions = document.createElement('div');
  actions.className = 'profile-actions';
  const filterBtn = document.createElement('button');
  filterBtn.type = 'button';
  filterBtn.className = 'btn';
  filterBtn.textContent = 'Filter table';
  filterBtn.addEventListener('click', () => {
    urlState.accounts = [handle];
    urlState.page = 1;
    urlState.profile = '';
    applyToUrl(urlState);
    els.accountFilter.value = handle;
    closeSidepanel(els.sidepanel);
    refresh();
  });
  actions.append(filterBtn);
  if (handle) {
    const xLink = document.createElement('a');
    xLink.className = 'btn ghost';
    xLink.href = `https://x.com/${encodeURIComponent(handle)}`;
    xLink.target = '_blank';
    xLink.rel = 'noopener';
    xLink.textContent = 'Open on X';
    actions.append(xLink);
  }
  body.append(actions);
  head.append(avatar, body);
  return head;
}

function profileStats(rows, user, manifestAccount) {
  const section = document.createElement('section');
  section.className = 'profile-section';
  section.append(profileSectionTitle('Account snapshot'));
  const grid = document.createElement('div');
  grid.className = 'profile-stat-grid';
  const mediaRows = rows.filter((row) => Array.isArray(row.media) && row.media.length > 0).length;
  const replyRows = rows.filter((row) => row.tweet_type === 'reply').length;
  const dates = rows.map((row) => dayKey(row.posted_at)).filter(Boolean).sort();
  const firstLast = dates.length ? `${dates[0]} to ${dates[dates.length - 1]}` : 'No posted dates';
  for (const [label, value] of [
    ['Archived tweets', fmtNum(rows.length)],
    ['Replies', fmtNum(replyRows)],
    ['With media', fmtNum(mediaRows)],
    ['Posted range', firstLast],
    ['Followers', fmtNum(user.followers_count)],
    ['Following', fmtNum(user.friends_count)],
    ['Statuses', fmtNum(user.statuses_count)],
    ['Latest capture', shortDate(manifestAccount?.latest_capture_at || user.observed_at)],
  ]) {
    grid.append(profileStat(label, value));
  }
  section.append(grid);
  return section;
}

function profileActivity(rows) {
  const section = document.createElement('section');
  section.className = 'profile-section';
  section.append(profileSectionTitle('Activity by posted date'));
  const model = profileActivityModel(rows);
  section.append(profileBarChart(model.buckets), profileCalendar(model.buckets, model.scannedDays));
  const note = document.createElement('div');
  note.className = 'profile-note';
  note.textContent =
    'Muted cells are days with no archived tweet for this account. Gray cells are conservatively inferred unscanned days because this viewer only sees capture dates present in loaded rows and the manifest.';
  section.append(note);
  return section;
}

function profileTweetList(rows) {
  const section = document.createElement('section');
  section.className = 'profile-section';
  section.append(profileSectionTitle(`Archived tweets (${fmtNum(rows.length)})`));
  const list = document.createElement('div');
  list.className = 'profile-tweets';
  if (rows.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'muted';
    empty.textContent = 'No loaded tweets for this account yet.';
    list.append(empty);
  }
  for (const row of rows.slice(0, 100)) {
    list.append(profileTweetItem(row));
  }
  if (rows.length > 100) {
    const more = document.createElement('div');
    more.className = 'profile-note';
    more.textContent = `Showing latest 100 of ${fmtNum(rows.length)} archived tweets. Use Filter table for the full set.`;
    list.append(more);
  }
  section.append(list);
  return section;
}

function profileTweetItem(row) {
  const item = document.createElement('button');
  item.type = 'button';
  item.className = 'profile-tweet';
  item.addEventListener('click', () => {
    selectedRowId = row.tweet_id;
    urlState.tweet = String(row.tweet_id || '');
    urlState.profile = '';
    applyToUrl(urlState);
    const thread = store.groupIntoThreads([row])[0] || null;
    openSidepanel(els.sidepanel, els.spTitle, els.spBody, row, thread);
    refreshSelectionHighlight();
  });
  const meta = document.createElement('div');
  meta.className = 'profile-tweet-meta';
  meta.textContent = [shortDate(row.posted_at), row.tweet_type || 'original'].filter(Boolean).join(' · ');
  const text = document.createElement('div');
  text.className = 'profile-tweet-text';
  text.textContent = row.text_resolved || row.text || '(no text)';
  item.append(meta, text);
  return item;
}

function profileActivityModel(rows) {
  const counts = new Map();
  for (const row of rows) {
    const day = dayKey(row.posted_at);
    if (!day) continue;
    counts.set(day, (counts.get(day) || 0) + 1);
  }
  const scannedDays = inferredScannedDays();
  const unique = [...counts.keys()].sort();
  if (unique.length === 0) unique.push(...scannedDays);
  unique.sort();
  const min = unique[0] || dayKey(new Date().toISOString());
  const max = unique[unique.length - 1] || min;
  const buckets = [];
  for (const day of daysBetween(min, max)) {
    buckets.push({
      day,
      count: counts.get(day) || 0,
      scanned: scannedDays.has(day),
    });
  }
  return { buckets, scannedDays };
}

function inferredScannedDays() {
  const days = new Set();
  for (const row of store.allRows) {
    for (const key of ['captured_at', 'first_captured_at', 'last_seen_at', 'unavailable_detected_at']) {
      const day = dayKey(row[key]);
      if (day) days.add(day);
    }
    for (const entry of Array.isArray(row.engagement_history) ? row.engagement_history : []) {
      const day = dayKey(entry?.captured_at);
      if (day) days.add(day);
    }
  }
  for (const account of manifest?.accounts ?? []) {
    const day = dayKey(account.latest_capture_at);
    if (day) days.add(day);
  }
  return days;
}

function profileBarChart(buckets) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.classList.add('profile-bars');
  svg.setAttribute('viewBox', '0 0 640 140');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', 'Tweet activity by posted date');
  const max = Math.max(1, ...buckets.map((bucket) => bucket.count));
  const visible = buckets.slice(-90);
  const width = 640 / Math.max(1, visible.length);
  visible.forEach((bucket, index) => {
    const height = bucket.count > 0 ? Math.max(3, (bucket.count / max) * 112) : 2;
    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('x', String(index * width + 1));
    rect.setAttribute('y', String(128 - height));
    rect.setAttribute('width', String(Math.max(1, width - 2)));
    rect.setAttribute('height', String(height));
    rect.setAttribute('rx', '1');
    rect.classList.add(bucket.count > 0 ? 'active' : bucket.scanned ? 'empty' : 'unscanned');
    const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
    title.textContent = `${bucket.day}: ${bucket.count} tweet${bucket.count === 1 ? '' : 's'}${bucket.scanned ? '' : ' (scan not inferred)'}`;
    rect.append(title);
    svg.append(rect);
  });
  return svg;
}

function profileCalendar(buckets, scannedDays) {
  const wrap = document.createElement('div');
  wrap.className = 'profile-calendar';
  const max = Math.max(1, ...buckets.map((bucket) => bucket.count));
  for (const bucket of buckets.slice(-120)) {
    const cell = document.createElement('span');
    cell.className = 'profile-day';
    if (bucket.count > 0) cell.classList.add(`level-${Math.min(4, Math.ceil((bucket.count / max) * 4))}`);
    else if (!scannedDays.has(bucket.day)) cell.classList.add('unscanned');
    else cell.classList.add('empty');
    cell.title = `${bucket.day}: ${bucket.count} archived tweet${bucket.count === 1 ? '' : 's'}${scannedDays.has(bucket.day) ? '' : '; scan not inferred'}`;
    wrap.append(cell);
  }
  return wrap;
}

function profileSectionTitle(text) {
  const h = document.createElement('h3');
  h.textContent = text;
  return h;
}

function profileStat(label, value) {
  const item = document.createElement('div');
  item.className = 'profile-stat';
  const v = document.createElement('div');
  v.className = 'profile-stat-value';
  v.textContent = value || '—';
  const k = document.createElement('div');
  k.className = 'profile-stat-label';
  k.textContent = label;
  item.append(v, k);
  return item;
}

function daysBetween(start, end) {
  const out = [];
  const cur = dateFromDay(start);
  const stop = dateFromDay(end);
  if (!cur || !stop) return out;
  while (cur <= stop) {
    out.push(cur.toISOString().slice(0, 10));
    cur.setUTCDate(cur.getUTCDate() + 1);
  }
  return out;
}

function dateFromDay(day) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(day ?? ''))) return null;
  const date = new Date(`${day}T00:00:00Z`);
  return Number.isNaN(date.valueOf()) ? null : date;
}

// --- Pager ---
els.pgFirst.addEventListener('click', () => goto(1));
els.pgPrev.addEventListener('click', () => goto(urlState.page - 1));
els.pgNext.addEventListener('click', () => goto(urlState.page + 1));
els.pgLast.addEventListener('click', () => goto(lastPage()));
els.timelineClear.addEventListener('click', () => {
  urlState.from = '';
  urlState.to = '';
  urlState.page = 1;
  els.dateFrom.value = '';
  els.dateTo.value = '';
  applyToUrl(urlState);
  refresh();
});

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

function renderTimelinePager(rows, threads) {
  els.timelineBars.replaceChildren();
  const dateFilterActive = Boolean(urlState.from || urlState.to);
  els.timelineClear.hidden = !dateFilterActive;
  const bins = buildTimelineBins(rows, threads);
  if (bins.length === 0) {
    els.timelineLabel.textContent = 'Date histogram';
    const empty = document.createElement('div');
    empty.className = 'timeline-empty';
    empty.textContent = 'No dated tweets in current filters';
    els.timelineBars.append(empty);
    return;
  }
  const unit = bins[0]?.unit || 'day';
  const maxTweets = Math.max(1, ...bins.map((bin) => bin.tweets));
  const totalTweets = bins.reduce((sum, bin) => sum + bin.tweets, 0);
  const firstBin = bins[0];
  const lastBin = bins[bins.length - 1];
  const coverageStart = firstBin.coverageStart || firstBin.start;
  const coverageEnd = lastBin.coverageEnd || lastBin.end;
  const activeRange = dateFilterActive
    ? ` · selected ${timelineRangeLabel(urlState.from || coverageStart, urlState.to || coverageEnd)}`
    : '';
  els.timelineLabel.textContent = `Date histogram: ${fmtNum(totalTweets)} tweet${
    totalTweets === 1 ? '' : 's'
  } · ${timelineRangeLabel(coverageStart, coverageEnd)} · by ${unit}${activeRange}`;

  const track = document.createElement('div');
  track.className = 'timeline-track';
  track.style.setProperty('--timeline-bin-count', String(bins.length));

  for (const bin of bins) {
    const bar = document.createElement('button');
    bar.type = 'button';
    const active = timelineBinIsActive(bin);
    bar.className = `timeline-bar${bin.tweets === 0 ? ' empty' : ''}${active ? ' active' : ''}${
      dateFilterActive && !active ? ' inactive' : ''
    }`;
    const scaled = bin.tweets === 0 ? 0 : Math.sqrt(bin.tweets / maxTweets);
    const height = bin.tweets === 0 ? 3 : Math.max(8, Math.round(scaled * 100));
    bar.style.setProperty('--bar-height', `${height}%`);
    bar.title = `${timelineRangeLabel(bin.start, bin.end)}: ${fmtNum(bin.tweets)} tweet${
      bin.tweets === 1 ? '' : 's'
    }; ${fmtNum(bin.threads)} thread${bin.threads === 1 ? '' : 's'}.`;
    bar.setAttribute('aria-label', bar.title);
    const fill = document.createElement('span');
    fill.className = 'timeline-bar-fill';
    fill.setAttribute('aria-hidden', 'true');
    bar.append(fill);
    bar.addEventListener('click', () => {
      urlState.from = bin.start;
      urlState.to = bin.end;
      urlState.page = 1;
      els.dateFrom.value = urlState.from;
      els.dateTo.value = urlState.to;
      applyToUrl(urlState);
      refresh();
    });
    track.append(bar);
  }
  const axis = document.createElement('div');
  axis.className = 'timeline-axis';
  const startLabel = document.createElement('span');
  startLabel.textContent = coverageStart;
  const unitLabel = document.createElement('span');
  unitLabel.className = 'timeline-axis-unit';
  unitLabel.textContent = `${bins.length} ${pluralize(unit, bins.length)}`;
  const endLabel = document.createElement('span');
  endLabel.textContent = coverageEnd;
  axis.append(startLabel, unitLabel, endLabel);
  els.timelineBars.append(track, axis);
}

function buildTimelineBins(rows, threads) {
  const dates = rows.map((row) => dateFromDay(dayKey(row.posted_at))).filter(Boolean);
  if (dates.length === 0) return [];
  dates.sort((a, b) => a - b);
  const min = dates[0];
  const max = dates[dates.length - 1];
  const coverageStart = dayFromDate(min);
  const coverageEnd = dayFromDate(max);
  const unit = timelineUnit(min, max);
  const bins = [];
  const byStart = new Map();
  let cur = timelineBinStart(min, unit);
  const stop = timelineBinStart(max, unit);
  while (cur <= stop) {
    const start = dayFromDate(cur);
    const end = dayFromDate(timelineBinEnd(cur, unit));
    const bin = {
      unit,
      start,
      end,
      label: timelineBinLabel(cur, unit),
      tweets: 0,
      threads: 0,
    };
    bins.push(bin);
    byStart.set(start, bin);
    cur = nextTimelineBin(cur, unit);
  }
  if (bins[0]) bins[0].coverageStart = coverageStart;
  if (bins[bins.length - 1]) bins[bins.length - 1].coverageEnd = coverageEnd;
  for (const row of rows) {
    const date = dateFromDay(dayKey(row.posted_at));
    if (!date) continue;
    const bin = byStart.get(dayFromDate(timelineBinStart(date, unit)));
    if (bin) bin.tweets += 1;
  }
  for (const thread of threads) {
    const date = dateFromDay(dayKey(thread?.master?.posted_at));
    if (!date) continue;
    const bin = byStart.get(dayFromDate(timelineBinStart(date, unit)));
    if (bin) bin.threads += 1;
  }
  return bins;
}

const TIMELINE_MAX_BINS = 72;

function timelineUnit(min, max) {
  const days = Math.max(1, Math.round((max - min) / 86_400_000) + 1);
  if (days <= TIMELINE_MAX_BINS) return 'day';
  if (Math.ceil(days / 7) <= TIMELINE_MAX_BINS) return 'week';
  const months = (max.getUTCFullYear() - min.getUTCFullYear()) * 12 + max.getUTCMonth() - min.getUTCMonth() + 1;
  if (months <= TIMELINE_MAX_BINS) return 'month';
  if (Math.ceil(months / 3) <= TIMELINE_MAX_BINS) return 'quarter';
  return 'year';
}

function timelineBinStart(date, unit) {
  const d = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  if (unit === 'day') return d;
  if (unit === 'week') {
    d.setUTCDate(d.getUTCDate() - d.getUTCDay());
    return d;
  }
  if (unit === 'quarter') {
    return new Date(Date.UTC(d.getUTCFullYear(), Math.floor(d.getUTCMonth() / 3) * 3, 1));
  }
  if (unit === 'year') return new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1));
}

function timelineBinEnd(start, unit) {
  const next = nextTimelineBin(start, unit);
  next.setUTCDate(next.getUTCDate() - 1);
  return next;
}

function nextTimelineBin(start, unit) {
  const next = new Date(start.valueOf());
  if (unit === 'day') next.setUTCDate(next.getUTCDate() + 1);
  else if (unit === 'week') next.setUTCDate(next.getUTCDate() + 7);
  else if (unit === 'quarter') next.setUTCMonth(next.getUTCMonth() + 3);
  else if (unit === 'year') next.setUTCFullYear(next.getUTCFullYear() + 1);
  else next.setUTCMonth(next.getUTCMonth() + 1);
  return next;
}

function timelineBinLabel(start, unit) {
  const year = start.getUTCFullYear();
  const month = String(start.getUTCMonth() + 1).padStart(2, '0');
  if (unit === 'day') return dayFromDate(start);
  if (unit === 'week') return `${dayFromDate(start)} week`;
  if (unit === 'quarter') return `${year} Q${Math.floor(start.getUTCMonth() / 3) + 1}`;
  if (unit === 'year') return String(year);
  return `${year}-${month}`;
}

function timelineBinIsActive(bin) {
  if (!urlState.from && !urlState.to) return false;
  const from = urlState.from || '0000-01-01';
  const to = urlState.to || '9999-12-31';
  return bin.start <= to && bin.end >= from;
}

function dayFromDate(date) {
  return date.toISOString().slice(0, 10);
}

function timelineRangeLabel(start, end) {
  return start === end ? start : `${start} to ${end}`;
}

function pluralize(unit, count) {
  return `${unit}${count === 1 ? '' : 's'}`;
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
    tagCertainty: urlState.tagcert || 'all',
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
  const timelineRows =
    urlState.from || urlState.to
      ? store.apply({
          accounts: urlState.accounts,
          accountCategories: urlState.categories,
          tags: urlState.tags,
          q: urlState.q,
          qfield: urlState.qfield,
          from: '',
          to: '',
          type: urlState.type,
          media: urlState.media,
          tagCertainty: urlState.tagcert || 'all',
          sort: urlState.sort,
          dir: urlState.dir,
          colFilters,
        })
      : filteredRows;
  const timelineThreads =
    timelineRows === filteredRows ? filteredThreads : store.groupIntoThreads(timelineRows);
  renderTimelinePager(timelineRows, timelineThreads);

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
    tagCertainty: urlState.tagcert || 'all',
    expandedThreads,
    onRowClick: (r) => {
      selectedRowId = r.tweet_id;
      urlState.tweet = String(r.tweet_id || '');
      urlState.profile = '';
      applyToUrl(urlState);
      // When the clicked row is a master that owns non-self replies,
      // hand the thread along so the sidepanel can render its "Other
      // replies" section. Lookup is O(threads) but called only on
      // click, so it stays cheap.
      const thread = filteredThreads.find((t) => t.master === r) || null;
      openSidepanel(els.sidepanel, els.spTitle, els.spBody, r, thread);
      refreshSelectionHighlight();
    },
    onAccountOpen: (handle) => openProfileForHandle(handle),
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
        countRows: rowsForColumnCounts(key),
        activeFilters: visibleColFilters,
        onChange: (col, set, opts = {}) => {
          const nextSet = set instanceof Set ? set : new Set(set || []);
          urlState.page = 1;
          if (col === 'tags') {
            // Tags filter rides on urlState so it survives reloads and
            // can be deep-linked, unlike the other col filters which
            // are session-local. Mirror the selection out.
            delete colFilters.tags;
            if (opts.tagCertainty) urlState.tagcert = opts.tagCertainty;
            urlState.tags = [...nextSet];
            applyToUrl(urlState);
          } else if (nextSet.size === 0) {
            delete colFilters[col];
          } else {
            colFilters[col] = new Set(nextSet);
          }
          refresh();
        },
        onSort: (dir) => {
          urlState.sort = key;
          urlState.dir = dir;
          applyToUrl(urlState);
          refresh();
        },
        tagCertainty: urlState.tagcert || 'all',
        mediaSettings,
        onMediaSettingsChange: (next) => {
          mediaSettings = normalizeMediaSettings({ ...mediaSettings, ...next });
          applyMediaSettings({ rerender: true });
        },
      }),
    onToggleThread: (threadId) => {
      const beforeX = window.scrollX;
      const beforeY = window.scrollY;
      const tableScrollLeft = els.tableWrap.scrollLeft;
      const tableScrollTop = els.tableWrap.scrollTop;
      if (expandedThreads.has(threadId)) expandedThreads.delete(threadId);
      else expandedThreads.add(threadId);
      refresh();
      requestAnimationFrame(() => {
        window.scrollTo(beforeX, beforeY);
        els.tableWrap.scrollLeft = tableScrollLeft;
        els.tableWrap.scrollTop = tableScrollTop;
      });
    },
  });

  if (!openSharedProfileFromUrl()) openSharedEntryFromUrl();
  refreshSelectionHighlight();
  updateChartsPanel();
}

function rowsForColumnCounts(key) {
  const scopedColFilters = {};
  for (const [col, values] of Object.entries(colFilters)) {
    if (col === key) continue;
    scopedColFilters[col] = values instanceof Set ? new Set(values) : new Set(values || []);
  }
  const includeTagFilter = key !== 'tags';
  return store.apply({
    accounts: urlState.accounts,
    accountCategories: urlState.categories,
    tags: includeTagFilter ? urlState.tags : [],
    q: urlState.q,
    qfield: urlState.qfield,
    from: urlState.from,
    to: urlState.to,
    type: urlState.type,
    media: urlState.media,
    tagCertainty: includeTagFilter ? urlState.tagcert || 'all' : 'all',
    sort: urlState.sort,
    dir: urlState.dir,
    colFilters: scopedColFilters,
  });
}

function openSharedEntryFromUrl() {
  const tweetId = String(urlState.tweet || '');
  if (!tweetId) return;
  if (selectedRowId === tweetId && !els.sidepanel.hidden) return;
  const row = store.getDisplayRowById(tweetId);
  if (!row) {
    if (loadProgress.total > 0 && loadProgress.completed >= loadProgress.total) {
      showError(`No archived entry found for tweet ${tweetId}.`, 4000);
    }
    return;
  }
  selectedRowId = String(row.tweet_id || tweetId);
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

function dayKey(value) {
  const text = String(value ?? '');
  return /^\d{4}-\d{2}-\d{2}/.test(text) ? text.slice(0, 10) : '';
}
function shortDate(value) {
  return dayKey(value) || '';
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
