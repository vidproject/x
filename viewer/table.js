// Table rendering with Excel-style column-header filter popups.
//
// Columns are defined as { key, label, render, filterable, sortable, default }.
// "default" controls whether the column is visible on first load — deletion is
// deliberately false per the deemphasis brief.
//
// The Tags column is special-cased: its filter popup aggregates tags into
// namespace parents plus exact-tag children, and a tweet matches if its tag
// set intersects the selected tags/categories (vs. equals one row value).
//
// Threading: when the dataset contains conversation threads, rows are
// rendered as collapsible thread groups (master row + indented slaves).
// Sort/filter/search operate on individual rows; threads simply group them
// for display. Standalone rows (the vast majority) render exactly as before.

import { combineTagMainSub, formatForFilter, tagNames, tagNamespace, tagSubtype } from './store.js';

export const COLUMNS = [
  {
    key: 'account_handle',
    label: 'Account',
    default: true,
    filterable: true,
    sortable: true,
    render: renderAccountCell,
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
    key: 'tags',
    label: 'Tags',
    default: true,
    filterable: true,
    sortable: false,
    render: (r) => renderTagPills(r),
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
    key: 'community_note',
    label: 'Note',
    default: false,
    filterable: true,
    sortable: false,
    render: (r) =>
      r.community_note
        ? `<span class="pill cn" title="${escape(r.community_note.summary ?? '')}">CN</span>`
        : '—',
  },
  {
    key: 'is_truncated',
    label: 'Trunc',
    default: false,
    filterable: true,
    sortable: false,
    render: (r) =>
      r.is_truncated
        ? '<span class="pill trunc" title="Only the 280-char head was returned">trunc</span>'
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

/**
 * Render the table body. When `threads` is supplied, rows are grouped into
 * thread blocks; otherwise the flat row list paints exactly as before.
 *
 * @param {{
 *   theadEl: HTMLElement, tbodyEl: HTMLElement,
 *   rows: Array<Record<string, unknown>>,
 *   threads?: Array<{master:any, slaves:any[], matchedCount:number, threadId:string}>,
 *   visible: string[], page: number, pageSize: number,
 *   sort: string, dir: 'asc'|'desc',
 *   colFilters: Record<string, Set<string>>,
 *   expandedThreads?: Set<string>,
 *   onRowClick: (row:any)=>void,
 *   onSortToggle: (key:string)=>void,
 *   onOpenColPop: (key:string, btn:HTMLElement)=>void,
 *   onToggleThread?: (threadId:string)=>void,
 * }} args
 */
export function renderTable(args) {
  const {
    theadEl,
    tbodyEl,
    rows,
    threads,
    visible,
    page,
    pageSize,
    sort,
    dir,
    colFilters,
    expandedThreads,
    onRowClick,
    onSortToggle,
    onOpenColPop,
    onToggleThread,
  } = args;
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

  // body — threads-or-rows
  tbodyEl.replaceChildren();
  if (threads && threads.length > 0) {
    paintThreaded({
      tbodyEl,
      threads,
      visible,
      page,
      pageSize,
      expandedThreads: expandedThreads ?? new Set(),
      onRowClick,
      onToggleThread,
    });
    return;
  }

  // flat fallback (kept for code paths that pre-empt threading)
  const start = (page - 1) * pageSize;
  const slice = rows.slice(start, start + pageSize);
  if (slice.length === 0) {
    emptyMessage(tbodyEl, visible.length || 1);
    return;
  }
  for (const r of slice) {
    tbodyEl.append(buildRow(r, visible, onRowClick));
  }
}

function paintThreaded({
  tbodyEl,
  threads,
  visible,
  page,
  pageSize,
  expandedThreads,
  onRowClick,
  onToggleThread,
}) {
  // Pagination counts threads, not rows. A page is N threads regardless of
  // how big they are when expanded. Keeps page sizes predictable.
  const start = (page - 1) * pageSize;
  const slice = threads.slice(start, start + pageSize);
  if (slice.length === 0) {
    emptyMessage(tbodyEl, visible.length || 1);
    return;
  }
  for (const thread of slice) {
    const expanded = expandedThreads.has(thread.threadId);
    const masterRow = buildRow(thread.master, visible, onRowClick);
    masterRow.classList.add('thread-master');
    masterRow.dataset.threadId = thread.threadId;
    const hasSelf = thread.selfSlaves.length > 0;
    const hasOther = thread.otherSlaves.length > 0;
    if (hasSelf || hasOther) {
      masterRow.classList.add('has-slaves');
      decorateMasterFirstCell(masterRow, thread, expanded, onToggleThread);
    }
    tbodyEl.append(masterRow);
    // Self-replies inline-expand under the master. Tracked-other and
    // public replies do not — they're reachable via the sidepanel on
    // master-row click so the table doesn't get spammed by a hundred
    // random reactions to a viral DHS tweet.
    if (expanded && hasSelf) {
      for (const slave of thread.selfSlaves) {
        const sr = buildRow(slave, visible, onRowClick);
        sr.classList.add('thread-slave');
        sr.dataset.threadId = thread.threadId;
        tbodyEl.append(sr);
      }
    }
  }
}

function decorateMasterFirstCell(masterRow, thread, expanded, onToggleThread) {
  const firstCell = masterRow.firstElementChild;
  if (!firstCell) return;
  const selfCount = thread.selfSlaves.length;
  const otherCount = thread.otherSlaves.length;
  const wrap = document.createElement('span');
  wrap.className = 'thread-affordances';
  if (selfCount > 0) {
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'thread-toggle';
    toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    toggle.title = expanded
      ? `Collapse ${selfCount} self-repl${selfCount === 1 ? 'y' : 'ies'}`
      : `Expand ${selfCount} self-repl${selfCount === 1 ? 'y' : 'ies'}`;
    toggle.textContent = `${expanded ? '▾' : '▸'} ${selfCount} self-repl${selfCount === 1 ? 'y' : 'ies'}`;
    toggle.addEventListener('click', (e) => {
      e.stopPropagation();
      onToggleThread?.(thread.threadId);
    });
    wrap.append(toggle);
  }
  if (otherCount > 0) {
    // Non-self replies aren't inlined; the badge invites the user to
    // open the sidepanel, where they appear in a dedicated section.
    // The badge itself doesn't take a click handler — it inherits the
    // row's click → open-sidepanel behavior.
    const badge = document.createElement('span');
    badge.className = 'thread-others-badge';
    badge.title = `${otherCount} reply / replies from other accounts — click the row to view in the side panel`;
    badge.textContent = `↪ ${otherCount} other${otherCount === 1 ? '' : 's'}`;
    wrap.append(badge);
  }
  firstCell.prepend(wrap);
}

function buildRow(r, visible, onRowClick) {
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
  return tr;
}

function emptyMessage(tbodyEl, colspan) {
  const tr = document.createElement('tr');
  const td = document.createElement('td');
  td.colSpan = colspan;
  td.style.textAlign = 'center';
  td.style.color = 'var(--fg-3)';
  td.style.padding = '24px';
  td.textContent = 'No tweets match the current filters.';
  tr.append(td);
  tbodyEl.append(tr);
}

/**
 * Open the column-filter popup. The Tags column gets a custom aggregator
 * that counts tag occurrences across all rows (vs. one value per row).
 */
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

  if (colKey !== 'tags') {
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
  }

  const search = document.createElement('input');
  search.type = 'search';
  search.className = 'col-search';
  search.placeholder = `Filter ${col.label.toLowerCase()}…`;
  popEl.append(search);

  // Aggregate value counts.
  const counts = new Map();
  if (colKey === 'tags') {
    for (const r of allRows) {
      for (const t of tagNames(r)) {
        counts.set(t, (counts.get(t) ?? 0) + 1);
      }
    }
  } else {
    for (const r of allRows) {
      const v = formatForFilter(r, colKey);
      counts.set(v, (counts.get(v) ?? 0) + 1);
    }
  }
  const allValues =
    colKey === 'tags'
      ? buildTagFilterValues(counts)
      : [...counts.entries()]
          .sort((a, b) => b[1] - a[1])
          .map(([value, count]) => ({ value, count, display: value === '' ? '(blank)' : value }));
  const allValueKeys = allValues.map((p) => p.value);
  const valueByKey = new Map(allValues.map((p) => [p.value, p]));
  const childValuesByParent = new Map();
  if (colKey === 'tags') {
    for (const p of allValues) {
      if (!p.child) continue;
      const children = childValuesByParent.get(p.parent) ?? [];
      children.push(p.value);
      childValuesByParent.set(p.parent, children);
    }
  }
  const rawActive = activeFilters[colKey];
  const hasActive =
    rawActive instanceof Set
      ? rawActive.size > 0
      : Array.isArray(rawActive)
        ? rawActive.length > 0
        : !!rawActive;
  const active = new Set(hasActive ? rawActive : allValueKeys);
  if (colKey === 'tags') normalizeTagSelections(active, allValues, childValuesByParent);

  const list = document.createElement('div');
  list.className = 'col-values';
  popEl.append(list);

  function renderList(filter) {
    list.replaceChildren();
    const f = filter.trim().toLowerCase();
    const visible = allValues.filter((p) => filterPairMatches(p, f));
    if (visible.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'col-empty';
      empty.textContent = 'No values';
      list.append(empty);
      return;
    }

    const allVisible = document.createElement('label');
    allVisible.className = 'col-val select-all';
    const allVisibleCb = document.createElement('input');
    allVisibleCb.type = 'checkbox';
    allVisibleCb.checked = visible.every((p) => active.has(p.value));
    allVisibleCb.indeterminate = !allVisibleCb.checked && visible.some((p) => active.has(p.value));
    allVisibleCb.addEventListener('change', () => {
      for (const p of visible) setFilterValue(p.value, allVisibleCb.checked);
      renderList(search.value);
    });
    const allVisibleTxt = document.createElement('span');
    allVisibleTxt.textContent = 'Select all';
    allVisible.append(allVisibleCb, allVisibleTxt);
    list.append(allVisible);

    for (const p of visible) {
      const lab = document.createElement('label');
      lab.className = p.child ? 'col-val child' : 'col-val';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = active.has(p.value);
      if (colKey === 'tags' && !p.child) {
        const children = childValuesByParent.get(p.value) ?? [];
        const checkedChildren = children.filter((child) => active.has(child)).length;
        cb.indeterminate = !cb.checked && checkedChildren > 0 && checkedChildren < children.length;
      }
      cb.addEventListener('change', () => {
        setFilterValue(p.value, cb.checked);
        renderList(search.value);
      });
      const txt = document.createElement('span');
      if (p.child) {
        const prefix = document.createElement('span');
        prefix.className = 'tag-child-prefix';
        prefix.textContent = '↳';
        txt.append(prefix, document.createTextNode(p.display));
      } else {
        txt.textContent = p.display;
      }
      const cnt = document.createElement('span');
      cnt.className = 'count';
      cnt.textContent = String(p.count);
      lab.append(cb, txt, cnt);
      list.append(lab);
    }

    function setFilterValue(value, checked) {
      const pair = valueByKey.get(value);
      if (colKey === 'tags' && pair && !pair.child) {
        const values = [pair.value, ...(childValuesByParent.get(pair.value) ?? [])];
        for (const key of values) {
          if (checked) active.add(key);
          else active.delete(key);
        }
        return;
      }
      if (checked) active.add(value);
      else active.delete(value);
      if (colKey === 'tags' && pair?.child) {
        active.delete(pair.parent);
        const siblings = childValuesByParent.get(pair.parent) ?? [];
        if (siblings.length > 0 && siblings.every((key) => active.has(key))) {
          active.add(pair.parent);
        }
      }
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
    if (colKey === 'tags') normalizeTagSelections(active, allValues, childValuesByParent);
    if (allValueKeys.every((value) => active.has(value))) {
      onChange(colKey, new Set());
    } else {
      onChange(
        colKey,
        colKey === 'tags' ? compressTagSelections(active, allValues) : new Set(active)
      );
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

function buildTagFilterValues(counts) {
  const byNamespace = new Map();
  for (const [tag, count] of counts.entries()) {
    const ns = tagNamespace(tag) || 'tag';
    let info = byNamespace.get(ns);
    if (!info) {
      info = { count: 0, tags: [] };
      byNamespace.set(ns, info);
    }
    info.count += count;
    info.tags.push({ tag, count });
  }

  const values = [];
  const namespaces = [...byNamespace.entries()].sort((a, b) => {
    const delta = b[1].count - a[1].count;
    return delta || a[0].localeCompare(b[0]);
  });
  for (const [ns, info] of namespaces) {
    values.push({ value: ns, count: info.count, display: `${ns}:` });
    for (const child of info.tags.sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag))) {
      values.push({
        value: combineTagMainSub(ns, child.tag),
        count: child.count,
        parent: ns,
        sub: child.tag,
        child: true,
        display: tagSubtype(child.tag),
      });
    }
  }
  return values;
}

function normalizeTagSelections(active, allValues, childValuesByParent) {
  for (const p of allValues) {
    if (p.child && active.has(p.sub)) {
      active.delete(p.sub);
      active.add(p.value);
    }
  }
  for (const [parent, children] of childValuesByParent.entries()) {
    if (active.has(parent)) {
      for (const child of children) active.add(child);
    } else if (children.length > 0 && children.every((child) => active.has(child))) {
      active.add(parent);
    }
  }
}

function compressTagSelections(active, allValues) {
  const next = new Set();
  for (const p of allValues) {
    if (p.child) {
      if (!active.has(p.parent) && active.has(p.value)) next.add(p.value);
    } else if (active.has(p.value)) {
      next.add(p.value);
    }
  }
  return next;
}

function filterPairMatches(pair, filter) {
  if (!filter) return true;
  return [pair.value, pair.display, pair.parent, pair.sub]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(filter));
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
// Per-author user map injected by app.js. Empty by default; populated
// from data/users.json once it loads.
let userLookup = new Map();
export function setUserLookup(map) {
  userLookup = map instanceof Map ? map : new Map();
}

function renderAccountCell(r) {
  const handle = r.account_handle ?? '';
  const userMeta = userLookup.get(handle) ?? {};
  const avatar = userMeta.avatar_url || r.author?.avatar_url || null;
  const displayName = userMeta.display_name || r.author?.display_name || null;
  const verifiedBadge = userMeta.verified || userMeta.is_blue_verified ? ' ✓' : '';
  const avatarHtml = avatar
    ? `<img class="acc-avatar" loading="lazy" alt="" src="${escape(avatar)}" />`
    : '<span class="acc-avatar acc-avatar-placeholder">·</span>';
  const handleHtml = `<span class="handle">@${escape(handle)}</span>`;
  const nameHtml = displayName
    ? `<span class="display-name" title="${escape(displayName)}">${escape(displayName)}${verifiedBadge}</span>`
    : '';
  return `<span class="acc-cell">${avatarHtml}${handleHtml}${nameHtml}</span>`;
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

function renderTagPills(r) {
  const tags = Array.isArray(r.tags) ? r.tags : [];
  if (tags.length === 0) return '<span class="muted">—</span>';
  // Cap rendered pills to avoid blowing out the cell on
  // crime-heavy DHS replies. The sidepanel shows the full list.
  const VISIBLE = 6;
  const html = [];
  for (const entry of tags.slice(0, VISIBLE)) {
    const name = typeof entry === 'string' ? entry : entry?.tag;
    if (!name) continue;
    const ns = String(name).split(':', 1)[0];
    const tentative = typeof entry === 'object' && entry?.tentative ? ' tentative' : '';
    const titleSuffix = typeof entry === 'object' && entry?.tentative ? ' (tentative)' : '';
    html.push(
      `<span class="tag-pill ns-${escape(ns)}${tentative}" title="${escape(name)}${titleSuffix}">${escape(name)}</span>`
    );
  }
  if (tags.length > VISIBLE) {
    html.push(`<span class="tag-pill more">+${tags.length - VISIBLE}</span>`);
  }
  return `<span class="tag-pills">${html.join('')}</span>`;
}
