#!/usr/bin/env node

import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { accessSync, constants as fsConstants } from 'node:fs';
import { appendFile, mkdir, writeFile } from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';

const ENDPOINT_NEEDLES = [
  'UserTweets',
  'UserTweetsAndReplies',
  'UserMedia',
  'UserHighlightsTweets',
  'TweetDetail',
  'TweetResultByRestId',
  'HomeTimeline',
  'HomeLatestTimeline',
  'SearchTimeline',
  'ListLatestTweetsTimeline',
  'Likes',
  'Bookmarks',
  'UserByScreenName',
  'UserByRestId',
  'BirdwatchFetchOneNote',
  'BirdwatchFetchNotes',
];

const ID_KEYS = new Set([
  'id',
  'id_str',
  'rest_id',
  'tweet_id',
  'conversation_id_str',
  'in_reply_to_status_id_str',
  'quoted_status_id_str',
]);

const TRACKING_HOST_PARTS = [
  'doubleclick.net',
  'google-analytics.com',
  'googletagmanager.com',
  'ads-twitter.com',
  'analytics.twitter.com',
  'analytics.x.com',
];

const MEDIA_HOST_PARTS = [
  'pbs.twimg.com',
  'video.twimg.com',
  'ton.twimg.com',
  'amp.twimg.com',
  'video.pscp.tv',
  'prod-fastly-us-west-1.video.pscp.tv',
];

const DEFAULTS = {
  outDir: '.skim/raw',
  profileDir: '.skim/profile',
  seconds: 180,
  scrolls: 80,
  scrollDelayMs: 1500,
  scrollFactor: 0.85,
  seekScrollFactor: 8,
  seekDelayMs: 350,
  maxBodyBytes: 15_000_000,
  headless: false,
  keepOpen: false,
  allowImages: false,
  allowMedia: false,
  allowFonts: false,
  allowStyles: false,
  metadataOnly: false,
  failOnZeroResponses: false,
  manual: false,
  loginBrowser: false,
};

class CdpClient {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.ws = null;
    this.nextId = 1;
    this.pending = new Map();
    this.handlers = new Map();
    this.closedHandlers = [];
  }

  async connect() {
    if (typeof WebSocket !== 'function') {
      throw new Error('This tool needs Node 22+ with global WebSocket support.');
    }

    this.ws = new WebSocket(this.wsUrl);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(
        () => reject(new Error('Timed out connecting to Chrome CDP.')),
        15_000
      );
      this.ws.addEventListener('open', () => {
        clearTimeout(timer);
        resolve();
      });
      this.ws.addEventListener('error', (event) => {
        clearTimeout(timer);
        reject(new Error(`CDP WebSocket error: ${event.message ?? 'unknown error'}`));
      });
    });

    this.ws.addEventListener('message', (event) => this.handleMessage(event.data));
    this.ws.addEventListener('close', () => {
      for (const { reject } of this.pending.values()) {
        reject(new Error('Chrome CDP connection closed.'));
      }
      this.pending.clear();
      for (const handler of this.closedHandlers) handler();
    });
  }

  handleMessage(data) {
    let message;
    try {
      message = JSON.parse(typeof data === 'string' ? data : Buffer.from(data).toString('utf8'));
    } catch (error) {
      console.warn(`Skipping malformed CDP message: ${error.message}`);
      return;
    }

    if (message.id) {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      clearTimeout(pending.timer);
      if (message.error) {
        pending.reject(new Error(`${pending.method}: ${message.error.message}`));
      } else {
        pending.resolve(message.result ?? {});
      }
      return;
    }

    const handlers = this.handlers.get(message.method);
    if (!handlers) return;
    for (const handler of handlers) {
      try {
        handler(message.params ?? {});
      } catch (error) {
        console.warn(`CDP handler for ${message.method} failed: ${error.message}`);
      }
    }
  }

  on(method, handler) {
    const handlers = this.handlers.get(method) ?? [];
    handlers.push(handler);
    this.handlers.set(method, handlers);
  }

  onClosed(handler) {
    this.closedHandlers.push(handler);
  }

  send(method, params = {}, timeoutMs = 30_000) {
    const id = this.nextId;
    this.nextId += 1;
    const payload = JSON.stringify({ id, method, params });
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method} timed out after ${timeoutMs}ms.`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer, method });
      this.ws.send(payload);
    });
  }

  close() {
    this.ws?.close();
  }
}

function usage(exitCode = 0) {
  const text = `
