/**
 * content.ts — content-script context (isolated world).
 *
 * The actual page-hook (which patches `fetch` / `XHR`) is declared as a
 * separate content script with `world: "MAIN"` in the manifest, so it runs
 * in the page world directly without us having to inject a <script> tag —
 * which X's nonce-based CSP would block.
 *
 * This file does three things:
 *
 *   1. Pings the background on load so the activity tail confirms the
 *      content script reached the X page (useful when diagnosing a silent
 *      "capture-now does nothing" failure).
 *   2. As a defensive fallback, also tries to inject `page-hook.js` via a
 *      script tag. Harmless on modern Firefox (the page-hook's TAG guard
 *      prevents double-init); covers the rare case where the MAIN-world
 *      content script entry isn't honored.
 *   3. Bridges `window.postMessage` from the page hook to the background:
 *      both the `*_READY` boot ping and the `IMM_ARCHIVE_CAPTURE` payload
 *      events.
 */

const TARGET = 'IMM_ARCHIVE_CAPTURE';
const READY = 'IMM_ARCHIVE_HOOK_READY';
const UNFOLD_SYNC_MS = 2_000;
const UNFOLD_SCAN_DEBOUNCE_MS = 250;
const LOW_BANDWIDTH_SCAN_DEBOUNCE_MS = 100;
const LOW_BANDWIDTH_RESCAN_MS = 1_500;
const LOW_BANDWIDTH_MEDIA_EVENTS = ['loadstart', 'loadedmetadata', 'play', 'playing'] as const;

interface UnfoldTargetsResponse {
  enabled?: boolean;
  coreHandles?: string[];
  relevantTweetIds?: string[];
}

let unfoldEnabled = false;
let unfoldCoreHandles = new Set<string>();
let unfoldRelevantTweetIds = new Set<string>();
let unfoldScanTimer: ReturnType<typeof setTimeout> | null = null;
const unfoldedControls = new WeakSet<Element>();
let lowBandwidthScrubberEnabled = false;
let lowBandwidthObserver: MutationObserver | null = null;
let lowBandwidthScanTimer: ReturnType<typeof setTimeout> | null = null;
let lowBandwidthRescanTimer: ReturnType<typeof setInterval> | null = null;

void browser.runtime
  .sendMessage({ type: 'content-alive', url: location.href })
  .catch((err: unknown) => {
    console.warn('[imm-archive] content-alive ping failed', err);
  });

function fallbackInject(): void {
  try {
    const src = browser.runtime.getURL('page-hook.js');
    const script = document.createElement('script');
    script.src = src;
    script.async = false;
    script.dataset.immArchive = '1';
    (document.head || document.documentElement).appendChild(script);
    script.onload = () => script.remove();
    script.onerror = () => {
      // CSP probably blocked this. Not fatal — the MAIN-world content script
      // declared in manifest.json is the primary path. Surface as a log so
      // we know what happened without lying about success.
      browser.runtime
        .sendMessage({
          type: 'log-content-event',
          level: 'warn',
          msg: 'inline page-hook injection blocked (CSP); relying on MAIN-world content script',
          url: location.href,
        })
        .catch(() => {});
    };
  } catch (err) {
    console.warn('[imm-archive] inline page-hook injection threw', err);
  }
}

fallbackInject();

function normalizeHandle(raw: string | null): string | null {
  if (!raw) return null;
  const cleaned = raw.trim().replace(/^@/, '').toLowerCase();
  return cleaned.length > 0 ? cleaned : null;
}

const STATUS_HOSTS = new Set(['x.com', 'twitter.com', 'mobile.twitter.com']);

function parseTweetUrl(href: string | null): { handle: string; id: string } | null {
  if (!href) return null;
  try {
    const u = new URL(href, location.href);
    if (!STATUS_HOSTS.has(u.hostname.toLowerCase())) return null;
    const parts = u.pathname.split('/').filter(Boolean);
    const statusIdx = parts.indexOf('status');
    if (statusIdx <= 0) return null;
    const handle = normalizeHandle(parts[statusIdx - 1] ?? null);
    const id = parts[statusIdx + 1] ?? null;
    if (!handle || !id || !/^\d+$/.test(id)) return null;
    return { handle, id };
  } catch {
    return null;
  }
}

