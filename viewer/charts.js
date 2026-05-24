import { tagNames, tagNamespace } from './store.js?v=lazycat8';

export const CHART_JS_URL = 'https://esm.sh/chart.js@4.5.0/auto?bundle';

export const CHART_VIEW_OPTIONS = [
  { value: 'time_series', label: 'Time series' },
  { value: 'breakdown', label: 'Breakdown' },
  { value: 'crosstab', label: 'Tag comparison' },
];

export const CHART_ELEMENT_OPTIONS = [
  { value: 'tweets', label: 'Tweet count' },
  { value: 'replies', label: 'Reply count' },
  { value: 'media', label: 'Media count' },
  { value: 'tags', label: 'Tag count' },
  { value: 'categories', label: 'Category count' },
  { value: 'hashtags', label: 'Hashtag count' },
  { value: 'mentions', label: 'Mention count' },
];

export const CHART_DIMENSION_OPTIONS = [
  { value: 'posted_time', label: 'Posted time' },
  { value: 'account', label: 'Account' },
  { value: 'category', label: 'Category' },
  { value: 'tag', label: 'Tag' },
  { value: 'tag_namespace', label: 'Tag namespace' },
  { value: 'tweet_type', label: 'Tweet type' },
  { value: 'media_kind', label: 'Media kind' },
  { value: 'posted_day', label: 'Posted day' },
  { value: 'posted_week', label: 'Posted week' },
  { value: 'posted_month', label: 'Posted month' },
  { value: 'posted_year', label: 'Posted year' },
  { value: 'lang', label: 'Language' },
  { value: 'deleted', label: 'Deleted' },
  { value: 'news_coverage', label: 'News coverage' },
];

export const CHART_SERIES_OPTIONS = [
  { value: 'none', label: 'Single series' },
  { value: 'account', label: 'Account' },
  { value: 'category', label: 'Category' },
  { value: 'tag', label: 'Tag' },
  { value: 'tag_namespace', label: 'Tag namespace' },
  { value: 'tweet_type', label: 'Tweet type' },
  { value: 'media_kind', label: 'Media kind' },
  { value: 'news_coverage', label: 'News coverage' },
];

