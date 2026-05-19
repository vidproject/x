// Table rendering with Excel-style column-header filter popups.
//
// Columns are defined as { key, label, render, filterable, sortable, default }.
// "default" controls whether the column is visible on first load — deletion is
// deliberately false per the deemphasis brief.

import { formatForFilter } from './store.js';

export const COLUMNS = [
  {
    key: 'account_handle',
    label: 'Account',
    default: true,
    filterable: true,
    sortable: true,
    render: (r) => `<span class="handle">@${escape(r.account_handle ?? '')}</span>`,
  },
  {
    key: 'posted_at',
    label: 'Posted',
    default: true,
    filterable: true,
    sortable: true,
    render: (r) =>
      r.posted_at
        ? `<span title="${escape(r.posted_at)}">${escape(fmtDate(r.posted_at))}</span>`
        : '—',
  },
  {
    key: 'tweet_type',
    label: 'Type',
    default: true,
    filterable: true,
    sortable: true,
    render: (r) =>
      `<span class="pill ${r.tweet_type}">${escape(r.tweet_type ?? 'original')}</span>`,
  },
  {
    key: 'text',
    label: 'Text',
    default: true,
    filterable: false,
    sortable: false,
    render: (r) =>
      `<span class="cell-text" title="${escape(r.text ?? '')}">${escape(truncate(r.text ?? '', 200))}</span>`,
  },
  {
    key: 'media_kinds',
    label: 'Media',
    default: true,
    filterable: true,
    sortable: false,
    render: (r) => mediaFlags(r),
  },
  {
    key: 'like_count',
    label: 'Likes',
    default: true,
    filterable: false,
    sortable: true,
    className: 'col-stats cell-num',
    render: (r) => fmtNum(r.like_count),
  },
  {
    key: 'retweet_count',
    label: 'RTs',
    default: true,
    filterable: false,
    sortable: true,
    className: 'col-stats cell-num',
    render: (r) => fmtNum(r.retweet_count),
  },
  {
    key: 'reply_count',
    label: 'Replies',
    default: false,
    filterable: false,
    sortable: true,
    className: 'col-stats cell-num',
    render: (r) => fmtNum(r.reply_count),
  },
  {
    key: 'view_count',
    label: 'Views',
    default: false,
    filterable: false,
    sortable: true,
    className: 'col-stats cell-num',
    render: (r) => fmtNum(r.view_count),
  },
  {
    key: 'lang',
    label: 'Lang',
    default: false,
    filterable: true,
    sortable: true,
    render: (r) => escape(r.lang ?? ''),
  },
  {
    key: 'wayback_url',
    label: 'Wayback',
    default: false,
    filterable: false,
    sortable: false,
    render: (r) =>
      r.wayback_url
        ? `<a class="tweet-link" href="${escape(r.wayback_url)}" target="_blank" rel="noopener">archived</a>`
        : '—',
  },
  {
    // Deemphasized per the brief: off by default, only available via Columns.
    key: 'deletion_detected_at',
    label: 'Deletion detected',
    default: false,
    filterable: true,
    sortable: true,
    render: (r) =>
      r.deletion_detected_at
        ? `<span class="deleted-cell" title="${escape(r.deletion_detected_at)}">${escape(fmtDate(r.deletion_detected_at))}</span>`
        : '—',
  },
  {
    key: 'tweet_url',
    label: 'Link',
    default: true,
    filterable: false,
    sortable: false,
    render: (r) =>
      r.tweet_url
        ? `<a class="tweet-link" href="${escape(r.tweet_url)}" target="_blank" rel="noopener">↗</a>`
        : '',
  },
];

const KEY_TO_COL = Object.fromEntries(COLUMNS.map((c) => [c.key, c]));

export function defaultVisibleColumns() {
  return COLUMNS.filter((c) => c.default).map((c) => c.key);
}

export function parseVisibleColumns(spec) {
  if (!spec) return defaultVisibleColumns();
  const keys = spec.split(',').filter((k) => KEY_TO_COL[k]);
  return keys.length > 0 ? keys : defaultVisibleColumns();
}

