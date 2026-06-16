export function archiveShareUrlForRow(row) {
  const url = new URL(location.href);
  const params = new URLSearchParams();
  params.set('tweet', String(row?.tweet_id || ''));
  url.hash = params.toString();
  return url.toString();
}

export function xTweetUrlForRow(row) {
  if (!row) return '';
  const originalId = String(row.retweeted_tweet_id || '').trim();
  if (originalId) return `https://x.com/i/web/status/${encodeURIComponent(originalId)}`;
  return String(row.tweet_url || '');
}

export function xTweetLinkLabel(row) {
  return row?.tweet_type === 'retweet' && row?.retweeted_tweet_id
    ? 'Open original on x.com'
    : 'Open on x.com';
}

export async function copyTextToClipboard(text) {
  const value = String(text ?? '');
  if (!value) return false;
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    await navigator.clipboard.writeText(value);
    return true;
  }
  const area = document.createElement('textarea');
  area.value = value;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.left = '-9999px';
  area.style.top = '0';
  document.body.append(area);
  area.select();
  let copied = false;
  try {
    copied = document.execCommand('copy');
  } finally {
    area.remove();
  }
  return copied;
}
