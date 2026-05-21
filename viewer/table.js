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
import { tagEntryName, tagNamespaceFor, tagTreeFromEntries } from './tag_hierarchy.js';

const MEDIA_COL_KEY = 'media_kinds';
export const TAG_CERTAINTY_LABELS = {
  all: 'Firm + tentative',
  firm: 'Firm only',
  tentative: 'Tentative only',
};
const TAG_FACET_SECTIONS = [
  {
    label: 'Primary facets',
    namespaces: ['topic', 'media', 'theme', 'legal', 'crime'],
  },
  {
    label: 'Evidence terms',
    namespaces: ['slogan', 'phrase', 'action', 'frame', 'subject', 'video', 'audio', 'speaker'],
  },
  {
    label: 'Analysis fields',
    namespaces: ['agency', 'country', 'origin', 'state', 'branch', 'status', 'format', 'genre'],
  },
];
const TAG_NAMESPACE_RANK = new Map(
  TAG_FACET_SECTIONS.flatMap((section, sectionIndex) =>
    section.namespaces.map((ns, index) => [ns, sectionIndex * 100 + index])
  )
);
const TAG_CHILD_VISIBLE_LIMIT = 5;
const TAG_CHILD_MIN_VISIBLE_COUNT = 10;
const MEDIA_THUMBNAIL_KEYS = [
  'thumbnail_url',
  'thumbnailUrl',
  'thumb_url',
  'thumbUrl',
  'poster_url',
  'posterUrl',
  'preview_image_url',
  'previewImageUrl',
  'preview_url',
  'previewUrl',
];

let mediaColumnConfig = {
  previews: false,
  posterBySha: new Map(),
};