export function renderColumnsMenu(menuEl, visible, onChange) {
  menuEl.replaceChildren();
  for (const col of COLUMNS) {
    const row = document.createElement('label');
    row.className = 'cols-row';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = visible.includes(col.key);
    cb.addEventListener('change', () => {
      const next = COLUMNS.map((c) => c.key).filter((k) =>
        k === col.key ? cb.checked : visible.includes(k)
      );
      onChange(next);
    });
    const label = document.createElement('span');
    label.textContent = col.label;
    if (col.key === 'deletion_detected_at') {
      const tag = document.createElement('span');
      tag.className = 'count';
      tag.textContent = 'off by default';
      row.append(cb, label, tag);
    } else {
      row.append(cb, label);
    }
    menuEl.append(row);
  }
}

export function renderTable({
  theadEl,
  tbodyEl,
  rows,
  visible,
  page,
  pageSize,
  sort,
  dir,
  colFilters,
  onRowClick,
  onSortToggle,
  onOpenColPop,
}) {
  // header
  theadEl.replaceChildren();
  for (const key of visible) {
    const col = KEY_TO_COL[key];
    if (!col) continue;
    const th = document.createElement('th');
    if (col.className) th.className = col.className;
    const head = document.createElement('span');
    head.className = 'col-head';
    const title = document.createElement('span');
    title.textContent = col.label;
    if (col.sortable) {
      title.style.cursor = 'pointer';
      title.addEventListener('click', () => onSortToggle(col.key));
      if (sort === col.key) title.textContent += dir === 'asc' ? ' ▲' : ' ▼';
    }
    head.append(title);
    if (col.filterable) {
      const btn = document.createElement('button');
      btn.className = 'col-filter-btn';
      btn.textContent = '▾';
      btn.title = 'Filter this column';
      if (colFilters && colFilters[col.key] && colFilters[col.key].size > 0) {
        btn.classList.add('active');
      }
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        onOpenColPop(col.key, btn);
      });
      head.append(btn);
    }
    th.append(head);
    theadEl.append(th);
  }

  // body (paginated slice)
  const start = (page - 1) * pageSize;
  const slice = rows.slice(start, start + pageSize);
  tbodyEl.replaceChildren();
  if (slice.length === 0) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = visible.length || 1;
    td.style.textAlign = 'center';
    td.style.color = 'var(--fg-3)';
    td.style.padding = '24px';
    td.textContent = 'No tweets match the current filters.';
    tr.append(td);
    tbodyEl.append(tr);
    return;
  }
  for (const r of slice) {
    const tr = document.createElement('tr');
    tr.dataset.tweetId = r.tweet_id;
    tr.addEventListener('click', () => onRowClick(r));
    for (const key of visible) {
      const col = KEY_TO_COL[key];
      if (!col) continue;
      const td = document.createElement('td');
      if (col.className) td.className = col.className;
      td.innerHTML = col.render(r);
      tr.append(td);
    }
    tbodyEl.append(tr);
  }
}

