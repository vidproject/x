// URL hash <-> filter state serialization.
//
// Filter state is encoded as &-separated key=value pairs in location.hash, with
// percent-encoding. Empty / default values are omitted to keep URLs compact.

const KEYS = [
  'accounts',
  'q',
  'from',
  'to',
  'type',
  'media',
  'sort',
  'dir',
  'page',
  'size',
  'cols',
];

export function defaults() {
  return {
    accounts: [],
    q: '',
    from: '',
    to: '',
    type: '',
    media: '',
    sort: 'posted_at',
    dir: 'desc',
    page: 1,
    size: 100,
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
    if (key === 'accounts') {
      out.accounts = v.split(',').filter(Boolean);
    } else if (key === 'page' || key === 'size') {
      const n = Number(v);
      if (Number.isFinite(n) && n > 0) out[key] = n;
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
    if (key === 'accounts') {
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
