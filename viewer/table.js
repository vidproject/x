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

import {
  combineTagMainSub,
  formatForFilter,
  splitTagMainSub,
  retweetedByHandles,
  tagNamespace,
  tagSubtype,
} from './store.js?v=lazycat2';
import { tagEntryName, tagNamespaceFor, tagTreeFromEntries } from './tag_hierarchy.js?v=lazycat2';
import { archiveShareUrlForRow, copyTextToClipboard, xTweetUrlForRow } from './links.js?v=lazycat2';

const MEDIA_COL_KEY = 'media_kinds';
export const TAG_CERTAINTY_LABELS = {
  all: 'Include tentative tags (default)',
  firm: 'Firm tags only',
  tentative: 'Only tentative tags',
};
const TAG_FACET_SECTIONS = [
  {
    label: 'Primary facets',
    namespaces: ['topic', 'event', 'media', 'theme', 'religion', 'legal', 'crime'],
  },
  {
    label: 'Evidence terms',
    namespaces: [
      'slogan',
      'phrase',
      'action',
      'frame',
      'subject',
      'event',
      'genre',
      'video',
      'audio',
      'speaker',
    ],
  },
  {
    label: 'Analysis fields',
    namespaces: ['agency', 'country', 'origin', 'state', 'military', 'status', 'format'],
  },
];
const TAG_NAMESPACE_RANK = new Map(
  TAG_FACET_SECTIONS.flatMap((section, sectionIndex) =>
    section.namespaces.map((ns, index) => [ns, sectionIndex * 100 + index])
  )
);
const TAG_CHILD_VISIBLE_LIMIT = 8;
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
    key: 'ocr_text',
    label: 'OCR',
    default: false,
    filterable: false,
    sortable: false,
    render: (r) => renderOcrCell(r),
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
        ? `<a class="tweet-link share-link" href="${escape(archiveShareUrlForRow(r))}" data-copy-share="1" aria-label="Copy share link" title="Copy share link">&#128279;</a>`
        : '',
  },
  {
    key: 'tweet_url',
    label: 'Link',
    default: true,
    filterable: false,
    sortable: false,
    render: (r) =>
      xTweetUrlForRow(r)
        ? `<a class="tweet-link" href="${escape(xTweetUrlForRow(r))}" target="_blank" rel="noopener">↗</a>`
        : '',
  },
  {
    key: 'retweeted_by',
    label: 'Retweeted by',
    default: false,
    filterable: true,
    sortable: false,
    render: (r) => renderRetweetedByCell(r),
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
 *   tagCertainty?: string,
 *   expandedThreads?: Set<string>,
 *   onRowClick: (row:any, options?:Record<string, unknown>)=>void,
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
    tagCertainty = 'all',
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
      const hasColumnFilter = colFilters && colFilters[col.key] && colFilters[col.key].size > 0;
      const hasCertaintyFilter = col.key === 'tags' && tagCertainty && tagCertainty !== 'all';
      if (hasColumnFilter || hasCertaintyFilter) {
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
    const hasRetweets =
      Array.isArray(thread.master.__retweet_promotions) &&
      thread.master.__retweet_promotions.length > 0;
    if (hasPromoted) {
      masterRow.classList.add('has-promoted-reply');
      masterRow.classList.add(`promoted-${topPromotionCategory(thread.promotedReplies)}`);
    }
    if (hasRetweets) masterRow.classList.add('has-retweet-promotion');
    if (hasSelf || hasPrivileged || hasOther || hasRetweets) {
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
  const retweets = Array.isArray(thread.master.__retweet_promotions)
    ? thread.master.__retweet_promotions
    : [];
  const selfCount = thread.selfSlaves.length;
  const privilegedCount = Array.isArray(thread.privilegedSlaves)
    ? thread.privilegedSlaves.length
    : 0;
  const inlineCount = selfCount + privilegedCount;
  const replyCount = thread.otherSlaves.length;
  if (inlineCount === 0 && replyCount === 0 && promotions.length === 0 && retweets.length === 0) {
    return;
  }

  const wrap = document.createElement('span');
  wrap.className = `thread-affordances promoted-${topPromotionCategory(promotions)}`;
  if (inlineCount > 0) {
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'thread-toggle';
    toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    const label = inlineReplyLabel(selfCount, privilegedCount);
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
  for (const group of retweetPromotionGroups(retweets)) {
    wrap.append(retweetBadge(group));
  }
  targetCell.append(wrap);
}

function inlineReplyLabel(selfCount, privilegedCount) {
  const labels = [];
  if (selfCount > 0) labels.push(`${selfCount} repl${selfCount === 1 ? 'y' : 'ies'}`);
  if (privilegedCount > 0) {
    labels.push(`${privilegedCount} core repl${privilegedCount === 1 ? 'y' : 'ies'}`);
  }
  return labels.join(' + ');
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
    badge.append(placeholder);
  }
  const label = document.createElement('span');
  label.textContent = `@${group.handle}${group.count > 1 ? ` x${group.count}` : ''}`;
  badge.append(label);
  const displayName = displayNameForRow(group.reply);
  badge.title = `${displayName ? `${displayName} ` : ''}@${group.handle} direct reply`;
  return badge;
}

function retweetPromotionGroups(promotions) {
  const byHandle = new Map();
  for (const promo of promotions) {
    const retweet = promo?.retweet;
    const handle = String(retweet?.account_handle ?? '');
    if (!handle) continue;
    let group = byHandle.get(handle);
    if (!group) {
      group = {
        handle,
        retweet,
        count: 0,
      };
      byHandle.set(handle, group);
    }
    group.count += 1;
  }
  return [...byHandle.values()].sort((a, b) => a.handle.localeCompare(b.handle));
}

function retweetBadge(group) {
  const badge = document.createElement('span');
  badge.className = 'thread-retweet-badge';
  const avatar = avatarForRow(group.retweet);
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
    badge.append(placeholder);
  }
  const label = document.createElement('span');
  label.textContent = `RT @${group.handle}${group.count > 1 ? ` x${group.count}` : ''}`;
  badge.append(label);
  const displayName = displayNameForRow(group.retweet);
  badge.title = `${displayName ? `${displayName} ` : ''}@${group.handle} retweeted`;
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
      for (const btn of td.querySelectorAll('[data-news-mentions]')) {
        btn.addEventListener('click', (event) => {
          event.preventDefault();
          event.stopPropagation();
          onRowClick(r, { scrollToNews: true });
        });
      }
      for (const link of td.querySelectorAll('.news-mention-link[href]')) {
        link.addEventListener('click', (event) => {
          event.stopPropagation();
        });
      }
    }
    for (const link of td.querySelectorAll('[data-copy-share]')) {
      link.addEventListener('click', async (event) => {
        event.preventDefault();
        event.stopPropagation();
        await copyShareLink(link);
      });
    }
    tr.append(td);
  }
  return tr;
}

