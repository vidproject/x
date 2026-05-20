import { tagNames, tagNamespace } from './store.js';

export const CHART_JS_URL = 'https://esm.sh/chart.js@4.5.0/auto?bundle';

export const CHART_ELEMENT_OPTIONS = [
  { value: 'tweets', label: 'Tweets' },
  { value: 'media', label: 'Media' },
  { value: 'tags', label: 'Tags' },
  { value: 'hashtags', label: 'Hashtags' },
  { value: 'mentions', label: 'Mentions' },
];

export const CHART_DIMENSION_OPTIONS = [
  { value: 'account', label: 'Account' },
  { value: 'category', label: 'Category' },
  { value: 'tweet_type', label: 'Tweet type' },
  { value: 'media_kind', label: 'Media kind' },
  { value: 'tag', label: 'Tag' },
  { value: 'tag_namespace', label: 'Tag namespace' },
  { value: 'posted_day', label: 'Posted day' },
  { value: 'posted_month', label: 'Posted month' },
  { value: 'lang', label: 'Language' },
  { value: 'deleted', label: 'Deleted' },
];

export const CHART_TYPE_OPTIONS = [
  { value: 'auto', label: 'Auto' },
  { value: 'bar', label: 'Bar' },
  { value: 'horizontal_bar', label: 'Horizontal bar' },
  { value: 'line', label: 'Line' },
  { value: 'doughnut', label: 'Doughnut' },
];

export const CHART_SCOPE_OPTIONS = [
  { value: 'filtered', label: 'Filtered rows' },
  { value: 'all', label: 'All loaded rows' },
];

export const CHART_SORT_OPTIONS = [
  { value: 'auto', label: 'Auto' },
  { value: 'count_desc', label: 'Count, high to low' },
  { value: 'count_asc', label: 'Count, low to high' },
  { value: 'label_asc', label: 'Label, A to Z' },
  { value: 'label_desc', label: 'Label, Z to A' },
];

const DEFAULTS = {
  element: 'tweets',
  dimension: 'category',
  chartType: 'auto',
  scope: 'filtered',
  limit: 20,
  minCount: 1,
  sort: 'auto',
  includeEmpty: false,
};

const CONTROL_DEFS = {
  element: {
    label: 'Element',
    type: 'select',
    options: CHART_ELEMENT_OPTIONS,
    aliases: ['element', 'elementSelect', 'elementControl', 'chartElement'],
  },
  dimension: {
    label: 'Dimension',
    type: 'select',
    options: CHART_DIMENSION_OPTIONS,
    aliases: ['dimension', 'dimensionSelect', 'dimensionControl', 'chartDimension'],
  },
  chartType: {
    label: 'Chart',
    type: 'select',
    options: CHART_TYPE_OPTIONS,
    aliases: ['chartType', 'type', 'typeSelect', 'chartTypeSelect'],
  },
  scope: {
    label: 'Rows',
    type: 'select',
    options: CHART_SCOPE_OPTIONS,
    aliases: ['scope', 'scopeSelect', 'rowScope'],
  },
  limit: {
    label: 'Top',
    type: 'number',
    min: 0,
    max: 500,
    aliases: ['limit', 'limitInput', 'topN', 'topNInput'],
  },
  minCount: {
    label: 'Min',
    type: 'number',
    min: 1,
    max: 1000000,
    aliases: ['minCount', 'minCountInput'],
  },
  sort: {
    label: 'Sort',
    type: 'select',
    options: CHART_SORT_OPTIONS,
    aliases: ['sort', 'sortSelect'],
  },
  includeEmpty: {
    label: 'Include empty buckets',
    type: 'checkbox',
    aliases: ['includeEmpty', 'includeEmptyInput', 'includeEmptyCheckbox'],
  },
};