export const COLUMNS = [
  {
    key: MEDIA_COL_KEY,
    label: 'Media',
    default: true,
    filterable: true,
    sortable: false,
    className: 'col-media',
    render: (r) => renderMediaColumn(r),
  },
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
    className: 'col-text',
    render: (r) =>
      `<span class="cell-text" title="${escape(r.text ?? '')}">${escape(r.text ?? '')}</span>`,
  },
  {
    key: 'tags',
    label: 'Tags',
    default: true,
    filterable: true,
    sortable: false,
    render: (r) => renderHierarchicalTagPills(r),
  },
  {
    key: 'media_description',
    label: 'Media desc',
    default: false,
    filterable: false,
    sortable: false,
    render: (r) => renderMediaDescription(r),
  },
  {
    key: 'video_duration',
    label: 'Video length',
    default: false,
    filterable: false,
    sortable: true,
    className: 'cell-num',
    sortValue: videoDurationSeconds,
    render: (r) => renderVideoDuration(r),
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
    // Deemphasized per the brief: available only via Columns.
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
    key: 'unavailable_detected_at',
    label: 'Unavailable',
    default: false,
    filterable: true,
    sortable: true,
    render: (r) =>
      r.unavailable_detected_at
        ? `<span class="deleted-cell" title="${escape(r.unavailable_text ?? r.unavailable_detected_at)}">${escape(r.unavailable_reason || fmtDate(r.unavailable_detected_at))}</span>`
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
    key: 'share',
    label: 'Share',
    default: true,
    filterable: false,
    sortable: false,
    render: (r) =>
      r.tweet_id
        ? `<a class="tweet-link share-link" href="${escape(shareUrlForRow(r))}">share</a>`
        : '',
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

export function setMediaColumnConfig(config = {}) {
  mediaColumnConfig = {
    previews: Boolean(config.previews),
    posterBySha: config.posterBySha instanceof Map ? config.posterBySha : new Map(),
  };
}

export function defaultVisibleColumns() {
  return normalizeVisibleColumns(COLUMNS.filter((c) => c.default).map((c) => c.key));
}

export function parseVisibleColumns(spec) {
  if (!spec) return defaultVisibleColumns();
  const keys = spec.split(',').filter((k) => KEY_TO_COL[k]);
  return keys.length > 0 ? normalizeVisibleColumns(keys) : defaultVisibleColumns();
}

function normalizeVisibleColumns(keys) {
  const out = keys.filter((key, index) => keys.indexOf(key) === index);
  if (!out.includes(MEDIA_COL_KEY)) return out;
  return [MEDIA_COL_KEY, ...out.filter((key) => key !== MEDIA_COL_KEY)];
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
    row.append(cb, label);
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
 *   threads?: Array<{master:any, slaves:any[], promotedReplies?:any[], matchedCount:number, threadId:string}>,
 *   visible: string[], page: number, pageSize: number,
 *   sort: string, dir: 'asc'|'desc',
 *   colFilters: Record<string, Set<string>>,
 *   expandedThreads?: Set<string>,
 *   onRowClick: (row:any)=>void,
 *   onAccountOpen?: (handle:string, row:any)=>void,
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
    onAccountOpen,
    onSortToggle,
    onOpenColPop,
    onToggleThread,
  } = args;
  const visibleKeys = normalizeVisibleColumns(visible);
  // header
  theadEl.replaceChildren();
  for (const key of visibleKeys) {
    const col = KEY_TO_COL[key];
    if (!col) continue;
    const th = document.createElement('th');
    th.dataset.colKey = col.key;
    if (col.className) th.className = col.className;
    const head = document.createElement('span');
    head.className = 'col-head';
    if (col.key !== MEDIA_COL_KEY) {
      const title = document.createElement('span');
      title.textContent = col.label;
      if (col.sortable) {
        title.style.cursor = 'pointer';
        title.addEventListener('click', () => onSortToggle(col.key));
        if (sort === col.key) title.textContent += dir === 'asc' ? ' ▲' : ' ▼';
      }
      head.append(title);
    }
    if (col.filterable) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'col-filter-btn';
      btn.title = `Filter ${col.label}`;
      btn.setAttribute('aria-label', `Filter ${col.label}`);
      btn.innerHTML =
        '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M2.25 3.25h11.5L9.25 8.3v3.35l-2.5 1.35V8.3L2.25 3.25z"/></svg>';
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
      visible: visibleKeys,
      page,
      pageSize,
      expandedThreads: expandedThreads ?? new Set(),
      onRowClick,
      onAccountOpen,
      onToggleThread,
    });
    return;
  }

  // flat fallback (kept for code paths that pre-empt threading)
  const start = (page - 1) * pageSize;
  const slice = rows.slice(start, start + pageSize);
  if (slice.length === 0) {
    emptyMessage(tbodyEl, visibleKeys.length || 1);
    return;
  }
  for (const r of slice) {
    tbodyEl.append(buildRow(r, visibleKeys, onRowClick, onAccountOpen));
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
  onAccountOpen,
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
    const masterRow = buildRow(thread.master, visible, onRowClick, onAccountOpen);
    masterRow.classList.add('thread-master');
    masterRow.dataset.threadId = thread.threadId;
    const hasSelf = thread.selfSlaves.length > 0;
    const privilegedSlaves = Array.isArray(thread.privilegedSlaves) ? thread.privilegedSlaves : [];
    const hasPrivileged = privilegedSlaves.length > 0;
    const hasOther = thread.otherSlaves.length > 0;
    const hasPromoted = Array.isArray(thread.promotedReplies) && thread.promotedReplies.length > 0;
    if (hasPromoted) {
      masterRow.classList.add('has-promoted-reply');
      masterRow.classList.add(`promoted-${topPromotionCategory(thread.promotedReplies)}`);
    }
    if (hasSelf || hasPrivileged || hasOther) {
      masterRow.classList.add('has-slaves');
      decorateMasterFirstCell(masterRow, thread, expanded, onToggleThread);
    }
    tbodyEl.append(masterRow);
    // Self-replies inline-expand under the master. Tracked-other and
    // public replies do not — they're reachable via the sidepanel on
    // master-row click so the table doesn't get spammed by a hundred
    // random reactions to a viral DHS tweet.
    if (expanded && (hasSelf || hasPrivileged)) {
      for (const slave of [...thread.selfSlaves, ...privilegedSlaves]) {
        const sr = buildRow(slave, visible, onRowClick, onAccountOpen);
        sr.classList.add('thread-slave');
        const privileged = slave.__thread_privileged_category;
        if (privileged) {
          sr.classList.add('thread-privileged-slave');
          sr.classList.add(`privileged-${privileged}`);
        }
        sr.dataset.threadId = thread.threadId;
        tbodyEl.append(sr);
      }
    }
  }
}