Usage:
  npm run skim:x -- --handle DHSgov [options]
  npm run skim:x -- --url https://x.com/DHSgov/with_replies [options]

Options:
  --handle <handle>          Open https://x.com/<handle>.
  --url <url>                Open a specific X/Twitter URL.
  --profile-dir <dir>        Persistent browser profile. Default: ${DEFAULTS.profileDir}
  --out <dir>                JSONL output directory. Default: ${DEFAULTS.outDir}
  --seconds <n>              Max runtime. Default: ${DEFAULTS.seconds}
  --scrolls <n>              Max scroll steps. Default: ${DEFAULTS.scrolls}
  --scroll-delay-ms <n>      Delay between scrolls. Default: ${DEFAULTS.scrollDelayMs}
  --scroll-factor <n>        Viewport heights per normal scroll. Default: ${DEFAULTS.scrollFactor}
  --seek-year <yyyy>         Fast-scroll until visible tweets reach this year.
  --seek-scroll-factor <n>   Viewport heights per seek scroll. Default: ${DEFAULTS.seekScrollFactor}
  --seek-delay-ms <n>        Delay between seek scrolls. Default: ${DEFAULTS.seekDelayMs}
  --chrome-path <path>       Chrome/Edge executable path.
  --headless                 Run without a visible browser window.
  --keep-open                Leave the browser open after the skim finishes.
  --allow-images             Let image requests load.
  --allow-media              Let video/audio requests load.
  --allow-fonts              Let font requests load.
  --allow-styles             Let stylesheet requests load.
  --metadata-only            Store endpoint metadata, IDs, and media URL candidates only.
  --fail-on-zero-responses   Exit nonzero if no GraphQL responses were captured.
  --manual                   Open the page and capture traffic, but do not auto-scroll.
  --login-browser            Open a normal Chrome login window with no CDP or blocking.
  --max-body-bytes <n>       Omit response bodies larger than this. Default: ${DEFAULTS.maxBodyBytes}
`;
  console.log(text.trim());
  process.exit(exitCode);
}

function parseArgs(argv) {
  const options = { ...DEFAULTS };
  const positional = [];

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const readValue = () => {
      const value = argv[index + 1];
      if (!value || value.startsWith('--')) {
        throw new Error(`${arg} needs a value.`);
      }
      index += 1;
      return value;
    };

    switch (arg) {
      case '--help':
      case '-h':
        usage(0);
        break;
      case '--handle':
        options.handle = stripHandle(readValue());
        break;
      case '--url':
        options.url = readValue();
        break;
      case '--profile-dir':
        options.profileDir = readValue();
        break;
      case '--out':
        options.outDir = readValue();
        break;
      case '--seconds':
        options.seconds = positiveNumber(arg, readValue());
        break;
      case '--scrolls':
        options.scrolls = positiveNumber(arg, readValue());
        break;
      case '--scroll-delay-ms':
        options.scrollDelayMs = positiveNumber(arg, readValue());
        break;
      case '--scroll-factor':
        options.scrollFactor = positiveNumber(arg, readValue());
        break;
      case '--seek-year':
        options.seekYear = positiveNumber(arg, readValue());
        break;
      case '--seek-scroll-factor':
        options.seekScrollFactor = positiveNumber(arg, readValue());
        break;
      case '--seek-delay-ms':
        options.seekDelayMs = positiveNumber(arg, readValue());
        break;
      case '--chrome-path':
        options.chromePath = readValue();
        break;
      case '--max-body-bytes':
        options.maxBodyBytes = positiveNumber(arg, readValue());
        break;
      case '--headless':
        options.headless = true;
        break;
      case '--keep-open':
        options.keepOpen = true;
        break;
      case '--allow-images':
        options.allowImages = true;
        break;
      case '--allow-media':
        options.allowMedia = true;
        break;
      case '--allow-fonts':
        options.allowFonts = true;
        break;
      case '--allow-styles':
        options.allowStyles = true;
        break;
      case '--metadata-only':
        options.metadataOnly = true;
        break;
      case '--fail-on-zero-responses':
        options.failOnZeroResponses = true;
        break;
      case '--manual':
      case '--login':
      case '--login-only':
        options.manual = true;
        break;
      case '--login-browser':
      case '--plain-login':
        options.loginBrowser = true;
        break;
      default:
        if (arg.startsWith('--')) throw new Error(`Unknown option: ${arg}`);
        positional.push(arg);
    }
  }

  if (!options.url && positional[0]) options.url = positional[0];
  if (!options.url && options.handle) options.url = `https://x.com/${options.handle}`;
  if (!options.url && options.loginBrowser) options.url = 'https://x.com/login';
  if (!options.url) throw new Error('Provide --handle or --url.');
  if (!/^https?:\/\//i.test(options.url)) {
    options.url = `https://x.com/${stripHandle(options.url)}`;
  }

  return options;
}