const ELEMENT_LABELS = Object.fromEntries(
  CHART_ELEMENT_OPTIONS.map((opt) => [opt.value, opt.label])
);
const DIMENSION_LABELS = Object.fromEntries(
  CHART_DIMENSION_OPTIONS.map((opt) => [opt.value, opt.label])
);
const VALID_ELEMENTS = new Set(CHART_ELEMENT_OPTIONS.map((opt) => opt.value));
const VALID_DIMENSIONS = new Set(CHART_DIMENSION_OPTIONS.map((opt) => opt.value));
const VALID_CHART_TYPES = new Set(CHART_TYPE_OPTIONS.map((opt) => opt.value));
const VALID_SCOPES = new Set(CHART_SCOPE_OPTIONS.map((opt) => opt.value));
const VALID_SORTS = new Set(CHART_SORT_OPTIONS.map((opt) => opt.value));

const PALETTE = [
  '#2563eb',
  '#dc2626',
  '#16a34a',
  '#f59e0b',
  '#7c3aed',
  '#0891b2',
  '#be123c',
  '#4b5563',
  '#65a30d',
  '#ea580c',
  '#0f766e',
  '#9333ea',
];

const state = {
  button: null,
  panel: null,
  closeBtn: null,
  canvas: null,
  statusEl: null,
  summaryEl: null,
  controls: {},
  getRows: () => [],
  getAllRows: null,
  categoryOf: null,
  chart: null,
  chartPromise: null,
  renderTimer: 0,
  renderSeq: 0,
  listeners: [],
};

export function initChartsPanel(options = {}) {
  detachListeners();
  applyBindings(options);
  ensurePanelScaffold(options);
  populateControls();
  attachListeners();

  if (isPanelOpen()) scheduleRender(0);

  return {
    update: updateChartsPanel,
    destroy: destroyChartsPanel,
    buildData: () => buildChartData(currentRows(), currentOptions()),
  };
}

export function updateChartsPanel(options = {}) {
  applyBindings(options);
  ensurePanelScaffold(options);
  populateControls();

  if (options.immediate || options.force) {
    return renderChartsPanel({ force: Boolean(options.force) });
  }

  scheduleRender();
  return undefined;
}

export function destroyChartsPanel() {
  clearPendingRender();
  detachListeners();
  destroyChart();
}

export function buildChartData(rows, options = {}) {
  const opts = normalizeOptions(options);
  const counts = new Map();
  let rowCount = 0;
  let elementCount = 0;

  for (const row of toArray(rows)) {
    rowCount += 1;
    for (const entry of elementEntries(row, opts.element)) {
      const values = dimensionValues(entry, opts);
      if (values.length === 0) continue;
      elementCount += 1;
      for (const value of values) {
        counts.set(value, (counts.get(value) || 0) + 1);
      }
    }
  }

  const minCount = Math.max(1, opts.minCount);
  let pairs = [...counts.entries()]
    .filter(([, count]) => count >= minCount)
    .map(([label, count]) => ({ label, count }));

  pairs = sortPairs(pairs, resolveSort(opts));
  if (opts.limit > 0) pairs = pairs.slice(0, opts.limit);

  return {
    labels: pairs.map((pair) => pair.label),
    values: pairs.map((pair) => pair.count),
    pairs,
    rowCount,
    elementCount,
    totalCount: pairs.reduce((sum, pair) => sum + pair.count, 0),
    element: opts.element,
    dimension: opts.dimension,
  };
}

function applyBindings(options) {
  if (!options || typeof options !== 'object') return;

  assignElement('button', options.button);
  assignElement('panel', options.panel);
  assignElement('closeBtn', options.closeBtn ?? options.closeButton);
  assignElement('canvas', options.canvas);
  assignElement(
    'statusEl',
    options.status ?? options.statusEl ?? options.message ?? options.messageEl
  );
  assignElement('summaryEl', options.summary ?? options.summaryEl);

  const controls = options.controls && typeof options.controls === 'object' ? options.controls : {};
  for (const [name, def] of Object.entries(CONTROL_DEFS)) {
    const control = findAliasedValue(options, controls, def.aliases);
    if (control !== undefined) state.controls[name] = control;
  }

  if (typeof options.getRows === 'function') state.getRows = options.getRows;
  else if (Array.isArray(options.rows)) state.getRows = () => options.rows;

  if (typeof options.getAllRows === 'function') state.getAllRows = options.getAllRows;
  else if (Array.isArray(options.allRows)) state.getAllRows = () => options.allRows;

  if (typeof options.categoryOf === 'function') state.categoryOf = options.categoryOf;
}