function parseHandleUrl(href: string | null): string | null {
  if (!href) return null;
  try {
    const u = new URL(href, location.href);
    if (!STATUS_HOSTS.has(u.hostname.toLowerCase())) return null;
    const first = u.pathname.split('/').filter(Boolean)[0];
    if (!first || first === 'i' || first === 'intent' || first === 'search') return null;
    return normalizeHandle(first);
  } catch {
    return null;
  }
}

function isVisible(el: Element): boolean {
  const rect = el.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) return false;
  const style = window.getComputedStyle(el);
  return style.visibility !== 'hidden' && style.display !== 'none';
}

function nearestTweetContainer(el: Element): Element | null {
  return el.closest('article') ?? el.closest('div[data-testid="cellInnerDiv"]');
}

function isReplyToCore(container: Element): boolean {
  const text = (container.textContent || '').toLowerCase();
  if (!text.includes('replying to')) return false;
  for (const link of Array.from(container.querySelectorAll('a[href]'))) {
    const handle = parseHandleUrl(link.getAttribute('href'));
    if (handle && unfoldCoreHandles.has(handle)) return true;
  }
  return false;
}

function isCoreRelevant(container: Element): boolean {
  for (const link of Array.from(container.querySelectorAll('a[href]'))) {
    const tweet = parseTweetUrl(link.getAttribute('href'));
    if (!tweet) continue;
    if (unfoldCoreHandles.has(tweet.handle)) return true;
    if (unfoldRelevantTweetIds.has(tweet.id)) return true;
  }
  return isReplyToCore(container);
}

function scanAndUnfoldShowMore(): void {
  unfoldScanTimer = null;
  if (!unfoldEnabled || unfoldCoreHandles.size === 0) return;
  const selectors = [
    '[data-testid="tweet-text-show-more-link"]',
    'button[data-testid="tweet-text-show-more-link"]',
    'article [role="button"][tabindex="0"]',
    'div[data-testid="cellInnerDiv"] [role="button"][tabindex="0"]',
  ];
  const seen = new Set<Element>();
  let clicked = 0;
  for (const sel of selectors) {
    for (const el of Array.from(document.querySelectorAll(sel))) {
      if (seen.has(el) || unfoldedControls.has(el)) continue;
      seen.add(el);
      if ((el.textContent || '').trim().toLowerCase() !== 'show more') continue;
      if (!isVisible(el)) continue;
      const container = nearestTweetContainer(el);
      if (!container || !isCoreRelevant(container)) continue;
      unfoldedControls.add(el);
      try {
        (el as HTMLElement).click();
        clicked += 1;
      } catch {
        // Ignore stale or synthetic controls.
      }
    }
  }
  if (clicked > 0) {
    browser.runtime.sendMessage({ type: 'show-more-unfolded', count: clicked }).catch(() => {});
  }
}

function scheduleUnfoldScan(delay = UNFOLD_SCAN_DEBOUNCE_MS): void {
  if (unfoldScanTimer !== null) return;
  unfoldScanTimer = setTimeout(scanAndUnfoldShowMore, delay);
}

async function refreshUnfoldTargets(): Promise<void> {
  try {
    const response = await browser.runtime.sendMessage({
      type: 'get-unfold-targets',
    });
    const raw = response && typeof response === 'object' ? (response as UnfoldTargetsResponse) : {};
    unfoldEnabled = raw.enabled !== false;
    unfoldCoreHandles = new Set(
      (raw.coreHandles ?? []).map((h) => h.toLowerCase().replace(/^@/, ''))
    );
    unfoldRelevantTweetIds = new Set(raw.relevantTweetIds ?? []);
    scheduleUnfoldScan(0);
  } catch {
    unfoldEnabled = false;
  }
}

function installUnfoldObserver(): void {
  const root = document.documentElement || document.body;
  if (!root) {
    setTimeout(installUnfoldObserver, 100);
    return;
  }
  const observer = new MutationObserver(() => scheduleUnfoldScan());
  observer.observe(root, { childList: true, subtree: true });
  window.addEventListener('scroll', () => scheduleUnfoldScan(), { passive: true });
  void refreshUnfoldTargets();
  setInterval(() => {
    void refreshUnfoldTargets();
  }, UNFOLD_SYNC_MS);
}

installUnfoldObserver();

function lowBandwidthSettingFrom(raw: unknown): boolean {
  if (!raw || typeof raw !== 'object') return false;
  return (raw as { lowBandwidthBrowsing?: unknown }).lowBandwidthBrowsing === true;
}

