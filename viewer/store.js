// In-memory store: holds loaded rows from one or more parquet files, builds a
// MiniSearch index lazily, exposes a filter+sort+paginate pipeline.
//
// Tags from sidecar parquets (joined in by app.js) live on `row.tags` as
// `Array<{tag, tentative?, source?}>`. The store treats them like any other
// filterable field.
//
// Threading: every row carries an effective `thread_id` (= conversation_id
// or tweet_id when no conversation_id exists). After filter+sort, the
// pipeline groups surviving rows into threads. Masters carry aggregated
// thread metadata (combined media count, latest activity, sibling count).

import MiniSearch from 'https://esm.sh/minisearch@7.1.2';

export const TAG_SUB_SEPARATOR = ' ⊂ ';

export function combineTagMainSub(main, sub) {
  return sub ? `${main}${TAG_SUB_SEPARATOR}${sub}` : main;
}

export function splitTagMainSub(value) {
  const text = String(value ?? '');
  const idx = text.indexOf(TAG_SUB_SEPARATOR);
  if (idx === -1) return { main: text, sub: '' };
  return {
    main: text.slice(0, idx),
    sub: text.slice(idx + TAG_SUB_SEPARATOR.length),
  };
}

export function tagNamespace(tag) {
  return String(tag ?? '').split(':', 1)[0];
}

export function tagSubtype(tag) {
  const text = String(tag ?? '');
  const idx = text.indexOf(':');
  return idx === -1 ? text : text.slice(idx + 1);
}

export class Store {
  constructor() {
    /** @type {Map<string, Array<Record<string, unknown>>>} */
    this.byHandle = new Map();
    /** @type {Array<Record<string, unknown>>} */
    this.allRows = [];
    /** @type {MiniSearch | null} */
    this.search = null;
    /** @type {Map<string, number>} */
    this.idIndex = new Map(); // tweet_id → allRows index
    /** @type {Map<string, Array<Record<string, unknown>>>} */
    this.threadIndex = new Map(); // thread_id → all rows in thread
    /** @type {Map<string, string>} */
    this.accountCategoryByHandle = new Map();
  }

  has(handle) {
    return this.byHandle.has(handle);
  }
  handles() {
    return [...this.byHandle.keys()];
  }

  setHandle(handle, rows) {
    this.byHandle.set(handle, rows);
    this.rebuild();
  }
  removeHandle(handle) {
    this.byHandle.delete(handle);
    this.rebuild();
  }

  /** Inject the per-tweet tag map sourced from sidecar parquets.
   * Tags are attached directly to each row so downstream code (filter,
   * sort, column render, CSV export, sidepanel) doesn't need a second
   * data structure. */
  applyTags(tagMap) {
    for (const r of this.allRows) {
      const id = String(r.tweet_id ?? '');
      r.tags = tagMap.get(id) ?? [];
    }
    this.search = null; // rebuild so tags enter the search corpus
  }

  /** Attach optional media-recognition rows from `data/tags/media_vision.parquet`.
   * These are downstream annotations, not canonical tweet data. */
  applyMediaInsights(insightMap) {
    for (const r of this.allRows) {
      const id = String(r.tweet_id ?? '');
      r.media_insights = insightMap.get(id) ?? [];
    }
    this.search = null;
  }

  /** Provide the manifest's account categorization so the filter pipeline
   * can match `row.account_handle` → category without a per-row lookup. */
  setAccountCategories(map) {
    this.accountCategoryByHandle = map instanceof Map ? map : new Map();
  }

  rebuild() {
    const all = [];
    for (const rows of this.byHandle.values()) {
      for (const r of rows) {
        delete r.__reply_promotions;
        all.push(r);
      }
    }
    this.allRows = all;
    this.idIndex = new Map();
    this.threadIndex = new Map();
    for (let i = 0; i < all.length; i++) {
      const r = all[i];
      const id = String(r.tweet_id ?? '');
      if (id) this.idIndex.set(id, i);
      const tid = String(r.conversation_id ?? r.tweet_id ?? '');
      if (!tid) continue;
      let list = this.threadIndex.get(tid);
      if (!list) {
        list = [];
        this.threadIndex.set(tid, list);
      }
      list.push(r);
    }
    this.annotateReplyPromotions();
    this.search = null; // rebuild lazily
  }

