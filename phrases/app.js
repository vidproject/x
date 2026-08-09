const PAGE_SIZE = 25;
const PHRASE_COLORS = {
  'criminal-alien': '#a23a35',
  'illegal-alien': '#286c9c',
  'angel-mother': '#a66f00',
  'angel-family': '#32734b',
};
const SOURCE_LABELS = {
  'tweet-text': 'Post text',
  'image-ocr': 'Image OCR',
  'video-frame-ocr': 'Video-frame OCR',
  transcript: 'Transcript',
};

const elements = {
  scope: document.querySelector('#scope-line'),
  filteredTotal: document.querySelector('#filtered-total'),
  summary: document.querySelector('#summary-body'),
  phrase: document.querySelector('#phrase-filter'),
  account: document.querySelector('#account-filter'),
  source: document.querySelector('#source-filter'),
  from: document.querySelector('#date-from'),
  to: document.querySelector('#date-to'),
  search: document.querySelector('#text-filter'),
  reset: document.querySelector('#reset-filters'),
  grain: document.querySelector('#grain-filter'),
  canvas: document.querySelector('#timeline-chart'),
  chartTooltip: document.querySelector('#chart-tooltip'),
  chartTitle: document.querySelector('#chart-title'),
  caption: document.querySelector('#chart-caption'),
  legend: document.querySelector('#chart-legend'),
  rangeStart: document.querySelector('#range-start'),
  rangeEnd: document.querySelector('#range-end'),
  rangeStartLabel: document.querySelector('#range-start-label'),
  rangeEndLabel: document.querySelector('#range-end-label'),
  rangeSelection: document.querySelector('#range-selection'),
  rangeReset: document.querySelector('#range-reset'),
  sort: document.querySelector('#sort-order'),
  resultCount: document.querySelector('#result-count'),
  results: document.querySelector('#results'),
  pager: document.querySelector('#pager'),
  template: document.querySelector('#tweet-template'),
};

let report;
let resizeTimer;
let chartModel;
let chartSelection;
let rangeMax = 1;
const state = {
  phrase: '',
  account: '',
  source: '',
  from: '',
  to: '',
  q: '',
  grain: 'day',
  sort: 'desc',
  page: 1,
};