function decorateMasterFirstCell(masterRow, thread, expanded, onToggleThread) {
  const targetCell =
    masterRow.querySelector('td[data-col-key="account_handle"]') ?? masterRow.firstElementChild;
  if (!targetCell) return;
  const promotions = Array.isArray(thread.promotedReplies) ? thread.promotedReplies : [];
  const selfCount = thread.selfSlaves.length;
  const privilegedCount = Array.isArray(thread.privilegedSlaves)
    ? thread.privilegedSlaves.length
    : 0;
  const inlineCount = selfCount + privilegedCount;
  const replyCount = thread.otherSlaves.length;
  if (inlineCount === 0 && replyCount === 0 && promotions.length === 0) return;

  const wrap = document.createElement('span');
  wrap.className = `thread-affordances promoted-${topPromotionCategory(promotions)}`;
  if (inlineCount > 0) {
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'thread-toggle';
    toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    const label =
      privilegedCount > 0 && selfCount === 0
        ? `${inlineCount} core repl${inlineCount === 1 ? 'y' : 'ies'}`
        : `${inlineCount} repl${inlineCount === 1 ? 'y' : 'ies'}`;
    toggle.title = expanded ? `Collapse ${label}` : `Expand ${label}`;
    toggle.textContent = `${expanded ? 'v' : '>'} ${label}`;
    toggle.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      onToggleThread?.(thread.threadId);
    });
    wrap.append(toggle);
  }
  if (replyCount > 0) {
    const count = document.createElement('span');
    count.className = 'thread-replies-summary';
    count.textContent = `${replyCount} repl${replyCount === 1 ? 'y' : 'ies'}`;
    wrap.append(count);
  }
  for (const group of promotedReplyGroups(promotions)) {
    wrap.append(promotedReplyBadge(group));
  }
  targetCell.append(wrap);
}

function topPromotionCategory(promotions) {
  if (!promotions.length) return 'none';
  return promotions.some((p) => p?.category === 'core') ? 'core' : 'officials';
}

function promotedReplyGroups(promotions) {
  const byHandle = new Map();
  for (const promo of promotions) {
    const reply = promo?.reply;
    const handle = String(reply?.account_handle ?? '');
    if (!handle) continue;
    const category = promo?.category === 'core' ? 'core' : 'officials';
    const key = `${category}:${handle}`;
    let group = byHandle.get(key);
    if (!group) {
      group = {
        category,
        handle,
        reply,
        count: 0,
      };
      byHandle.set(key, group);
    }
    group.count += 1;
  }
  return [...byHandle.values()].sort((a, b) => {
    const byPriority = promotionPriority(b.category) - promotionPriority(a.category);
    if (byPriority) return byPriority;
    return a.handle.localeCompare(b.handle);
  });
}

function promotionPriority(category) {
  return category === 'core' ? 2 : category === 'officials' ? 1 : 0;
}

function promotedReplyBadge(group) {
  const badge = document.createElement('span');
  badge.className = `thread-reply-badge promo-${group.category}`;
  const avatar = avatarForRow(group.reply);
  if (avatar) {
    const img = document.createElement('img');
    img.className = 'thread-reply-avatar';
    img.loading = 'lazy';
    img.alt = '';
    img.src = avatar;
    badge.append(img);
  } else {
    const placeholder = document.createElement('span');
    placeholder.className = 'thread-reply-avatar thread-reply-avatar-placeholder';
    placeholder.textContent = '·';
    badge.append(placeholder);
  }
  const label = document.createElement('span');
  label.textContent = `@${group.handle}${group.count > 1 ? ` x${group.count}` : ''}`;
  badge.append(label);
  const displayName = displayNameForRow(group.reply);
  badge.title = `${displayName ? `${displayName} ` : ''}@${group.handle} direct reply`;
  return badge;
}