function positiveNumber(name, raw) {
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`${name} must be a positive number.`);
  }
  return value;
}

function stripHandle(raw) {
  return raw
    .trim()
    .replace(/^@/, '')
    .replace(/^https?:\/\/(?:www\.)?(?:x|twitter)\.com\//i, '')
    .split(/[/?#]/)[0];
}

async function main() {
  let options;
  try {
    options = parseArgs(process.argv.slice(2));
  } catch (error) {
    console.error(error.message);
    usage(1);
  }

  const startedAt = new Date();
  const runId = `${formatStamp(startedAt)}-${shortHash(options.url)}`;
  const cwd = process.cwd();
  const outDir = path.resolve(cwd, options.outDir);
  const profileDir = path.resolve(cwd, options.profileDir);
  const targetSlug = targetSlugFor(options);
  const outPath = path.join(outDir, `${targetSlug}-${runId}.jsonl`);
  const summaryPath = path.join(outDir, `${targetSlug}-${runId}.summary.json`);

  await mkdir(profileDir, { recursive: true });

  const browserPath = findBrowser(options.chromePath);
  if (options.loginBrowser) {
    launchPlainLoginBrowser(browserPath, profileDir, options);
    console.log(`Opened normal Chrome login window at ${options.url}`);
    console.log(`Profile: ${profileDir}`);
    console.log('After logging in, rerun skim:x with the same --profile-dir.');
    return;
  }

  await mkdir(outDir, { recursive: true });

  const port = await findFreePort();
  const chrome = launchBrowser(browserPath, port, profileDir, options);
  let client;
  const capturedRequestIds = new Map();
  const captureTasks = new Set();
  const stats = {
    blockedTotal: 0,
    blockedByType: new Map(),
    blockedByHost: new Map(),
    endpoints: new Map(),
    candidateTweetIds: new Set(),
    mediaUrlCandidates: new Set(),
    responseCount: 0,
    responseErrors: [],
    retryClicks: 0,
    showMoreClicks: 0,
  };
  let writeChain = Promise.resolve();

  const shutdown = async (exitCode = 130) => {
    try {
      client?.close();
    } catch {
      // Best effort on interrupt.
    }
    if (!options.keepOpen) stopBrowser(chrome);
    process.exit(exitCode);
  };
  process.once('SIGINT', () => void shutdown(130));
  process.once('SIGTERM', () => void shutdown(143));

  try {
    const target = await createTarget(port, chrome);
    client = new CdpClient(target.webSocketDebuggerUrl);
    await client.connect();

    const writeRecord = (record) => {
      writeChain = writeChain.then(() =>
        appendFile(outPath, `${JSON.stringify(record)}\n`, 'utf8')
      );
      return writeChain;
    };

    client.on('Fetch.requestPaused', (event) => {
      void handlePausedRequest(client, event, options, stats);
    });
    client.on('Network.responseReceived', (event) => {
      const endpoint = endpointFromUrl(event.response?.url ?? '');
      if (!endpoint) return;
      const status = event.response?.status ?? 0;
      const mimeType = event.response?.mimeType ?? '';
      if (status < 200 || status >= 400) return;
      if (mimeType && !/json|javascript|text/i.test(mimeType)) return;
      capturedRequestIds.set(event.requestId, {
        endpoint,
        url: event.response.url,
        status,
        mimeType,
        encodedDataLength: event.response.encodedDataLength ?? 0,
      });
    });
    client.on('Network.loadingFinished', (event) => {
      const meta = capturedRequestIds.get(event.requestId);
      if (!meta) return;
      capturedRequestIds.delete(event.requestId);
      const task = captureResponse(
        client,
        event.requestId,
        meta,
        options,
        stats,
        runId,
        writeRecord
      );
      captureTasks.add(task);
      task.finally(() => captureTasks.delete(task));
    });
    client.on('Network.loadingFailed', (event) => {
      capturedRequestIds.delete(event.requestId);
    });

    await client.send('Page.enable');
    await client.send('Runtime.enable');
    await client.send('Network.enable');
    await client.send('Network.setCacheDisabled', { cacheDisabled: true });
    await client.send('Fetch.enable', {
      patterns: [{ urlPattern: '*', requestStage: 'Request' }],
    });

    console.log(`Opening ${options.url}`);
    console.log(`Profile: ${profileDir}`);
    console.log(`Output:  ${outPath}`);
    console.log(
      'Blocking images, media, fonts, stylesheets, and common tracking hosts by default.'
    );

    await client.send('Page.navigate', { url: options.url });
    await wait(5000);
    if (options.manual) {
      console.log('Manual mode enabled: no auto-scroll or retry clicks. Use the browser normally.');
      await waitForManualSession(client, chrome);
    } else {
      await skimPage(client, options, stats);
    }
    await wait(2500);
    await Promise.allSettled([...captureTasks]);
    await writeChain;

    const summary = buildSummary({
      options,
      runId,
      startedAt,
      outPath,
      summaryPath,
      stats,
    });
    await writeFile(summaryPath, `${JSON.stringify(summary, null, 2)}\n`, 'utf8');

    console.log('');
    console.log(`Captured ${summary.captured_response_count} GraphQL responses.`);
    console.log(`Candidate tweet IDs: ${summary.unique_candidate_tweet_ids}`);
    console.log(`Media URL candidates: ${summary.unique_media_url_candidates}`);
    console.log(`Blocked requests: ${summary.blocked_request_count}`);
    console.log(`Summary: ${summaryPath}`);
    if (options.failOnZeroResponses && summary.captured_response_count === 0) {
      console.error('No GraphQL responses captured; treating this as a failed skim.');
      process.exitCode = 2;
    }

    if (options.keepOpen) {
      console.log('Browser left open because --keep-open was set. Press Ctrl+C here when done.');
      await new Promise(() => {});
    }

    client.close();
    stopBrowser(chrome);
  } catch (error) {
    console.error(error.stack ?? error.message);
    if (!options.keepOpen) stopBrowser(chrome);
    process.exit(1);
  }
}

async function handlePausedRequest(client, event, options, stats) {
  const decision = requestBlockDecision(event, options);
  if (decision.block) {
    stats.blockedTotal += 1;
    increment(stats.blockedByType, decision.reason);
    increment(stats.blockedByHost, hostnameFor(event.request.url));
    try {
      await client.send('Fetch.failRequest', {
        requestId: event.requestId,
        errorReason: 'BlockedByClient',
      });
    } catch {
      // The request may have raced ahead; ignore.
    }
    return;
  }

  try {
    await client.send('Fetch.continueRequest', { requestId: event.requestId });
  } catch {
    // The request may already be gone.
  }
}

function requestBlockDecision(event, options) {
  const url = event.request?.url ?? '';
  const type = event.resourceType ?? 'Other';
  const host = hostnameFor(url);
  const lowerUrl = url.toLowerCase();

  if (TRACKING_HOST_PARTS.some((part) => host.includes(part))) {
    return { block: true, reason: 'tracking' };
  }

  if (!options.allowMedia && MEDIA_HOST_PARTS.some((part) => host.includes(part))) {
    return { block: true, reason: 'media-host' };
  }

  if (!options.allowImages && type === 'Image') return { block: true, reason: 'image' };
  if (!options.allowMedia && type === 'Media') return { block: true, reason: 'media' };
  if (!options.allowFonts && type === 'Font') return { block: true, reason: 'font' };
  if (!options.allowStyles && type === 'Stylesheet') return { block: true, reason: 'stylesheet' };

  if (!options.allowMedia && /\.(m3u8|mp4|m4s|webm|mov|aac|mp3)(?:[?#]|$)/i.test(lowerUrl)) {
    return { block: true, reason: 'media-extension' };
  }

  if (!options.allowImages && /\.(jpg|jpeg|png|gif|webp|avif|svg)(?:[?#]|$)/i.test(lowerUrl)) {
    return { block: true, reason: 'image-extension' };
  }

  return { block: false, reason: 'allowed' };
}

async function captureResponse(client, requestId, meta, options, stats, runId, writeRecord) {
  try {
    const bodyResult = await client.send('Network.getResponseBody', { requestId }, 20_000);
    const bodyText = bodyResult.base64Encoded
      ? Buffer.from(bodyResult.body, 'base64').toString('utf8')
      : bodyResult.body;
    const bodyBytes = Buffer.byteLength(bodyText, 'utf8');
    const sha256 = createHash('sha256').update(bodyText).digest('hex');
    let parsed = null;
    let parseError = null;
    try {
      parsed = JSON.parse(bodyText);
    } catch (error) {
      parseError = error.message;
    }

    const scan = parsed ? scanJson(parsed) : { tweetIds: new Set(), mediaUrls: new Set() };
    for (const id of scan.tweetIds) stats.candidateTweetIds.add(id);
    for (const mediaUrl of scan.mediaUrls) stats.mediaUrlCandidates.add(mediaUrl);
    increment(stats.endpoints, meta.endpoint);
    stats.responseCount += 1;

    const record = {
      schema_version: 1,
      kind: 'x-graphql-response',
      captured_at: new Date().toISOString(),
      run_id: runId,
      endpoint: meta.endpoint,
      url: meta.url,
      page_url: await currentPageUrl(client),
      status: meta.status,
      mime_type: meta.mimeType,
      encoded_data_length: meta.encodedDataLength,
      body_size_bytes: bodyBytes,
      sha256,
      candidate_tweet_ids: [...scan.tweetIds].sort(),
      media_url_candidates: [...scan.mediaUrls].sort(),
    };

    if (parseError) {
      record.parse_error = parseError;
    } else if (!options.metadataOnly && bodyBytes <= options.maxBodyBytes) {
      record.body = parsed;
    } else {
      record.body_omitted_reason = options.metadataOnly
        ? 'metadata-only'
        : `body exceeds --max-body-bytes (${options.maxBodyBytes})`;
    }

    await writeRecord(record);
  } catch (error) {
    stats.responseErrors.push({
      endpoint: meta.endpoint,
      url: meta.url,
      error: error.message,
    });
  }
}

async function currentPageUrl(client) {
  try {
    const result = await client.send('Runtime.evaluate', {
      expression: 'location.href',
      returnByValue: true,
    });
    return result.result?.value ?? null;
  } catch {
    return null;
  }
}

function scanJson(root) {
  const tweetIds = new Set();
  const mediaUrls = new Set();
  const seen = new Set();

  const visit = (value, key = '') => {
    if (value == null) return;
    if (typeof value === 'string') {
      if (ID_KEYS.has(key) && /^\d{15,22}$/.test(value)) tweetIds.add(value);
      for (const match of value.matchAll(/\/status(?:es)?\/(\d{15,22})/g)) {
        tweetIds.add(match[1]);
      }
      if (isMediaUrlCandidate(value)) mediaUrls.add(stripTrackingQuery(value));
      return;
    }
    if (typeof value !== 'object') return;
    if (seen.has(value)) return;
    seen.add(value);

    if (typeof value.rest_id === 'string' && value.legacy && typeof value.legacy === 'object') {
      tweetIds.add(value.rest_id);
    }

    if (Array.isArray(value)) {
      for (const item of value) visit(item, key);
      return;
    }

    for (const [childKey, childValue] of Object.entries(value)) {
      visit(childValue, childKey);
    }
  };

  visit(root);
  return { tweetIds, mediaUrls };
}

function isMediaUrlCandidate(value) {
  return /(?:pbs\.twimg\.com\/(?:media|ext_tw_video_thumb|amplify_video_thumb|tweet_video_thumb)|video\.twimg\.com)/i.test(
    value
  );
}

function stripTrackingQuery(raw) {
  try {
    const url = new URL(raw);
    const format = url.searchParams.get('format');
    const name = url.searchParams.get('name');
    url.search = '';
    if (format) url.searchParams.set('format', format);
    if (name) url.searchParams.set('name', name);
    return url.toString();
  } catch {
    return raw;
  }
}

async function skimPage(client, options, stats) {
  const stopAt = Date.now() + options.seconds * 1000;
  if (options.seekYear) {
    await seekToYear(client, options, stopAt);
  }
  for (let index = 0; index < options.scrolls && Date.now() < stopAt; index += 1) {
    stats.showMoreClicks += await expandShowMore(client);
    const retryClicked = await clickRetryIfVisible(client);
    if (retryClicked) stats.retryClicks += 1;
    try {
      await client.send(
        'Runtime.evaluate',
        {
          expression: `window.scrollBy(0, Math.max(700, Math.floor(window.innerHeight * ${JSON.stringify(options.scrollFactor)}))); true;`,
          returnByValue: true,
        },
        8_000
      );
    } catch (error) {
      const message = `Scroll evaluation failed at ${index + 1}/${options.scrolls}: ${error.message}`;
      stats.responseErrors.push(message);
      console.warn(message);
      break;
    }
    await wait(350);
    stats.showMoreClicks += await expandShowMore(client);
    if ((index + 1) % 10 === 0) {
      console.log(
        `Scrolled ${index + 1}/${options.scrolls}; captured ${stats.responseCount}; show-more ${stats.showMoreClicks}; blocked ${stats.blockedTotal}.`
      );
    }
    await wait(options.scrollDelayMs);
  }
}

async function seekToYear(client, options, stopAt) {
  console.log(`Seeking quickly to visible ${options.seekYear} content before normal capture...`);
  for (let index = 0; Date.now() < stopAt; index += 1) {
    const range = await visibleTweetYearRange(client);
    if (range.minYear !== null && range.minYear <= options.seekYear) {
      console.log(
        `Seek reached visible year ${range.minYear} after ${index} fast scrolls; switching to normal capture.`
      );
      return;
    }
    try {
      await client.send(
        'Runtime.evaluate',
        {
          expression: `window.scrollBy(0, Math.max(1200, Math.floor(window.innerHeight * ${JSON.stringify(options.seekScrollFactor)}))); true;`,
          returnByValue: true,
        },
        8_000
      );
    } catch (error) {
      console.warn(`Seek evaluation failed after ${index + 1} fast scrolls: ${error.message}`);
      return;
    }
    if ((index + 1) % 25 === 0) {
      const label =
        range.minYear === null
          ? 'no visible tweet dates yet'
          : `visible years ${range.minYear}-${range.maxYear}`;
      console.log(`Seek ${index + 1}: ${label}.`);
    }
    await wait(options.seekDelayMs);
  }
  console.log(
    'Seek timer expired before the target year appeared; continuing normal capture here.'
  );
}

async function visibleTweetYearRange(client) {
  const expression = `
(() => {
  const years = [...document.querySelectorAll('article time[datetime], div[data-testid="cellInnerDiv"] time[datetime]')]
    .map((el) => {
      const dt = el.getAttribute('datetime') || '';
      const year = Number(dt.slice(0, 4));
      return Number.isFinite(year) ? year : null;
    })
    .filter((year) => year !== null);
  if (!years.length) return { minYear: null, maxYear: null };
  return { minYear: Math.min(...years), maxYear: Math.max(...years) };
})()
`;
  try {
    const result = await client.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
    }, 8_000);
    return result.result?.value ?? { minYear: null, maxYear: null };
  } catch {
    return { minYear: null, maxYear: null };
  }
}

async function expandShowMore(client) {
  const expression = `
(() => {
  const selectors = [
    '[data-testid="tweet-text-show-more-link"]',
    'button[data-testid="tweet-text-show-more-link"]',
    'article [role="button"][tabindex="0"]',
    'div[data-testid="cellInnerDiv"] [role="button"][tabindex="0"]',
  ];
  const isVisible = (el) => {
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none';
  };
  const seen = new Set();
  let clicked = 0;
  for (const selector of selectors) {
    for (const el of document.querySelectorAll(selector)) {
      if (seen.has(el)) continue;
      seen.add(el);
      const text = (el.textContent || '').trim().toLowerCase();
      if (text !== 'show more') continue;
      if (!isVisible(el)) continue;
      const link = el.closest('a[href]');
      if (link) {
        const href = link.getAttribute('href') || '';
        if (/\\/status\\/\\d+|\\/i\\/web\\/status\\/\\d+/.test(href)) continue;
      }
      try {
        el.click();
        clicked += 1;
      } catch {
        // Ignore stale controls.
      }
    }
  }
  return clicked;
})()
`;
  try {
    const result = await client.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
    }, 8_000);
    return Number(result.result?.value ?? 0) || 0;
  } catch {
    return 0;
  }
}

async function clickRetryIfVisible(client) {
  const expression = `
(() => {
  const nodes = [...document.querySelectorAll('button, [role="button"], span, div')];
  const hit = nodes.find((node) => {
    const text = (node.textContent || '').trim();
    if (!text || text.length > 140) return false;
    return /^(retry|try again|try reloading)$/i.test(text) || /something went wrong/i.test(text);
  });
  if (!hit) return false;
  const button = hit.closest('button, [role="button"]') || hit;
  button.click();
  return true;
})()
`;
  try {
    const result = await client.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
    }, 8_000);
    return Boolean(result.result?.value);
  } catch {
    return false;
  }
}

function endpointFromUrl(rawUrl) {
  if (!rawUrl) return null;
  let decoded = rawUrl;
  try {
    decoded = decodeURIComponent(rawUrl);
  } catch {
    // Use raw URL if decoding fails.
  }
  return ENDPOINT_NEEDLES.find((needle) => decoded.includes(needle)) ?? null;
}

function buildSummary({ options, runId, startedAt, outPath, summaryPath, stats }) {
  const finishedAt = new Date();
  return {
    schema_version: 1,
    kind: 'x-skim-summary',
    run_id: runId,
    target_url: options.url,
    started_at: startedAt.toISOString(),
    finished_at: finishedAt.toISOString(),
    elapsed_seconds: Math.round((finishedAt.getTime() - startedAt.getTime()) / 1000),
    profile_dir: path.resolve(process.cwd(), options.profileDir),
    output_jsonl: outPath,
    summary_json: summaryPath,
    capture_options: {
      seconds: options.seconds,
      scrolls: options.scrolls,
      scroll_delay_ms: options.scrollDelayMs,
      scroll_factor: options.scrollFactor,
      seek_year: options.seekYear ?? null,
      seek_scroll_factor: options.seekScrollFactor,
      seek_delay_ms: options.seekDelayMs,
      metadata_only: options.metadataOnly,
      max_body_bytes: options.maxBodyBytes,
      manual: options.manual,
      blocked_by_default: {
        images: !options.allowImages,
        media: !options.allowMedia,
        fonts: !options.allowFonts,
        styles: !options.allowStyles,
      },
    },
    captured_response_count: stats.responseCount,
    endpoints: sortedObject(stats.endpoints),
    unique_candidate_tweet_ids: stats.candidateTweetIds.size,
    candidate_tweet_ids: [...stats.candidateTweetIds].sort(),
    unique_media_url_candidates: stats.mediaUrlCandidates.size,
    media_url_candidates_sample: [...stats.mediaUrlCandidates].sort().slice(0, 100),
    blocked_request_count: stats.blockedTotal,
    blocked_by_reason: sortedObject(stats.blockedByType),
    blocked_by_host_top: sortedObject(stats.blockedByHost, 30),
    retry_clicks: stats.retryClicks,
    show_more_clicks: stats.showMoreClicks,
    response_errors: stats.responseErrors,
  };
}

function sortedObject(map, limit = Infinity) {
  return Object.fromEntries(
    [...map.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, limit)
  );
}

function increment(map, key) {
  map.set(key, (map.get(key) ?? 0) + 1);
}

async function createTarget(port, chrome) {
  await waitForCdp(port, chrome);
  const response = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' });
  if (!response.ok) {
    throw new Error(`Could not create Chrome target: HTTP ${response.status}`);
  }
  const target = await response.json();
  if (!target.webSocketDebuggerUrl) {
    throw new Error('Chrome target did not expose a WebSocket debugger URL.');
  }
  return target;
}

async function waitForCdp(port, chrome) {
  const deadline = Date.now() + 20_000;
  let lastError = null;
  while (Date.now() < deadline) {
    if (chrome.exitCode !== null) {
      throw new Error(`Chrome exited before CDP opened.${browserDiagnostics(chrome)}`);
    }
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (response.ok) return;
    } catch (error) {
      lastError = error;
    }
    await wait(250);
  }
  throw new Error(
    `Chrome did not open CDP on port ${port}: ${lastError?.message ?? 'timeout'}${browserDiagnostics(chrome)}`
  );
}

