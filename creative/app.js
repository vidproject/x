const MANIFEST_URL = '../data/creative/manifest.json';
const STORAGE_KEY = 'vidproject:x:creative-review:decisions:v1';

const QUEUE_DEFS = [
  { id: 'high-confidence', label: 'High confidence' },
  { id: 'candidates', label: 'Candidates' },
  { id: '2016-2020', label: '2016-2020' },
  { id: 'superlikes', label: 'Superlikes' },
];

const state = {
  items: new Map(),
  queues: new Map(),
  explicitQueues: new Map(),
  sourceErrors: [],
  decisions: loadDecisions(),
  activeQueue: 'high-confidence',
  currentKey: null,
  mediaIndex: 0,
  history: [],
  unreviewedOnly: true,
};

const els = {
  loadStatus: byId('load-status'),
  queueTabs: byId('queue-tabs'),
  workspace: byId('workspace'),
  emptyState: byId('empty-state'),
  emptyTitle: byId('empty-title'),
  emptyBody: byId('empty-body'),
  mediaTitle: byId('media-title'),
  progressText: byId('progress-text'),
  decisionPill: byId('decision-pill'),
  mediaStage: byId('media-stage'),
  mediaStrip: byId('media-strip'),
  metaGrid: byId('meta-grid'),
  tweetText: byId('tweet-text'),
  evidenceSummary: byId('evidence-summary'),
  reasonList: byId('reason-list'),
  analysisStack: byId('analysis-stack'),
  tagList: byId('tag-list'),
  sourceLinks: byId('source-links'),
  unreviewedOnly: byId('unreviewed-only'),
  exportBtn: byId('export-btn'),
  importInput: byId('import-input'),
  backBtn: byId('back-btn'),
  noBtn: byId('no-btn'),
  superlikeBtn: byId('superlike-btn'),
  yesBtn: byId('yes-btn'),
};

bindEvents();
load();

function byId(id) {
  return document.getElementById(id);
}

function bindEvents() {
  els.unreviewedOnly.addEventListener('change', () => {
    state.unreviewedOnly = els.unreviewedOnly.checked;
    selectQueue(state.activeQueue, { preserveCurrent: true });
  });

  els.exportBtn.addEventListener('click', exportDecisions);
  els.importInput.addEventListener('change', importDecisions);
  els.backBtn.addEventListener('click', goBack);
  els.noBtn.addEventListener('click', () => decide('no'));
  els.superlikeBtn.addEventListener('click', () => decide('superlike'));
  els.yesBtn.addEventListener('click', () => decide('yes'));
}