function buildRow(r, visible, onRowClick, onAccountOpen) {
  const tr = document.createElement('tr');
  tr.dataset.tweetId = r.tweet_id;
  tr.addEventListener('click', () => onRowClick(r));
  for (const key of visible) {
    const col = KEY_TO_COL[key];
    if (!col) continue;
    const td = document.createElement('td');
    td.dataset.colKey = col.key;
    if (col.className) td.className = col.className;
    td.innerHTML = col.render(r);
    if (col.key === 'account_handle') {
      for (const btn of td.querySelectorAll('[data-account-profile]')) {
        btn.addEventListener('click', (event) => {
          event.preventDefault();
          event.stopPropagation();
          const handle = btn.getAttribute('data-account-profile') || r.account_handle || '';
          if (handle) onAccountOpen?.(handle, r);
        });
      }
    }
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
  tagCertainty = 'all',
  onTagCertaintyChange,
  mediaSettings,
  onMediaSettingsChange,
}) {
  const col = KEY_TO_COL[colKey];
  if (!col) return;
  const rect = anchorBtn.getBoundingClientRect();
  popEl.style.top = `${Math.round(rect.bottom + 4)}px`;
  popEl.style.left = `${Math.max(8, Math.round(rect.right - 240))}px`;
  popEl.hidden = false;
  popEl.replaceChildren();

  if (colKey === MEDIA_COL_KEY) {
    popEl.append(buildMediaPopupSettings(mediaSettings, onMediaSettingsChange));
  }
  if (colKey === 'tags') {
    popEl.append(buildTagCertaintyControl(tagCertainty, onTagCertaintyChange));
  }

  if (colKey !== 'tags' && col.sortable) {
    const sortRow = document.createElement('div');
    sortRow.className = 'col-sort-row';
    const asc = document.createElement('button');
    asc.type = 'button';
    asc.className = 'btn ghost';
    asc.textContent = 'Sort A → Z';
    asc.addEventListener('click', () => {
      onSort('asc');
      close();
    });
    const desc = document.createElement('button');
    desc.type = 'button';
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
  search.placeholder =
    colKey === 'tags'
      ? 'Search all tag namespaces and values...'
      : `Filter ${col.label.toLowerCase()}...`;
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
  const expandedTagParents = new Set();
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
  list.className = colKey === 'tags' ? 'col-values tag-browser' : 'col-values';
  popEl.append(list);

  function renderList(filter) {
    list.replaceChildren();
    const f = filter.trim().toLowerCase();
    const visible =
      colKey === 'tags' ? allValues : allValues.filter((p) => filterPairMatches(p, f));
    const matchingValues =
      colKey === 'tags' ? allValues.filter((p) => filterPairMatches(p, f)) : visible;
    if (visible.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'col-empty';
      empty.textContent = 'No values';
      list.append(empty);
      return;
    }

    if (colKey === 'tags') {
      const activeBlock = buildActiveTagFilterBlock(allValues, allValueKeys, active, (value) => {
        setFilterValue(value, false);
        renderList(search.value);
      });
      if (activeBlock) list.append(activeBlock);
    }
    if (colKey === 'tags' && f && matchingValues.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'col-empty';
      empty.textContent = 'No matching tags';
      list.append(empty);
      return;
    }

    const allVisible = document.createElement('label');
    allVisible.className = 'col-val select-all';
    const allVisibleCb = document.createElement('input');
    allVisibleCb.type = 'checkbox';
    allVisibleCb.checked = matchingValues.every((p) => active.has(p.value));
    allVisibleCb.indeterminate =
      !allVisibleCb.checked && matchingValues.some((p) => active.has(p.value));
    allVisibleCb.addEventListener('change', () => {
      for (const p of matchingValues) setFilterValue(p.value, allVisibleCb.checked);
      renderList(search.value);
    });
    const allVisibleTxt = document.createElement('span');
    allVisibleTxt.textContent = colKey === 'tags' && f ? 'Select matches' : 'Select all';
    allVisible.append(allVisibleCb, allVisibleTxt);
    list.append(allVisible);

    const renderedValues =
      colKey === 'tags' ? collapsedTagValues(allValues, f, expandedTagParents) : visible;
    for (const p of renderedValues) {
      if (p.section) {
        const section = document.createElement('div');
        section.className = 'tag-facet-section';
        section.textContent = p.section;
        list.append(section);
        continue;
      }
      if (p.hint) {
        const hint = document.createElement('div');
        hint.className = 'tag-browser-hint';
        hint.textContent = p.hint;
        list.append(hint);
        continue;
      }
      if (p.toggle) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'tag-more-btn';
        btn.textContent = p.expanded
          ? `Collapse ${p.parent}: tags`
          : `Search to find ${p.hiddenCount} smaller ${p.parent}: tags`;
        btn.addEventListener('click', () => {
          if (expandedTagParents.has(p.parent)) expandedTagParents.delete(p.parent);
          else expandedTagParents.add(p.parent);
          renderList(search.value);
        });
        list.append(btn);
        continue;
      }
      const lab = document.createElement('label');
      lab.className = p.child ? 'col-val child' : 'col-val namespace';
      if (colKey === 'tags' && !p.child) {
        const expander = document.createElement('button');
        expander.type = 'button';
        expander.className = 'tag-fold-btn';
        expander.textContent = expandedTagParents.has(p.value) ? '-' : '+';
        expander.title = expandedTagParents.has(p.value)
          ? `Collapse ${p.value}: tags`
          : `Open ${p.value}: tags`;
        expander.setAttribute('aria-label', expander.title);
        expander.addEventListener('click', (event) => {
          event.preventDefault();
          event.stopPropagation();
          if (expandedTagParents.has(p.value)) expandedTagParents.delete(p.value);
          else expandedTagParents.add(p.value);
          renderList(search.value);
        });
        lab.append(expander);
      }
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
        prefix.textContent = '';
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
  apply.type = 'button';
  apply.className = 'btn';
  apply.textContent = 'Apply';
  const clear = document.createElement('button');
  clear.type = 'button';
  clear.className = 'btn ghost';
  clear.textContent = 'Clear';
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'btn ghost';
  cancel.textContent = 'Cancel';
  apply.addEventListener('mousedown', stopPopupButtonEvent);
  apply.addEventListener('click', (event) => {
    stopPopupButtonEvent(event);
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
  clear.title = 'Clear this column filter';
  clear.addEventListener('mousedown', stopPopupButtonEvent);
  clear.addEventListener('click', (event) => {
    stopPopupButtonEvent(event);
    onChange(colKey, new Set());
    close();
  });
  cancel.addEventListener('mousedown', stopPopupButtonEvent);
  cancel.addEventListener('click', (event) => {
    stopPopupButtonEvent(event);
    close();
  });
  actions.append(apply, clear, cancel);
  popEl.append(actions);

  function stopPopupButtonEvent(event) {
    event.preventDefault();
    event.stopPropagation();
  }
  function close() {
    popEl.hidden = true;
    document.removeEventListener('mousedown', away);
  }
  function away(e) {
    if (!popEl.contains(e.target) && e.target !== anchorBtn) close();
  }
  setTimeout(() => document.addEventListener('mousedown', away), 0);
}

function buildTagCertaintyControl(tagCertainty = 'all', onTagCertaintyChange) {
  const wrap = document.createElement('div');
  wrap.className = 'tag-certainty-control';
  const label = document.createElement('span');
  label.textContent = 'Certainty';
  const select = document.createElement('select');
  select.className = 'select';
  for (const [value, text] of Object.entries(TAG_CERTAINTY_LABELS)) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = text;
    select.append(option);
  }
  select.value = TAG_CERTAINTY_LABELS[tagCertainty] ? tagCertainty : 'all';
  select.addEventListener('change', () => onTagCertaintyChange?.(select.value));
  wrap.append(label, select);
  return wrap;
}

function buildMediaPopupSettings(mediaSettings = {}, onMediaSettingsChange) {
  const settings = {
    previews: Boolean(mediaSettings?.previews),
    thumbWidth: Number(mediaSettings?.thumbWidth) || 22,
    fit: mediaSettings?.fit === 'vertical' ? 'vertical' : 'horizontal',
  };
  const wrap = document.createElement('div');
  wrap.className = 'media-pop-settings';

  const toggle = document.createElement('label');
  toggle.className = 'media-pop-toggle';
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = settings.previews;
  const toggleText = document.createElement('span');
  toggleText.textContent = 'Show thumbnails';
  toggle.append(cb, toggleText);

  const details = document.createElement('div');
  details.className = 'media-pop-detail';

  const widthLabel = document.createElement('label');
  widthLabel.textContent = 'Max px';
  const width = document.createElement('input');
  width.type = 'number';
  width.min = '16';
  width.max = '48';
  width.step = '1';
  width.value = String(settings.thumbWidth);
  widthLabel.append(width);

  const fitLabel = document.createElement('label');
  fitLabel.textContent = 'Fit';
  const fit = document.createElement('select');
  fit.className = 'select';
  for (const [value, label] of [
    ['horizontal', 'Horizontal'],
    ['vertical', 'Vertical'],
  ]) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    option.selected = value === settings.fit;
    fit.append(option);
  }
  fitLabel.append(fit);

  details.append(widthLabel, fitLabel);
  wrap.append(toggle, details);

  cb.addEventListener('change', () => {
    onMediaSettingsChange?.({ previews: cb.checked });
  });
  width.addEventListener('change', () => {
    onMediaSettingsChange?.({ thumbWidth: width.value });
  });
  fit.addEventListener('change', () => {
    onMediaSettingsChange?.({ fit: fit.value });
  });

  return wrap;
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
    const delta = tagNamespaceRank(a[0]) - tagNamespaceRank(b[0]);
    return delta || b[1].count - a[1].count || a[0].localeCompare(b[0]);
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

function collapsedTagValues(values, filter, expandedParents) {
  const parents = values.filter((p) => !p.child);
  const childrenByParent = new Map();
  for (const child of values.filter((p) => p.child)) {
    const children = childrenByParent.get(child.parent) ?? [];
    children.push(child);
    childrenByParent.set(child.parent, children);
  }

  if (filter) {
    const out = [];
    out.push({ section: 'Matching tags' });
    for (const parent of parents) {
      const children = childrenByParent.get(parent.value) ?? [];
      const matchingChildren = children.filter((child) => filterPairMatches(child, filter));
      const parentMatches = filterPairMatches(parent, filter);
      if (!parentMatches && matchingChildren.length === 0) continue;
      out.push(parent);
      out.push(...matchingChildren);
    }
    return out;
  }

  const out = [];
  let lastSection = '';
  for (const parent of parents) {
    const section = tagNamespaceSection(parent.value);
    if (section !== lastSection) {
      out.push({ section });
      lastSection = section;
    }
    out.push(parent);
    if (!expandedParents.has(parent.value)) continue;

    const children = childrenByParent.get(parent.value) ?? [];
    const visibleChildren =
      children.length === 1
        ? children
        : children
            .filter((child) => child.count >= TAG_CHILD_MIN_VISIBLE_COUNT)
            .slice(0, TAG_CHILD_VISIBLE_LIMIT);
    out.push(...visibleChildren);
    const hiddenCount = Math.max(0, children.length - visibleChildren.length);
    if (hiddenCount > 0) {
      out.push({
        toggle: true,
        parent: parent.value,
        expanded: true,
        hiddenCount,
      });
    }
  }
  out.push({ hint: 'Search to find low-frequency tags inside collapsed namespaces.' });
  return out;
}

function buildActiveTagFilterBlock(allValues, allValueKeys, active, onRemove) {
  if (allValueKeys.every((value) => active.has(value))) return null;
  const selected = [...compressTagSelections(active, allValues)]
    .map((value) => allValues.find((p) => p.value === value))
    .filter(Boolean);
  if (selected.length === 0) return null;

  const wrap = document.createElement('div');
  wrap.className = 'tag-active-filters';
  const title = document.createElement('div');
  title.className = 'tag-active-title';
  title.textContent = 'Active filters';
  wrap.append(title);
  for (const p of selected.slice(0, 12)) {
    const pill = document.createElement('button');
    pill.type = 'button';
    pill.className = 'tag-active-pill';
    pill.title = `Remove ${p.sub || p.value}`;
    pill.textContent = p.child ? `${p.parent}: ${p.display}` : p.display;
    pill.addEventListener('click', () => onRemove(p.value));
    wrap.append(pill);
  }
  if (selected.length > 12) {
    const more = document.createElement('span');
    more.className = 'tag-active-more';
    more.textContent = `+${selected.length - 12} more`;
    wrap.append(more);
  }
  return wrap;
}

function tagNamespaceRank(ns) {
  return TAG_NAMESPACE_RANK.get(ns) ?? 1000;
}

function tagNamespaceSection(ns) {
  for (const section of TAG_FACET_SECTIONS) {
    if (section.namespaces.includes(ns)) return section.label;
  }
  return 'Other namespaces';
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

function shareUrlForRow(row) {
  const url = new URL(location.href);
  const params = new URLSearchParams();
  params.set('tweet', String(row.tweet_id || ''));
  url.hash = params.toString();
  return url.toString();
}

function renderAccountCell(r) {
  const handle = r.account_handle ?? '';
  const userMeta = userMetaForRow(r);
  const avatar = avatarForRow(r);
  const displayName = displayNameForRow(r);
  const verifiedBadge = userMeta.verified || userMeta.is_blue_verified ? ' ✓' : '';
  const avatarHtml = avatar
    ? `<img class="acc-avatar" loading="lazy" alt="" src="${escape(avatar)}" />`
    : '<span class="acc-avatar acc-avatar-placeholder">·</span>';
  const handleHtml = handle
    ? `<button type="button" class="account-profile-link handle" data-account-profile="${escape(handle)}" title="Open @${escape(handle)} profile">@${escape(handle)}</button>`
    : '<span class="handle">@</span>';
  const nameHtml = displayName
    ? `<button type="button" class="account-profile-link display-name" data-account-profile="${escape(handle)}" title="Open @${escape(handle)} profile">${escape(displayName)}${verifiedBadge}</button>`
    : '';
  return `<span class="acc-cell">${avatarHtml}${handleHtml}${renderAccountBadges(userMeta)}${nameHtml}</span>`;
}

function userMetaForRow(r) {
  return userLookup.get(r?.account_handle) ?? {};
}

function renderAccountBadges(userMeta) {
  const badges = serviceBadgesForUserMeta(userMeta);
  if (badges.length === 0) return '';
  const labels = {
    veteran: 'Veteran or military',
    police: 'Police or law enforcement',
    'retired-police': 'Retired police or law enforcement',
    'retired-government': 'Retired government official',
  };
  return badges
    .map((badge) => {
      const key = String(badge ?? '');
      const label = labels[key];
      if (!label) return '';
      return `<span class="acc-badge acc-badge-${escape(key)}" title="${escape(label)}" aria-label="${escape(label)}"></span>`;
    })
    .join('');
}

function serviceBadgesForUserMeta(userMeta) {
  const text = `${userMeta?.display_name ?? ''} ${userMeta?.description ?? ''}`;
  const badges = [];
  if (
    /\b(veteran|vet\b|retired\s+(?:u\.?s\.?\s+)?(?:army|navy|marine|air\s+force|space\s+force)|(?:u\.?s\.?\s+)?(?:army|navy|marines?|air\s+force|space\s+force)\b)/i.test(
      text
    )
  ) {
    badges.push('veteran');
  }
  if (
    /\b(?:ret\.?|retired|former|ex)[-\s]+(?:deputy\s+)?(?:police|sheriff|law\s+enforcement|border\s+patrol\s+agent|(?:u\.?s\.?\s+)?marshal|criminal\s+investigator)\b|\b(?:deputy\s+)?(?:u\.?s\.?\s+)?marshal\s+ret\.?\b/i.test(
      text
    )
  ) {
    badges.push('retired-police');
  } else if (
    /\b(police|sheriff|law\s+enforcement|border\s+patrol\s+agent|(?:deputy\s+)?(?:u\.?s\.?\s+)?marshal|criminal\s+investigator)\b/i.test(
      text
    )
  ) {
    badges.push('police');
  }
  if (
    userMeta?.verified_type !== 'Government' &&
    /\b(?:ret\.?|retired|former|ex)[-\s]+(?:secretary|administrator|commissioner|director|u\.?s\.?\s+attorney|inspector\s+general|civil\s+servant|federal\s+employee|government\s+official)\b/i.test(
      text
    )
  ) {
    badges.push('retired-government');
  }
  return badges;
}

function avatarForRow(r) {
  const userMeta = userMetaForRow(r);
  return userMeta.avatar_url || r?.author?.avatar_url || null;
}

function displayNameForRow(r) {
  const userMeta = userMetaForRow(r);
  return userMeta.display_name || r?.author?.display_name || null;
}

function renderMediaColumn(r) {
  if (mediaColumnConfig.previews) {
    const preview = mediaPreviewForRow(r);
    if (preview) return renderMediaPreview(preview);
  }
  return renderMediaSymbol(r);
}

function mediaPreviewForRow(r) {
  const media = Array.isArray(r.media) ? r.media : [];
  for (const m of media) {
    if (!m || typeof m !== 'object') continue;
    const type = normalizeMediaType(m.media_type);
    const url = mediaPreviewUrl(m, type);
    if (!url) continue;
    return {
      type,
      url,
      alt: type === 'photo' ? m.alt_text || 'Archived photo' : `${mediaTypeLabel(type)} poster`,
    };
  }
  return null;
}

function mediaPreviewUrl(media, type) {
  if (type === 'photo') return stringOrNull(media.release_asset_url);
  if (type !== 'video' && type !== 'gif') return null;
  for (const key of MEDIA_THUMBNAIL_KEYS) {
    const url = stringOrNull(media[key]);
    if (url) return url;
  }
  const sha = stringOrNull(media.sha256);
  return sha ? mediaColumnConfig.posterBySha.get(sha) || null : null;
}

function renderMediaPreview(preview) {
  const videoClass = preview.type === 'video' || preview.type === 'gif' ? ' has-video' : '';
  const title = `${mediaTypeLabel(preview.type)} preview`;
  return (
    `<span class="media-thumb-frame media-${escape(preview.type)}${videoClass}" title="${escape(title)}">` +
    `<img class="media-thumb" loading="lazy" decoding="async" fetchpriority="low" alt="${escape(preview.alt)}" src="${escape(preview.url)}" />` +
    '</span>'
  );
}

function renderMediaSymbol(r) {
  const summary = mediaSummary(r);
  return `<span class="media-symbol media-${summary.key}" title="${escape(summary.label)}" aria-label="${escape(summary.label)}">${summary.symbol}</span>`;
}

function mediaSummary(r) {
  const media = Array.isArray(r.media) ? r.media : [];
  let photos = 0;
  let videos = 0;
  let gifs = 0;
  for (const item of media) {
    const type = normalizeMediaType(item?.media_type);
    if (type === 'photo') photos += 1;
    else if (type === 'video') videos += 1;
    else if (type === 'gif') gifs += 1;
  }
  if (photos > 0 && videos + gifs > 0) {
    return { key: 'mixed', label: 'photo + video', symbol: '🖼️▶️' };
  }
  if (photos > 0) return { key: 'photo', label: 'photo', symbol: '🖼️' };
  if (videos > 0) return { key: 'video', label: 'video', symbol: '▶️' };
  if (gifs > 0) return { key: 'gif', label: 'gif', symbol: '🎞️' };
  return { key: 'text', label: 'text only', symbol: '📝' };
}

function normalizeMediaType(type) {
  if (type === 'animated_gif') return 'gif';
  if (type === 'video' || type === 'photo') return type;
  return '';
}

function mediaTypeLabel(type) {
  return type === 'gif' ? 'gif' : type || 'media';
}

function stringOrNull(value) {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

/**
 * Longest video/animated_gif duration in seconds (0 when the tweet has no
 * video media). Used both for the cell renderer and as the sort key.
 */
function videoDurationSeconds(r) {
  const media = Array.isArray(r.media) ? r.media : [];
  let max = 0;
  for (const m of media) {
    if (!m) continue;
    if (m.media_type !== 'video' && m.media_type !== 'animated_gif') continue;
    const d = Number(m.duration_sec);
    if (Number.isFinite(d) && d > max) max = d;
  }
  return max;
}

function renderVideoDuration(r) {
  const secs = videoDurationSeconds(r);
  if (secs <= 0) return '<span class="muted">—</span>';
  const minutes = Math.floor(secs / 60);
  const remainder = Math.round(secs - minutes * 60);
  const label =
    minutes > 0 ? `${minutes}:${String(remainder).padStart(2, '0')}` : `${Math.round(secs)}s`;
  return `<span title="${escape(`${secs.toFixed(1)}s`)}">${escape(label)}</span>`;
}

function renderMediaDescription(r) {
  const insights = Array.isArray(r.media_insights) ? r.media_insights : [];
  const text = insights
    .map((entry) => entry?.description)
    .filter(Boolean)
    .join(' ');
  if (!text) return '<span class="muted">—</span>';
  return `<span class="cell-text" title="${escape(text)}">${escape(text)}</span>`;
}

function renderHierarchicalTagPills(r) {
  const tree = tagTreeFromEntries(Array.isArray(r.tags) ? r.tags : []);
  if (tree.length === 0) return '<span class="muted">&mdash;</span>';
  // Cap rendered pills to avoid blowing out the cell on crime-heavy rows.
  // Parent + visible children count as separate pills; the sidepanel shows all.
  const visible = 6;
  const html = [];
  let rendered = 0;
  let total = 0;
  for (const node of tree) total += 1 + node.children.length;
  for (const node of tree) {
    if (rendered >= visible) break;
    const remaining = visible - rendered;
    const children = node.children.slice(0, Math.max(0, remaining - 1));
    html.push(renderTagNode(node.entry, children));
    rendered += 1 + children.length;
  }
  if (total > rendered) {
    html.push(`<span class="tag-pill more">+${total - rendered}</span>`);
  }
  return `<span class="tag-pills tag-tree">${html.join('')}</span>`;
}

function renderTagNode(entry, children) {
  const childHtml = children.map((child) => renderTagChild(child)).join('');
  return `<span class="tag-node">${renderTagPill(entry)}${childHtml}</span>`;
}

function renderTagChild(entry) {
  return `<span class="tag-child">${renderTagPill(entry, { child: true })}</span>`;
}

function renderTagPill(entry, options = {}) {
  const name = tagEntryName(entry);
  if (!name) return '';
  const ns = tagNamespaceFor(name);
  const tentative = typeof entry === 'object' && entry?.tentative ? ' tentative' : '';
  const titleSuffix = typeof entry === 'object' && entry?.tentative ? ' (tentative)' : '';
  const child = options.child ? ' tag-pill-child' : '';
  return `<span class="tag-pill ns-${escape(ns)}${tentative}${child}" title="${escape(name)}${titleSuffix}">${escape(name)}</span>`;
}

function renderTagPills(r) {
  const tags = uniqueTagEntries(Array.isArray(r.tags) ? r.tags : []);
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

function uniqueTagEntries(tags) {
  const seen = new Set();
  const out = [];
  for (const entry of tags) {
    const name = typeof entry === 'string' ? entry : entry?.tag;
    if (!name || seen.has(name)) continue;
    seen.add(name);
    out.push(entry);
  }
  return out;
}