  getById(id) {
    const idx = this.idIndex.get(String(id));
    return idx === undefined ? null : this.allRows[idx];
  }

  /** Build the full-text index on demand. */
  ensureSearch() {
    if (this.search) return this.search;
    const mini = new MiniSearch({
      idField: 'tweet_id',
      fields: [
        'text',
        'text_resolved',
        'tags_str',
        'mentions_str',
        'account_handle',
        'tag_names',
        'media_insight_text',
      ],
      storeFields: ['tweet_id'],
      searchOptions: {
        prefix: true,
        fuzzy: false,
      },
    });
    const docs = this.allRows.map((r) => ({
      tweet_id: r.tweet_id,
      text: r.text || '',
      text_resolved: r.text_resolved || '',
      tags_str: Array.isArray(r.hashtags) ? r.hashtags.join(' ') : '',
      mentions_str: Array.isArray(r.mentions) ? r.mentions.join(' ') : '',
      account_handle: r.account_handle || '',
      tag_names: tagNames(r).join(' '),
      media_insight_text: mediaInsightText(r),
    }));
    mini.addAll(docs);
    this.search = mini;
    return mini;
  }

  /**
   * Apply filters and return the filtered+sorted row list.
   * @param {{
   *   accounts: string[], q: string, from: string, to: string,
   *   type: string, media: string, sort: string, dir: 'asc'|'desc',
   *   colFilters?: Record<string, Set<string>>,
   *   tags?: string[],
   *   accountCategories?: string[],
   *   includeDeleted?: boolean
   * }} filt
   */
  apply(filt) {
    let rows = this.allRows;
    if (filt.accounts && filt.accounts.length > 0) {
      const set = new Set(filt.accounts);
      rows = rows.filter((r) => set.has(r.account_handle));
    }
    if (filt.accountCategories && filt.accountCategories.length > 0) {
      const set = new Set(filt.accountCategories);
      rows = rows.filter((r) => set.has(this.categoryOf(r)));
    }
    if (filt.from) {
      const fromIso = `${filt.from}T00:00:00Z`;
      rows = rows.filter((r) => (r.posted_at || '') >= fromIso);
    }
    if (filt.to) {
      const toIso = `${filt.to}T23:59:59Z`;
      rows = rows.filter((r) => (r.posted_at || '') <= toIso);
    }
    if (filt.type) {
      rows = rows.filter((r) => r.tweet_type === filt.type);
    }
    if (filt.media) {
      rows = rows.filter((r) => matchMediaFilter(r, filt.media));
    }
    if (filt.tags && filt.tags.length > 0) {
      // Tag filter is OR-across-selected, so a tweet matches when its
      // tag set intersects the selected set. ANDing would make the
      // selector useless once you exceed 1-2 selections — every tweet
      // would drop out.
      const want = new Set(filt.tags);
      rows = rows.filter((r) => tagFilterMatches(r, want));
    }
    if (filt.colFilters) {
      for (const [col, allowed] of Object.entries(filt.colFilters)) {
        if (!allowed || allowed.size === 0) continue;
        rows = rows.filter((r) => allowed.has(formatForFilter(r, col)));
      }
    }
    if (filt.q && filt.q.trim()) {
      const q = filt.q.trim();
      if (/[*?]/.test(q)) {
        const re = wildcardToRegex(q);
        rows = rows.filter((r) => re.test(haystack(r)));
      } else {
        const idSet = runMiniSearch(this.ensureSearch(), q);
        rows = rows.filter((r) => idSet.has(String(r.tweet_id)));
      }
    }
    // Sort.
    const dir = filt.dir === 'asc' ? 1 : -1;
    const sortKey = filt.sort || 'posted_at';
    rows = rows.slice().sort((a, b) => compare(a, b, sortKey) * dir);
    return rows;
  }