function assignElement(name, value) {
  if (value === undefined) return;
  state[name] = resolveDomElement(value);
}

function findAliasedValue(options, controls, aliases) {
  for (const alias of aliases) {
    if (Object.prototype.hasOwnProperty.call(controls, alias)) return controls[alias];
    if (Object.prototype.hasOwnProperty.call(options, alias)) return options[alias];
  }
  return undefined;
}

function resolveDomElement(value) {
  if (!value || typeof value !== 'string') return value || null;
  return document.getElementById(value) || document.querySelector(value);
}

function ensurePanelScaffold(options) {
  const panel = state.panel;
  if (!panel || options.createControls === false) return;

  state.closeBtn =
    state.closeBtn ||
    panel.querySelector('[data-chart-close]') ||
    panel.querySelector('[data-charts-close]');
  state.statusEl =
    state.statusEl ||
    panel.querySelector('[data-chart-status]') ||
    panel.querySelector('[data-charts-status]');
  state.summaryEl =
    state.summaryEl ||
    panel.querySelector('[data-chart-summary]') ||
    panel.querySelector('[data-charts-summary]');
  state.canvas =
    state.canvas ||
    panel.querySelector('[data-chart-canvas]') ||
    panel.querySelector('[data-charts-canvas]') ||
    panel.querySelector('canvas');

  for (const name of Object.keys(CONTROL_DEFS)) {
    state.controls[name] =
      state.controls[name] ||
      panel.querySelector(`[data-chart-control="${name}"]`) ||
      panel.querySelector(`[data-charts-control="${name}"]`);
  }

  const missingControlNames = Object.keys(CONTROL_DEFS).filter((name) => !state.controls[name]);
  if (missingControlNames.length > 0) {
    const controlsWrap =
      panel.querySelector('[data-chart-controls]') || document.createElement('div');
    controlsWrap.dataset.chartControls = 'true';
    controlsWrap.className = controlsWrap.className || 'charts-controls';
    if (!controlsWrap.parentNode) panel.append(controlsWrap);

    for (const name of missingControlNames) {
      const control = createControl(name, CONTROL_DEFS[name]);
      controlsWrap.append(control.wrap);
      state.controls[name] = control.input;
    }
  }

  if (!state.statusEl) {
    state.statusEl = document.createElement('div');
    state.statusEl.dataset.chartStatus = 'true';
    state.statusEl.className = 'charts-status';
    state.statusEl.hidden = true;
    panel.append(state.statusEl);
  }

  if (!state.summaryEl) {
    state.summaryEl = document.createElement('div');
    state.summaryEl.dataset.chartSummary = 'true';
    state.summaryEl.className = 'charts-summary';
    panel.append(state.summaryEl);
  }

  if (!state.canvas) {
    state.canvas = document.createElement('canvas');
    state.canvas.dataset.chartCanvas = 'true';
    panel.append(state.canvas);
  }

  if (!state.canvas.getAttribute('height')) state.canvas.setAttribute('height', '320');
}

function createControl(name, def) {
  const wrap = document.createElement('label');
  wrap.className = `charts-control charts-control-${name}`;

  const text = document.createElement('span');
  text.textContent = def.label;
  wrap.append(text);

  let input;
  if (def.type === 'select') {
    input = document.createElement('select');
    input.className = 'select';
    appendOptions(input, def.options);
  } else {
    input = document.createElement('input');
    input.type = def.type;
    if (def.type === 'number') {
      input.min = String(def.min ?? 0);
      input.max = String(def.max ?? 1000000);
      input.step = '1';
      input.className = 'select';
    }
  }

  input.dataset.chartControl = name;
  setControlValue(input, DEFAULTS[name], name);
  wrap.append(input);
  return { wrap, input };
}

