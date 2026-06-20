const DATA_URL = '../data/list/review-list.json';

const collectionId = document.body.dataset.collection || 'highlights';
const collectionLabels = {
  highlights: 'Highlights',
  relevant_items: 'Relevant Items',
};

const els = {
  status: document.getElementById('status-line'),
  summary: document.getElementById('summary-row'),
  list: document.getElementById('item-list'),
};

load();

async function load() {
  try {
    const payload = await fetchJson(DATA_URL);
    const items = payload?.collections?.[collectionId] || [];
    renderSummary(payload.metadata || {}, items);
    renderItems(items);
    setStatus(
      `${formatNumber(items.length)} ${items.length === 1 ? 'item' : 'items'} loaded`
    );
  } catch (error) {
    setStatus(`Could not load ${DATA_URL}: ${error.message}`);
    els.summary.replaceChildren();
    els.list.replaceChildren(emptyState('Creative list data is not available.'));
  }
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`.trim());
  }
  return response.json();
}

function renderSummary(metadata, items) {
  els.summary.replaceChildren();
  const label = collectionLabels[collectionId] || 'Items';
  const generated = metadata.generated_at ? formatDateTime(metadata.generated_at) : 'Unknown';
  const source = metadata.source_decisions_file || 'Unknown export';
  const duplicateText = `${formatNumber(
    metadata.duplicate_selected_items_removed || 0
  )} duplicate${metadata.duplicate_selected_items_removed === 1 ? '' : 's'} removed`;
  [
    [`${label}`, formatNumber(items.length)],
    ['Generated', generated],
    ['Decision Export', source],
    ['Deduplication', duplicateText],
  ].forEach(([name, value]) => {
    const block = document.createElement('div');
    block.className = 'summary-block';
    const title = document.createElement('span');
    title.textContent = name;
    const content = document.createElement('strong');
    content.textContent = value;
    block.append(title, content);
    els.summary.append(block);
  });
}

function renderItems(items) {
  els.list.replaceChildren();
  if (!items.length) {
    els.list.append(emptyState('No selected items are present in this collection.'));
    return;
  }
  items.forEach((item, index) => {
    els.list.append(itemCard(item, index + 1));
  });
}

function itemCard(item, ordinal) {
  const article = document.createElement('article');
  article.className = 'item-card';

  const header = document.createElement('header');
  header.className = 'item-head';
  const titleBlock = document.createElement('div');
  const title = document.createElement('h2');
  title.textContent = `${ordinal}. ${accountLabel(item)}`;
  const meta = document.createElement('p');
  meta.className = 'item-meta';
  meta.textContent = [
    formatDate(item.posted_at),
    item.decision === 'superlike' ? 'Highlight' : 'Relevant Item',
    item.detail_source || 'source unknown',
  ]
    .filter(Boolean)
    .join(' / ');
  titleBlock.append(title, meta);

  const linkRow = document.createElement('div');
  linkRow.className = 'link-row';
  addLink(linkRow, 'Tweet', item.tweet_url);
  const firstMedia = firstPlayableMedia(item);
  addLink(linkRow, 'Archive', firstMedia?.archive_url);
  header.append(titleBlock, linkRow);
  article.append(header);

  const body = document.createElement('div');
  body.className = 'item-body';
  body.append(mediaColumn(item), detailColumn(item));
  article.append(body);
  return article;
}

function mediaColumn(item) {
  const column = document.createElement('div');
  column.className = 'media-column';
  const mediaItems = (item.media || []).filter((media) => media?.archive_url);
  if (!mediaItems.length) {
    column.append(emptyState('No archived media asset is attached to this item.'));
    return column;
  }
  mediaItems.forEach((media) => {
    const wrap = document.createElement('figure');
    wrap.className = 'media-wrap';
    if (isVideo(media)) {
      const video = document.createElement('video');
      video.controls = true;
      video.preload = 'metadata';
      video.playsInline = true;
      if (media.thumbnail_url) {
        video.poster = media.thumbnail_url;
      }
      const source = document.createElement('source');
      source.src = media.archive_url;
      source.type = media.archive_url.split('?')[0].toLowerCase().endsWith('.webm')
        ? 'video/webm'
        : 'video/mp4';
      video.append(source);
      wrap.append(video);
    } else {
      const img = document.createElement('img');
      img.src = media.archive_url;
      img.alt = mediaAlt(item, media);
      img.loading = 'lazy';
      wrap.append(img);
    }
    const caption = document.createElement('figcaption');
    caption.textContent = [media.media_id, media.duration_sec ? `${media.duration_sec}s` : '']
      .filter(Boolean)
      .join(' / ');
    wrap.append(caption);
    column.append(wrap);
  });
  return column;
}

function detailColumn(item) {
  const column = document.createElement('div');
  column.className = 'detail-column';
  column.append(textBlock('Tweet', item.tweet_text || 'No tweet text captured.'));

  const evidence = item.evidence || {};
  const evidenceText = [evidence.summary, evidence.notable_text].filter(Boolean).join('\n\n');
  if (evidenceText) {
    column.append(textBlock('Evidence', evidenceText));
  }

  const firstMedia = firstPlayableMedia(item) || (item.media || [])[0];
  if (firstMedia) {
    const analysis = firstMedia.analysis || {};
    const description = analysis.description?.text || '';
    const ocr = analysis.ocr?.text || '';
    const transcript = analysis.transcript?.text || '';
    if (description) {
      column.append(textBlock('Visual Description', description));
    }
    if (ocr) {
      column.append(textBlock('OCR', ocr));
    }
    if (transcript) {
      column.append(textBlock('Transcript', transcript));
    }
  }

  const tags = [
    ...(item.preference_categories || []),
    ...(item.creative_forms || []).map((value) => `form:${value}`),
    ...(item.subjects || []).map((value) => `subject:${value}`),
    ...(item.tags || []),
  ];
  column.append(tagBlock(tags));
  return column;
}

function textBlock(title, body) {
  const section = document.createElement('section');
  section.className = 'text-block';
  const h3 = document.createElement('h3');
  h3.textContent = title;
  const p = document.createElement('p');
  p.textContent = body;
  section.append(h3, p);
  return section;
}

function tagBlock(tags) {
  const section = document.createElement('section');
  section.className = 'text-block';
  const h3 = document.createElement('h3');
  h3.textContent = 'Tags';
  const row = document.createElement('div');
  row.className = 'tag-row';
  unique(tags)
    .filter(Boolean)
    .slice(0, 32)
    .forEach((tag) => {
      const chip = document.createElement('span');
      chip.className = 'tag';
      chip.textContent = tag;
      row.append(chip);
    });
  if (!row.children.length) {
    const chip = document.createElement('span');
    chip.className = 'tag muted';
    chip.textContent = 'untagged';
    row.append(chip);
  }
  section.append(h3, row);
  return section;
}

function emptyState(message) {
  const box = document.createElement('div');
  box.className = 'empty-state';
  box.textContent = message;
  return box;
}

function addLink(parent, label, href) {
  if (!href) {
    return;
  }
  const link = document.createElement('a');
  link.href = href;
  link.target = '_blank';
  link.rel = 'noopener';
  link.textContent = label;
  parent.append(link);
}

function firstPlayableMedia(item) {
  return (item.media || []).find((media) => media?.archive_url);
}

function isVideo(media) {
  const type = String(media.type || media.media_type || '').toLowerCase();
  const url = String(media.archive_url || '').split('?')[0].toLowerCase();
  return type.includes('video') || type.includes('gif') || /\.(mp4|webm|mov|m4v)$/.test(url);
}

function mediaAlt(item, media) {
  return (
    media?.analysis?.description?.text ||
    item.tweet_text ||
    `${accountLabel(item)} media item`
  );
}

function accountLabel(item) {
  const account = item.account || {};
  const handle = account.handle ? `@${account.handle}` : '';
  return account.label && handle ? `${account.label} (${handle})` : account.label || handle || 'Unknown account';
}

function setStatus(message) {
  if (els.status) {
    els.status.textContent = message;
  }
}

function formatDate(value) {
  if (!value) {
    return '';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

function formatDateTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(Number(value) || 0);
}

function unique(values) {
  return [...new Set(values.map((value) => String(value || '').trim()).filter(Boolean))];
}