  /**
   * Group surviving rows into threads. Each thread carries:
   *
   *   master       — the conversation root (or the earliest captured
   *                  sibling when the root itself isn't archived).
   *   selfSlaves   — replies authored by the same handle as the master.
   *                  These are the "DHS continues its own thread" case
   *                  worth inlining; they read as part of the master's
   *                  message and don't spam.
   *   otherSlaves  — every other reply (tracked-other accounts AND
   *                  random `_misc` chatter). Not inlined; the viewer
   *                  surfaces them in the sidepanel on row click.
   *
   * A thread is included if at least one of its members survived the
   * filter; non-surviving siblings still get pulled in so context isn't
   * lost when the filter is narrow.
   *
   * @param {Array<Record<string, unknown>>} filteredRows
   */
  groupIntoThreads(filteredRows) {
    const seenThreads = new Set();
    /** @type {Array<{master: any, selfSlaves: any[], otherSlaves: any[], matchedCount: number, threadId: string}>} */
    const threads = [];
    for (const r of filteredRows) {
      const tid = String(r.conversation_id ?? r.tweet_id ?? '');
      if (!tid || seenThreads.has(tid)) continue;
      seenThreads.add(tid);
      const full = this.threadIndex.get(tid) ?? [r];
      const ordered = full.slice().sort((a, b) => {
        const av = String(a.posted_at ?? '');
        const bv = String(b.posted_at ?? '');
        return av.localeCompare(bv);
      });
      let master = ordered.find((x) => String(x.tweet_id) === tid);
      if (!master) master = ordered[0];
      const masterHandle = master.account_handle;
      const selfSlaves = [];
      const otherSlaves = [];
      for (const x of ordered) {
        if (x === master) continue;
        if (x.account_handle === masterHandle) selfSlaves.push(x);
        else otherSlaves.push(x);
      }
      const filteredSet = new Set(filteredRows);
      const matchedCount = ordered.filter((x) => filteredSet.has(x)).length;
      threads.push({
        master,
        selfSlaves,
        otherSlaves,
        promotedReplies: replyPromotionsFor(master),
        matchedCount,
        threadId: tid,
      });
    }
    return threads;
  }

  categoryOf(row) {
    const handle = row.account_handle;
    // `_misc.parquet` aggregates non-tracked authors, so they're
    // implicitly `public` regardless of which non-tracked handle wrote
    // the tweet.
    const own = this.accountCategoryByHandle.get(handle) || 'public';
    return dominantCategory(own, promotedCategoryOf(row));
  }

  annotateReplyPromotions() {
    for (const reply of this.allRows) {
      if (reply.tweet_type !== 'reply') continue;
      const parentId = String(reply.reply_to_tweet_id ?? '');
      if (!parentId) continue;
      const parent = this.getById(parentId);
      if (!parent || parent === reply) continue;
      if (parent.account_handle === reply.account_handle) continue;

      const category = this.accountCategoryByHandle.get(reply.account_handle);
      if (category !== 'core' && category !== 'officials') continue;
      const promotions = replyPromotionsFor(parent);
      promotions.push({
        category,
        reply,
      });
      parent.__reply_promotions = promotions;
    }
  }
}

function replyPromotionsFor(row) {
  return Array.isArray(row?.__reply_promotions) ? row.__reply_promotions : [];
}

function promotedCategoryOf(row) {
  const promotions = replyPromotionsFor(row);
  if (promotions.some((p) => p?.category === 'core')) return 'core';
  if (promotions.some((p) => p?.category === 'officials')) return 'officials';
  return '';
}

function dominantCategory(own, promoted) {
  const priority = {
    core: 5,
    officials: 4,
    government: 3,
    public_figures: 2,
    public: 1,
    '': 0,
  };
  return (priority[promoted] ?? 0) > (priority[own] ?? 0) ? promoted : own;
}

function tagFilterMatches(row, selections) {
  const exact = new Set();
  const namespaces = new Set();
  for (const value of selections) {
    const text = String(value ?? '');
    if (!text) continue;
    const { main, sub } = splitTagMainSub(text);
    if (sub) exact.add(sub);
    else if (main.includes(':')) exact.add(main);
    else namespaces.add(main);
  }
  return tagNames(row).some((tag) => exact.has(tag) || namespaces.has(tagNamespace(tag)));
}