function launchBrowser(browserPath, port, profileDir, options) {
  const args = [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profileDir}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-background-networking',
    '--disable-client-side-phishing-detection',
    '--disable-component-update',
    '--disable-default-apps',
    '--disable-domain-reliability',
    '--disable-dev-shm-usage',
    '--disable-extensions',
    '--disable-features=AutofillServerCommunication,InterestFeedContentSuggestions,MediaRouter,OptimizationHints,Translate',
    '--disable-gpu',
    '--disable-sync',
    '--mute-audio',
    '--no-sandbox',
    '--autoplay-policy=user-gesture-required',
    '--remote-allow-origins=*',
    '--window-size=1000,1200',
    'about:blank',
  ];
  if (options.headless) args.unshift('--headless=new');
  if (!options.allowImages) args.unshift('--blink-settings=imagesEnabled=false');

  const child = spawn(browserPath, args, {
    stdio: ['ignore', 'ignore', 'pipe'],
    windowsHide: true,
  });
  child.stderrText = '';
  child.stderr?.on('data', (chunk) => {
    child.stderrText = `${child.stderrText}${chunk.toString('utf8')}`.slice(-8000);
  });
  child.on('exit', (code, signal) => {
    if ((code || signal) && !child.intentionalClose) {
      console.warn(`Browser exited (${code ?? signal}).`);
    }
  });
  return child;
}

