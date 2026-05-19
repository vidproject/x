// CSV export of the currently filtered row set. We export a flat subset of the
// canonical schema — nested arrays/structs are joined into pipe-separated
// strings to keep the file usable in spreadsheets.

const COLS = [
  'tweet_id',
  'account_handle',
  'posted_at',
  'tweet_type',
  'text_resolved',
  'lang',
  'hashtags',
  'mentions',
  'like_count',
  'retweet_count',
  'reply_count',
  'quote_count',
  'view_count',
  'media_kinds',
  'media_count',
  'media_release_urls',
  'tweet_url',
  'wayback_url',
  'deletion_detected_at',
  'capture_run_id',
];

export function exportCsv(rows, filename) {
  const lines = [COLS.map(csvCell).join(',')];
  for (const r of rows) {
    const media = Array.isArray(r.media) ? r.media : [];
    const flat = {
      ...r,
      hashtags: Array.isArray(r.hashtags) ? r.hashtags.join('|') : '',
      mentions: Array.isArray(r.mentions) ? r.mentions.join('|') : '',
      media_kinds: media
        .map((m) => m && m.media_type)
        .filter(Boolean)
        .join('|'),
      media_count: media.length,
      media_release_urls: media
        .map((m) => m && m.release_asset_url)
        .filter(Boolean)
        .join('|'),
    };
    lines.push(COLS.map((c) => csvCell(flat[c])).join(','));
  }
  const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename || `imm-archive-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, 0);
}

function csvCell(v) {
  if (v === null || v === undefined) return '';
  const s = String(v);
  if (/[",\n]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}
