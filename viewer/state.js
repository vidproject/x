// URL hash <-> filter state serialization.
//
// Filter state is encoded as &-separated key=value pairs in location.hash, with
// percent-encoding. Empty / default values are omitted to keep URLs compact.

const KEYS = [
  'accounts',
  'categories',
  'tags',
  'profile',
  'tweet',
  'q',
  'qfield',
  'from',
  'to',
  'type',
  'media',
  'tagcert',
  'sort',
  'dir',
  'page',
  'size',
  'cols',
];

const DEFAULT_PAGE_SIZE = 20;
const MAX_PAGE_SIZE = 200;

function normalizePageSize(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_PAGE_SIZE;
  return Math.min(Math.floor(n), MAX_PAGE_SIZE);
}

export function defaults() {
  return {
    accounts: [],
    categories: [],
    tags: [],
    profile: '',
    tweet: '',
    q: '',
    qfield: 'all',
    from: '',
    to: '',
    type: '',
    media: '',
    tagcert: 'all',
    sort: 'posted_at',
    dir: 'desc',
    page: 1,
    size: DEFAULT_PAGE_SIZE,
    cols: '', // empty = default visible columns
  };
}

export function fromHash(hash) {
  const out = defaults();
  const raw = (hash || location.hash || '').replace(/^#/, '');
  if (!raw) return out;
  const params = new URLSearchParams(raw);
  for (const key of KEYS) {
    const v = params.get(key);
    if (v === null) continue;
    if (key === 'accounts' || key === 'categories' || key === 'tags') {
      out[key] = v.split(',').filter(Boolean);
    } else if (key === 'page' || key === 'size') {
      const n = Number(v);
      if (key === 'page') {
        if (Number.isFinite(n) && n > 0) out[key] = Math.floor(n);
      } else {
        out[key] = normalizePageSize(v);
      }
    } else {
      out[key] = v;
    }
  }
  return out;
}

export function toHash(state) {
  const dflt = defaults();
  const params = new URLSearchParams();
  for (const key of KEYS) {
    const value = state[key];
    if (key === 'accounts' || key === 'categories' || key === 'tags') {
      if (value && value.length > 0) params.set(key, value.join(','));
      continue;
    }
    if (value === undefined || value === null) continue;
    if (value === '' || value === dflt[key]) continue;
    params.set(key, String(value));
  }
  const s = params.toString();
  return s ? `#${s}` : '';
}

export function applyToUrl(state) {
  const next = toHash(state);
  if (next === location.hash) return;
  const url = `${location.pathname}${location.search}${next}`;
  history.replaceState(null, '', url);
}