function readHash() {
  const params = new URLSearchParams(location.hash.replace(/^#/, ''));
  for (const key of Object.keys(state)) {
    if (params.has(key)) state[key] = params.get(key);
  }
  state.page = Math.max(1, Number.parseInt(state.page, 10) || 1);
  if (!['day', 'week', 'month'].includes(state.grain)) state.grain = 'day';
  if (!['asc', 'desc'].includes(state.sort)) state.sort = 'desc';
}

function writeHash() {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(state)) {
    if (
      value &&
      !(key === 'grain' && value === 'day') &&
      !(key === 'sort' && value === 'desc') &&
      !(key === 'page' && value === 1)
    ) {
      params.set(key, String(value));
    }
  }
  history.replaceState(null, '', params.size ? `#${params}` : location.pathname);
}

function hasEvidence(tweet, phraseKey = state.phrase, source = state.source) {
  return tweet.evidence.some((item) => {
    return (!phraseKey || item.phrase === phraseKey) && (!source || item.source === source);
  });
}

function searchableText(tweet) {
  return [
    tweet.text,
    tweet.account_handle,
    tweet.account_group,
    ...tweet.evidence.map((item) => item.snippet),
  ]
    .join(' ')
    .toLocaleLowerCase();
}

function baseFilteredTweets() {
  const query = state.q.trim().toLocaleLowerCase();
  return report.tweets.filter((tweet) => {
    if (state.account && tweet.account_group !== state.account) return false;
    if (state.from && tweet.posted_at.slice(0, 10) < state.from) return false;
    if (state.to && tweet.posted_at.slice(0, 10) > state.to) return false;
    if (query && !searchableText(tweet).includes(query)) return false;
    if (state.source && !hasEvidence(tweet, '', state.source)) return false;
    return true;
  });
}

function filteredTweets() {
  const rows = baseFilteredTweets().filter((tweet) => {
    if (state.phrase && !tweet.phrases.includes(state.phrase)) return false;
    if (state.source && !hasEvidence(tweet, state.phrase, state.source)) return false;
    return true;
  });
  rows.sort((a, b) => {
    const value = a.posted_at.localeCompare(b.posted_at) || a.tweet_id.localeCompare(b.tweet_id);
    return state.sort === 'asc' ? value : -value;
  });
  return rows;
}

function phraseLabel(key) {
  return report.phrases.find((phrase) => phrase.key === key)?.label || key;
}

function phraseClass(key) {
  return `phrase-${key}`;
}

function countForPhrase(rows, key) {
  return rows.reduce(
    (total, tweet) =>
      total +
      Number(
        tweet.phrases.includes(key) && (!state.source || hasEvidence(tweet, key, state.source))
      ),
    0
  );
}

function renderPhraseControls() {
  elements.phrase.replaceChildren();
  const options = [{ key: '', label: 'All phrases' }, ...report.phrases];
  for (const phrase of options) {
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.value = phrase.key;
    button.textContent = phrase.label;
    button.setAttribute('aria-pressed', String(state.phrase === phrase.key));
    if (phrase.key) button.classList.add(phraseClass(phrase.key));
    button.addEventListener('click', () => {
      chartSelection = null;
      state.phrase = phrase.key;
      state.page = 1;
      render();
    });
    elements.phrase.append(button);
  }
}

function renderSummary(rows) {
  elements.summary.replaceChildren();
  for (const header of document.querySelectorAll('.summary-table th[data-account]')) {
    header.hidden = Boolean(state.account && header.dataset.account !== state.account);
  }
  for (const phrase of report.phrases) {
    const row = document.createElement('tr');
    row.className = phraseClass(phrase.key);
    if (state.phrase === phrase.key) row.classList.add('selected');
    const labelCell = document.createElement('th');
    labelCell.scope = 'row';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'summary-phrase';
    button.setAttribute('aria-pressed', String(state.phrase === phrase.key));
    button.textContent = phrase.label;
    button.addEventListener('click', () => {
      chartSelection = null;
      state.phrase = state.phrase === phrase.key ? '' : phrase.key;
      state.page = 1;
      render();
    });
    labelCell.append(button);
    row.append(labelCell);

    const totalCell = document.createElement('td');
    totalCell.textContent = countForPhrase(rows, phrase.key).toLocaleString();
    row.append(totalCell);
    for (const account of report.scope.account_groups) {
      const count = rows.filter(
        (tweet) =>
          tweet.account_group === account &&
          tweet.phrases.includes(phrase.key) &&
          (!state.source || hasEvidence(tweet, phrase.key, state.source))
      ).length;
      const cell = document.createElement('td');
      cell.textContent = count.toLocaleString();
      cell.hidden = Boolean(state.account && account !== state.account);
      row.append(cell);
    }
    elements.summary.append(row);
  }
}

function sourceLabel(source) {
  return SOURCE_LABELS[source] || source;
}

function renderEvidence(tweet, container) {
  const evidence = tweet.evidence.filter((item) => {
    return (
      (!state.phrase || item.phrase === state.phrase) &&
      (!state.source || item.source === state.source)
    );
  });
  const unique = new Map();
  for (const item of evidence) {
    unique.set(`${item.phrase}|${item.source}|${item.media_id}|${item.snippet}`, item);
  }
  container.replaceChildren();
  for (const item of unique.values()) {
    const row = document.createElement('div');
    row.className = 'evidence-item';
    const heading = document.createElement('strong');
    heading.textContent = `${phraseLabel(item.phrase)} in ${sourceLabel(item.source)}`;
    const snippet = document.createElement('span');
    snippet.textContent = `: ${item.snippet}`;
    row.append(heading, snippet);
    container.append(row);
  }
}

function mediaKind(media) {
  return ['video', 'animated_gif'].includes(media.media_type) ? 'video' : 'image';
}

function showMedia(tweet, container, button) {
  if (!container.hidden) {
    container.hidden = true;
    container.replaceChildren();
    button.textContent = `Show media (${tweet.media.length})`;
    return;
  }
  container.replaceChildren();
  for (const media of tweet.media) {
    let node;
    if (mediaKind(media) === 'video') {
      node = document.createElement('video');
      node.controls = true;
      node.preload = 'metadata';
    } else {
      node = document.createElement('img');
      node.loading = 'lazy';
      node.alt = media.alt_text || 'Archived post image';
    }
    node.src = media.release_asset_url;
    container.append(node);
  }
  container.hidden = false;
  button.textContent = 'Hide media';
}

function actionLink(label, href) {
  const link = document.createElement('a');
  link.className = 'button';
  link.href = href;
  link.target = '_blank';
  link.rel = 'noopener';
  link.textContent = label;
  return link;
}

function renderTweet(tweet) {
  const row = elements.template.content.firstElementChild.cloneNode(true);
  row.querySelector('.tweet-meta').textContent =
    `${tweet.posted_at.slice(0, 10)} | @${tweet.account_handle} | ${tweet.account_group} | ${tweet.tweet_id}`;
  const tags = row.querySelector('.tweet-tags');
  for (const key of tweet.phrases) {
    const tag = document.createElement('span');
    tag.className = `tag ${phraseClass(key)}`;
    tag.textContent = phraseLabel(key);
    tags.append(tag);
  }
  const relevantSources = [
    ...new Set(
      tweet.evidence
        .filter((item) => !state.phrase || item.phrase === state.phrase)
        .map((item) => item.source)
    ),
  ];
  for (const source of relevantSources) {
    const tag = document.createElement('span');
    tag.className = 'tag source-tag';
    tag.textContent = sourceLabel(source);
    tags.append(tag);
  }
  const text = row.querySelector('.tweet-text');
  text.textContent = tweet.text || 'No authored post text archived.';
  if (!tweet.text) text.classList.add('muted');
  renderEvidence(tweet, row.querySelector('.evidence-list'));
  const actions = row.querySelector('.tweet-actions');
  actions.append(actionLink('Archive record', `../#tweet=${tweet.tweet_id}`));
  if (tweet.tweet_url) actions.append(actionLink('View on X', tweet.tweet_url));
  if (tweet.media.length) {
    const mediaButton = document.createElement('button');
    mediaButton.type = 'button';
    mediaButton.className = 'button';
    mediaButton.textContent = `Show media (${tweet.media.length})`;
    const mediaArea = row.querySelector('.media-area');
    mediaButton.addEventListener('click', () => showMedia(tweet, mediaArea, mediaButton));
    actions.append(mediaButton);
  }
  return row;
}

function renderResults(rows) {
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  state.page = Math.min(state.page, pages);
  const start = (state.page - 1) * PAGE_SIZE;
  elements.resultCount.textContent = rows.length
    ? `Showing ${start + 1}-${Math.min(start + PAGE_SIZE, rows.length)} of ${rows.length.toLocaleString()} unique tweets`
    : 'No tweets match these filters.';
  elements.results.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    empty.textContent = 'No direct phrase evidence matches the current filters.';
    elements.results.append(empty);
  } else {
    for (const tweet of rows.slice(start, start + PAGE_SIZE))
      elements.results.append(renderTweet(tweet));
  }
  elements.pager.replaceChildren();
  const previous = document.createElement('button');
  previous.type = 'button';
  previous.className = 'button';
  previous.textContent = 'Previous';
  previous.disabled = state.page <= 1;
  previous.addEventListener('click', () => changePage(state.page - 1));
  const status = document.createElement('span');
  status.className = 'pager-status';
  status.textContent = `Page ${state.page} of ${pages}`;
  const next = document.createElement('button');
  next.type = 'button';
  next.className = 'button';
  next.textContent = 'Next';
  next.disabled = state.page >= pages;
  next.addEventListener('click', () => changePage(state.page + 1));
  elements.pager.append(previous, status, next);
}