async function load() {
  setStatus('Loading manifest...');
  try {
    const manifest = await fetchJson(MANIFEST_URL);
    const refs = collectManifestRefs(manifest);
    addInlinePayloads(refs);

    const externalRefs = refs.filter((ref) => ref.href);
    const payloads = await Promise.all(
      externalRefs.map(async (ref) => {
        try {
          const payload = await fetchJson(ref.href);
          return { ref, payload };
        } catch (error) {
          state.sourceErrors.push(`${ref.label}: ${error.message}`);
          return null;
        }
      })
    );

    for (const entry of payloads) {
      if (entry) {
        addItemsFromPayload(entry.ref, entry.payload);
      }
    }

    deriveQueues();
    const count = state.items.size;
    const loaded = externalRefs.length - state.sourceErrors.length;
    setStatus(
      `${formatNumber(count)} items loaded from ${formatNumber(loaded)} file${loaded === 1 ? '' : 's'}${
        state.sourceErrors.length ? `; ${state.sourceErrors.length} source issue(s)` : ''
      }`
    );
  } catch (error) {
    setStatus(`Could not load ${MANIFEST_URL}: ${error.message}`);
    deriveQueues();
  }

  renderQueueTabs();
  selectQueue(state.activeQueue);
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`.trim());
  }
  return response.json();
}

function collectManifestRefs(manifest) {
  const refs = [];
  const base = new URL(MANIFEST_URL, window.location.href);

  const addRef = (name, value, kind) => {
    if (!value) {
      return;
    }
    const normalized = normalizeRef(name, value, kind, base);
    if (normalized) {
      refs.push(normalized);
    }
  };

  scanManifestMap(manifest?.queues, 'queue', addRef);
  scanManifestMap(manifest?.metadata?.queues, 'queue', addRef);
  scanManifestMap(manifest?.datasets, 'dataset', addRef);
  scanManifestMap(manifest?.metadata?.datasets, 'dataset', addRef);

  if (Array.isArray(manifest?.items)) {
    refs.push({
      id: 'manifest',
      label: 'Manifest',
      kind: 'queue',
      inlinePayload: manifest,
    });
  }

  const seen = new Set();
  return refs.filter((ref) => {
    const key = ref.href || `${ref.id}:inline`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function scanManifestMap(value, kind, addRef) {
  if (!value) {
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((entry, index) => {
      const name = entry?.id || entry?.name || entry?.label || `${kind}-${index + 1}`;
      addRef(name, entry, kind);
    });
    return;
  }
  if (typeof value === 'object') {
    for (const [name, entry] of Object.entries(value)) {
      addRef(name, entry, kind);
    }
  }
}

function normalizeRef(name, value, kind, base) {
  if (typeof value === 'string') {
    return {
      id: normalizeQueueId(name),
      label: humanize(name),
      kind,
      href: new URL(value, base).href,
    };
  }

  if (!value || typeof value !== 'object') {
    return null;
  }

  const path = value.path || value.file || value.href || value.url || value.src;
  const id = normalizeQueueId(value.id || value.name || name);
  const label = value.label || humanize(value.name || name);
  if (path) {
    return {
      id,
      label,
      kind,
      href: new URL(path, base).href,
    };
  }

  if (Array.isArray(value.items) || Array.isArray(value.data) || Array.isArray(value.records)) {
    return {
      id,
      label,
      kind,
      inlinePayload: value,
    };
  }

  return null;
}

function addInlinePayloads(refs) {
  for (const ref of refs) {
    if (ref.inlinePayload) {
      addItemsFromPayload(ref, ref.inlinePayload);
    }
  }
}

function addItemsFromPayload(ref, payload) {
  const rawItems = payloadItems(payload);
  const queueMembers = [];
  rawItems.forEach((raw, index) => {
    const item = normalizeItem(raw, ref, payload?.metadata, index);
    if (!item) {
      return;
    }

    const existing = state.items.get(item.key);
    if (existing) {
      existing.sources = unique([...existing.sources, ...item.sources]);
      existing.media = existing.media.length ? existing.media : item.media;
      existing.tags = unique([...existing.tags, ...item.tags]);
    } else {
      state.items.set(item.key, item);
    }
    queueMembers.push(item.key);
  });

  if (isKnownQueue(ref.id)) {
    mergeExplicitQueue(ref.id, queueMembers);
  }
}

function payloadItems(payload) {
  if (Array.isArray(payload)) {
    return payload;
  }
  for (const key of ['items', 'data', 'records', 'candidates', 'queue']) {
    if (Array.isArray(payload?.[key])) {
      return payload[key];
    }
  }
  return [];
}

function normalizeItem(raw, ref, metadata, index) {
  if (!raw || typeof raw !== 'object') {
    return null;
  }

  const media = normalizeMedia(raw);
  const tweetId = stringValue(raw.tweet_id || raw.tweetId || raw.id);
  const mediaId = stringValue(raw.media_id || raw.mediaId || media[0]?.id);
  const key = stringValue(
    raw.item_key || raw.key || raw.review_key || raw.id || joinKey(tweetId, mediaId)
  );
  const fallbackKey = `${ref.id || 'source'}:${index + 1}`;
  const account = raw.account || {};
  const evidence = raw.evidence || {};
  const engagement = raw.engagement || {};
  const postedAt = stringValue(raw.posted_at || raw.created_at || raw.date);
  const era = raw.era || metadata?.era || eraFromDate(postedAt);
  const tags = unique([
    ...arrayValues(raw.tags),
    ...arrayValues(raw.genre_tags),
    ...arrayValues(raw.produced_video_tags),
    ...arrayValues(raw.candidate_visual_tags),
    ...arrayValues(raw.creative_forms).map((value) => `form:${value}`),
    ...arrayValues(raw.subjects).map((value) => `subject:${value}`),
  ]);

  return {
    key: key || fallbackKey,
    tweetId,
    mediaId,
    accountHandle: stringValue(account.handle || raw.account_handle || raw.handle),
    accountLabel: stringValue(
      account.label || raw.account_label || account.handle || raw.account_handle
    ),
    accountCategory: stringValue(account.category || raw.account_category),
    postedAt,
    era,
    tweetUrl: stringValue(raw.tweet_url || raw.url),
    tweetText: stringValue(
      raw.tweet_text || raw.text_resolved || raw.text || raw.tweet_text_excerpt
    ),
    reviewState: stringValue(raw.review_state || raw.status),
    confidence: stringValue(raw.confidence),
    basis: stringValue(raw.inclusion_basis || raw.basis || raw.bucket),
    score: numberValue(raw.score || raw.priority),
    readiness: raw.readiness || {},
    media,
    evidenceSummary: stringValue(
      evidence.summary ||
        raw.evidence_summary ||
        raw.description ||
        raw.visual_observation ||
        raw.summary
    ),
    notableText: stringValue(evidence.notable_text || raw.notable_text),
    reasons: unique([...arrayValues(evidence.reasons), ...arrayValues(raw.reasons)]),
    sourceSidecars: unique([
      ...arrayValues(evidence.source_sidecars),
      ...arrayValues(raw.source_rows),
    ]),
    tags,
    engagement: {
      likes: numberValue(engagement.likes || raw.like_count),
      retweets: numberValue(engagement.retweets || raw.retweet_count),
      quotes: numberValue(engagement.quotes || raw.quote_count),
      views: numberValue(engagement.views || raw.view_count),
    },
    sources: unique([ref.label || ref.id].filter(Boolean)),
  };
}

function normalizeMedia(raw) {
  const mediaItems = [];
  if (Array.isArray(raw.media)) {
    mediaItems.push(...raw.media);
  } else if (Array.isArray(raw.media_items)) {
    mediaItems.push(...raw.media_items);
  } else if (raw.archive_url || raw.release_asset_url || raw.media_url || raw.url) {
    mediaItems.push(raw);
  }

  return mediaItems
    .map((media, index) => {
      if (!media || typeof media !== 'object') {
        return null;
      }
      const archiveUrl =
        media.archive_url ||
        media.release_asset_url ||
        media.media_url ||
        media.asset_url ||
        media.url;
      const originalUrl = media.original_url || media.source_url;
      return {
        id: stringValue(media.media_id || media.id || raw.media_id || `media-${index + 1}`),
        type: normalizeMediaType(media.type || media.media_type || raw.media_type, archiveUrl),
        archiveUrl: normalizeAssetUrl(archiveUrl),
        originalUrl: stringValue(originalUrl),
        thumbnailUrl: normalizeAssetUrl(media.thumbnail_url || media.thumbnail || media.poster),
        durationSec: numberValue(media.duration_sec || media.duration),
        width: numberValue(media.width),
        height: numberValue(media.height),
        readiness: media.readiness || {},
        analysis: media.analysis || {},
      };
    })
    .filter((media) => media && media.archiveUrl);
}

function normalizeMediaType(value, url) {
  const type = stringValue(value).toLowerCase();
  if (type.includes('photo') || type.includes('image')) {
    return 'image';
  }
  if (type.includes('video') || type.includes('gif')) {
    return 'video';
  }
  const cleanUrl = stringValue(url).split('?')[0].toLowerCase();
  if (/\.(png|jpe?g|webp|gif)$/.test(cleanUrl)) {
    return 'image';
  }
  if (/\.(mp4|webm|mov|m4v)$/.test(cleanUrl)) {
    return 'video';
  }
  return type || 'media';
}

function normalizeAssetUrl(value) {
  const url = stringValue(value);
  if (!url) {
    return '';
  }
  if (/^(https?:|data:|blob:|\/|\.\.?\/)/i.test(url)) {
    return url;
  }
  if (url.startsWith('data/')) {
    return `../${url}`;
  }
  return url;
}

function deriveQueues() {
  for (const def of QUEUE_DEFS) {
    state.queues.set(def.id, []);
  }

  if (state.explicitQueues.size) {
    for (const def of QUEUE_DEFS) {
      const explicit = state.explicitQueues.get(def.id);
      if (explicit?.length) {
        state.queues.set(
          def.id,
          explicit.filter((key) => state.items.has(key))
        );
      }
    }
  }

  const items = [...state.items.values()];
  fillQueueIfEmpty(
    'high-confidence',
    items
      .filter((item) => isHighConfidence(item))
      .sort(byConfidenceThenEngagement)
      .map((item) => item.key)
  );
  fillQueueIfEmpty(
    'candidates',
    items
      .filter((item) => !isHighConfidence(item) && item.era !== '2016_2020')
      .sort(byConfidenceThenEngagement)
      .map((item) => item.key)
  );
  fillQueueIfEmpty(
    '2016-2020',
    items
      .filter((item) => item.era === '2016_2020' || isYearRange(item.postedAt, 2016, 2020))
      .sort(byConfidenceThenEngagement)
      .map((item) => item.key)
  );
  state.queues.set('superlikes', superlikedKeys());
}

function fillQueueIfEmpty(id, keys) {
  if (!state.queues.get(id)?.length) {
    state.queues.set(id, unique(keys));
  }
}

function mergeExplicitQueue(id, keys) {
  const queueId = normalizeQueueId(id);
  if (!isKnownQueue(queueId)) {
    return;
  }
  const existing = state.explicitQueues.get(queueId) || [];
  state.explicitQueues.set(queueId, unique([...existing, ...keys]));
}

function isKnownQueue(id) {
  return QUEUE_DEFS.some((def) => def.id === id);
}

function isHighConfidence(item) {
  return (
    item.confidence.toLowerCase() === 'high' ||
    item.reviewState.toLowerCase() === 'curated' ||
    item.score >= 100
  );
}

function byConfidenceThenEngagement(left, right) {
  return (
    Number(isHighConfidence(right)) - Number(isHighConfidence(left)) ||
    right.score - left.score ||
    engagementScore(right) - engagementScore(left) ||
    right.postedAt.localeCompare(left.postedAt)
  );
}

function engagementScore(item) {
  return item.engagement.likes + item.engagement.retweets + item.engagement.quotes;
}

function renderQueueTabs() {
  els.queueTabs.replaceChildren();
  for (const def of QUEUE_DEFS) {
    const keys = state.queues.get(def.id) || [];
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'queue-tab';
    button.dataset.queueId = def.id;
    button.setAttribute('aria-pressed', String(def.id === state.activeQueue));
    button.append(textNode(def.label));

    const count = document.createElement('span');
    count.className = 'queue-count';
    count.textContent = formatNumber(visibleKeys(def.id).length || keys.length);
    button.append(count);
    button.addEventListener('click', () => selectQueue(def.id));
    els.queueTabs.append(button);
  }
}

function selectQueue(queueId, options = {}) {
  state.activeQueue = queueId;
  state.mediaIndex = 0;
  state.queues.set('superlikes', superlikedKeys());

  const keys = visibleKeys(queueId);
  if (options.preserveCurrent && state.currentKey && keys.includes(state.currentKey)) {
    render();
    return;
  }

  if (!options.preserveCurrent) {
    state.history = [];
  }
  state.currentKey = keys[0] || null;
  render();
}

function visibleKeys(queueId) {
  const keys = state.queues.get(queueId) || [];
  if (!state.unreviewedOnly || queueId === 'superlikes') {
    return keys.filter((key) => state.items.has(key));
  }
  return keys.filter((key) => state.items.has(key) && !state.decisions[key]);
}

function allQueueKeys(queueId) {
  return (state.queues.get(queueId) || []).filter((key) => state.items.has(key));
}

function render() {
  renderQueueTabs();
  const item = state.currentKey ? state.items.get(state.currentKey) : null;
  const keys = visibleKeys(state.activeQueue);
  const allKeys = allQueueKeys(state.activeQueue);
  const hasCurrentOutsideFilter = item && !keys.includes(item.key);

  els.workspace.hidden = !item;
  els.emptyState.hidden = Boolean(item);
  if (!item) {
    renderEmpty(keys, allKeys);
    updateReviewButtons(null);
    return;
  }

  const positionSet = hasCurrentOutsideFilter ? allKeys : keys;
  const position = Math.max(0, positionSet.indexOf(item.key)) + 1;
  const total = positionSet.length || 1;
  els.mediaTitle.textContent = itemTitle(item);
  els.progressText.textContent = `${formatNumber(position)} of ${formatNumber(total)} in ${queueLabel(
    state.activeQueue
  )}`;
  renderDecision(item.key);
  renderMedia(item);
  renderDetails(item);
  updateReviewButtons(item);
}

function renderEmpty(keys, allKeys) {
  const loadedItems = state.items.size;
  if (!loadedItems) {
    els.emptyTitle.textContent = 'Creative data not available';
    els.emptyBody.textContent =
      window.location.protocol === 'file:'
        ? `Serve this directory over HTTP so the page can fetch ${MANIFEST_URL}.`
        : `No items were loaded from ${MANIFEST_URL}.`;
    return;
  }
  if (
    !keys.length &&
    allKeys.length &&
    state.unreviewedOnly &&
    state.activeQueue !== 'superlikes'
  ) {
    els.emptyTitle.textContent = 'Queue reviewed';
    els.emptyBody.textContent = `${queueLabel(state.activeQueue)} has no unreviewed candidates.`;
    return;
  }
  els.emptyTitle.textContent = 'Nothing to review';
  els.emptyBody.textContent = `${queueLabel(state.activeQueue)} has no candidates in the loaded data.`;
}

function renderDecision(key) {
  const record = state.decisions[key];
  const decision = record?.decision || 'undecided';
  els.decisionPill.className = `decision-pill ${decision === 'undecided' ? '' : decision}`;
  els.decisionPill.textContent = humanize(decision);
}

function renderMedia(item) {
  els.mediaStage.replaceChildren();
  els.mediaStrip.replaceChildren();

  if (!item.media.length) {
    els.mediaStage.append(mediaFallback('No archived media URL is available for this candidate.'));
    return;
  }

  if (state.mediaIndex >= item.media.length) {
    state.mediaIndex = 0;
  }
  const media = item.media[state.mediaIndex];
  if (media.type === 'image') {
    const image = document.createElement('img');
    image.src = media.archiveUrl;
    image.alt = mediaAlt(item, media);
    image.loading = 'eager';
    image.addEventListener('error', () => {
      els.mediaStage.replaceChildren(mediaFallback('Image failed to load.', media.archiveUrl));
    });
    els.mediaStage.append(image);
  } else if (media.type === 'video') {
    const video = document.createElement('video');
    video.controls = true;
    video.preload = 'metadata';
    video.playsInline = true;
    if (media.thumbnailUrl) {
      video.poster = media.thumbnailUrl;
    }
    const source = document.createElement('source');
    source.src = media.archiveUrl;
    source.type = media.archiveUrl.split('?')[0].toLowerCase().endsWith('.webm')
      ? 'video/webm'
      : 'video/mp4';
    video.append(source);
    video.addEventListener('error', () => {
      els.mediaStage.replaceChildren(mediaFallback('Video failed to load.', media.archiveUrl));
    });
    els.mediaStage.append(video);
  } else {
    els.mediaStage.append(
      mediaFallback('Media type is not directly previewable.', media.archiveUrl)
    );
  }

  if (item.media.length > 1) {
    item.media.forEach((entry, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'media-chip';
      button.setAttribute('aria-pressed', String(index === state.mediaIndex));
      if (entry.thumbnailUrl) {
        const thumb = document.createElement('img');
        thumb.src = entry.thumbnailUrl;
        thumb.alt = '';
        button.append(thumb);
      }
      const label = document.createElement('span');
      label.textContent = `${index + 1}. ${entry.type}${entry.durationSec ? ` ${entry.durationSec}s` : ''}`;
      button.append(label);
      button.addEventListener('click', () => {
        state.mediaIndex = index;
        renderMedia(item);
      });
      els.mediaStrip.append(button);
    });
  }
}

function mediaFallback(message, href = '') {
  const box = document.createElement('div');
  box.className = 'media-fallback';
  const inner = document.createElement('div');
  const text = document.createElement('p');
  text.textContent = message;
  inner.append(text);
  if (href) {
    const link = document.createElement('a');
    link.href = href;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = 'Open archive asset';
    inner.append(link);
  }
  box.append(inner);
  return box;
}

function renderDetails(item) {
  els.metaGrid.replaceChildren();
  addMeta(
    'Account',
    item.accountHandle ? `@${item.accountHandle}` : item.accountLabel || 'Unknown'
  );
  addMeta('Date', formatDate(item.postedAt));
  addMeta('Confidence', item.confidence || item.reviewState || 'Unknown');
  addMeta('Score', item.score ? formatNumber(item.score) : 'Unknown');
  addMeta('Basis', item.basis || 'Unknown');
  addMeta('Ready', readinessLabel(item));
  addMeta('Engagement', formatEngagement(item.engagement));

  els.tweetText.textContent = item.tweetText || 'No tweet text captured.';
  els.evidenceSummary.textContent =
    [item.evidenceSummary, item.notableText].filter(Boolean).join('\n\n') ||
    'No evidence summary supplied.';

  els.reasonList.replaceChildren();
  for (const reason of item.reasons.slice(0, 8)) {
    const li = document.createElement('li');
    li.textContent = reason;
    els.reasonList.append(li);
  }

  renderAnalysis(item);

  els.tagList.replaceChildren();
  const tags = item.tags.length ? item.tags : ['untagged'];
  for (const tag of tags.slice(0, 36)) {
    const chip = document.createElement('span');
    chip.className = 'tag';
    chip.textContent = tag;
    els.tagList.append(chip);
  }

  els.sourceLinks.replaceChildren();
  addLink('Tweet', item.tweetUrl);
  const media = item.media[state.mediaIndex] || item.media[0];
  addLink('Archive asset', media?.archiveUrl);
  addLink('Original media', media?.originalUrl);
}

function renderAnalysis(item) {
  els.analysisStack.replaceChildren();
  const media = item.media[state.mediaIndex] || item.media[0];
  if (!media) {
    els.analysisStack.append(analysisEmpty('No media analysis is attached to this row.'));
    return;
  }

  const analysis = media.analysis || {};
  const readiness = media.readiness || {};
  const description = analysis.description || {};
  const ocr = analysis.ocr || {};
  const audio = analysis.audio || {};
  const transcript = analysis.transcript || {};
  const keyframes = analysis.keyframes || {};

  const statusParts = [];
  if (readiness.ready === true) {
    statusParts.push('ready');
  }
  if (media.type === 'video') {
    statusParts.push(`keyframes ${keyframes.frame_count || 0}`);
    if (audio.status) {
      statusParts.push(`audio ${audio.status}`);
    }
    if (transcript.status) {
      statusParts.push(`transcript ${transcript.status}`);
    }
  }
  if (ocr.status_counts) {
    statusParts.push(`OCR ${statusCountsText(ocr.status_counts)}`);
  }
  addAnalysisCard('Readiness', statusParts.join(' / ') || 'Ready sidecars present.');
  addAnalysisCard(
    `Description${description.source ? ` (${description.source})` : ''}`,
    description.text || 'No description text attached.'
  );
  addAnalysisCard('OCR', ocr.text || 'OCR completed with no readable text.', {
    meta: ocr.status_counts ? statusCountsText(ocr.status_counts) : '',
  });
  if (media.type === 'video') {
    addAnalysisCard('Transcript', transcript.text || 'Transcript completed with no speech text.', {
      meta: transcript.status ? `status ${transcript.status}` : '',
    });
  }
}

function analysisEmpty(message) {
  const node = document.createElement('p');
  node.className = 'analysis-empty';
  node.textContent = message;
  return node;
}

function addAnalysisCard(title, text, options = {}) {
  const details = document.createElement('details');
  details.className = 'analysis-card';
  details.open = title === 'Readiness' || title.startsWith('Description');

  const summary = document.createElement('summary');
  const label = document.createElement('span');
  label.textContent = title;
  summary.append(label);
  if (options.meta) {
    const meta = document.createElement('small');
    meta.textContent = options.meta;
    summary.append(meta);
  }

  const body = document.createElement('pre');
  body.textContent = stringValue(text) || 'No text.';
  details.append(summary, body);
  els.analysisStack.append(details);
}

function addMeta(label, value) {
  const cell = document.createElement('div');
  cell.className = 'meta-cell';
  const labelNode = document.createElement('div');
  labelNode.className = 'meta-label';
  labelNode.textContent = label;
  const valueNode = document.createElement('div');
  valueNode.className = 'meta-value';
  valueNode.textContent = stringValue(value) || 'Unknown';
  cell.append(labelNode, valueNode);
  els.metaGrid.append(cell);
}

function addLink(label, href) {
  if (!href) {
    return;
  }
  const link = document.createElement('a');
  link.href = href;
  link.target = '_blank';
  link.rel = 'noopener';
  link.textContent = label;
  els.sourceLinks.append(link);
}

function updateReviewButtons(item) {
  const disabled = !item;
  for (const button of [els.noBtn, els.superlikeBtn, els.yesBtn]) {
    button.disabled = disabled;
    button.classList.remove('active');
  }
  els.backBtn.disabled = state.history.length === 0;
  if (!item) {
    return;
  }
  const decision = state.decisions[item.key]?.decision;
  if (decision === 'no') {
    els.noBtn.classList.add('active');
  } else if (decision === 'superlike') {
    els.superlikeBtn.classList.add('active');
  } else if (decision === 'yes') {
    els.yesBtn.classList.add('active');
  }
}

function decide(decision) {
  const item = state.currentKey ? state.items.get(state.currentKey) : null;
  if (!item) {
    return;
  }
  state.decisions[item.key] = {
    decision,
    decided_at: new Date().toISOString(),
    queue: state.activeQueue,
    item_key: item.key,
    tweet_id: item.tweetId,
    media_id: item.mediaId,
    account_handle: item.accountHandle,
    posted_at: item.postedAt,
    tweet_url: item.tweetUrl,
  };
  saveDecisions();
  state.queues.set('superlikes', superlikedKeys());
  advance();
}

function advance() {
  const current = state.currentKey;
  if (!current) {
    render();
    return;
  }

  const allKeys = allQueueKeys(state.activeQueue);
  const visible = visibleKeys(state.activeQueue);
  const sourceKeys = state.unreviewedOnly || state.activeQueue === 'superlikes' ? visible : allKeys;
  const currentIndex = allKeys.indexOf(current);
  const next = sourceKeys.find((key) => allKeys.indexOf(key) > currentIndex) || null;

  state.history.push(current);
  state.currentKey = next;
  state.mediaIndex = 0;
  render();
}

function goBack() {
  const previous = state.history.pop();
  if (!previous) {
    return;
  }
  state.currentKey = previous;
  state.mediaIndex = 0;
  render();
}

function exportDecisions() {
  const payload = {
    schema_version: 1,
    exported_at: new Date().toISOString(),
    source_manifest: MANIFEST_URL,
    decision_count: Object.keys(state.decisions).length,
    decisions: state.decisions,
  };
  const blob = new Blob([JSON.stringify(payload, null, 2) + '\n'], { type: 'application/json' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = `creative-review-decisions-${timestampForFile()}.json`;
  document.body.append(link);
  link.click();
  URL.revokeObjectURL(link.href);
  link.remove();
}

async function importDecisions(event) {
  const file = event.target.files?.[0];
  event.target.value = '';
  if (!file) {
    return;
  }
  try {
    const payload = JSON.parse(await file.text());
    const incoming = normalizeImportedDecisions(payload);
    let count = 0;
    for (const [key, record] of Object.entries(incoming)) {
      if (!record.decision) {
        continue;
      }
      state.decisions[key] = {
        ...state.decisions[key],
        ...record,
        imported_at: new Date().toISOString(),
      };
      count += 1;
    }
    saveDecisions();
    state.queues.set('superlikes', superlikedKeys());
    setStatus(`Imported ${formatNumber(count)} decision${count === 1 ? '' : 's'}.`);
    selectQueue(state.activeQueue, { preserveCurrent: true });
  } catch (error) {
    setStatus(`Import failed: ${error.message}`);
  }
}

function normalizeImportedDecisions(payload) {
  const source = payload?.decisions || payload;
  const out = {};
  if (Array.isArray(source)) {
    for (const entry of source) {
      if (!entry || typeof entry !== 'object') {
        continue;
      }
      const key = stringValue(
        entry.item_key || entry.key || joinKey(entry.tweet_id, entry.media_id)
      );
      const decision = normalizeDecision(entry.decision || entry.value);
      if (key && decision) {
        out[key] = { ...entry, decision };
      }
    }
    return out;
  }
  if (source && typeof source === 'object') {
    for (const [key, value] of Object.entries(source)) {
      const record = typeof value === 'string' ? { decision: value } : value;
      const decision = normalizeDecision(record?.decision || record?.value);
      if (decision) {
        out[key] = { ...record, decision };
      }
    }
  }
  return out;
}

function normalizeDecision(value) {
  const clean = stringValue(value).toLowerCase();
  return ['yes', 'no', 'superlike'].includes(clean) ? clean : '';
}

function superlikedKeys() {
  const explicit = state.explicitQueues.get('superlikes') || [];
  const local = Object.entries(state.decisions)
    .filter(([, record]) => record?.decision === 'superlike')
    .map(([key]) => key);
  return unique([...explicit, ...local]).filter((key) => state.items.has(key));
}

function loadDecisions() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch (_error) {
    return {};
  }
}

function saveDecisions() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state.decisions));
}

function setStatus(message) {
  els.loadStatus.textContent = message;
}

function queueLabel(id) {
  return QUEUE_DEFS.find((queue) => queue.id === id)?.label || humanize(id);
}

function normalizeQueueId(value) {
  const clean = stringValue(value).toLowerCase().replace(/_/g, '-');
  if (clean.includes('super')) {
    return 'superlikes';
  }
  if (clean.includes('2016') || clean.includes('historical') || clean.includes('older')) {
    return '2016-2020';
  }
  if (clean.includes('high')) {
    return 'high-confidence';
  }
  if (clean.includes('candidate')) {
    return 'candidates';
  }
  return clean || 'candidates';
}

function itemTitle(item) {
  const handle = item.accountHandle
    ? `@${item.accountHandle}`
    : item.accountLabel || 'Unknown account';
  return `${handle} ${formatDate(item.postedAt)}`;
}

function mediaAlt(item, media) {
  const handle = item.accountHandle ? `@${item.accountHandle}` : item.accountLabel || 'candidate';
  return `${media.type} from ${handle}`;
}

function eraFromDate(value) {
  if (isYearRange(value, 2016, 2020)) {
    return '2016_2020';
  }
  const year = yearFromDate(value);
  if (year >= 2025) {
    return '2025_plus';
  }
  return '';
}

function isYearRange(value, min, max) {
  const year = yearFromDate(value);
  return year >= min && year <= max;
}

function yearFromDate(value) {
  const match = stringValue(value).match(/^(\d{4})/);
  return match ? Number(match[1]) : 0;
}

function formatDate(value) {
  if (!value) {
    return 'Unknown';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return stringValue(value);
  }
  return date.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
  });
}

function formatEngagement(engagement) {
  const parts = [
    ['L', engagement.likes],
    ['R', engagement.retweets],
    ['Q', engagement.quotes],
    ['V', engagement.views],
  ]
    .filter(([, value]) => value)
    .map(([label, value]) => `${label} ${formatNumber(value)}`);
  return parts.length ? parts.join(' / ') : 'Unknown';
}

function readinessLabel(item) {
  const readiness = item.readiness || {};
  if (readiness.ready === true) {
    return `${readiness.media_count || item.media.length || 0} media ready`;
  }
  const blockers = arrayValues(readiness.blockers);
  return blockers.length ? blockers.join(' / ') : 'Unknown';
}

function statusCountsText(counts) {
  if (!counts || typeof counts !== 'object') {
    return '';
  }
  return Object.entries(counts)
    .map(([status, count]) => `${status}: ${formatNumber(count)}`)
    .join(', ');
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(Number(value) || 0);
}

function humanize(value) {
  return stringValue(value)
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function timestampForFile() {
  return new Date().toISOString().replace(/[-:]/g, '').replace(/\..+/, 'Z');
}

function joinKey(tweetId, mediaId) {
  return [tweetId, mediaId].filter(Boolean).join(':');
}

function stringValue(value) {
  return value == null ? '' : String(value).trim();
}

function numberValue(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function arrayValues(value) {
  if (!value) {
    return [];
  }
  if (Array.isArray(value)) {
    return value.map((entry) => {
      if (entry && typeof entry === 'object' && 'tag' in entry) {
        return stringValue(entry.tag);
      }
      return stringValue(entry);
    });
  }
  return stringValue(value)
    .split(/[;,]/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function textNode(value) {
  return document.createTextNode(value);
}