export const CHART_TIME_OPTIONS = [
  { value: 'day', label: 'Day' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
  { value: 'year', label: 'Year' },
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
  { value: 'all', label: 'All catalog rows' },
];

export const CHART_SORT_OPTIONS = [
  { value: 'auto', label: 'Auto' },
  { value: 'count_desc', label: 'Count, high to low' },
  { value: 'count_asc', label: 'Count, low to high' },
  { value: 'label_asc', label: 'Label, A to Z' },
  { value: 'label_desc', label: 'Label, Z to A' },
];

const DEFAULTS = {
  view: 'time_series',
  element: 'tweets',
  dimension: 'category',
  series: 'none',
  granularity: 'month',
  chartType: 'auto',
  scope: 'filtered',
  account: '',
  from: '',
  to: '',
  focusTag: '',
  limit: 20,
  minCount: 1,
  sort: 'auto',
  includeEmpty: false,
};

const CONTROL_DEFS = {
  view: {
    label: 'View',
    type: 'select',
    options: CHART_VIEW_OPTIONS,
    aliases: ['view', 'viewSelect', 'chartView'],
  },
  element: {
    label: 'Metric',
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
  series: {
    label: 'Compare',
    type: 'select',
    options: CHART_SERIES_OPTIONS,
    aliases: ['series', 'seriesSelect', 'compare', 'compareSelect'],
  },
  granularity: {
    label: 'Group time by',
    type: 'select',
    options: CHART_TIME_OPTIONS,
    aliases: ['granularity', 'timeGranularity', 'groupByTime'],
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
  account: {
    label: 'Account',
    type: 'select',
    dynamic: 'accounts',
    aliases: ['account', 'accountSelect', 'accountFilter', 'user', 'userSelect'],
  },
  from: {
    label: 'From',
    type: 'date',
    aliases: ['from', 'fromDate', 'dateFrom'],
  },
  to: {
    label: 'To',
    type: 'date',
    aliases: ['to', 'toDate', 'dateTo'],
  },
  focusTag: {
    label: 'Tag/topic',
    type: 'text',
    dynamic: 'tags',
    aliases: ['focusTag', 'tag', 'tagFilter', 'topic', 'topicFilter'],
  },
  limit: {
    label: 'Top N',
    type: 'number',
    min: 0,
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

const VIEW_LABELS = Object.fromEntries(CHART_VIEW_OPTIONS.map((opt) => [opt.value, opt.label]));
const ELEMENT_LABELS = Object.fromEntries(
  CHART_ELEMENT_OPTIONS.map((opt) => [opt.value, opt.label])
);
const DIMENSION_LABELS = Object.fromEntries(
  CHART_DIMENSION_OPTIONS.map((opt) => [opt.value, opt.label])
);
const SERIES_LABELS = Object.fromEntries(CHART_SERIES_OPTIONS.map((opt) => [opt.value, opt.label]));
const VALID_ELEMENTS = new Set(CHART_ELEMENT_OPTIONS.map((opt) => opt.value));
const VALID_DIMENSIONS = new Set(CHART_DIMENSION_OPTIONS.map((opt) => opt.value));
const VALID_VIEWS = new Set(CHART_VIEW_OPTIONS.map((opt) => opt.value));
const VALID_SERIES = new Set(CHART_SERIES_OPTIONS.map((opt) => opt.value));
const VALID_GRANULARITIES = new Set(CHART_TIME_OPTIONS.map((opt) => opt.value));
const VALID_CHART_TYPES = new Set(CHART_TYPE_OPTIONS.map((opt) => opt.value));
const VALID_SCOPES = new Set(CHART_SCOPE_OPTIONS.map((opt) => opt.value));
const VALID_SORTS = new Set(CHART_SORT_OPTIONS.map((opt) => opt.value));
const NO_LIMIT = 0;
const MAX_TOP_N = Number.MAX_SAFE_INTEGER;
const RENDER_POINT_WARNING = 25000;

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
  tableEl: null,
  controls: {},
  getRows: () => [],
  getAllRows: null,
  categoryOf: null,
  chart: null,
  chartPromise: null,
  renderTimer: 0,
  renderSeq: 0,
  listeners: [],
  tagListEl: null,
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
  const inputRows = toArray(rows);
  const chartRows = applyChartFilters(inputRows, opts);
  if (opts.view === 'time_series') return buildTimeSeriesData(chartRows, opts, inputRows.length);
  if (opts.view === 'crosstab') return buildCrosstabData(chartRows, opts, inputRows.length);
  return buildBreakdownData(chartRows, opts, inputRows.length);
}

function buildBreakdownData(rows, opts, sourceRowCount) {
  const counts = new Map();
  let rowCount = 0;
  let elementCount = 0;

  for (const row of rows) {
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

  const datasets = [
    {
      label: `${ELEMENT_LABELS[opts.element]} by ${DIMENSION_LABELS[opts.dimension]}`,
      data: pairs.map((pair) => pair.count),
    },
  ];

  return {
    labels: pairs.map((pair) => pair.label),
    values: datasets[0].data,
    datasets,
    pairs,
    rowCount,
    sourceRowCount,
    elementCount,
    totalCount: pairs.reduce((sum, pair) => sum + pair.count, 0),
    element: opts.element,
    dimension: opts.dimension,
    view: opts.view,
    series: opts.series,
    granularity: opts.granularity,
  };
}

function buildTimeSeriesData(rows, opts, sourceRowCount) {
  const seriesDimension = opts.series === 'none' ? null : opts.series;
  const labelsSet = new Set();
  const countsBySeries = new Map();
  const totalsBySeries = new Map();
  let elementCount = 0;

  for (const row of rows) {
    const bucket = dateBucket(row.posted_at, opts.granularity);
    if (!isRealTimeBucket(bucket)) continue;
    labelsSet.add(bucket);
    for (const entry of elementEntries(row, opts.element)) {
      elementCount += 1;
      const seriesValues = seriesDimension
        ? dimensionValues(entry, { ...opts, dimension: seriesDimension })
        : ['All'];
      for (const seriesValue of emptyAware(seriesValues, opts.includeEmpty, '(none)')) {
        addNestedCount(countsBySeries, seriesValue, bucket, 1);
        totalsBySeries.set(seriesValue, (totalsBySeries.get(seriesValue) || 0) + 1);
      }
    }
  }

  let labels = sortedTimeLabels([...labelsSet], opts.granularity);
  if (opts.includeEmpty) labels = expandedTimeLabels(labels, rows, opts);

  const seriesNames = limitLabels(sortSeriesLabels(totalsBySeries), opts.limit);
  const datasets = seriesNames.map((name, idx) => ({
    label: name,
    data: labels.map((label) => countsBySeries.get(name)?.get(label) || 0),
    borderColor: PALETTE[idx % PALETTE.length],
    backgroundColor: PALETTE[idx % PALETTE.length],
  }));

  const first = datasets[0]?.data ?? [];
  return {
    labels,
    values: first,
    datasets,
    pairs: labels.map((label, idx) => ({
      label,
      count: datasets.reduce((sum, dataset) => sum + Number(dataset.data[idx] || 0), 0),
    })),
    rowCount: rows.length,
    sourceRowCount,
    elementCount,
    totalCount: datasets.reduce(
      (sum, dataset) => sum + dataset.data.reduce((inner, value) => inner + Number(value || 0), 0),
      0
    ),
    element: opts.element,
    dimension: 'posted_time',
    view: opts.view,
    series: opts.series,
    granularity: opts.granularity,
  };
}

function buildCrosstabData(rows, opts, sourceRowCount) {
  const focus = String(opts.focusTag || '').trim();
  if (!focus) {
    return emptyModel(rows, opts, sourceRowCount, 'Choose a tag/topic to compare.');
  }

  const counts = new Map();
  let elementCount = 0;
  for (const row of rows) {
    const matches = rowMatchesTag(row, focus);
    for (const entry of elementEntries(row, opts.element)) {
      elementCount += 1;
      const values = dimensionValues(entry, opts);
      for (const value of values) {
        const record = counts.get(value) ?? { label: value, match: 0, other: 0 };
        if (matches) record.match += 1;
        else record.other += 1;
        counts.set(value, record);
      }
    }
  }

  let pairs = [...counts.values()]
    .filter((pair) => pair.match + pair.other >= Math.max(1, opts.minCount))
    .map((pair) => ({
      ...pair,
      count: pair.match + pair.other,
      share: pair.match + pair.other > 0 ? pair.match / (pair.match + pair.other) : 0,
    }));
  pairs = sortPairs(pairs, resolveSort(opts));
  if (opts.limit > 0) pairs = pairs.slice(0, opts.limit);

  return {
    labels: pairs.map((pair) => pair.label),
    values: pairs.map((pair) => pair.match),
    datasets: [
      {
        label: `With ${focus}`,
        data: pairs.map((pair) => pair.match),
        backgroundColor: PALETTE[0],
      },
      {
        label: `Without ${focus}`,
        data: pairs.map((pair) => pair.other),
        backgroundColor: PALETTE[7],
      },
    ],
    pairs,
    rowCount: rows.length,
    sourceRowCount,
    elementCount,
    totalCount: pairs.reduce((sum, pair) => sum + pair.count, 0),
    element: opts.element,
    dimension: opts.dimension,
    view: opts.view,
    series: opts.series,
    granularity: opts.granularity,
    focusTag: focus,
  };
}

function emptyModel(rows, opts, sourceRowCount, message) {
  return {
    labels: [],
    values: [],
    datasets: [],
    pairs: [],
    rowCount: rows.length,
    sourceRowCount,
    elementCount: 0,
    totalCount: 0,
    element: opts.element,
    dimension: opts.dimension,
    view: opts.view,
    series: opts.series,
    granularity: opts.granularity,
    message,
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
  assignElement(
    'tableEl',
    options.table ?? options.tableEl ?? options.details ?? options.detailsEl
  );

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
  state.tableEl =
    state.tableEl ||
    panel.querySelector('[data-chart-table]') ||
    panel.querySelector('[data-charts-table]');
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

  if (!state.tableEl) {
    state.tableEl = document.createElement('div');
    state.tableEl.dataset.chartTable = 'true';
    state.tableEl.className = 'charts-table-wrap';
    panel.append(state.tableEl);
  }
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
      if (def.max !== undefined) input.max = String(def.max);
      input.step = '1';
    }
    input.className = 'select';
    if (def.dynamic === 'tags') attachTagList(input);
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

    if (name === 'focusTag') attachTagList(control);
  }
  populateDynamicControls();
}

function appendOptions(select, options) {
  for (const opt of options ?? []) {
    const option = document.createElement('option');
    option.value = opt.value;
    option.textContent = opt.label;
    select.append(option);
  }
}

function populateDynamicControls() {
  const rows = currentRowsForControls();
  const accountControl = state.controls.account;
  if (isSelectControl(accountControl)) {
    const options = accountOptions(rows);
    replaceSelectOptions(accountControl, [{ value: '', label: 'All accounts' }, ...options]);
  }

  const tagControl = state.controls.focusTag;
  if (isDomControl(tagControl)) {
    attachTagList(tagControl);
    if (state.tagListEl) replaceDataListOptions(state.tagListEl, tagOptions(rows));
  }
}

function currentRowsForControls() {
  if (typeof state.getAllRows === 'function') return toArray(state.getAllRows());
  if (typeof state.getRows === 'function') return toArray(state.getRows());
  return [];
}

function accountOptions(rows) {
  const counts = new Map();
  for (const row of rows) {
    const raw = String(row?.account_handle ?? '').trim();
    if (!raw) continue;
    counts.set(raw, (counts.get(raw) || 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([handle, count]) => ({
      value: handle,
      label: `${formatHandle(handle)} (${formatNumber(count)})`,
    }));
}

function tagOptions(rows) {
  const counts = new Map();
  for (const row of rows) {
    for (const tag of tagNames(row)) counts.set(tag, (counts.get(tag) || 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 1000)
    .map(([tag, count]) => ({ value: tag, label: `${tag} (${formatNumber(count)})` }));
}

function replaceSelectOptions(select, options) {
  const previous = select.value;
  select.replaceChildren();
  appendOptions(select, options);
  if (options.some((opt) => opt.value === previous)) select.value = previous;
  else select.value = '';
}

function replaceDataListOptions(list, options) {
  list.replaceChildren();
  for (const opt of options) {
    const option = document.createElement('option');
    option.value = opt.value;
    option.label = opt.label;
    list.append(option);
  }
}

function attachTagList(input) {
  if (!isDomControl(input) || !('setAttribute' in input)) return;
  const doc = input.ownerDocument || document;
  if (!state.tagListEl) {
    state.tagListEl = doc.getElementById('chart-tag-options') || doc.createElement('datalist');
    state.tagListEl.id = 'chart-tag-options';
    if (!state.tagListEl.parentNode) doc.body.append(state.tagListEl);
  }
  input.setAttribute('list', state.tagListEl.id);
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
    const eventName =
      name === 'limit' ||
      name === 'minCount' ||
      name === 'from' ||
      name === 'to' ||
      name === 'focusTag'
        ? 'input'
        : 'change';
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

function isSelectControl(value) {
  return isDomControl(value) && String(value.tagName || '').toLowerCase() === 'select';
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
  renderDetailsTable(model, opts);

  if (model.labels.length === 0) {
    destroyChart();
    setStatus(
      'empty',
      model.message || 'No chartable values for the selected metric and dimension.'
    );
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
  const pointCount = model.datasets.reduce((sum, dataset) => sum + dataset.data.length, 0);
  if (pointCount > RENDER_POINT_WARNING) {
    setStatus(
      'warn',
      `Rendering ${formatNumber(pointCount)} chart points. Lower Top N or narrow the chart filters if interaction gets sluggish.`
    );
  } else {
    setStatus('', '');
  }
}

function currentOptions() {
  return normalizeOptions({
    view: readControl('view', DEFAULTS.view),
    element: readControl('element', DEFAULTS.element),
    dimension: readControl('dimension', DEFAULTS.dimension),
    series: readControl('series', DEFAULTS.series),
    granularity: readControl('granularity', DEFAULTS.granularity),
    chartType: readControl('chartType', DEFAULTS.chartType),
    scope: readControl('scope', DEFAULTS.scope),
    account: readControl('account', DEFAULTS.account),
    from: readControl('from', DEFAULTS.from),
    to: readControl('to', DEFAULTS.to),
    focusTag: readControl('focusTag', DEFAULTS.focusTag),
    limit: readControl('limit', DEFAULTS.limit),
    minCount: readControl('minCount', DEFAULTS.minCount),
    sort: readControl('sort', DEFAULTS.sort),
    includeEmpty: readControl('includeEmpty', DEFAULTS.includeEmpty),
    categoryOf: state.categoryOf,
  });
}

function normalizeOptions(options) {
  const view = validOption(options.view, VALID_VIEWS, DEFAULTS.view);
  const element = validOption(options.element, VALID_ELEMENTS, DEFAULTS.element);
  const dimension = validOption(options.dimension, VALID_DIMENSIONS, DEFAULTS.dimension);
  const series = validOption(options.series, VALID_SERIES, DEFAULTS.series);
  const granularity = validOption(options.granularity, VALID_GRANULARITIES, DEFAULTS.granularity);
  const chartType = validOption(options.chartType, VALID_CHART_TYPES, DEFAULTS.chartType);
  const scope = validOption(options.scope, VALID_SCOPES, DEFAULTS.scope);
  const sort = validOption(options.sort, VALID_SORTS, DEFAULTS.sort);
  const limit = normalizeNumber(options.limit, DEFAULTS.limit, NO_LIMIT, MAX_TOP_N);
  const minCount = normalizeNumber(options.minCount, DEFAULTS.minCount, 1, 1000000);
  const includeEmpty = normalizeBoolean(options.includeEmpty);
  const account = String(options.account ?? '').trim();
  const from = normalizeDateInput(options.from);
  const to = normalizeDateInput(options.to);
  const focusTag = String(options.focusTag ?? '').trim();

  return {
    view,
    element,
    dimension,
    series,
    granularity,
    chartType,
    scope,
    account,
    from,
    to,
    focusTag,
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

function normalizeDateInput(value) {
  const text = String(value ?? '').trim();
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text : '';
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

function applyChartFilters(rows, opts) {
  const account = String(opts.account || '')
    .replace(/^@/, '')
    .toLocaleLowerCase();
  return rows.filter((row) => {
    if (account) {
      const handle = String(row?.account_handle ?? '')
        .replace(/^@/, '')
        .toLocaleLowerCase();
      if (handle !== account) return false;
    }
    const postedDate = postedDateString(row?.posted_at);
    if (opts.from && (!postedDate || postedDate < opts.from)) return false;
    if (opts.to && (!postedDate || postedDate > opts.to)) return false;
    if (opts.focusTag && opts.view !== 'crosstab' && !rowMatchesTag(row, opts.focusTag))
      return false;
    return true;
  });
}

function postedDateString(value) {
  const match = String(value ?? '').match(/^(\d{4}-\d{2}-\d{2})/);
  return match ? match[1] : '';
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
  if (element === 'replies') return row?.tweet_type === 'reply' ? [{ row, value: row }] : [];
  if (element === 'categories')
    return [{ row, category: categoryLabel(row, state.categoryOf), value: row }];

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
  if (opts.dimension === 'posted_time') return [dateBucket(row.posted_at, opts.granularity)];
  if (opts.dimension === 'posted_day') return [dateBucket(row.posted_at, 'day')];
  if (opts.dimension === 'posted_week') return [dateBucket(row.posted_at, 'week')];
  if (opts.dimension === 'posted_month') return [dateBucket(row.posted_at, 'month')];
  if (opts.dimension === 'posted_year') return [dateBucket(row.posted_at, 'year')];
  if (opts.dimension === 'lang') return [nonEmpty(row.lang, '(unknown language)')];
  if (opts.dimension === 'deleted') return [deletedBucket(row)];
  if (opts.dimension === 'news_coverage') return [newsCoverageBucket(row)];

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

function newsCoverageBucket(row) {
  const count = Number(row?.news_mention_count ?? toArray(row?.news_mentions).length ?? 0);
  return count > 0 ? 'mentioned in news' : 'not found in news';
}

function dateBucket(value, granularity) {
  const text = postedDateString(value);
  if (!text) return '(no posted date)';
  if (granularity === 'year') return text.slice(0, 4);
  if (granularity === 'month') return text.slice(0, 7);
  if (granularity === 'week') return weekBucket(text);
  return text;
}

function weekBucket(dateText) {
  const date = parseUtcDate(dateText);
  if (!date) return '(no posted date)';
  const day = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((date - yearStart) / 86400000 + 1) / 7);
  return `${date.getUTCFullYear()}-W${String(week).padStart(2, '0')}`;
}

function parseUtcDate(value) {
  const match = String(value ?? '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return null;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return Number.isNaN(date.getTime()) ? null : date;
}

function isRealTimeBucket(value) {
  return value && value !== '(no posted date)';
}

function sortedTimeLabels(labels, granularity) {
  return labels
    .slice()
    .sort((a, b) =>
      timeBucketSortKey(a, granularity).localeCompare(timeBucketSortKey(b, granularity))
    );
}

function timeBucketSortKey(label, granularity) {
  if (granularity === 'week') return label.replace('-W', '-');
  return label;
}

function expandedTimeLabels(labels, rows, opts) {
  const range = timeRange(labels, rows, opts);
  if (!range) return labels;
  const out = [];
  const cursor = parseUtcDate(range.start);
  const end = parseUtcDate(range.end);
  if (!cursor || !end) return labels;
  while (cursor <= end) {
    out.push(dateBucket(formatDate(cursor), opts.granularity));
    advanceDate(cursor, opts.granularity);
  }
  return uniqueStrings(out);
}

function timeRange(labels, rows, opts) {
  const dates = [];
  if (opts.from) dates.push(opts.from);
  if (opts.to) dates.push(opts.to);
  for (const row of rows) {
    const date = postedDateString(row?.posted_at);
    if (date) dates.push(date);
  }
  if (dates.length === 0 && labels.length === 0) return null;
  dates.sort();
  return { start: opts.from || dates[0], end: opts.to || dates[dates.length - 1] };
}

function advanceDate(date, granularity) {
  if (granularity === 'year') date.setUTCFullYear(date.getUTCFullYear() + 1, 0, 1);
  else if (granularity === 'month') date.setUTCMonth(date.getUTCMonth() + 1, 1);
  else if (granularity === 'week') date.setUTCDate(date.getUTCDate() + 7);
  else date.setUTCDate(date.getUTCDate() + 1);
}

function formatDate(date) {
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}-${String(
    date.getUTCDate()
  ).padStart(2, '0')}`;
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

function rowMatchesTag(row, selection) {
  const want = String(selection ?? '')
    .trim()
    .toLocaleLowerCase();
  if (!want) return true;
  const wantNamespace = want.endsWith(':') ? want.slice(0, -1) : '';
  return tagNames(row).some((tag) => {
    const lower = tag.toLocaleLowerCase();
    if (lower === want) return true;
    if (wantNamespace && tagNamespace(lower) === wantNamespace) return true;
    return !want.includes(':') && tagNamespace(lower) === want;
  });
}

function nonEmpty(value, fallback) {
  const text = String(value ?? '').trim();
  return text || fallback;
}

function hasValue(value) {
  return value !== undefined && value !== null && String(value).trim() !== '';
}

function addNestedCount(map, outer, inner, amount) {
  let innerMap = map.get(outer);
  if (!innerMap) {
    innerMap = new Map();
    map.set(outer, innerMap);
  }
  innerMap.set(inner, (innerMap.get(inner) || 0) + amount);
}

function sortSeriesLabels(totalsBySeries) {
  return [...totalsBySeries.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([label]) => label);
}

function limitLabels(labels, limit) {
  return limit > 0 ? labels.slice(0, limit) : labels;
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
  if (
    opts.dimension === 'posted_time' ||
    opts.dimension === 'posted_day' ||
    opts.dimension === 'posted_week' ||
    opts.dimension === 'posted_month' ||
    opts.dimension === 'posted_year'
  )
    return 'label_asc';
  return 'count_desc';
}

function chartConfig(model, opts) {
  const chartType = resolveChartType(opts, model);
  const isDoughnut = chartType.type === 'doughnut';
  const isLine = chartType.type === 'line';
  const colors = model.labels.map((_, idx) => PALETTE[idx % PALETTE.length]);
  const title = chartTitle(model, opts);
  const datasets = (
    model.datasets.length > 0 ? model.datasets : [{ label: title, data: model.values }]
  ).map((dataset, datasetIdx) => {
    const color =
      dataset.borderColor || dataset.backgroundColor || PALETTE[datasetIdx % PALETTE.length];
    return {
      label: dataset.label || title,
      data: dataset.data,
      backgroundColor: isDoughnut ? colors : dataset.backgroundColor || color,
      borderColor: dataset.borderColor || color,
      borderWidth: isLine ? 2 : 1,
      fill: false,
      tension: isLine ? 0.2 : 0,
    };
  });

  return {
    type: chartType.type,
    data: {
      labels: model.labels,
      datasets,
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: chartType.indexAxis,
      plugins: {
        legend: {
          display: isDoughnut || datasets.length > 1,
          position: 'bottom',
        },
        title: {
          display: true,
          text: title,
        },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y ?? ctx.parsed.x ?? ctx.parsed}`,
          },
        },
      },
      scales: isDoughnut
        ? {}
        : {
            x: {
              beginAtZero: true,
              stacked: opts.view === 'crosstab',
              ticks: {
                autoSkip: true,
                maxTicksLimit: opts.view === 'time_series' ? 16 : 20,
                precision: 0,
              },
            },
            y: {
              beginAtZero: true,
              stacked: opts.view === 'crosstab',
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

  if (opts.view === 'time_series') return { type: 'line' };
  if (
    opts.dimension === 'posted_time' ||
    opts.dimension === 'posted_day' ||
    opts.dimension === 'posted_week' ||
    opts.dimension === 'posted_month' ||
    opts.dimension === 'posted_year'
  )
    return { type: 'line' };
  if (opts.view === 'crosstab') return { type: 'bar', indexAxis: 'y' };
  if (model.labels.length > 10) return { type: 'bar', indexAxis: 'y' };
  return { type: 'bar' };
}

function chartTitle(model, opts) {
  if (opts.view === 'time_series') {
    const compare = opts.series === 'none' ? '' : ` by ${SERIES_LABELS[opts.series].toLowerCase()}`;
    return `${ELEMENT_LABELS[opts.element]} over time${compare}`;
  }
  if (opts.view === 'crosstab') {
    return `${ELEMENT_LABELS[opts.element]} with/without ${model.focusTag || opts.focusTag} by ${
      DIMENSION_LABELS[opts.dimension]
    }`;
  }
  return `${ELEMENT_LABELS[opts.element]} by ${DIMENSION_LABELS[opts.dimension]}`;
}

function renderDetailsTable(model, opts) {
  if (!state.tableEl) return;
  const rows = model.pairs.slice(0, 200);
  if (rows.length === 0) {
    state.tableEl.replaceChildren();
    return;
  }

  const table = document.createElement('table');
  table.className = 'charts-table';
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  const headers =
    opts.view === 'crosstab'
      ? ['Bucket', `With ${model.focusTag || opts.focusTag}`, 'Without', 'Share']
      : ['Bucket', 'Count'];
  for (const header of headers) {
    const th = document.createElement('th');
    th.textContent = header;
    headRow.append(th);
  }
  thead.append(headRow);
  table.append(thead);

  const tbody = document.createElement('tbody');
  for (const row of rows) {
    const tr = document.createElement('tr');
    appendCell(tr, row.label);
    if (opts.view === 'crosstab') {
      appendCell(tr, formatNumber(row.match), true);
      appendCell(tr, formatNumber(row.other), true);
      appendCell(tr, `${Math.round(row.share * 1000) / 10}%`, true);
    } else {
      appendCell(tr, formatNumber(row.count), true);
    }
    tbody.append(tr);
  }
  table.append(tbody);

  const note = document.createElement('div');
  note.className = 'charts-table-note';
  note.textContent =
    model.pairs.length > rows.length
      ? `Showing first ${formatNumber(rows.length)} table rows; chart uses ${formatNumber(
          model.pairs.length
        )}.`
      : `Showing ${formatNumber(rows.length)} table rows.`;

  state.tableEl.replaceChildren(table, note);
}

function appendCell(row, value, numeric = false) {
  const td = document.createElement('td');
  td.textContent = String(value ?? '');
  if (numeric) td.className = 'num';
  row.append(td);
}

function summaryText(model, opts) {
  const rows = formatNumber(model.rowCount);
  const sourceRows = formatNumber(model.sourceRowCount);
  const elements = formatNumber(model.elementCount);
  const buckets = formatNumber(model.labels.length);
  const elementLabel = ELEMENT_LABELS[opts.element].toLowerCase();
  const dimension =
    opts.view === 'time_series'
      ? `${opts.granularity} buckets`
      : `${DIMENSION_LABELS[opts.dimension].toLowerCase()} buckets`;
  const narrowed = model.rowCount === model.sourceRowCount ? rows : `${rows} of ${sourceRows}`;
  const focus = opts.focusTag ? ` Tag/topic: ${opts.focusTag}.` : '';
  return `${VIEW_LABELS[opts.view]}: ${elements} ${elementLabel} across ${buckets} ${dimension} from ${narrowed} rows.${focus}`;
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