export function openColumnFilterPopup({
  popEl,
  anchorBtn,
  colKey,
  allRows,
  activeFilters,
  onChange,
  onSort,
}) {
  const col = KEY_TO_COL[colKey];
  if (!col) return;
  const rect = anchorBtn.getBoundingClientRect();
  popEl.style.top = `${Math.round(rect.bottom + 4)}px`;
  popEl.style.left = `${Math.max(8, Math.round(rect.right - 240))}px`;
  popEl.hidden = false;
  popEl.replaceChildren();

  const sortRow = document.createElement('div');
  sortRow.className = 'col-sort-row';
  const asc = document.createElement('button');
  asc.className = 'btn ghost';
  asc.textContent = 'Sort A → Z';
  asc.addEventListener('click', () => {
    onSort('asc');
    close();
  });
  const desc = document.createElement('button');
  desc.className = 'btn ghost';
  desc.textContent = 'Sort Z → A';
  desc.addEventListener('click', () => {
    onSort('desc');
    close();
  });
  sortRow.append(asc, desc);
  popEl.append(sortRow);

  const search = document.createElement('input');
  search.type = 'search';
  search.className = 'col-search';
  search.placeholder = `Filter ${col.label.toLowerCase()}…`;
  popEl.append(search);

  // Aggregate value counts.
  const counts = new Map();
  for (const r of allRows) {
    const v = formatForFilter(r, colKey);
    counts.set(v, (counts.get(v) ?? 0) + 1);
  }
  const allValues = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  const active = new Set(activeFilters[colKey] || []);
  const allowAll = active.size === 0;

  const list = document.createElement('div');
  list.className = 'col-values';
  popEl.append(list);

  function renderList(filter) {
    list.replaceChildren();
    const f = filter.toLowerCase();
    for (const [value, count] of allValues) {
      if (f && !String(value).toLowerCase().includes(f)) continue;
      const lab = document.createElement('label');
      lab.className = 'col-val';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = allowAll || active.has(value);
      cb.addEventListener('change', () => {
        if (cb.checked) active.add(value);
        else active.delete(value);
      });
      const txt = document.createElement('span');
      txt.textContent = value === '' ? '(blank)' : value;
      const cnt = document.createElement('span');
      cnt.className = 'count';
      cnt.textContent = String(count);
      lab.append(cb, txt, cnt);
      list.append(lab);
    }
  }
  renderList('');
  search.addEventListener('input', () => renderList(search.value));

  const actions = document.createElement('div');
  actions.className = 'col-actions';
  const apply = document.createElement('button');
  apply.className = 'btn';
  apply.textContent = 'Apply';
  const clear = document.createElement('button');
  clear.className = 'btn ghost';
  clear.textContent = 'Clear';
  const cancel = document.createElement('button');
  cancel.className = 'btn ghost';
  cancel.textContent = 'Cancel';
  apply.addEventListener('click', () => {
    if (active.size === allValues.length) {
      onChange(colKey, new Set());
    } else {
      onChange(colKey, active);
    }
    close();
  });
  clear.addEventListener('click', () => {
    onChange(colKey, new Set());
    close();
  });
  cancel.addEventListener('click', close);
  actions.append(apply, clear, cancel);
  popEl.append(actions);

  function close() {
    popEl.hidden = true;
    document.removeEventListener('mousedown', away);
  }
  function away(e) {
    if (!popEl.contains(e.target) && e.target !== anchorBtn) close();
  }
  setTimeout(() => document.addEventListener('mousedown', away), 0);
}

// ---- helpers ----
function escape(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    c === '&' ? '&amp;' : c === '<' ? '&lt;' : c === '>' ? '&gt;' : c === '"' ? '&quot;' : '&#39;'
  );
}
function truncate(s, n) {
  if (typeof s !== 'string') return '';
  if (s.length <= n) return s;
  return s.slice(0, n) + '…';
}
function fmtDate(iso) {
  if (typeof iso !== 'string' || iso.length < 10) return iso || '';
  return iso.slice(0, 10);
}
function fmtNum(v) {
  if (v == null) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString('en-US');
}
function mediaFlags(r) {
  const media = Array.isArray(r.media) ? r.media : [];
  if (media.length === 0) return '<span class="muted">—</span>';
  const kinds = new Map();
  for (const m of media) {
    const k = m && m.media_type;
    if (!k) continue;
    kinds.set(k, (kinds.get(k) ?? 0) + 1);
  }
  const html = [];
  for (const [kind, count] of kinds) {
    const cls = kind === 'animated_gif' ? 'gif' : kind === 'video' ? 'video' : 'photo';
    html.push(
      `<span class="media-flag ${cls}">${kind === 'animated_gif' ? 'gif' : kind}${count > 1 ? ` ×${count}` : ''}</span>`
    );
  }
  return `<span class="media-flags">${html.join('')}</span>`;
}
