// In-memory store: holds loaded rows from one or more parquet files, builds a
// MiniSearch index lazily, exposes a filter+sort+paginate pipeline.

import MiniSearch from 'https://esm.sh/minisearch@7.1.2';

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

  rebuild() {
    const all = [];
    for (const rows of this.byHandle.values()) {
      for (const r of rows) all.push(r);
    }
    this.allRows = all;
    this.idIndex = new Map();
    for (let i = 0; i < all.length; i++) {
      const id = String(all[i].tweet_id ?? '');
      if (id) this.idIndex.set(id, i);
    }
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
      fields: ['text', 'text_resolved', 'tags_str', 'mentions_str', 'account_handle'],
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
   *   includeDeleted?: boolean
   * }} filt
   */
  apply(filt) {
    let rows = this.allRows;
    if (filt.accounts && filt.accounts.length > 0) {
      const set = new Set(filt.accounts);
      rows = rows.filter((r) => set.has(r.account_handle));
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
  ];
  return parts.join(' ');
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