function populateControls() {
  for (const [name, def] of Object.entries(CONTROL_DEFS)) {
    const control = state.controls[name];
    if (!isDomControl(control)) continue;

    if (def.type === 'select' && control.options && control.options.length === 0) {
      appendOptions(control, def.options);
    }

    if (control.value === '' && DEFAULTS[name] !== undefined) {
      setControlValue(control, DEFAULTS[name], name);
    }
  }
}

function appendOptions(select, options) {
  for (const opt of options) {
    const option = document.createElement('option');
    option.value = opt.value;
    option.textContent = opt.label;
    select.append(option);
  }
}

function setControlValue(control, value, name) {
  if (!isDomControl(control)) return;
  if (name === 'includeEmpty' && 'checked' in control) control.checked = Boolean(value);
  else if ('value' in control) control.value = String(value);
}

function attachListeners() {
  listen(state.button, 'click', () => {
    setPanelOpen(!isPanelOpen());
  });
  listen(state.closeBtn, 'click', () => {
    setPanelOpen(false);
  });

  for (const [name, control] of Object.entries(state.controls)) {
    if (!isDomControl(control)) continue;
    const eventName = name === 'limit' || name === 'minCount' ? 'input' : 'change';
    listen(control, eventName, () => scheduleRender());
  }
}

function listen(target, eventName, handler) {
  if (!target || typeof target.addEventListener !== 'function') return;
  target.addEventListener(eventName, handler);
  state.listeners.push(() => target.removeEventListener(eventName, handler));
}

function detachListeners() {
  for (const remove of state.listeners.splice(0)) remove();
}

function isDomControl(value) {
  return value && typeof value === 'object' && 'nodeType' in value;
}

function setPanelOpen(open) {
  if (state.panel) {
    state.panel.hidden = !open;
    state.panel.setAttribute('aria-hidden', open ? 'false' : 'true');
  }
  if (state.button) {
    state.button.setAttribute('aria-pressed', open ? 'true' : 'false');
  }

  if (open) scheduleRender(0);
  else destroyChart();
}

function isPanelOpen() {
  if (!state.panel) return Boolean(state.canvas);
  return !state.panel.hidden && state.panel.getAttribute('aria-hidden') !== 'true';
}

function scheduleRender(delay = 120) {
  clearPendingRender();
  state.renderTimer = setTimeout(() => {
    state.renderTimer = 0;
    renderChartsPanel();
  }, delay);
}

function clearPendingRender() {
  if (!state.renderTimer) return;
  clearTimeout(state.renderTimer);
  state.renderTimer = 0;
}

async function renderChartsPanel({ force = false } = {}) {
  clearPendingRender();
  const seq = ++state.renderSeq;

  if (!force && !isPanelOpen()) return;
  if (!state.canvas || typeof state.canvas.getContext !== 'function') {
    setStatus('error', 'Charts panel is missing a canvas element.');
    destroyChart();
    return;
  }

  const opts = currentOptions();
  const rows = currentRows(opts);
  const model = buildChartData(rows, opts);
  setSummary(summaryText(model, opts));

  if (model.labels.length === 0) {
    destroyChart();
    setStatus('empty', 'No chartable values for the selected element and dimension.');
    return;
  }

  let Chart;
  try {
    setStatus('loading', 'Loading Chart.js...');
    Chart = await loadChartConstructor();
  } catch (err) {
    if (seq !== state.renderSeq) return;
    destroyChart();
    setStatus('error', `Unable to load Chart.js from ${CHART_JS_URL}. ${errorMessage(err)}`);
    return;
  }

  if (seq !== state.renderSeq || (!force && !isPanelOpen())) return;

  const context = state.canvas.getContext('2d');
  destroyChart();
  state.chart = new Chart(context, chartConfig(model, opts));
  setStatus('', '');
}