function isScrubbableMedia(el: EventTarget | Element | null): el is HTMLMediaElement {
  return el instanceof HTMLVideoElement || el instanceof HTMLAudioElement;
}

function scrubMediaElement(el: HTMLMediaElement): void {
  try {
    el.pause();
  } catch {
    // Ignore stale media nodes.
  }
  try {
    el.autoplay = false;
    el.preload = 'none';
    el.removeAttribute('autoplay');
    el.removeAttribute('src');
    el.src = '';
    if ('srcObject' in el) el.srcObject = null;
    for (const source of Array.from(el.querySelectorAll('source[src]'))) {
      source.removeAttribute('src');
    }
    el.load();
  } catch {
    // X can replace nodes during scroll; best-effort scrubbing is enough.
  }
}

function scanLowBandwidthMedia(): void {
  lowBandwidthScanTimer = null;
  if (!lowBandwidthScrubberEnabled) return;
  for (const el of Array.from(document.querySelectorAll('video,audio'))) {
    if (isScrubbableMedia(el)) scrubMediaElement(el);
  }
}

function scheduleLowBandwidthScan(delay = LOW_BANDWIDTH_SCAN_DEBOUNCE_MS): void {
  if (!lowBandwidthScrubberEnabled || lowBandwidthScanTimer !== null) return;
  lowBandwidthScanTimer = setTimeout(scanLowBandwidthMedia, delay);
}

function onLowBandwidthMediaEvent(event: Event): void {
  if (!lowBandwidthScrubberEnabled) return;
  if (isScrubbableMedia(event.target)) scrubMediaElement(event.target);
}

function setLowBandwidthScrubber(on: boolean): void {
  if (lowBandwidthScrubberEnabled === on) return;
  lowBandwidthScrubberEnabled = on;

  if (on) {
    const root = document.documentElement || document.body;
    if (root) {
      lowBandwidthObserver = new MutationObserver(() => scheduleLowBandwidthScan());
      lowBandwidthObserver.observe(root, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ['src'],
      });
    }
    for (const eventName of LOW_BANDWIDTH_MEDIA_EVENTS) {
      document.addEventListener(eventName, onLowBandwidthMediaEvent, true);
    }
    lowBandwidthRescanTimer = setInterval(scanLowBandwidthMedia, LOW_BANDWIDTH_RESCAN_MS);
    scheduleLowBandwidthScan(0);
    return;
  }

  if (lowBandwidthObserver) {
    lowBandwidthObserver.disconnect();
    lowBandwidthObserver = null;
  }
  if (lowBandwidthScanTimer !== null) {
    clearTimeout(lowBandwidthScanTimer);
    lowBandwidthScanTimer = null;
  }
  if (lowBandwidthRescanTimer !== null) {
    clearInterval(lowBandwidthRescanTimer);
    lowBandwidthRescanTimer = null;
  }
  for (const eventName of LOW_BANDWIDTH_MEDIA_EVENTS) {
    document.removeEventListener(eventName, onLowBandwidthMediaEvent, true);
  }
}

async function refreshLowBandwidthScrubber(): Promise<void> {
  try {
    const result = await browser.storage.local.get('settings');
    setLowBandwidthScrubber(lowBandwidthSettingFrom(result.settings));
  } catch {
    setLowBandwidthScrubber(false);
  }
}

browser.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== 'local' || !changes.settings) return;
  setLowBandwidthScrubber(lowBandwidthSettingFrom(changes.settings.newValue));
});

void refreshLowBandwidthScrubber();

window.addEventListener('message', (event: MessageEvent) => {
  if (event.source !== window) return;
  const data = event.data as unknown;
  if (!data || typeof data !== 'object') return;
  const d = data as Record<string, unknown>;

  if (d.source === READY) {
    browser.runtime
      .sendMessage({
        type: 'page-hook-active',
        url: typeof d.url === 'string' ? d.url : location.href,
      })
      .catch(() => {});
    return;
  }

  if (d.source !== TARGET) return;
  const endpoint = typeof d.endpoint === 'string' ? d.endpoint : null;
  const url = typeof d.url === 'string' ? d.url : null;
  const pageUrl = typeof d.page_url === 'string' ? d.page_url : location.href;
  const response = d.response;
  if (!endpoint || !url || response === undefined) return;
  browser.runtime
    .sendMessage({ type: 'graphql-capture', endpoint, url, pageUrl, response })
    .catch((err) => {
      console.warn('[imm-archive] sendMessage failed', err);
    });
});