async function copyShareLink(link) {
  const href = link.getAttribute('href') || '';
  if (!href) return;
  const oldTitle = link.getAttribute('title') || '';
  try {
    const copied = await copyTextToClipboard(href);
    link.classList.toggle('copied', copied);
    link.setAttribute('title', copied ? 'Copied share link' : 'Could not copy link');
  } catch {
    link.setAttribute('title', 'Could not copy link');
  }
  window.setTimeout(() => {
    link.classList.remove('copied');
    if (oldTitle) link.setAttribute('title', oldTitle);
  }, 1500);
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
  countRows,
  activeFilters,
  onChange,
  onSort,
  tagCertainty = 'all',
  mediaSettings,
  onMediaSettingsChange,
}) {
  const col = KEY_TO_COL[colKey];
  if (!col) return;
  const rect = anchorBtn.getBoundingClientRect();
  const top = Math.round(rect.bottom + 4);
  const popupWidth = colKey === 'tags' ? 460 : 240;
  popEl.style.top = `${top}px`;
  popEl.style.left = `${Math.max(8, Math.round(rect.right - popupWidth))}px`;
  popEl.style.maxHeight = `${Math.max(220, window.innerHeight - top - 8)}px`;
  popEl.hidden = false;
  popEl.classList.toggle('tag-pop', colKey === 'tags');
  popEl.replaceChildren();

  if (colKey === MEDIA_COL_KEY) {
    popEl.append(buildMediaPopupSettings(mediaSettings, onMediaSettingsChange));
  }
  if (colKey === 'tags') {
    buildResearchTagFilterPopup({
      popEl,
      rows: Array.isArray(countRows) ? countRows : allRows,
      activeValues: activeFilters[colKey],
      tagCertainty,
      onApply: (next, opts) => onChange(colKey, next, opts),
      close,
    });
    setTimeout(() => document.addEventListener('mousedown', away), 0);
    return;
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
  search.placeholder = `Filter ${col.label.toLowerCase()}...`;
  popEl.append(search);

  const rowsForCounts = Array.isArray(countRows) ? countRows : allRows;

  // Aggregate value counts.
  const counts = new Map();
  if (colKey === 'retweeted_by') {
    for (const r of rowsForCounts) {
      const handles = retweetedByHandles(r);
      if (handles.length === 0) {
        counts.set('', (counts.get('') ?? 0) + 1);
        continue;
      }
      for (const handle of handles) counts.set(handle, (counts.get(handle) ?? 0) + 1);
    }
  } else {
    for (const r of rowsForCounts) {
      const v = formatForFilter(r, colKey);
      counts.set(v, (counts.get(v) ?? 0) + 1);
    }
  }
  const allValues = [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([value, count]) => ({ value, count, display: value === '' ? '(blank)' : value }));
  const allValueKeys = allValues.map((p) => p.value);
  const rawActive = activeFilters[colKey];
  const hasActive =
    rawActive instanceof Set
      ? rawActive.size > 0
      : Array.isArray(rawActive)
        ? rawActive.length > 0
        : !!rawActive;
  const active = new Set(hasActive ? rawActive : allValueKeys);

  const list = document.createElement('div');
  list.className = 'col-values';
  popEl.append(list);

  function renderList(filter) {
    list.replaceChildren();
    const f = filter.trim().toLowerCase();
    const visible = allValues.filter((p) => filterValueMatches(p, f));
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
    allVisibleCb.indeterminate =
      !allVisibleCb.checked && visible.some((p) => active.has(p.value));
    allVisibleCb.addEventListener('change', () => {
      for (const p of visible) {
        if (allVisibleCb.checked) active.add(p.value);
        else active.delete(p.value);
      }
      renderList(search.value);
    });
    const allVisibleTxt = document.createElement('span');
    allVisibleTxt.textContent = 'Select all';
    allVisible.append(allVisibleCb, allVisibleTxt);
    list.append(allVisible);

    for (const p of visible) {
      const lab = document.createElement('label');
      lab.className = 'col-val';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = active.has(p.value);
      cb.addEventListener('change', () => {
        if (cb.checked) active.add(p.value);
        else active.delete(p.value);
        renderList(search.value);
      });
      const txt = document.createElement('span');
      txt.textContent = p.display;
      const cnt = document.createElement('span');
      cnt.className = 'count';
      cnt.textContent = String(p.count);
      lab.append(cb, txt, cnt);
      list.append(lab);
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
    if (allValueKeys.every((value) => active.has(value))) {
      onChange(colKey, new Set());
    } else {
      onChange(colKey, new Set(active));
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

function buildResearchTagFilterPopup({
  popEl,
  rows,
  activeValues,
  tagCertainty = 'all',
  onApply,
  close,
}) {
  const rowsForCounts = Array.isArray(rows) ? rows : [];
  const active = new Set(filterValuesArray(activeValues));
  const expandedGroups = new Set();
  let certainty = TAG_CERTAINTY_LABELS[tagCertainty] ? tagCertainty : 'all';

  // The tag counts shown in the browser depend only on the certainty mode
  // (the caller scopes the rows with the tag filter excluded), so they don't
  // change as the user picks tags. Build the namespace groups once per
  // certainty and reuse them, which lets every toggle apply live without
  // re-deriving the value map. Recompute only when certainty changes.
  let groups = buildResearchTagGroups(tagCountsForRows(rowsForCounts, certainty));
  let valueByKey = researchTagValueMap(groups);
  function rebuildGroups() {
    groups = buildResearchTagGroups(tagCountsForRows(rowsForCounts, certainty));
    valueByKey = researchTagValueMap(groups);
  }

  // Selections apply live: every checkbox toggle, chip removal, and certainty
  // change pushes straight to the URL/table so results update immediately and
  // a shared link always matches what the user sees. There is no staged
  // "Apply" step to forget.
  function liveApply() {
    onApply(normalizeResearchTagSelections(active, valueByKey), { tagCertainty: certainty });
  }

  const header = document.createElement('div');
  header.className = 'tag-filter-head';
  const title = document.createElement('div');
  title.className = 'tag-filter-title';
  title.textContent = 'Tags';
  const summary = document.createElement('div');
  summary.className = 'tag-filter-summary';
  header.append(title, summary);
  popEl.append(header);

  const certaintyControl = buildTagCertaintyControl(certainty, (mode) => {
    certainty = TAG_CERTAINTY_LABELS[mode] ? mode : 'all';
    rebuildGroups();
    liveApply();
    render();
  });
  const certaintySelect = certaintyControl.querySelector('select');
  popEl.append(certaintyControl);

  const search = document.createElement('input');
  search.type = 'search';
  search.className = 'col-search';
  search.placeholder = 'Search any tag, e.g. vance, music, ice...';
  search.setAttribute(
    'aria-label',
    'Search tags by namespace or value (matches the full namespace:slug text)'
  );
  popEl.append(search);

  const selectedBlock = document.createElement('div');
  selectedBlock.className = 'tag-selected';
  popEl.append(selectedBlock);

  const list = document.createElement('div');
  list.className = 'col-values tag-browser';
  popEl.append(list);

  const actions = document.createElement('div');
  actions.className = 'col-actions';
  const clear = document.createElement('button');
  clear.type = 'button';
  clear.className = 'btn ghost';
  clear.textContent = 'Clear';
  clear.title = 'Clear all selected tags, the search box, and the certainty mode';
  const done = document.createElement('button');
  done.type = 'button';
  done.className = 'btn';
  done.textContent = 'Done';
  done.title = 'Close (your tag selections are already applied)';
  actions.append(clear, done);
  popEl.append(actions);

  function render() {
    const filter = search.value.trim().toLowerCase();
    summary.textContent = `${fmtNum(totalTagOccurrences(groups))} tag hit${
      totalTagOccurrences(groups) === 1 ? '' : 's'
    } in scope`;

    renderSelectedTags(selectedBlock, active, valueByKey, () => {
      liveApply();
      render();
    });
    list.replaceChildren();

    const onToggle = () => {
      liveApply();
      render();
    };

    let rendered = 0;
    let lastSection = '';
    for (const group of groups) {
      const groupMatches = tagFilterPairMatches(group, filter);
      const matchingChildren = group.children.filter(
        (child) => groupMatches || tagFilterPairMatches(child, filter)
      );
      // With a search active, show any namespace whose header OR a child
      // matches — so a specific subtype (e.g. "vance") is directly reachable
      // and selectable without first picking its namespace. With no search,
      // show every namespace; selecting just the header filters the whole
      // namespace, and its subtypes are one click away below it.
      if (filter && !groupMatches && matchingChildren.length === 0) continue;

      if (group.section !== lastSection) {
        const section = document.createElement('div');
        section.className = 'tag-facet-section';
        section.textContent = group.section;
        list.append(section);
        lastSection = group.section;
      }

      list.append(buildTagNamespaceRow(group, active, onToggle));
      const visibleChildren =
        filter || expandedGroups.has(group.value)
          ? matchingChildren
          : matchingChildren.slice(0, TAG_CHILD_VISIBLE_LIMIT);
      for (const child of visibleChildren) {
        list.append(buildTagChildRow(child, active, onToggle));
      }
      const hidden = matchingChildren.length - visibleChildren.length;
      if (hidden > 0) {
        const more = document.createElement('button');
        more.type = 'button';
        more.className = 'tag-more-btn';
        more.textContent = `Show ${fmtNum(hidden)} more ${group.value}: tags`;
        more.addEventListener('click', () => {
          expandedGroups.add(group.value);
          render();
        });
        list.append(more);
      }
      rendered += 1;
    }

    if (rendered === 0) {
      const empty = document.createElement('div');
      empty.className = 'col-empty';
      empty.textContent = filter ? 'No matching tags' : 'No tags in scope';
      list.append(empty);
    }
  }

  search.addEventListener('input', () => render());

  let clearHandled = false;
  function clearTagFilter(event) {
    stopPopupButtonEvent(event);
    if (clearHandled) return;
    clearHandled = true;
    active.clear();
    certainty = 'all';
    if (certaintySelect) certaintySelect.value = certainty;
    search.value = '';
    expandedGroups.clear();
    onApply(new Set(), { tagCertainty: 'all' });
    close();
  }
  clear.addEventListener('mousedown', clearTagFilter);
  clear.addEventListener('click', clearTagFilter);
  done.addEventListener('mousedown', stopPopupButtonEvent);
  done.addEventListener('click', (event) => {
    stopPopupButtonEvent(event);
    close();
  });

  render();

  function stopPopupButtonEvent(event) {
    event.preventDefault();
    event.stopPropagation();
  }
}

function buildTagCertaintyControl(tagCertainty = 'all', onTagCertaintyChange) {
  const wrap = document.createElement('div');
  wrap.className = 'tag-certainty-control';
  const label = document.createElement('span');
  label.textContent = 'Tentative';
  label.title =
    'Tentative tags are best-guess model/heuristic assignments. They are included by default; this control only narrows by certainty and never blocks normal tag filtering.';
  const select = document.createElement('select');
  select.className = 'select';
  select.title =
    'Tentative tags are included by default. Switch to "Firm tags only" to drop best-guess tags, or "Only tentative tags" to inspect them. This narrows results; it does not gate the tag picker.';
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
  toggle.title = 'Turn media thumbnails on or collapse this column to compact media markers';
  toggle.append(cb, toggleText);

  const details = document.createElement('div');
  details.className = 'media-pop-detail';

  const widthLabel = document.createElement('label');
  widthLabel.textContent = 'Max px';
  const width = document.createElement('input');
  width.type = 'number';
  width.title = 'Maximum thumbnail size in pixels';
  width.min = '16';
  width.max = '48';
  width.step = '1';
  width.value = String(settings.thumbWidth);
  widthLabel.append(width);

  const fitLabel = document.createElement('label');
  fitLabel.textContent = 'Fit';
  const fit = document.createElement('select');
  fit.className = 'select';
  fit.title = 'Choose whether thumbnails fit by width or height';
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

function filterValuesArray(values) {
  if (values instanceof Set) return [...values];
  if (Array.isArray(values)) return values;
  return values ? [values] : [];
}

function tagCountsForRows(rows, certainty) {
  const counts = new Map();
  for (const row of rows) {
    const seen = new Set();
    for (const { name, tentative } of tagEntriesForPopup(row)) {
      if (!tagEntryCertaintyMatches(tentative, certainty) || seen.has(name)) continue;
      seen.add(name);
      counts.set(name, (counts.get(name) ?? 0) + 1);
    }
  }
  return counts;
}

function buildResearchTagGroups(counts) {
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

  return [...byNamespace.entries()]
    .sort((a, b) => {
      const delta = tagNamespaceRank(a[0]) - tagNamespaceRank(b[0]);
      return delta || b[1].count - a[1].count || a[0].localeCompare(b[0]);
    })
    .map(([ns, info]) => ({
      value: ns,
      namespace: ns,
      count: info.count,
      display: `${ns}:`,
      section: tagNamespaceSection(ns),
      children: info.tags
        .sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag))
        .map((child) => ({
          value: combineTagMainSub(ns, child.tag),
          count: child.count,
          parent: ns,
          sub: child.tag,
          child: true,
          display: tagSubtype(child.tag),
        })),
    }));
}

function researchTagValueMap(groups) {
  const map = new Map();
  for (const group of groups) {
    map.set(group.value, group);
    for (const child of group.children) map.set(child.value, child);
  }
  return map;
}

function totalTagOccurrences(groups) {
  return groups.reduce((sum, group) => sum + group.count, 0);
}

function renderSelectedTags(wrap, active, valueByKey, onRemove) {
  wrap.replaceChildren();
  const selected = [...active]
    .map((value) => ({ value, label: formatTagSelectionLabel(value, valueByKey) }))
    .sort((a, b) => a.label.localeCompare(b.label));
  wrap.hidden = selected.length === 0;
  if (selected.length === 0) return;

  const title = document.createElement('div');
  title.className = 'tag-selected-title';
  title.textContent = `Selected (${fmtNum(selected.length)})`;
  wrap.append(title);
  for (const item of selected) {
    const pill = document.createElement('button');
    pill.type = 'button';
    pill.className = 'tag-selection-pill';
    pill.title = `Remove ${item.label}`;
    pill.setAttribute('aria-label', `Remove tag filter ${item.label}`);
    const text = document.createElement('span');
    text.className = 'tag-selection-pill-label';
    text.textContent = item.label;
    const x = document.createElement('span');
    x.className = 'tag-selection-pill-x';
    x.setAttribute('aria-hidden', 'true');
    x.textContent = '×';
    pill.append(text, x);
    pill.addEventListener('click', () => {
      active.delete(item.value);
      onRemove();
    });
    wrap.append(pill);
  }
}

function buildTagNamespaceRow(group, active, onChange) {
  const lab = document.createElement('label');
  lab.className = 'col-val namespace tag-group-row';
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = active.has(group.value);
  cb.indeterminate =
    !cb.checked && group.children.some((child) => active.has(child.value));
  cb.addEventListener('change', () => {
    if (cb.checked) {
      active.add(group.value);
      for (const child of group.children) active.delete(child.value);
    } else {
      active.delete(group.value);
    }
    onChange();
  });
  const txt = document.createElement('span');
  txt.className = 'tag-group-label';
  txt.textContent = group.display;
  const cnt = document.createElement('span');
  cnt.className = 'count';
  cnt.textContent = String(group.count);
  lab.append(cb, txt, cnt);
  return lab;
}

function buildTagChildRow(child, active, onChange) {
  const parentActive = active.has(child.parent);
  const lab = document.createElement('label');
  lab.className = `col-val child tag-child-row${parentActive ? ' parent-active' : ''}`;
  if (parentActive) lab.title = `Select only ${child.parent}: ${child.display}`;
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = active.has(child.value);
  cb.addEventListener('change', () => {
    if (cb.checked) {
      active.delete(child.parent);
      active.add(child.value);
    } else {
      active.delete(child.value);
    }
    onChange();
  });
  const txt = document.createElement('span');
  txt.className = 'tag-child-tag';
  txt.textContent = child.display;
  const cnt = document.createElement('span');
  cnt.className = 'count';
  cnt.textContent = String(child.count);
  lab.append(cb, txt, cnt);
  return lab;
}

function tagFilterPairMatches(pair, filter) {
  if (!filter) return true;
  return [pair.value, pair.display, pair.parent, pair.sub, pair.namespace]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(filter));
}

function normalizeResearchTagSelections(active, valueByKey) {
  const next = new Set();
  for (const value of active) {
    const pair = valueByKey.get(value);
    if (pair?.child && active.has(pair.parent)) continue;
    next.add(value);
  }
  return next;
}

function formatTagSelectionLabel(value, valueByKey) {
  const pair = valueByKey.get(value);
  if (pair?.child) return `${pair.parent}: ${pair.display}`;
  if (pair) return pair.display;
  const { main, sub } = splitTagMainSub(value);
  if (sub) return `${main}: ${tagSubtype(sub)}`;
  return main.includes(':') ? main : `${main}:`;
}

function tagEntriesForPopup(row) {
  const tags = Array.isArray(row?.tags) ? row.tags : [];
  const out = [];
  for (const entry of tags) {
    const name = typeof entry === 'string' ? entry : entry?.tag;
    if (!name) continue;
    out.push({ name, tentative: typeof entry === 'object' && Boolean(entry?.tentative) });
  }
  return out;
}

function tagEntryCertaintyMatches(tentative, mode) {
  if (mode === 'firm') return !tentative;
  if (mode === 'tentative') return Boolean(tentative);
  return true;
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

function filterValueMatches(pair, filter) {
  if (!filter) return true;
  return [pair.value, pair.display]
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
  return `<span class="acc-cell">${avatarHtml}${handleHtml}${renderNewsMentionBadge(r)}${renderAccountBadges(userMeta)}${nameHtml}</span>`;
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

function renderNewsMentionBadge(row) {
  const mentions = Array.isArray(row?.news_mentions) ? row.news_mentions.filter(Boolean) : [];
  if (mentions.length === 0) return '';
  const rawCount = Number(row?.news_mention_count ?? mentions.length);
  const count = Number.isFinite(rawCount) && rawCount > 0 ? rawCount : mentions.length;
  const label = count === 1 ? '1 news mention' : `${count} news mentions`;
  const mention = mentions[0] ?? {};
  const href = typeof mention.url === 'string' && mention.url ? mention.url : '';
  if (count === 1 && href) {
    return `<a class="news-mention-link" href="${escape(href)}" target="_blank" rel="noopener" aria-label="${escape(label)}" title="${escape(label)}">&#128240;</a>`;
  }
  return `<button type="button" class="news-mention-link news-mention-jump" data-news-mentions="1" aria-label="${escape(label)}" title="${escape(label)}">&#128240;</button>`;
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

function renderOcrCell(r) {
  const text = String(r.ocr_text || '');
  if (!text) return '<span class="muted">—</span>';
  return `<span class="cell-text" title="${escape(text)}">${escape(text)}</span>`;
}

function renderRetweetedByCell(r) {
  const handles = retweetedByHandles(r);
  if (handles.length === 0) return '<span class="muted">&mdash;</span>';
  return handles
    .map(
      (handle) =>
        `<span class="thread-retweet-badge" title="Retweeted by @${escape(handle)}">RT @${escape(handle)}</span>`
    )
    .join(' ');
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

function _renderTagPills(r) {
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