function launchPlainLoginBrowser(browserPath, profileDir, options) {
  const child = spawn(
    browserPath,
    [
      `--user-data-dir=${profileDir}`,
      '--no-first-run',
      '--no-default-browser-check',
      '--new-window',
      options.url,
    ],
    {
      detached: true,
      stdio: 'ignore',
      windowsHide: false,
    }
  );
  child.unref();
}

function stopBrowser(chrome) {
  if (chrome.exitCode !== null) return;
  chrome.intentionalClose = true;
  chrome.kill();
}

function browserDiagnostics(chrome) {
  const parts = [];
  if (chrome.exitCode !== null) parts.push(` exitCode=${chrome.exitCode}`);
  if (chrome.signalCode) parts.push(` signal=${chrome.signalCode}`);
  const stderr = chrome.stderrText?.trim();
  if (stderr) parts.push(`\nBrowser stderr:\n${stderr}`);
  return parts.length ? ` ${parts.join('')}` : '';
}

function findBrowser(explicitPath) {
  if (explicitPath) {
    assertExecutable(explicitPath);
    return explicitPath;
  }

  const candidates = browserCandidates();
  for (const candidate of candidates) {
    if (path.isAbsolute(candidate)) {
      try {
        assertExecutable(candidate);
        return candidate;
      } catch {
        continue;
      }
    }
    if (commandExists(candidate)) return candidate;
  }

  throw new Error(
    'Could not find Chrome or Edge. Pass --chrome-path "C:\\\\Program Files\\\\Google\\\\Chrome\\\\Application\\\\chrome.exe".'
  );
}