function currentOptions() {
  return normalizeOptions({
    element: readControl('element', DEFAULTS.element),
    dimension: readControl('dimension', DEFAULTS.dimension),
    chartType: readControl('chartType', DEFAULTS.chartType),
    scope: readControl('scope', DEFAULTS.scope),
    limit: readControl('limit', DEFAULTS.limit),
    minCount: readControl('minCount', DEFAULTS.minCount),
    sort: readControl('sort', DEFAULTS.sort),
    includeEmpty: readControl('includeEmpty', DEFAULTS.includeEmpty),
    categoryOf: state.categoryOf,
  });
}

function normalizeOptions(options) {
  const element = validOption(options.element, VALID_ELEMENTS, DEFAULTS.element);
  const dimension = validOption(options.dimension, VALID_DIMENSIONS, DEFAULTS.dimension);
  const chartType = validOption(options.chartType, VALID_CHART_TYPES, DEFAULTS.chartType);
  const scope = validOption(options.scope, VALID_SCOPES, DEFAULTS.scope);
  const sort = validOption(options.sort, VALID_SORTS, DEFAULTS.sort);
  const limit = normalizeNumber(options.limit, DEFAULTS.limit, 0, 500);
  const minCount = normalizeNumber(options.minCount, DEFAULTS.minCount, 1, 1000000);
  const includeEmpty = normalizeBoolean(options.includeEmpty);

  return {
    element,
    dimension,
    chartType,
    scope,
    sort,
    limit,
    minCount,
    includeEmpty,
    categoryOf: typeof options.categoryOf === 'function' ? options.categoryOf : state.categoryOf,
  };
}

function validOption(value, valid, fallback) {
  const text = String(value ?? '');
  return valid.has(text) ? text : fallback;
}

function normalizeNumber(value, fallback, min, max) {
  const num = Number(value);
  if (!Number.isFinite(num)) return fallback;
  return Math.min(max, Math.max(min, Math.floor(num)));
}

function normalizeBoolean(value) {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'string') return value === 'true' || value === '1' || value === 'on';
  return Boolean(value);
}

function readControl(name, fallback) {
  const control = state.controls[name];
  if (!isDomControl(control)) return control ?? fallback;
  if (name === 'includeEmpty' && 'checked' in control) return control.checked;
  if ('value' in control) return control.value;
  return fallback;
}

function currentRows(opts = currentOptions()) {
  if (opts.scope === 'all' && typeof state.getAllRows === 'function')
    return toArray(state.getAllRows());
  if (typeof state.getRows === 'function') return toArray(state.getRows());
  return [];
}

function toArray(value) {
  if (Array.isArray(value)) return value;
  if (value && typeof value[Symbol.iterator] === 'function') return [...value];
  return [];
}

async function loadChartConstructor() {
  if (!state.chartPromise) {
    state.chartPromise = import(CHART_JS_URL)
      .then((mod) => {
        const Chart = mod.default || mod.Chart;
        if (!Chart) throw new Error('Chart constructor was not exported.');
        return Chart;
      })
      .catch((err) => {
        state.chartPromise = null;
        throw err;
      });
  }
  return state.chartPromise;
}

function destroyChart() {
  if (!state.chart) return;
  state.chart.destroy();
  state.chart = null;
}

function elementEntries(row, element) {
  if (element === 'tweets') return [{ row, value: row }];

  if (element === 'media') {
    return toArray(row?.media)
      .filter((media) => media && typeof media === 'object')
      .map((media) => ({ row, media, value: media }));
  }

  if (element === 'tags') {
    return tagNames(row).map((tag) => ({ row, tag, value: tag }));
  }

  if (element === 'hashtags') {
    return stringList(row?.hashtags, ['text', 'tag', 'hashtag'])
      .map(normalizeHashtag)
      .map((hashtag) => ({ row, hashtag, value: hashtag }));
  }

  if (element === 'mentions') {
    return stringList(row?.mentions, ['username', 'screen_name', 'handle', 'account_handle'])
      .map(normalizeMention)
      .map((mention) => ({ row, mention, value: mention }));
  }

  return [];
}

