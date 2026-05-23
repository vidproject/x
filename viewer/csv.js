// CSV export of the currently filtered row set. We export a flat subset of the
// canonical schema — nested arrays/structs are joined into pipe-separated
// strings to keep the file usable in spreadsheets.

import { retweetedByHandles } from './store.js?v=lazycat4';

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
  'media_descriptions',
  'media_release_urls',
  'ocr_text',
  'news_mention_count',
  'news_articles',
  'retweeted_by',
  'tweet_url',
  'wayback_url',
  'deletion_detected_at',
  'unavailable_detected_at',
  'unavailable_reason',
  'unavailable_text',
  'unavailable_source_url',
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
      media_descriptions: Array.isArray(r.media_insights)
        ? r.media_insights
            .map((entry) => entry && entry.description)
            .filter(Boolean)
            .join('|')
        : '',
      media_release_urls: media
        .map((m) => m && m.release_asset_url)
        .filter(Boolean)
        .join('|'),
      ocr_text: String(r.ocr_text || ''),
      news_mention_count: Array.isArray(r.news_mentions) ? r.news_mentions.length : 0,
      news_articles: Array.isArray(r.news_mentions)
        ? r.news_mentions
            .map((entry) =>
              [
                entry?.source,
                entry?.title,
                entry?.url,
                entry?.published_at,
                entry?.match_type,
                typeof entry?.confidence === 'number' ? `confidence ${entry.confidence}` : '',
                Array.isArray(entry?.matched_fields)
                  ? `fields ${entry.matched_fields.join('|')}`
                  : '',
              ]
                .filter(Boolean)
                .join(' - ')
            )
            .filter(Boolean)
            .join('|')
        : '',
      retweeted_by: retweetedByHandles(r).join('|'),
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