function browserCandidates() {
  const envCandidates = [process.env.CHROME_PATH, process.env.EDGE_PATH].filter(Boolean);
  if (process.platform === 'win32') {
    return [
      ...envCandidates,
      path.join(process.env.PROGRAMFILES ?? '', 'Google\\Chrome\\Application\\chrome.exe'),
      path.join(process.env['PROGRAMFILES(X86)'] ?? '', 'Google\\Chrome\\Application\\chrome.exe'),
      path.join(process.env.LOCALAPPDATA ?? '', 'Google\\Chrome\\Application\\chrome.exe'),
      path.join(process.env.PROGRAMFILES ?? '', 'Microsoft\\Edge\\Application\\msedge.exe'),
      path.join(process.env['PROGRAMFILES(X86)'] ?? '', 'Microsoft\\Edge\\Application\\msedge.exe'),
      'chrome',
      'chrome.exe',
      'msedge',
      'msedge.exe',
    ];
  }
  if (process.platform === 'darwin') {
    return [
      ...envCandidates,
      '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
      'google-chrome',
      'chromium',
      'microsoft-edge',
    ];
  }
  return [
    ...envCandidates,
    'google-chrome',
    'google-chrome-stable',
    'chromium',
    'chromium-browser',
    'microsoft-edge',
  ];
}