function dimensionValues(entry, opts) {
  const row = entry.row || {};

  if (opts.dimension === 'account')
    return [nonEmpty(formatHandle(row.account_handle), '(unknown account)')];
  if (opts.dimension === 'category') return [categoryLabel(row, opts.categoryOf)];
  if (opts.dimension === 'tweet_type') return [nonEmpty(row.tweet_type, 'original')];
  if (opts.dimension === 'media_kind') return mediaKindValues(entry);
  if (opts.dimension === 'posted_day') return [dateBucket(row.posted_at, 'day')];
  if (opts.dimension === 'posted_month') return [dateBucket(row.posted_at, 'month')];
  if (opts.dimension === 'lang') return [nonEmpty(row.lang, '(unknown language)')];
  if (opts.dimension === 'deleted') return [deletedBucket(row)];

  if (opts.dimension === 'tag') {
    const tags = entry.tag ? [entry.tag] : tagNames(row);
    return emptyAware(tags, opts.includeEmpty, '(no tags)');
  }

  if (opts.dimension === 'tag_namespace') {
    const namespaces = uniqueStrings((entry.tag ? [entry.tag] : tagNames(row)).map(tagNamespace));
    return emptyAware(namespaces, opts.includeEmpty, '(no tag namespace)');
  }

  return [];
}

function mediaKindValues(entry) {
  if (entry.media) return [mediaKind(entry.media)];

  const kinds = uniqueStrings(
    toArray(entry.row?.media)
      .filter((media) => media && typeof media === 'object')
      .map(mediaKind)
  );
  return kinds.length > 0 ? kinds : ['text only'];
}

function categoryLabel(row, categoryOf) {
  if (typeof categoryOf === 'function') {
    try {
      const category = categoryOf(row);
      if (category) return String(category);
    } catch (err) {
      console.warn('charts categoryOf failed', err);
    }
  }
  return nonEmpty(row.account_category ?? row.category, '(uncategorized)');
}

function deletedBucket(row) {
  if (hasValue(row.deletion_detected_at)) return 'deleted';
  if (hasValue(row.unavailable_detected_at)) return 'unavailable';
  return 'available';
}

function dateBucket(value, granularity) {
  const text = String(value ?? '');
  const match = text.match(/^(\d{4}-\d{2})(?:-(\d{2}))?/);
  if (!match) return '(no posted date)';
  return granularity === 'month' ? match[1] : `${match[1]}-${match[2] || '01'}`;
}

function mediaKind(media) {
  return nonEmpty(media?.media_type ?? media?.type ?? media?.kind, 'unknown media');
}

function formatHandle(value) {
  const text = String(value ?? '').trim();
  if (!text) return '';
  return text.startsWith('@') ? text : `@${text}`;
}

function normalizeHashtag(value) {
  const text = String(value ?? '').trim();
  if (!text) return '';
  return text.startsWith('#') ? text : `#${text}`;
}

function normalizeMention(value) {
  const text = String(value ?? '').trim();
  if (!text) return '';
  return text.startsWith('@') ? text : `@${text}`;
}

function stringList(value, objectKeys = []) {
  const out = [];
  for (const item of toArray(value)) {
    if (typeof item === 'string' || typeof item === 'number') {
      out.push(String(item));
      continue;
    }
    if (!item || typeof item !== 'object') continue;
    for (const key of objectKeys) {
      if (hasValue(item[key])) {
        out.push(String(item[key]));
        break;
      }
    }
  }
  return uniqueStrings(out.map((text) => text.trim()).filter(Boolean));
}

function uniqueStrings(values) {
  const seen = new Set();
  const out = [];
  for (const value of values) {
    const text = String(value ?? '').trim();
    if (!text || seen.has(text)) continue;
    seen.add(text);
    out.push(text);
  }
  return out;
}

function emptyAware(values, includeEmpty, emptyLabel) {
  const clean = uniqueStrings(values);
  if (clean.length > 0) return clean;
  return includeEmpty ? [emptyLabel] : [];
}