function tagNames(row) {
  const ts = row.tags;
  if (!Array.isArray(ts)) return [];
  const seen = new Set();
  const out = [];
  for (const t of ts) {
    const name = typeof t === 'string' ? t : t && typeof t.tag === 'string' ? t.tag : '';
    if (!name || seen.has(name)) continue;
    seen.add(name);
    out.push(name);
  }
  return out;
}

function runMiniSearch(mini, q) {
  // Token AND: every space-separated token must match (prefix).
  const tokens = q.split(/\s+/).filter(Boolean);
  let result = null;
  for (const tok of tokens) {
    const hits = mini.search(tok, { prefix: true });
    const ids = new Set(hits.map((r) => String(r.id)));
    if (result === null) {
      result = ids;
    } else {
      for (const id of [...result]) if (!ids.has(id)) result.delete(id);
    }
  }
  return result ?? new Set();
}

function wildcardToRegex(q) {
  const body = q
    .split(/\s+/)
    .filter(Boolean)
    .map((tok) =>
      tok
        .replace(/[.+^${}()|[\]\\]/g, '\\$&')
        .replace(/\*/g, '.*')
        .replace(/\?/g, '.')
    )
    .join('.*');
  return new RegExp(body, 'i');
}

function haystack(r) {
  const parts = [
    r.text || '',
    r.text_resolved || '',
    r.account_handle || '',
    Array.isArray(r.hashtags) ? r.hashtags.join(' ') : '',
    Array.isArray(r.mentions) ? r.mentions.join(' ') : '',
    tagNames(r).join(' '),
    mediaInsightText(r),
  ];
  return parts.join(' ');
}

function mediaInsightText(row) {
  const insights = Array.isArray(row.media_insights) ? row.media_insights : [];
  return insights
    .map((entry) => [entry?.description, entry?.summary_text].filter(Boolean).join(' '))
    .filter(Boolean)
    .join(' ');
}

function matchMediaFilter(r, kind) {
  const media = Array.isArray(r.media) ? r.media : [];
  if (kind === 'none') return media.length === 0;
  if (kind === 'video') return media.some((m) => m && m.media_type === 'video');
  if (kind === 'photo') return media.some((m) => m && m.media_type === 'photo');
  return true;
}

function compare(a, b, key) {
  const va = valueOf(a, key);
  const vb = valueOf(b, key);
  if (va == null && vb == null) return 0;
  if (va == null) return -1;
  if (vb == null) return 1;
  if (typeof va === 'number' && typeof vb === 'number') return va - vb;
  return String(va).localeCompare(String(vb));
}

function valueOf(row, key) {
  if (key === 'media_count') {
    return Array.isArray(row.media) ? row.media.length : 0;
  }
  if (key === 'media_description') {
    return mediaInsightText(row);
  }
  if (key === 'video_duration') {
    // Mirrors viewer/table.js#videoDurationSeconds. Kept here so the
    // store-side sort doesn't need to import from the rendering module.
    const media = Array.isArray(row.media) ? row.media : [];
    let max = 0;
    for (const m of media) {
      if (!m) continue;
      if (m.media_type !== 'video' && m.media_type !== 'animated_gif') continue;
      const d = Number(m.duration_sec);
      if (Number.isFinite(d) && d > max) max = d;
    }
    return max;
  }
  return row[key];
}

// Stable format used when surfacing column values to the column-filter popup.
export function formatForFilter(row, col) {
  const v = valueOf(row, col);
  if (col === 'posted_at' || col === 'last_seen_at') {
    return typeof v === 'string' ? v.slice(0, 10) : '';
  }
  if (col === 'media_kinds') {
    const media = Array.isArray(row.media) ? row.media : [];
    if (media.length === 0) return 'text only';
    const kinds = new Set(media.map((m) => (m && m.media_type) || ''));
    return [...kinds].sort().join('+');
  }
  if (v == null) return '';
  return String(v);
}

export { tagNames };
