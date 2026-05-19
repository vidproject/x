// Orchestrator: wires the UI controls to the Store, manages URL state, theme,
// column visibility, CSV export, and lazy parquet loading.

import { exportCsv } from './csv.js';
import { loadParquetRows } from './parquet.js';
import { applyToUrl, defaults as defaultState, fromHash } from './state.js';
import { Store } from './store.js';
import {
  openColumnFilterPopup,
  parseVisibleColumns,
  renderColumnsMenu,
  renderTable,
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
  dlBtn: $('dl-btn'),
  dlMenu: $('dl-menu'),
  colsBtn: $('cols-btn'),
  colsMenu: $('cols-menu'),
  filterBtn: $('filter-btn'),
  csvBtn: $('csv-btn'),
  tipsBtn: $('tips-btn'),
  tips: $('tips'),
  themeBtn: $('theme-btn'),
  themeIcon: $('theme-icon'),
  toolbar: $('toolbar'),
  search: $('search'),
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
};

// --- State ---
const store = new Store();
let manifest = null;
let urlState = fromHash();
let visibleCols = parseVisibleColumns(urlState.cols);
let filteredRows = [];
/** @type {Record<string, Set<string>>} */
let colFilters = {};
let selectedRowId = null;
const LOADING = new Set();

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
  } catch {
    manifest = { accounts: [] };
    els.emptyDetail.textContent =
      'No data/manifest.json found yet. Once captures land and the ingest workflow runs, accounts will appear here.';
  }
  paintDlMenu();
  paintHdrStats();
  if ((manifest.accounts || []).length === 0) {
    els.empty.hidden = false;
  }
}

function paintHdrStats() {
  const accounts = manifest?.accounts ?? [];
  const totalRows = accounts.reduce((s, a) => s + (a.row_count || 0), 0);
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
    `${fmtNum(totalRows)} tweets · ${fmtNum(totalMedia)} media · ` +
    `${accounts.length} account${accounts.length === 1 ? '' : 's'}${loading}${failed}`;
}

function paintDlMenu() {
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
    row.addEventListener('click', () => toggleAccountFilter(a.handle));
    if (urlState.accounts.includes(a.handle)) row.classList.add('active');
    els.dlMenu.append(row);
  }
}

function toggleAccountFilter(handle) {
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

async function loadAllAccounts() {
  const accounts = manifest?.accounts ?? [];
  if (accounts.length === 0) return;

  loadProgress = { completed: 0, total: accounts.length, failed: 0 };
  els.emptyDetail.textContent = `Loading 0 / ${accounts.length} accounts…`;
  setSpinner(true);
  paintHdrStats();

  const queue = [...accounts];
  const inFlight = new Set();

  async function loadOne(account) {
    try {
      const rows = await loadParquetRows(`data/${account.handle}.parquet`);
      // Push without triggering a rebuild per-account; we batch rebuilds
      // every PROGRESSIVE_REFRESH_EVERY completions for snappier display.
      store.byHandle.set(account.handle, rows);
    } catch (err) {
      loadProgress.failed += 1;
      console.warn(`[viewer] failed to load ${account.handle}:`, err);
    } finally {
      loadProgress.completed += 1;
      els.emptyDetail.textContent = `Loading ${loadProgress.completed} / ${loadProgress.total} accounts…`;
      paintHdrStats();
      if (
        loadProgress.completed % PROGRESSIVE_REFRESH_EVERY === 0 ||
        loadProgress.completed === loadProgress.total
      ) {
        store.rebuild();
        revealUi();
        refresh();
      }
    }
  }

  while (queue.length > 0 || inFlight.size > 0) {
    while (inFlight.size < LOAD_CONCURRENCY && queue.length > 0) {
      const account = queue.shift();
      const p = loadOne(account).finally(() => inFlight.delete(p));
      inFlight.add(p);
    }
    if (inFlight.size > 0) await Promise.race(inFlight);
  }

  setSpinner(false);
  if (loadProgress.failed > 0) {
    showError(
      `Loaded ${loadProgress.total - loadProgress.failed} of ${loadProgress.total} accounts; ${loadProgress.failed} failed (see devtools console).`,
      10000
    );
  }
  paintHdrStats();
}

function revealUi() {
  els.empty.hidden = true;
  els.toolbar.hidden = false;
  els.resultBar.hidden = false;
  els.tableWrap.hidden = false;
  els.pager.hidden = false;
}

// --- Toolbar wiring ---
els.search.value = urlState.q;
els.dateFrom.value = urlState.from;
els.dateTo.value = urlState.to;
els.tweetType.value = urlState.type;
els.mediaType.value = urlState.media;
els.pageSize.value = String(urlState.size);

let searchDebounce;
els.search.addEventListener('input', () => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => {
    urlState.q = els.search.value;
    urlState.page = 1;
    applyToUrl(urlState);
    refresh();
  }, 150);
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
  const accounts = urlState.accounts;
  urlState = { ...defaultState(), accounts };
  colFilters = {};
  els.search.value = '';
  els.dateFrom.value = '';
  els.dateTo.value = '';
  els.tweetType.value = '';
  els.mediaType.value = '';
  els.pageSize.value = '100';
  applyToUrl(urlState);
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
wireDropdown(els.dlBtn, els.dlMenu);
wireDropdown(els.colsBtn, els.colsMenu);
function wireDropdown(btn, menu) {
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const willOpen = menu.hidden;
    // Close any other dropdowns first.
    for (const m of [els.dlMenu, els.colsMenu]) {
      if (m !== menu) m.hidden = true;
    }
    menu.hidden = !willOpen;
    btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
  });
}
document.addEventListener('mousedown', (e) => {
  for (const m of [els.dlMenu, els.colsMenu]) {
    if (!m.hidden && !m.contains(e.target) && e.target !== els.dlBtn && e.target !== els.colsBtn) {
      m.hidden = true;
    }
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
  closeSidepanel(els.sidepanel);
  refreshSelectionHighlight();
});

// --- Pager ---
els.pgFirst.addEventListener('click', () => goto(1));
els.pgPrev.addEventListener('click', () => goto(urlState.page - 1));
els.pgNext.addEventListener('click', () => goto(urlState.page + 1));
els.pgLast.addEventListener('click', () => goto(lastPage()));

function lastPage() {
  return Math.max(1, Math.ceil(filteredRows.length / urlState.size));
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
  els.search.value = urlState.q;
  els.dateFrom.value = urlState.from;
  els.dateTo.value = urlState.to;
  els.tweetType.value = urlState.type;
  els.mediaType.value = urlState.media;
  els.pageSize.value = String(urlState.size);
  visibleCols = parseVisibleColumns(urlState.cols);
  refresh();
});