function nonEmpty(value, fallback) {
  const text = String(value ?? '').trim();
  return text || fallback;
}

function hasValue(value) {
  return value !== undefined && value !== null && String(value).trim() !== '';
}

function sortPairs(pairs, sort) {
  const sorted = pairs.slice();
  sorted.sort((a, b) => {
    if (sort === 'count_asc') return a.count - b.count || a.label.localeCompare(b.label);
    if (sort === 'label_asc') return a.label.localeCompare(b.label) || b.count - a.count;
    if (sort === 'label_desc') return b.label.localeCompare(a.label) || b.count - a.count;
    return b.count - a.count || a.label.localeCompare(b.label);
  });
  return sorted;
}

function resolveSort(opts) {
  if (opts.sort !== 'auto') return opts.sort;
  if (opts.dimension === 'posted_day' || opts.dimension === 'posted_month') return 'label_asc';
  return 'count_desc';
}

function chartConfig(model, opts) {
  const chartType = resolveChartType(opts, model);
  const isDoughnut = chartType.type === 'doughnut';
  const isLine = chartType.type === 'line';
  const colors = model.labels.map((_, idx) => PALETTE[idx % PALETTE.length]);

  return {
    type: chartType.type,
    data: {
      labels: model.labels,
      datasets: [
        {
          label: `${ELEMENT_LABELS[opts.element]} by ${DIMENSION_LABELS[opts.dimension]}`,
          data: model.values,
          backgroundColor: isLine ? PALETTE[0] : colors,
          borderColor: isLine ? PALETTE[0] : colors,
          borderWidth: isLine ? 2 : 1,
          fill: false,
          tension: isLine ? 0.2 : 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      indexAxis: chartType.indexAxis,
      plugins: {
        legend: {
          display: isDoughnut,
          position: 'bottom',
        },
        title: {
          display: true,
          text: `${ELEMENT_LABELS[opts.element]} by ${DIMENSION_LABELS[opts.dimension]}`,
        },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.label}: ${ctx.parsed.y ?? ctx.parsed.x ?? ctx.parsed}`,
          },
        },
      },
      scales: isDoughnut
        ? {}
        : {
            x: {
              beginAtZero: true,
              ticks: {
                precision: 0,
              },
            },
            y: {
              beginAtZero: true,
              ticks: {
                precision: 0,
              },
            },
          },
    },
  };
}

function resolveChartType(opts, model) {
  if (opts.chartType === 'line') return { type: 'line' };
  if (opts.chartType === 'doughnut') return { type: 'doughnut' };
  if (opts.chartType === 'horizontal_bar') return { type: 'bar', indexAxis: 'y' };
  if (opts.chartType === 'bar') return { type: 'bar' };

  if (opts.dimension === 'posted_day' || opts.dimension === 'posted_month') return { type: 'line' };
  if (model.labels.length > 10) return { type: 'bar', indexAxis: 'y' };
  return { type: 'bar' };
}

function summaryText(model, opts) {
  const rows = formatNumber(model.rowCount);
  const elements = formatNumber(model.elementCount);
  const buckets = formatNumber(model.labels.length);
  const elementLabel = ELEMENT_LABELS[opts.element].toLowerCase();
  const dimensionLabel = DIMENSION_LABELS[opts.dimension].toLowerCase();
  return `${elements} ${elementLabel} across ${buckets} ${dimensionLabel} buckets from ${rows} rows.`;
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString();
}

function setStatus(kind, message) {
  if (!state.statusEl) return;
  state.statusEl.hidden = !message;
  state.statusEl.textContent = message;
  if (kind) state.statusEl.dataset.status = kind;
  else delete state.statusEl.dataset.status;
}

function setSummary(message) {
  if (!state.summaryEl) return;
  state.summaryEl.textContent = message;
}

function errorMessage(err) {
  const message = err && err.message ? String(err.message) : String(err ?? '');
  return message ? `Error: ${message}` : 'Check the network connection and try again.';
}