function assertExecutable(candidate) {
  accessSync(candidate, fsConstants.X_OK);
}

function commandExists(command) {
  const checker = process.platform === 'win32' ? 'where.exe' : 'which';
  const result = spawnSync(checker, [command], { stdio: 'ignore' });
  return result.status === 0;
}

async function findFreePort() {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      const port = typeof address === 'object' && address ? address.port : null;
      server.close(() => {
        if (port) resolve(port);
        else reject(new Error('Could not allocate a local port.'));
      });
    });
  });
}

function hostnameFor(rawUrl) {
  try {
    return new URL(rawUrl).hostname.toLowerCase();
  } catch {
    return '';
  }
}

function targetSlugFor(options) {
  if (options.handle) return safeSlug(options.handle);
  try {
    const url = new URL(options.url);
    return safeSlug(`${url.hostname}${url.pathname}`) || 'x-skim';
  } catch {
    return 'x-skim';
  }
}

function safeSlug(value) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 80);
}

function shortHash(value) {
  return createHash('sha1').update(value).digest('hex').slice(0, 8);
}

function formatStamp(date) {
  return date
    .toISOString()
    .replace(/[-:]/g, '')
    .replace(/\.\d+Z$/, 'Z');
}

function wait(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function waitForManualSession(client, chrome) {
  return new Promise((resolve) => {
    let resolved = false;
    const done = () => {
      if (resolved) return;
      resolved = true;
      resolve();
    };
    chrome.once('exit', done);
    client.onClosed(done);
  });
}

await main();