// --- Filter button (toggles toolbar visibility) ---
let filterBarVisible = true;
els.filterBtn.addEventListener('click', () => {
  filterBarVisible = !filterBarVisible;
  els.toolbar.hidden = !filterBarVisible || store.handles().length === 0;
  els.filterBtn.setAttribute('aria-pressed', filterBarVisible ? 'true' : 'false');
});
els.filterBtn.setAttribute('aria-pressed', 'true');

// --- Render pipeline ---
function refresh() {
  filteredRows = store.apply({
    accounts: urlState.accounts,
    q: urlState.q,
    from: urlState.from,
    to: urlState.to,
    type: urlState.type,
    media: urlState.media,
    sort: urlState.sort,
    dir: urlState.dir,
    colFilters,
  });

  // Result count + page bounds.
  const total = filteredRows.length;
  const page = Math.min(urlState.page, lastPage());
  if (page !== urlState.page) urlState.page = page;
  const start = (page - 1) * urlState.size;
  const end = Math.min(total, start + urlState.size);
  els.resultCount.textContent =
    total === 0
      ? 'No matches.'
      : `Showing ${fmtNum(start + 1)}–${fmtNum(end)} of ${fmtNum(total)} tweet${total === 1 ? '' : 's'}.`;
  els.pgLabel.textContent = `Page ${fmtNum(page)} of ${fmtNum(lastPage())}`;
  els.pgFirst.disabled = page === 1;
  els.pgPrev.disabled = page === 1;
  els.pgNext.disabled = page === lastPage();
  els.pgLast.disabled = page === lastPage();

  renderTable({
    theadEl: els.theadRow,
    tbodyEl: els.tbody,
    rows: filteredRows,
    visible: visibleCols,
    page: urlState.page,
    pageSize: urlState.size,
    sort: urlState.sort,
    dir: urlState.dir,
    colFilters,
    onRowClick: (r) => {
      selectedRowId = r.tweet_id;
      openSidepanel(els.sidepanel, els.spTitle, els.spBody, r);
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
        activeFilters: colFilters,
        onChange: (col, set) => {
          if (set.size === 0) delete colFilters[col];
          else colFilters[col] = set;
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
  });

  refreshSelectionHighlight();
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
loadManifest().then(() => loadAllAccounts());