function changePage(page) {
  state.page = page;
  render();
  document.querySelector('#results-title').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function weekStart(value) {
  const date = new Date(`${value}T00:00:00Z`);
  const daysSinceMonday = (date.getUTCDay() + 6) % 7;
  date.setUTCDate(date.getUTCDate() - daysSinceMonday);
  return date.toISOString().slice(0, 10);
}

function bucketForDate(value) {
  if (state.grain === 'month') return value.slice(0, 7);
  if (state.grain === 'week') return weekStart(value);
  return value.slice(0, 10);
}

function aggregateSeries(rows, phraseKey) {
  const counts = new Map();
  for (const tweet of rows) {
    if (
      !tweet.phrases.includes(phraseKey) ||
      (state.source && !hasEvidence(tweet, phraseKey, state.source))
    )
      continue;
    const bucket = bucketForDate(tweet.posted_at.slice(0, 10));
    counts.set(bucket, (counts.get(bucket) || 0) + 1);
  }
  return counts;
}

function bucketRange(rows) {
  const dates = rows.map((tweet) => tweet.posted_at.slice(0, 10)).sort();
  if (!dates.length) return [];
  const firstBucket = bucketForDate(dates[0]);
  const lastBucket = bucketForDate(dates.at(-1));
  const start = new Date(
    state.grain === 'month' ? `${firstBucket}-01T00:00:00Z` : `${firstBucket}T00:00:00Z`
  );
  const end = new Date(
    state.grain === 'month' ? `${lastBucket}-01T00:00:00Z` : `${lastBucket}T00:00:00Z`
  );
  const buckets = [];
  const cursor = new Date(start);
  while (cursor <= end) {
    buckets.push(cursor.toISOString().slice(0, state.grain === 'month' ? 7 : 10));
    if (state.grain === 'month') cursor.setUTCMonth(cursor.getUTCMonth() + 1);
    else cursor.setUTCDate(cursor.getUTCDate() + (state.grain === 'week' ? 7 : 1));
  }
  return buckets;
}

function chartColor(key) {
  const dark = matchMedia('(prefers-color-scheme: dark)').matches;
  const darkColors = {
    'criminal-alien': '#e47870',
    'illegal-alien': '#6fb4e1',
    'angel-mother': '#e0ac43',
    'angel-family': '#73bd8c',
  };
  return dark ? darkColors[key] : PHRASE_COLORS[key];
}

function drawChart(rows) {
  const canvas = elements.canvas;
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(rect.width * ratio));
  canvas.height = Math.max(1, Math.round(rect.height * ratio));
  const context = canvas.getContext('2d');
  context.scale(ratio, ratio);
  const width = rect.width;
  const height = rect.height;
  const margin = { top: 14, right: 16, bottom: 34, left: 48 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const buckets = bucketRange(rows);
  const phraseKeys = state.phrase ? [state.phrase] : report.phrases.map((phrase) => phrase.key);
  const series = phraseKeys.map((key) => ({ key, counts: aggregateSeries(rows, key) }));
  const populatedIndices = buckets.flatMap((bucket, index) =>
    series.some((item) => (item.counts.get(bucket) || 0) > 0) ? [index] : []
  );
  chartModel = {
    buckets,
    series,
    populatedIndices,
    margin,
    plotWidth,
    plotHeight,
    context,
    ratio,
    maxValue: 1,
  };
  const maxValue = Math.max(
    1,
    ...series.flatMap((item) => buckets.map((bucket) => item.counts.get(bucket) || 0))
  );
  chartModel.maxValue = maxValue;
  const textColor = getComputedStyle(document.documentElement).getPropertyValue('--muted').trim();
  const gridColor = getComputedStyle(document.documentElement).getPropertyValue('--border').trim();
  context.clearRect(0, 0, width, height);
  context.font = '11px system-ui, sans-serif';
  context.fillStyle = textColor;
  context.strokeStyle = gridColor;
  context.lineWidth = 1;
  for (let tick = 0; tick <= 4; tick += 1) {
    const y = margin.top + plotHeight - (tick / 4) * plotHeight;
    context.beginPath();
    context.moveTo(margin.left, y);
    context.lineTo(width - margin.right, y);
    context.stroke();
    const value = Math.round((tick / 4) * maxValue);
    context.fillText(value.toLocaleString(), 4, y + 4);
  }
  if (!buckets.length) {
    context.fillText('No data for current filters', margin.left + 12, margin.top + 24);
    return;
  }
  const labelEvery = Math.max(
    1,
    Math.ceil(buckets.length / Math.max(2, Math.floor(plotWidth / 72)))
  );
  buckets.forEach((bucket, index) => {
    if (index % labelEvery && index !== buckets.length - 1) return;
    const x =
      margin.left +
      (buckets.length === 1 ? plotWidth / 2 : (index / (buckets.length - 1)) * plotWidth);
    context.save();
    context.translate(x, height - 8);
    context.rotate(-0.4);
    context.textAlign = index === 0 ? 'left' : index === buckets.length - 1 ? 'right' : 'center';
    context.fillText(bucket, 0, 0);
    context.restore();
  });
  for (const item of series) {
    context.strokeStyle = chartColor(item.key);
    context.lineWidth = 2;
    context.beginPath();
    buckets.forEach((bucket, index) => {
      const x =
        margin.left +
        (buckets.length === 1 ? plotWidth / 2 : (index / (buckets.length - 1)) * plotWidth);
      const y = margin.top + plotHeight - ((item.counts.get(bucket) || 0) / maxValue) * plotHeight;
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.stroke();
  }
  chartModel.baseImage = context.getImageData(0, 0, canvas.width, canvas.height);
  restoreChartHighlight();
  const intervalLabel =
    state.grain === 'day'
      ? 'day'
      : state.grain === 'week'
        ? 'Monday-Sunday week'
        : 'calendar month';
  elements.caption.textContent = `Each point is one ${intervalLabel}${state.account ? ` for ${state.account}` : ' across the requested accounts'}. Empty positions resolve to the closest interval with hits; click to show its tweets.`;
  elements.legend.replaceChildren();
  for (const key of phraseKeys) {
    const item = document.createElement('span');
    item.className = `legend-item ${phraseClass(key)}`;
    item.innerHTML = '<span class="legend-swatch"></span><span></span>';
    item.lastElementChild.textContent = phraseLabel(key);
    elements.legend.append(item);
  }
}

function dateIndex(value) {
  const scopeStart = new Date(`${report.scope.earliest.slice(0, 10)}T00:00:00Z`);
  const date = new Date(`${value}T00:00:00Z`);
  return Math.round((date - scopeStart) / 86_400_000);
}

function dateAtIndex(index) {
  const date = new Date(`${report.scope.earliest.slice(0, 10)}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + index);
  return date.toISOString().slice(0, 10);
}

function syncRangeControls() {
  const start = state.from ? dateIndex(state.from) : 0;
  const end = state.to ? dateIndex(state.to) : rangeMax;
  elements.rangeStart.value = String(start);
  elements.rangeEnd.value = String(end);
  elements.rangeStartLabel.value = dateAtIndex(start);
  elements.rangeEndLabel.value = dateAtIndex(end);
  const startPercent = (start / rangeMax) * 100;
  const endPercent = (end / rangeMax) * 100;
  elements.rangeSelection.style.left = `${startPercent}%`;
  elements.rangeSelection.style.width = `${endPercent - startPercent}%`;
}

function updateRange(source) {
  let start = Number(elements.rangeStart.value);
  let end = Number(elements.rangeEnd.value);
  if (start > end) {
    if (source === 'start') {
      end = start;
      elements.rangeEnd.value = String(end);
    } else {
      start = end;
      elements.rangeStart.value = String(start);
    }
  }
  chartSelection = null;
  state.from = start === 0 ? '' : dateAtIndex(start);
  state.to = end === rangeMax ? '' : dateAtIndex(end);
  state.page = 1;
  render();
}

function configureRangeControls() {
  rangeMax = dateIndex(report.scope.latest.slice(0, 10));
  for (const input of [elements.rangeStart, elements.rangeEnd]) {
    input.max = String(rangeMax);
    input.step = '1';
  }
  elements.rangeStart.addEventListener('input', () => updateRange('start'));
  elements.rangeEnd.addEventListener('input', () => updateRange('end'));
  elements.rangeReset.addEventListener('click', () => {
    chartSelection = null;
    state.from = '';
    state.to = '';
    state.page = 1;
    render();
  });
}

function syncInputs() {
  elements.account.value = state.account;
  elements.source.value = state.source;
  elements.from.value = state.from;
  elements.to.value = state.to;
  elements.search.value = state.q;
  elements.sort.value = state.sort;
  elements.chartTitle.textContent =
    state.grain === 'day' ? 'Daily hits' : state.grain === 'week' ? 'Weekly hits' : 'Monthly hits';
  for (const button of elements.grain.querySelectorAll('button')) {
    button.setAttribute('aria-pressed', String(button.dataset.value === state.grain));
  }
  syncRangeControls();
}

function bucketDateRange(bucket) {
  let first;
  let last;
  if (state.grain === 'month') {
    const [year, month] = bucket.split('-').map(Number);
    first = `${bucket}-01`;
    last = new Date(Date.UTC(year, month, 0)).toISOString().slice(0, 10);
  } else if (state.grain === 'week') {
    first = bucket;
    const end = new Date(`${bucket}T00:00:00Z`);
    end.setUTCDate(end.getUTCDate() + 6);
    last = end.toISOString().slice(0, 10);
  } else {
    first = bucket;
    last = bucket;
  }
  const scopeFirst = state.from || report.scope.earliest.slice(0, 10);
  const scopeLast = state.to || report.scope.latest.slice(0, 10);
  return [first < scopeFirst ? scopeFirst : first, last > scopeLast ? scopeLast : last];
}

function chartXAtIndex(index) {
  const { buckets, margin, plotWidth } = chartModel;
  return (
    margin.left +
    (buckets.length === 1 ? plotWidth / 2 : (index / (buckets.length - 1)) * plotWidth)
  );
}

function restoreChartBase() {
  if (!chartModel?.baseImage) return;
  const { context, baseImage } = chartModel;
  context.save();
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.putImageData(baseImage, 0, 0);
  context.restore();
}

function highlightChartIndex(index) {
  restoreChartBase();
  if (index === null || !chartModel.populatedIndices.includes(index)) return;
  const { buckets, series, context, margin, plotHeight, maxValue } = chartModel;
  const bucket = buckets[index];
  const activeSeries = series.filter((item) => (item.counts.get(bucket) || 0) > 0);
  const barWidth = activeSeries.length === 1 ? 8 : 5;
  const spacing = activeSeries.length === 1 ? 0 : 6;
  const baseline = margin.top + plotHeight;
  const center = chartXAtIndex(index);
  context.save();
  activeSeries.forEach((item, seriesIndex) => {
    const value = item.counts.get(bucket) || 0;
    const top = margin.top + plotHeight - (value / maxValue) * plotHeight;
    const displayHeight = Math.max(baseline - top + 3, 18);
    const offset = (seriesIndex - (activeSeries.length - 1) / 2) * spacing;
    context.fillStyle = chartColor(item.key);
    context.fillRect(
      center + offset - barWidth / 2,
      baseline - displayHeight,
      barWidth,
      displayHeight
    );
  });
  context.restore();
}

function restoreChartHighlight() {
  if (!chartSelection || chartSelection.grain !== state.grain || !chartModel?.buckets.length) {
    restoreChartBase();
    return;
  }
  const index = chartModel.buckets.indexOf(chartSelection.bucket);
  highlightChartIndex(index === -1 ? null : index);
}

function nearestPopulatedIndex(index) {
  const populated = chartModel.populatedIndices;
  if (!populated.length) return null;
  let low = 0;
  let high = populated.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (populated[middle] < index) low = middle + 1;
    else high = middle;
  }
  const after = populated[low];
  const before = populated[low - 1];
  if (before === undefined) return after;
  if (after === undefined) return before;
  return index - before <= after - index ? before : after;
}

function chartIndexAtEvent(event, snapToData = false) {
  if (!chartModel?.buckets.length) return null;
  const rect = elements.canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const { buckets, margin, plotWidth, plotHeight } = chartModel;
  if (
    x < margin.left ||
    x > margin.left + plotWidth ||
    y < margin.top ||
    y > margin.top + plotHeight
  ) {
    return null;
  }
  const index = Math.max(
    0,
    Math.min(buckets.length - 1, Math.round(((x - margin.left) / plotWidth) * (buckets.length - 1)))
  );
  if (!snapToData) return index;
  return nearestPopulatedIndex(index);
}

function showChartTooltip(event) {
  const index = chartIndexAtEvent(event, true);
  if (index === null) {
    elements.chartTooltip.hidden = true;
    return;
  }
  const rect = elements.canvas.getBoundingClientRect();
  const { buckets, series } = chartModel;
  const x = chartXAtIndex(index);
  highlightChartIndex(index);
  const tooltip = elements.chartTooltip;
  tooltip.replaceChildren();
  const date = document.createElement('strong');
  const [first, last] = bucketDateRange(buckets[index]);
  date.textContent = first === last ? first : `${first} to ${last}`;
  tooltip.append(date);
  for (const item of series) {
    const line = document.createElement('span');
    line.className = `tooltip-line ${phraseClass(item.key)}`;
    line.innerHTML = '<i></i><span></span><b></b>';
    line.querySelector('span').textContent = phraseLabel(item.key);
    line.querySelector('b').textContent = (item.counts.get(buckets[index]) || 0).toLocaleString();
    tooltip.append(line);
  }
  tooltip.style.left = `${Math.max(92, Math.min(rect.width - 92, x))}px`;
  tooltip.hidden = false;
}

function selectChartDate(event) {
  const index = chartIndexAtEvent(event, true);
  if (index === null) return;
  chartSelection = { grain: state.grain, bucket: chartModel.buckets[index] };
  const [first, last] = bucketDateRange(chartModel.buckets[index]);
  state.from = first;
  state.to = last;
  state.page = 1;
  render();
  requestAnimationFrame(() => {
    document.querySelector('#results-title').scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}

function render() {
  syncInputs();
  renderPhraseControls();
  const baseRows = baseFilteredTweets();
  const rows = filteredTweets();
  renderSummary(baseRows);
  elements.filteredTotal.textContent = rows.length.toLocaleString();
  renderResults(rows);
  drawChart(baseRows);
  writeHash();
}

function bindControls() {
  const bindings = [
    [elements.account, 'account', 'change'],
    [elements.source, 'source', 'change'],
    [elements.from, 'from', 'change'],
    [elements.to, 'to', 'change'],
    [elements.search, 'q', 'input'],
    [elements.sort, 'sort', 'change'],
  ];
  for (const [element, key, event] of bindings) {
    element.addEventListener(event, () => {
      chartSelection = null;
      state[key] = element.value;
      state.page = 1;
      render();
    });
  }
  elements.canvas.addEventListener('pointermove', showChartTooltip);
  elements.canvas.addEventListener('pointerdown', showChartTooltip);
  elements.canvas.addEventListener('click', selectChartDate);
  elements.grain.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-value]');
    if (!button) return;
    chartSelection = null;
    state.grain = button.dataset.value;
    state.page = 1;
    render();
  });
  elements.canvas.addEventListener('pointerleave', () => {
    elements.chartTooltip.hidden = true;
    restoreChartHighlight();
  });
  elements.reset.addEventListener('click', () => {
    chartSelection = null;
    Object.assign(state, {
      phrase: '',
      account: '',
      source: '',
      from: '',
      to: '',
      q: '',
      grain: 'day',
      sort: 'desc',
      page: 1,
    });
    render();
  });
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => drawChart(baseFilteredTweets()), 120);
  });
}

async function start() {
  readHash();
  const response = await fetch('data.json');
  if (!response.ok) throw new Error(`Unable to load report data (${response.status})`);
  report = await response.json();
  elements.scope.textContent = `${report.scope.earliest.slice(0, 10)} to ${report.scope.latest.slice(0, 10)} | ${report.coverage.catalog_tweets.toLocaleString()} posts searched | text, OCR, and transcripts`;
  configureRangeControls();
  bindControls();
  render();
}

start().catch((error) => {
  elements.scope.textContent = 'Report data failed to load.';
  elements.results.textContent = error.message;
});
