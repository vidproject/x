/**
 * background.ts — extension service worker.
 *
 * Owns the capture pipeline: receives `graphql-capture` messages from the
 * content script, normalizes them, accumulates per-handle run buffers in
 * `browser.storage.local`, and commits them to the configured GitHub repo
 * via the Contents API.
 *
 * Service workers in MV3 may be evicted at any time, so all critical state
 * lives in storage (never module-scope). On every wake we re-derive what we
 * need; pending flushes are driven by `browser.alarms` rather than timers.
 */

import {
  AUTO_SCROLL_MAX_SEC,
  AUTO_SCROLL_MIN_SEC,
  FALLBACK_ACCOUNTS,
  FLUSH_ALARM_MINUTES,
  FLUSH_IDLE_MS,
  FLUSH_TWEET_THRESHOLD,
  PROFILE_ENDPOINTS,
  TWEET_ENDPOINTS,
  VERIFY_CONNECTION_INTERVAL_MS,
} from './lib/config.js';
import { GitHubClient, GitHubError, toBase64 } from './lib/github.js';
import { describeError, error as logErr, info, setBroadcaster, warn } from './lib/logger.js';
import { filterRelated, normalize } from './lib/normalize.js';
import { newRunId, shortRunId } from './lib/runids.js';
import {
  bumpCounter,
  clearActivity,
  clearAll,
  clearRunBuffer,
  dequeueMediaCrawl,
  dequeueRefetch,
  engagementSig,
  enqueueMediaCrawl,
  enqueueRefetch,
  getAccounts,
  getAccountsRefreshedAt,
  getActivity,
  getAutoScrollSession,
  getCommittedIndex,
  getConnection,
  getCounters,
  getMediaCrawlQueue,
  getMediaCrawlSession,
  getRefetchQueue,
  getRefetchSession,
  getRunBuffer,
  getRunBuffers,
  getSettings,
  isCommitted,
  mediaCrawlQueueTotal,
  nextMediaCrawlTarget,
  nextRefetchTarget,
  pruneCommittedIndex,
  purgeUnrelatedState,
  refetchQueueTotal,
  setAccounts,
  setAccountsRefreshedAt,
  setAutoScrollSession,
  setBufferedCount,
  setCommittedIndex,
  setConnection,
  setMediaCrawlSession,
  setRefetchSession,
  setRunBuffer,
  type RunBuffer,
  updateSettings,
} from './lib/storage.js';
import type {
  CanonicalTweet,
  CapturePayload,
  ConnectionState,
  ExtensionState,
  LogEvent,
  QuarantinePayload,
  RuntimeMessage,
  SeenPayload,
  Settings,
} from './lib/types.js';
import { parseAccountsYaml } from './lib/yaml.js';

const EXT_VERSION = browser.runtime.getManifest().version;

setBroadcaster((ev: LogEvent) => {
  browser.runtime.sendMessage({ type: 'log-event', event: ev }).catch(() => {});
});

// --- Wake handlers --------------------------------------------------------

browser.runtime.onInstalled.addListener(async () => {
  await info('extension installed', { version: EXT_VERSION });
  await onWake('install');
});

browser.runtime.onStartup.addListener(() => {
  void onWake('startup');
});

// Force at least one wake to register alarms / verify connection.
void onWake('module-load');

async function onWake(reason: string): Promise<void> {
  try {
    await ensureAlarms();
    await ensureAutoScrollAlarm();
    await flushOrphanedBuffersIfStale();
    await reinjectIntoOpenTabs();
    // Drop dedup-index entries older than 30 days so the store doesn't
    // grow unbounded over months of capture sessions.
    const pruned = await pruneCommittedIndex(30 * 24 * 60 * 60 * 1000);
    if (pruned > 0) await info('pruned committed index', { pruned });
    const settings = await getSettings();
    if (settings.pat && settings.owner && settings.repo) {
      await verifyConnection(false);
      await refreshAccountsList(false);
    } else {
      await setConnection({
        status: 'not-configured',
        login: null,
        checkedAt: new Date().toISOString(),
        error: null,
        defaultBranch: null,
        configuredBranchExists: null,
        rateLimitResetAt: null,
      });
      await info('extension awake; settings incomplete', { reason });
    }
  } catch (err) {
    await logErr('wake failed', { reason, ...describeError(err) });
  }
}

/**
 * Re-inject the MAIN-world page-hook + isolated-world content script into
 * every already-open x.com / twitter.com tab.
 *
 * Manifest content_scripts only inject on fresh navigation (run_at:
 * document_start). When the user reloads the extension while X tabs are
 * open, those tabs keep their content scripts but never get a new page-hook
 * — so fetch/XHR stays unpatched and captures silently fail. Doing this on
 * every wake is idempotent (page-hook has a TAG guard) and cheap.
 */
async function reinjectIntoOpenTabs(): Promise<void> {
  let tabs: browser.tabs.Tab[];
  try {
    tabs = await browser.tabs.query({ url: ['https://x.com/*', 'https://twitter.com/*'] });
  } catch (err) {
    await warn('could not query open tabs', describeError(err));
    return;
  }
  if (tabs.length === 0) return;
  for (const tab of tabs) {
    if (typeof tab.id !== 'number') continue;
    try {
      await browser.scripting.executeScript({
        target: { tabId: tab.id },
        files: ['page-hook.js'],
        // Firefox 128+ supports the MAIN execution world via this API, but
        // the @types/firefox-webext-browser package we're on still only
        // declares 'ISOLATED'. Cast through the literal union.
        world: 'MAIN' as 'ISOLATED',
      });
      await browser.scripting.executeScript({
        target: { tabId: tab.id },
        files: ['content.js'],
      });
      await info('re-injected scripts into open tab', {
        tabId: tab.id,
        url: shortenUrl(tab.url ?? ''),
      });
    } catch (err) {
      // Some tabs (about: pages, discarded tabs, mid-navigation) reject
      // executeScript. That's fine — we'll catch them on the next wake or
      // when the user navigates.
      await warn('re-inject failed for tab; continuing', {
        tabId: tab.id,
        url: shortenUrl(tab.url ?? ''),
        ...describeError(err),
      });
    }
  }
}

async function ensureAlarms(): Promise<void> {
  await browser.alarms.clear('flush-sweep');
  await browser.alarms.clear('verify-connection');
  await browser.alarms.create('flush-sweep', { periodInMinutes: FLUSH_ALARM_MINUTES });
  await browser.alarms.create('verify-connection', {
    periodInMinutes: VERIFY_CONNECTION_INTERVAL_MS / 60_000,
  });
}

// Firefox MV3 alarms have a 30s minimum period, but we want sub-minute
// scrolling. The auto-scroll loop therefore runs on an in-SW interval.
// `autoScrollSession` in storage is the source of truth for whether the loop
// is meant to be running — the timer is just the local execution arm.
let autoScrollTimer: ReturnType<typeof setInterval> | null = null;

// Wait-for-ingest timeout: if a refetch / media-crawl tab navigation doesn't
// produce an ingested capture within this many ms, advance anyway so we
// don't deadlock on deleted / 404 / paywalled tweets.
const INGEST_WAIT_MS = 25_000;

async function ensureAutoScrollAlarm(): Promise<void> {
  // Was the loop running before the SW eviction? Re-arm if so.
  const sess = await getAutoScrollSession();
  const s = await getSettings();
  if (sess !== null && s.enabled !== false) {
    armAutoScrollTimer(s.autoScrollIntervalSec);
  } else {
    clearAutoScrollTimer();
  }
}

function clearAutoScrollTimer(): void {
  if (autoScrollTimer !== null) {
    clearInterval(autoScrollTimer);
    autoScrollTimer = null;
  }
}

function armAutoScrollTimer(intervalSec: number): void {
  clearAutoScrollTimer();
  const clamped = Math.min(
    AUTO_SCROLL_MAX_SEC,
    Math.max(AUTO_SCROLL_MIN_SEC, Math.round(intervalSec))
  );
  autoScrollTimer = setInterval(() => {
    void autoScrollTick().catch((err) => {
      void warn('auto-scroll tick failed', describeError(err));
    });
  }, clamped * 1000);
}

async function startAutoScrollLoop(): Promise<void> {
  const s = await getSettings();
  await setAutoScrollSession({
    startedAt: new Date().toISOString(),
    scrollCount: 0,
    ingestedCount: 0,
    expandedCount: 0,
  });
  armAutoScrollTimer(s.autoScrollIntervalSec);
  await info('auto-scroll loop started', { interval_sec: s.autoScrollIntervalSec });
  // Fire one immediate tick so the user sees motion right away.
  void autoScrollTick();
  await broadcastState();
}

async function cancelAutoScrollLoop(): Promise<void> {
  clearAutoScrollTimer();
  const sess = await getAutoScrollSession();
  await setAutoScrollSession(null);
  await info('auto-scroll loop cancelled', {
    scrolls: sess?.scrollCount ?? 0,
    ingested: sess?.ingestedCount ?? 0,
    expanded: sess?.expandedCount ?? 0,
  });
  await broadcastState();
}

async function autoScrollTick(): Promise<void> {
  let tabs: browser.tabs.Tab[];
  try {
    tabs = await browser.tabs.query({ url: ['https://x.com/*', 'https://twitter.com/*'] });
  } catch (err) {
    await warn('auto-scroll: tab query failed', describeError(err));
    return;
  }
  if (tabs.length === 0) return;
  let scrollCount = 0;
  let expandedCount = 0;
  for (const tab of tabs) {
    if (typeof tab.id !== 'number') continue;
    if (tab.discarded) continue;
    try {
      const results = (await browser.scripting.executeScript({
        target: { tabId: tab.id },
        world: 'MAIN' as 'ISOLATED',
        // Cast: the @types/firefox-webext-browser type signature insists
        // `func` returns void, but the API actually surfaces the return
        // value via `result[0].result`. We use that for the expand count.
        func: (() => {
          // 1. Click any visible "Show more" links in long-tweet bodies so X
          //    inlines the note_tweet body and our page-hook captures the
          //    full text without the refetch detour. Try several selectors
          //    because X rotates the testid label semi-regularly.
          const SHOW_MORE_SELECTORS = [
            '[data-testid="tweet-text-show-more-link"]',
            'button[data-testid="tweet-text-show-more-link"]',
            'div[data-testid="cellInnerDiv"] [role="button"][tabindex="0"]',
          ];
          const clicked = new Set<Element>();
          let expanded = 0;
          for (const sel of SHOW_MORE_SELECTORS) {
            for (const el of Array.from(document.querySelectorAll(sel))) {
              if (clicked.has(el)) continue;
              // Confirm it's actually the show-more affordance by text.
              const t = (el.textContent || '').trim().toLowerCase();
              if (t !== 'show more' && t !== 'show this thread') continue;
              const rect = el.getBoundingClientRect();
              if (rect.width === 0 || rect.height === 0) continue;
              clicked.add(el);
              try {
                (el as HTMLElement).click();
                expanded += 1;
              } catch {
                // ignore
              }
            }
          }
          // 2. Scroll the page. Dispatch End key first since many X surfaces
          //    (lists, search, replies) bind pagination triggers to it via
          //    React handlers; then explicitly scroll the document.
          const opts: KeyboardEventInit = {
            key: 'End',
            code: 'End',
            keyCode: 35,
            which: 35,
            bubbles: true,
            cancelable: true,
          };
          const focus = (document.activeElement as HTMLElement | null) ?? document.body;
          focus.dispatchEvent(new KeyboardEvent('keydown', opts));
          focus.dispatchEvent(new KeyboardEvent('keyup', opts));
          window.scrollTo({
            top: document.documentElement.scrollHeight,
            behavior: 'auto',
          });
          return { expanded };
        }) as () => void,
      })) as Array<{ result?: unknown }>;
      scrollCount += 1;
      const r = results[0]?.result as { expanded?: number } | undefined;
      if (r && typeof r.expanded === 'number') expandedCount += r.expanded;
    } catch {
      // Tabs that disallow scripting (about:, discarded, mid-navigation)
      // just get skipped — they'll be eligible on a later tick.
    }
  }
  if (scrollCount > 0) {
    const sess = await getAutoScrollSession();
    if (sess !== null) {
      sess.scrollCount += scrollCount;
      sess.expandedCount += expandedCount;
      await setAutoScrollSession(sess);
      // No broadcast on every tick — the 15s sidebar refresh picks it up.
    }
  }
}

browser.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'flush-sweep') {
    void flushIdleBuffers();
  } else if (alarm.name === 'verify-connection') {
    void verifyConnection(false);
  }
  // refetch / media-crawl previously ran via alarms; they now use
  // setInterval to escape the 30s alarm floor. Leave the names listed
  // nowhere so any orphaned alarms (from older builds) just no-op.
});

// --- Toolbar action: toggle the sidebar -----------------------------------

if (browser.action?.onClicked) {
  browser.action.onClicked.addListener(async () => {
    try {
      await browser.sidebarAction.toggle();
    } catch (err) {
      // Fallback: open options if sidebar can't be toggled.
      await warn('sidebar toggle failed; opening options', describeError(err));
      await browser.runtime.openOptionsPage();
    }
  });
}

// --- Runtime message dispatch --------------------------------------------

browser.runtime.onMessage.addListener((msg: unknown, _sender) => {
  if (!isRuntimeMessage(msg)) return undefined;
  return handleMessage(msg).catch((err) => {
    void logErr('message handler failed', { type: msg.type, ...describeError(err) });
    throw err;
  });
});

function isRuntimeMessage(m: unknown): m is RuntimeMessage {
  return !!m && typeof m === 'object' && typeof (m as { type?: unknown }).type === 'string';
}

/** Messages that drive the capture pipeline. When the master switch is OFF
 * we ignore these but still respond to status / configuration queries. */
const CAPTURE_PIPELINE_MESSAGES = new Set<RuntimeMessage['type']>([
  'graphql-capture',
  'capture-now',
  'capture-all',
  'capture-this-page',
  'flush-all',
  'flush-handle',
  'start-refetch',
  'start-media-crawl',
  'start-auto-scroll',
]);

async function isEnabled(): Promise<boolean> {
  const s = await getSettings();
  return s.enabled !== false;
}

async function handleMessage(msg: RuntimeMessage): Promise<unknown> {
  // Master switch: when the user has paused the extension we still need
  // the meta-channels (get-state, toggle-enabled, open-options, …) so the
  // sidebar stays functional, but anything that would actually capture,
  // commit, or drive a tab is a no-op.
  if (!(await isEnabled()) && CAPTURE_PIPELINE_MESSAGES.has(msg.type)) {
    return { ok: true, paused: true };
  }
  switch (msg.type) {
    case 'graphql-capture':
      await onGraphqlCapture(msg.endpoint, msg.url, msg.response);
      return { ok: true };
    case 'content-alive':
      await info('content script alive on page', { url: shortenUrl(msg.url) });
      return { ok: true };
    case 'page-hook-active':
      await info('page hook patched fetch/XHR', { url: shortenUrl(msg.url) });
      return { ok: true };
    case 'log-content-event':
      if (msg.level === 'warn') {
        await warn(msg.msg, { url: shortenUrl(msg.url) });
      } else if (msg.level === 'error') {
        await logErr(msg.msg, { url: shortenUrl(msg.url) });
      } else {
        await info(msg.msg, { url: shortenUrl(msg.url) });
      }
      return { ok: true };
    case 'get-state':
      return buildState();
    case 'capture-now':
      await captureNow(msg.handle);
      return buildState();
    case 'capture-all':
      await captureAll();
      return buildState();
    case 'capture-this-page':
      await captureThisPage();
      return buildState();
    case 'flush-all':
      await flushAll('user');
      return buildState();
    case 'flush-handle':
      await flushHandle(msg.handle, 'user');
      return buildState();
    case 'toggle-auto-capture':
      await updateSettings({ autoCapture: msg.on });
      await info('auto-capture toggled', { on: msg.on });
      return buildState();
    case 'toggle-enabled': {
      const s = await updateSettings({ enabled: msg.on });
      if (!msg.on) {
        // Pausing stops all in-flight loops so the user gets immediate
        // quiet, not a "one more tick" surprise.
        clearAutoScrollTimer();
        stopRefetchInterval();
        stopMediaCrawlInterval();
        await browser.alarms.clear('refetch-tick'); // legacy cleanup
        await browser.alarms.clear('media-crawl-tick');
      } else {
        // Resuming re-arms any loops whose session is still set.
        const sess = await getAutoScrollSession();
        if (sess !== null) armAutoScrollTimer(s.autoScrollIntervalSec);
      }
      await info('extension master switch toggled', { on: msg.on });
      return buildState();
    }
    case 'start-auto-scroll':
      await startAutoScrollLoop();
      return buildState();
    case 'cancel-auto-scroll':
      await cancelAutoScrollLoop();
      return buildState();
    case 'set-auto-scroll-interval': {
      const seconds = Math.min(
        AUTO_SCROLL_MAX_SEC,
        Math.max(AUTO_SCROLL_MIN_SEC, Math.round(msg.seconds))
      );
      await updateSettings({ autoScrollIntervalSec: seconds });
      // Re-arm any active loops with the new interval.
      const sess = await getAutoScrollSession();
      if (sess !== null) armAutoScrollTimer(seconds);
      if (refetchIntervalHandle !== null) startRefetchInterval(seconds);
      if (mediaCrawlIntervalHandle !== null) startMediaCrawlInterval(seconds);
      return buildState();
    }
    case 'start-refetch':
      await startRefetchLoop();
      return buildState();
    case 'cancel-refetch':
      await cancelRefetchLoop();
      return buildState();
    case 'start-media-crawl':
      await startMediaCrawlLoop();
      return buildState();
    case 'cancel-media-crawl':
      await cancelMediaCrawlLoop();
      return buildState();
    case 'purge-unrelated': {
      const accs = await getAccounts();
      const tracked = new Set(accs.map((a) => a.handle.toLowerCase()));
      const summary = await purgeUnrelatedState(tracked);
      await info('purged unrelated state', summary);
      return buildState();
    }
    case 'refresh-accounts':
      await refreshAccountsList(true);
      return buildState();
    case 'verify-connection':
      await verifyConnection(true);
      return buildState();
    case 'clear-activity':
      await clearActivity();
      return { ok: true };
    case 'open-options':
      await browser.runtime.openOptionsPage();
      return { ok: true };
    case 'open-viewer': {
      const s = await getSettings();
      const url = viewerUrl(s);
      await browser.tabs.create({ url });
      return { ok: true };
    }
    default:
      return { ok: false, error: 'unknown message type' };
  }
}

// --- Capture pipeline -----------------------------------------------------

async function onGraphqlCapture(endpoint: string, url: string, response: unknown): Promise<void> {
  if (PROFILE_ENDPOINTS.has(endpoint)) return; // not tweet-bearing
  if (!TWEET_ENDPOINTS.has(endpoint)) return;

  const accounts = await getAccounts();
  // We deliberately do NOT short-circuit when the PAT is missing. The
  // normalize step is cheap, the buffer survives service-worker eviction,
  // and once the PAT is set the next alarm or user-triggered flush commits
  // everything we've seen. Dropping captures here was hiding the user's
  // first browsing session entirely.

  // Two-stage filter:
  //   1. Build EVERY canonical tweet we can find in the response (no handle
  //      filter at the normalizer level — we still want quoted/RT'd parents
  //      that appear as sibling nodes).
  //   2. Apply `filterRelated` post-hoc to drop anything that has no
  //      relationship to a tracked account. The old behaviour ("empty
  //      allowed-set on user-page endpoints, full filter on home/search")
  //      was way too permissive on the Replies tab: every random replier's
  //      reply landed in a per-handle buffer keyed by their own handle.
  const tracked = new Set(accounts.map((a) => a.handle.toLowerCase()));
  const capturedAt = new Date().toISOString();

  let normalized;
  try {
    normalized = normalize(response, {
      capturedAt,
      runId: 'pending',
      endpoint,
      allowedHandles: new Set<string>(),
    });
  } catch (err) {
    await quarantine('normalize-threw', endpoint, url, response, err);
    return;
  }
  // Drop unrelated tweets. The filter is endpoint-aware:
  //   - On the home / search timelines we'd already been filtering to
  //     tracked authors only — keep that behaviour but go through the same
  //     `filterRelated` helper so the rules are uniform.
  //   - On user-page endpoints we now also drop anything that isn't either
  //     authored-by, mentioning, replying-to, or referenced-by a tracked
  //     account.
  const beforeFilter = normalized.tweets.length;
  normalized.tweets = filterRelated(normalized.tweets, tracked);
  const droppedUnrelated = beforeFilter - normalized.tweets.length;
  if (droppedUnrelated > 0) {
    await info('dropped unrelated tweets', {
      endpoint,
      dropped: droppedUnrelated,
      kept: normalized.tweets.length,
    });
  }

  // Diagnostic: every tweet-endpoint payload gets a line in the activity
  // tail with what we saw vs kept. When observed=0 we also emit a shape
  // probe (key paths + typenames) so we can tell whether X returned an
  // empty envelope, a login-wall response, or a new shape we don't know how
  // to walk yet.
  if (normalized.observed_ids.length === 0) {
    await info('graphql payload empty — shape probe', {
      endpoint,
      typenames: probeTypenames(response).slice(0, 30),
      tweet_keys: probeFirstTypeShape(response, 'Tweet'),
      tweet_core_keys: probeTypedNested(response, 'Tweet', ['core']),
      tweet_legacy_keys: probeTypedNested(response, 'Tweet', ['legacy']),
      user_results_result_keys: probeTypedNested(response, 'Tweet', [
        'core',
        'user_results',
        'result',
      ]),
      user_results_result_core_keys: probeTypedNested(response, 'Tweet', [
        'core',
        'user_results',
        'result',
        'core',
      ]),
      user_results_result_legacy_keys: probeTypedNested(response, 'Tweet', [
        'core',
        'user_results',
        'result',
        'legacy',
      ]),
    });
  } else {
    await info('graphql payload seen', {
      endpoint,
      observed: normalized.observed_ids.length,
      kept: normalized.tweets.length,
    });
  }

  if (normalized.tweets.length === 0) return;

  // Refetch queue housekeeping — runs BEFORE the dedup short-circuit. A
  // refetch capture comes back with the same engagement counts (we're
  // only after the full text), so the dedup below would silently drop
  // it and the queue would never drain. Hoist the enqueue/dequeue here
  // so the loop reliably advances even when no commit happens.
  //
  // Also: if either loop's inflight target appears here, mark the loop
  // as "ingested" so the next tick can advance. The wait-for-ingest
  // guard otherwise blocks until the navigation-timeout fires.
  const refetchSess = await getRefetchSession();
  const mediaCrawlSess = await getMediaCrawlSession();
  for (const t of normalized.tweets) {
    if (t.is_truncated) {
      await enqueueRefetch(t.account_handle, t.tweet_id);
    } else {
      await dequeueRefetch(t.account_handle, t.tweet_id);
    }
    // Successfully built tweets are no longer "partial" — drop from the
    // media-crawl queue regardless of which handle we'd hinted earlier.
    await dequeueMediaCrawl(t.tweet_id);
    if (refetchSess?.inflight?.tweetId === t.tweet_id) {
      refetchSess.inflight = null;
      refetchSess.processed += 1;
      await setRefetchSession(refetchSess);
    }
    if (mediaCrawlSess?.inflight?.tweetId === t.tweet_id) {
      mediaCrawlSess.inflight = null;
      mediaCrawlSess.processed += 1;
      await setMediaCrawlSession(mediaCrawlSess);
    }
  }

  // Media-crawl queue: tweet-shaped nodes the walker saw but couldn't turn
  // into a full canonical tweet. Enqueue only IDs we haven't already
  // committed (user-requested dedup against the archive).
  for (const partial of normalized.partial_ids) {
    if (await isCommitted(partial.tweet_id)) continue;
    await enqueueMediaCrawl(partial.hint_handle, partial.tweet_id);
  }
  if (normalized.partial_ids.length > 0) {
    await info('media-crawl: enqueued partial captures', {
      endpoint,
      partial: normalized.partial_ids.length,
      queue_total: await mediaCrawlQueueTotal(),
    });
  }

  // Cross-session dedup: drop tweets we've already committed with identical
  // engagement counts. Likes/RTs/replies/quotes still trigger a recapture
  // so engagement_history grows over time. view_count is intentionally
  // excluded — it churns too fast to be useful as a freshness signal.
  // `is_truncated` is part of the signature so a refetch that flips
  // truncated→full is treated as a material change and gets committed.
  const committedIdx = await getCommittedIndex();
  let dedupedSkipped = 0;
  const liveTweets: typeof normalized.tweets = [];
  for (const t of normalized.tweets) {
    const prev = committedIdx[t.account_handle]?.[t.tweet_id];
    const sig = engagementSig(
      t.like_count,
      t.retweet_count,
      t.reply_count,
      t.quote_count,
      t.is_truncated
    );
    if (prev && prev.sig === sig) {
      dedupedSkipped += 1;
      continue;
    }
    liveTweets.push(t);
  }
  if (dedupedSkipped > 0) {
    await info('dedup: skipped unchanged tweets', {
      endpoint,
      skipped: dedupedSkipped,
      remaining: liveTweets.length,
    });
  }
  if (liveTweets.length === 0) return;

  // Group surviving tweets by author handle.
  const byHandle = new Map<string, CanonicalTweet[]>();
  for (const t of liveTweets) {
    const arr = byHandle.get(t.account_handle) ?? [];
    arr.push(t);
    byHandle.set(t.account_handle, arr);
  }

  for (const [handle, tweets] of byHandle) {
    const buf = await ensureRunBuffer(handle, capturedAt, url, endpoint);
    let added = 0;
    for (const t of tweets) {
      const existing = buf.tweets_by_id[t.tweet_id];
      if (existing) {
        // Preserve first_captured_at, append engagement snapshot, refresh
        // last_seen_at + counts (the latest scrape wins for counts).
        existing.last_seen_at = capturedAt;
        existing.like_count = t.like_count;
        existing.retweet_count = t.retweet_count;
        existing.reply_count = t.reply_count;
        existing.quote_count = t.quote_count;
        existing.view_count = t.view_count;
        existing.bookmark_count = t.bookmark_count;
        const last = existing.engagement_history[existing.engagement_history.length - 1];
        const snap = t.engagement_history[0];
        if (snap && (!last || last.captured_at !== snap.captured_at)) {
          existing.engagement_history.push(snap);
        }
      } else {
        const stamped: CanonicalTweet = { ...t, capture_run_id: buf.run_id };
        buf.tweets_by_id[t.tweet_id] = stamped;
        added += 1;
      }
      if (!buf.tweet_ids_observed.includes(t.tweet_id)) {
        buf.tweet_ids_observed.push(t.tweet_id);
      }
    }
    if (!buf.endpoints_seen.includes(endpoint)) buf.endpoints_seen.push(endpoint);
    buf.last_capture_at = capturedAt;
    await setRunBuffer(handle, buf);
    await setBufferedCount(handle, Object.keys(buf.tweets_by_id).length);
    if (added > 0) {
      await info('captured tweets', {
        handle,
        endpoint,
        added,
        total: Object.keys(buf.tweets_by_id).length,
      });
      // Bump the auto-scroll progress so the user sees "X ingested" tick up
      // in real time. We count actually-new tweets, not duplicates.
      const asSess = await getAutoScrollSession();
      if (asSess !== null) {
        asSess.ingestedCount += added;
        await setAutoScrollSession(asSess);
      }
    }
    if (Object.keys(buf.tweets_by_id).length >= FLUSH_TWEET_THRESHOLD) {
      await flushHandle(handle, 'threshold');
    }
  }
  await broadcastState();
}

async function ensureRunBuffer(
  handle: string,
  ts: string,
  sourceUrl: string,
  endpoint: string
): Promise<RunBuffer> {
  const cur = await getRunBuffer(handle);
  if (cur) return cur;
  const buf: RunBuffer = {
    run_id: newRunId(),
    account_handle: handle,
    started_at: ts,
    last_capture_at: ts,
    tweets_by_id: {},
    tweet_ids_observed: [],
    endpoints_seen: [endpoint],
    source_url: sourceUrl,
  };
  await setRunBuffer(handle, buf);
  await info('run started', { handle, run: shortRunId(buf.run_id) });
  return buf;
}

// --- Flush logic ----------------------------------------------------------

async function flushIdleBuffers(): Promise<void> {
  const all = await getRunBuffers();
  const now = Date.now();
  for (const [handle, buf] of Object.entries(all)) {
    const last = Date.parse(buf.last_capture_at);
    if (Number.isFinite(last) && now - last >= FLUSH_IDLE_MS) {
      await flushHandle(handle, 'idle');
    }
  }
}

async function flushOrphanedBuffersIfStale(): Promise<void> {
  // On worker wake, any buffer older than the idle threshold gets a chance to
  // commit immediately — useful when the worker was evicted before idle-flush.
  // If the PAT/owner/repo aren't set we'd just emit "flush deferred" for every
  // single handle in storage (the diagnostic showed this firing 300+ times in
  // a row when the extension woke before settings load), so short-circuit
  // here instead.
  const settings = await getSettings();
  if (!settings.pat || !settings.owner || !settings.repo) return;
  const all = await getRunBuffers();
  const now = Date.now();
  for (const [handle, buf] of Object.entries(all)) {
    const last = Date.parse(buf.last_capture_at);
    if (Number.isFinite(last) && now - last >= FLUSH_IDLE_MS) {
      await flushHandle(handle, 'wake');
    }
  }
}

async function flushAll(reason: string): Promise<void> {
  const all = await getRunBuffers();
  for (const handle of Object.keys(all)) {
    await flushHandle(handle, reason);
  }
}

async function flushHandle(handle: string, reason: string): Promise<void> {
  const buf = await getRunBuffer(handle);
  if (!buf) return;
  const tweets = Object.values(buf.tweets_by_id);
  if (tweets.length === 0) {
    await clearRunBuffer(handle);
    return;
  }
  const settings = await getSettings();
  if (!settings.pat || !settings.owner || !settings.repo) {
    await warn('flush deferred: settings incomplete', { handle });
    return;
  }
  const client = new GitHubClient(settings);
  const ts = isoCompact(buf.started_at);
  const day = buf.started_at.slice(0, 10);
  const rawPath = `raw/${handle}/${ts}-${shortRunId(buf.run_id)}.json`;
  const seenPath = `seen/${handle}/${ts}-${shortRunId(buf.run_id)}.json`;

  const capture: CapturePayload = {
    schema_version: 1,
    capture_run_id: buf.run_id,
    account_handle: handle,
    captured_at: buf.last_capture_at,
    endpoint: buf.endpoints_seen.join(','),
    user_agent: navigator.userAgent,
    source_url: buf.source_url,
    tweets,
  };
  const seen: SeenPayload = {
    schema_version: 1,
    capture_run_id: buf.run_id,
    account_handle: handle,
    captured_at: buf.last_capture_at,
    tweet_ids_observed: buf.tweet_ids_observed,
  };

  const commitMsg = `capture: ${handle} ${day} ${tweets.length} tweet${tweets.length === 1 ? '' : 's'} (run ${shortRunId(buf.run_id)})`;

  try {
    await client.putFile({
      path: rawPath,
      contentBase64: toBase64(JSON.stringify(capture, null, 2) + '\n'),
      message: commitMsg,
    });
    await client.putFile({
      path: seenPath,
      contentBase64: toBase64(JSON.stringify(seen, null, 2) + '\n'),
      message: `seen: ${handle} ${day} ${seen.tweet_ids_observed.length} ids (run ${shortRunId(buf.run_id)})`,
    });
    await clearRunBuffer(handle);
    {
      // Counter bump: actually INCREMENT todayCount and totalCommitted by
      // the number of tweets we just persisted (the previous code read the
      // existing values and wrote them back, leaving the counter pegged at
      // zero).
      const prev = (await getCounters())[handle];
      const today = new Date().toISOString().slice(0, 10);
      const carriedToday = prev && prev.todayDate === today ? prev.todayCount : 0;
      await bumpCounter(handle, {
        todayCount: carriedToday + tweets.length,
        todayDate: today,
        lastCaptureAt: buf.last_capture_at,
        totalCommitted: (prev?.totalCommitted ?? 0) + tweets.length,
        bufferedCount: 0,
      });
    }
    // Record commits in the dedup index so we don't re-commit them next
    // browse with unchanged engagement.
    {
      const idx = await getCommittedIndex();
      const inner = idx[handle] ?? {};
      const nowIso = new Date().toISOString();
      for (const t of tweets) {
        inner[t.tweet_id] = {
          ts: nowIso,
          sig: engagementSig(
            t.like_count,
            t.retweet_count,
            t.reply_count,
            t.quote_count,
            t.is_truncated
          ),
        };
      }
      idx[handle] = inner;
      await setCommittedIndex(idx);
    }
    await info('flush committed', {
      handle,
      reason,
      tweets: tweets.length,
      run: shortRunId(buf.run_id),
      path: rawPath,
    });
    {
      const prev = await getConnection();
      await setConnection({
        status: 'ok',
        login: prev.login,
        checkedAt: new Date().toISOString(),
        error: null,
        defaultBranch: prev.defaultBranch,
        configuredBranchExists: prev.configuredBranchExists,
        rateLimitResetAt: null,
      });
    }
  } catch (err) {
    if (err instanceof GitHubError) {
      const conn = await getConnection();
      const status: ConnectionState['status'] =
        err.category === 'auth'
          ? 'auth-error'
          : err.category === 'rate-limit'
            ? 'rate-limited'
            : err.category === 'network'
              ? 'network-error'
              : conn.status;
      await setConnection({
        status,
        login: conn.login,
        checkedAt: new Date().toISOString(),
        error: err.message,
        defaultBranch: conn.defaultBranch,
        configuredBranchExists: conn.configuredBranchExists,
        rateLimitResetAt:
          err.category === 'rate-limit' ? err.rateLimitResetAt : conn.rateLimitResetAt,
      });
      await logErr('flush failed', {
        handle,
        reason,
        category: err.category,
        status: err.status,
        message: err.message,
        rateLimitResetAt: err.rateLimitResetAt,
      });
    } else {
      await logErr('flush failed', { handle, reason, ...describeError(err) });
    }
    // Leave buffer intact for retry.
  } finally {
    await broadcastState();
  }
}

// --- Capture-now / capture-all -------------------------------------------

async function captureNow(handle: string): Promise<void> {
  // Land on the Replies tab so X fetches via UserTweetsAndReplies — that
  // endpoint returns both top-level posts and replies, which is the superset
  // we want for the archive. The plain `/<handle>` URL hits UserTweets,
  // which omits replies. The page-hook intercepts either endpoint, but
  // forcing the broader tab on user-initiated captures is the right default.
  const targetUrl = `https://x.com/${handle}/with_replies`;
  await info('capture-now requested', { handle, tab: 'with_replies' });
  if (!(await getRunBuffer(handle))) {
    const now = new Date().toISOString();
    await setRunBuffer(handle, {
      run_id: newRunId(),
      account_handle: handle,
      started_at: now,
      last_capture_at: now,
      tweets_by_id: {},
      tweet_ids_observed: [],
      endpoints_seen: [],
      source_url: targetUrl,
    });
  }
  await browser.tabs.create({ url: targetUrl, active: true });
}

async function captureAll(): Promise<void> {
  const accounts = await getAccounts();
  await info('capture-all requested', { count: accounts.length });
  for (const a of accounts) {
    await captureNow(a.handle);
  }
}

/**
 * Capture whatever the user is currently looking at: ensures page-hook is
 * patched into the active tab, then nudges the page with a programmatic
 * scroll so X fires another batch of GraphQL requests we can intercept.
 *
 * Less disruptive than `Capture now` (no new tab, no navigation) and works
 * for arbitrary X URLs (specific tweet threads, search results, lists)
 * that don't map cleanly to one of the configured handles.
 */
async function captureThisPage(): Promise<void> {
  let tabs: browser.tabs.Tab[];
  try {
    tabs = await browser.tabs.query({ active: true, currentWindow: true });
  } catch (err) {
    await warn('capture-this-page: could not query active tab', describeError(err));
    return;
  }
  const tab = tabs[0];
  if (!tab || typeof tab.id !== 'number' || !tab.url) {
    await warn('capture-this-page: no active tab');
    return;
  }
  if (!/^https:\/\/(x|twitter)\.com\//.test(tab.url)) {
    await warn('capture-this-page: active tab is not on x.com / twitter.com', {
      url: shortenUrl(tab.url),
    });
    return;
  }
  await info('capture-this-page requested', { url: shortenUrl(tab.url) });
  // (Re-)inject the page-hook + isolated content script so fetch/XHR are
  // patched even on tabs opened before the extension reloaded.
  try {
    await browser.scripting.executeScript({
      target: { tabId: tab.id },
      files: ['page-hook.js'],
      world: 'MAIN' as 'ISOLATED',
    });
    await browser.scripting.executeScript({
      target: { tabId: tab.id },
      files: ['content.js'],
    });
  } catch (err) {
    await warn('capture-this-page: re-inject failed', describeError(err));
  }
  // Nudge X to fire more GraphQL requests by scrolling. Most timeline /
  // conversation views fetch on intersection-observer triggers.
  try {
    await browser.scripting.executeScript({
      target: { tabId: tab.id },
      world: 'MAIN' as 'ISOLATED',
      func: () => {
        const h = window.innerHeight;
        window.scrollBy({ top: h * 1.5, behavior: 'smooth' });
        setTimeout(() => window.scrollBy({ top: h * 1.5, behavior: 'smooth' }), 600);
        setTimeout(() => window.scrollBy({ top: h * 1.5, behavior: 'smooth' }), 1200);
      },
    });
  } catch (err) {
    await warn('capture-this-page: scroll-nudge failed', describeError(err));
  }
}

// --- Refetch loop ---------------------------------------------------------
//
// X truncates long tweets in timeline payloads — `note_tweet` is only
// inlined for some endpoints. Re-opening each truncated tweet's detail page
// causes the page-hook to capture the full `note_tweet` body. The loop
// drives that by navigating a single dedicated tab through the queue, one
// tweet at a time, gated on the page-hook actually ingesting a response for
// the tweet we just navigated to (so deleted / paywalled / 404'd tweets
// don't make us spin and so we don't slam X with refreshes faster than the
// tab can load).

const REFETCH_TAB_KEY = '__imm_archive_refetch_tab_id__';

async function getRefetchTabId(): Promise<number | null> {
  const stored = await browser.storage.local.get(REFETCH_TAB_KEY);
  const id = stored[REFETCH_TAB_KEY];
  return typeof id === 'number' ? id : null;
}

async function setRefetchTabId(id: number | null): Promise<void> {
  if (id === null) {
    await browser.storage.local.remove(REFETCH_TAB_KEY);
  } else {
    await browser.storage.local.set({ [REFETCH_TAB_KEY]: id });
  }
}

async function startRefetchLoop(): Promise<void> {
  const total = await refetchQueueTotal();
  if (total === 0) {
    await info('refetch: nothing queued');
    return;
  }
  const s = await getSettings();
  const seconds = Math.min(
    AUTO_SCROLL_MAX_SEC,
    Math.max(AUTO_SCROLL_MIN_SEC, Math.round(s.autoScrollIntervalSec))
  );
  await setRefetchSession({
    totalAtStart: total,
    processed: 0,
    startedAt: new Date().toISOString(),
    inflight: null,
  });
  startRefetchInterval(seconds);
  void refetchTick();
  await info('refetch loop started', { queued: total, interval_sec: seconds });
  await broadcastState();
}

let refetchIntervalHandle: ReturnType<typeof setInterval> | null = null;

function startRefetchInterval(seconds: number): void {
  if (refetchIntervalHandle !== null) clearInterval(refetchIntervalHandle);
  refetchIntervalHandle = setInterval(() => {
    void refetchTick().catch((err) => {
      void warn('refetch tick failed', describeError(err));
    });
  }, seconds * 1000);
}

function stopRefetchInterval(): void {
  if (refetchIntervalHandle !== null) {
    clearInterval(refetchIntervalHandle);
    refetchIntervalHandle = null;
  }
}

async function cancelRefetchLoop(): Promise<void> {
  stopRefetchInterval();
  await browser.alarms.clear('refetch-tick'); // legacy cleanup
  const tabId = await getRefetchTabId();
  await setRefetchTabId(null);
  const sess = await getRefetchSession();
  await setRefetchSession(null);
  await info('refetch loop cancelled', {
    had_tab: tabId !== null,
    processed: sess?.processed ?? 0,
  });
  await broadcastState();
}

async function refetchTick(): Promise<void> {
  let sess = await getRefetchSession();
  if (sess === null) {
    // Loop was cancelled or never started; nothing to do.
    stopRefetchInterval();
    return;
  }
  // Wait-for-ingest guard: if we're still expecting a capture for the
  // previously-navigated target, only advance once we've either seen the
  // capture (onGraphqlCapture clears `inflight`) or hit the timeout.
  if (sess.inflight !== null) {
    const elapsed = Date.now() - Date.parse(sess.inflight.navigatedAt);
    if (elapsed < INGEST_WAIT_MS) {
      return; // still waiting — page-hook may yet capture the response.
    }
    // Timed out. Treat as "this target won't ingest" and drop it.
    await warn('refetch: target timed out waiting for ingest', {
      tweet_id: sess.inflight.tweetId,
      waited_ms: elapsed,
    });
    // Find which handle this id is queued under so we can dequeue it.
    const q = await getRefetchQueue();
    for (const [handle, ids] of Object.entries(q)) {
      if (ids.includes(sess.inflight.tweetId)) {
        await dequeueRefetch(handle, sess.inflight.tweetId);
        break;
      }
    }
    sess.processed += 1;
    sess.inflight = null;
    await setRefetchSession(sess);
  }

  const target = await nextRefetchTarget();
  if (!target) {
    stopRefetchInterval();
    await setRefetchTabId(null);
    await setRefetchSession(null);
    await info('refetch loop complete', { processed: sess.processed });
    await broadcastState();
    return;
  }

  const url = `https://x.com/${target.handle}/status/${target.tweetId}`;
  let tabId = await getRefetchTabId();
  if (tabId !== null) {
    try {
      await browser.tabs.get(tabId);
    } catch {
      tabId = null;
    }
  }
  try {
    if (tabId === null) {
      const tab = await browser.tabs.create({ url, active: false });
      if (typeof tab.id === 'number') await setRefetchTabId(tab.id);
    } else {
      await browser.tabs.update(tabId, { url });
    }
    sess = (await getRefetchSession()) ?? sess;
    sess.inflight = {
      tweetId: target.tweetId,
      navigatedAt: new Date().toISOString(),
    };
    await setRefetchSession(sess);
    await info('refetch tick', {
      handle: target.handle,
      tweet_id: target.tweetId,
      remaining: await refetchQueueTotal(),
      processed: sess.processed,
      total_at_start: sess.totalAtStart,
    });
  } catch (err) {
    await warn('refetch navigate failed; dropping target', {
      handle: target.handle,
      tweet_id: target.tweetId,
      ...describeError(err),
    });
    await dequeueRefetch(target.handle, target.tweetId);
    sess.processed += 1;
    sess.inflight = null;
    await setRefetchSession(sess);
  }
  await broadcastState();
}

// --- Media-crawl loop -----------------------------------------------------
//
// Mirrors the refetch loop but targets the media-crawl queue: tweets that
// the normalizer observed via the Media tab / UserMedia endpoint with
// insufficient data to build a full canonical tweet. The crawl loop walks
// a dedicated tab through each tweet's detail page (handle-less URL form
// when the hint-handle is missing), at the auto-scroll cadence, so the
// page-hook can capture the real tweet shape.

const MEDIA_CRAWL_TAB_KEY = '__imm_archive_media_crawl_tab_id__';

async function getMediaCrawlTabId(): Promise<number | null> {
  const stored = await browser.storage.local.get(MEDIA_CRAWL_TAB_KEY);
  const id = stored[MEDIA_CRAWL_TAB_KEY];
  return typeof id === 'number' ? id : null;
}

async function setMediaCrawlTabId(id: number | null): Promise<void> {
  if (id === null) await browser.storage.local.remove(MEDIA_CRAWL_TAB_KEY);
  else await browser.storage.local.set({ [MEDIA_CRAWL_TAB_KEY]: id });
}

async function startMediaCrawlLoop(): Promise<void> {
  const total = await mediaCrawlQueueTotal();
  if (total === 0) {
    await info('media-crawl: nothing queued');
    return;
  }
  const s = await getSettings();
  const seconds = Math.min(
    AUTO_SCROLL_MAX_SEC,
    Math.max(AUTO_SCROLL_MIN_SEC, Math.round(s.autoScrollIntervalSec))
  );
  await setMediaCrawlSession({
    totalAtStart: total,
    processed: 0,
    startedAt: new Date().toISOString(),
    inflight: null,
  });
  startMediaCrawlInterval(seconds);
  void mediaCrawlTick();
  await info('media-crawl loop started', { queued: total, interval_sec: seconds });
  await broadcastState();
}

let mediaCrawlIntervalHandle: ReturnType<typeof setInterval> | null = null;

function startMediaCrawlInterval(seconds: number): void {
  if (mediaCrawlIntervalHandle !== null) clearInterval(mediaCrawlIntervalHandle);
  mediaCrawlIntervalHandle = setInterval(() => {
    void mediaCrawlTick().catch((err) => {
      void warn('media-crawl tick failed', describeError(err));
    });
  }, seconds * 1000);
}

function stopMediaCrawlInterval(): void {
  if (mediaCrawlIntervalHandle !== null) {
    clearInterval(mediaCrawlIntervalHandle);
    mediaCrawlIntervalHandle = null;
  }
}

async function cancelMediaCrawlLoop(): Promise<void> {
  stopMediaCrawlInterval();
  await browser.alarms.clear('media-crawl-tick'); // legacy cleanup
  const tabId = await getMediaCrawlTabId();
  await setMediaCrawlTabId(null);
  const sess = await getMediaCrawlSession();
  await setMediaCrawlSession(null);
  await info('media-crawl loop cancelled', {
    had_tab: tabId !== null,
    processed: sess?.processed ?? 0,
  });
  await broadcastState();
}

async function mediaCrawlTick(): Promise<void> {
  let sess = await getMediaCrawlSession();
  if (sess === null) {
    stopMediaCrawlInterval();
    return;
  }
  if (sess.inflight !== null) {
    const elapsed = Date.now() - Date.parse(sess.inflight.navigatedAt);
    if (elapsed < INGEST_WAIT_MS) {
      return; // still waiting on the page-hook
    }
    await warn('media-crawl: target timed out waiting for ingest', {
      tweet_id: sess.inflight.tweetId,
      waited_ms: elapsed,
    });
    await dequeueMediaCrawl(sess.inflight.tweetId);
    sess.processed += 1;
    sess.inflight = null;
    await setMediaCrawlSession(sess);
  }

  const target = await nextMediaCrawlTarget();
  if (!target) {
    stopMediaCrawlInterval();
    await setMediaCrawlTabId(null);
    await setMediaCrawlSession(null);
    await info('media-crawl loop complete', { processed: sess.processed });
    await broadcastState();
    return;
  }
  // Skip if already in the archive — partial captures can outlive a
  // separate full capture path.
  if (await isCommitted(target.tweetId)) {
    await dequeueMediaCrawl(target.tweetId);
    sess.processed += 1;
    sess.inflight = null;
    await setMediaCrawlSession(sess);
    await broadcastState();
    return;
  }

  // Always use the explicit handle URL when we know one. The handle-less
  // `i/status/<id>` form has surfaced as "this page doesn't exist" in
  // practice (X's router doesn't always resolve it for tweets that need a
  // login wall or have author-context redirects).
  const url =
    target.bucket === '_unknown'
      ? `https://x.com/i/status/${target.tweetId}`
      : `https://x.com/${target.bucket}/status/${target.tweetId}`;
  let tabId = await getMediaCrawlTabId();
  if (tabId !== null) {
    try {
      await browser.tabs.get(tabId);
    } catch {
      tabId = null;
    }
  }
  try {
    if (tabId === null) {
      const tab = await browser.tabs.create({ url, active: false });
      if (typeof tab.id === 'number') await setMediaCrawlTabId(tab.id);
    } else {
      await browser.tabs.update(tabId, { url });
    }
    sess = (await getMediaCrawlSession()) ?? sess;
    sess.inflight = {
      tweetId: target.tweetId,
      navigatedAt: new Date().toISOString(),
    };
    await setMediaCrawlSession(sess);
    await info('media-crawl tick', {
      tweet_id: target.tweetId,
      hint_handle: target.bucket === '_unknown' ? null : target.bucket,
      remaining: await mediaCrawlQueueTotal(),
      processed: sess.processed,
      total_at_start: sess.totalAtStart,
    });
  } catch (err) {
    await warn('media-crawl navigate failed; dropping target', {
      tweet_id: target.tweetId,
      ...describeError(err),
    });
    await dequeueMediaCrawl(target.tweetId);
    sess.processed += 1;
    sess.inflight = null;
    await setMediaCrawlSession(sess);
  }
  await broadcastState();
}

// --- Quarantine -----------------------------------------------------------

async function quarantine(
  reason: string,
  endpoint: string,
  url: string,
  raw: unknown,
  err: unknown
): Promise<void> {
  await logErr('quarantining capture', { reason, endpoint, url, ...describeError(err) });
  const settings = await getSettings();
  if (!settings.pat || !settings.owner || !settings.repo) return;
  const payload: QuarantinePayload = {
    schema_version: 1,
    reason,
    endpoint,
    captured_at: new Date().toISOString(),
    source_url: url,
    error: describeError(err),
    raw,
  };
  const ts = isoCompact(payload.captured_at);
  const hash = await sha8(JSON.stringify(payload));
  const path = `raw/_quarantine/${ts}-${hash}.json`;
  try {
    const client = new GitHubClient(settings);
    await client.putFile({
      path,
      contentBase64: toBase64(JSON.stringify(payload, null, 2) + '\n'),
      message: `quarantine: ${reason} ${endpoint}`,
    });
  } catch (qerr) {
    await logErr('quarantine commit failed', { ...describeError(qerr) });
  }
}

async function sha8(s: string): Promise<string> {
  const buf = new TextEncoder().encode(s);
  const digest = await crypto.subtle.digest('SHA-256', buf);
  const hex = Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
  return hex.slice(0, 8);
}

// --- Connection verification & accounts list -----------------------------

async function verifyConnection(force: boolean): Promise<void> {
  const settings = await getSettings();
  if (!settings.pat || !settings.owner || !settings.repo) {
    await setConnection({
      status: 'not-configured',
      login: null,
      checkedAt: new Date().toISOString(),
      error: null,
      defaultBranch: null,
      configuredBranchExists: null,
      rateLimitResetAt: null,
    });
    await broadcastState();
    return;
  }
  const conn = await getConnection();
  if (!force) {
    // Skip if we're inside the reported rate-limit window — re-checking
    // before reset just wastes more of the same exhausted quota and keeps
    // the 403s coming.
    if (conn.status === 'rate-limited' && conn.rateLimitResetAt !== null) {
      const nowSec = Math.floor(Date.now() / 1000);
      if (nowSec < conn.rateLimitResetAt) return;
    }
    // Apply the periodic-check gate to every status, not just 'ok'. The
    // previous behavior re-verified on every wake whenever the connection
    // wasn't 'ok', which during a rate-limit window meant 2 API calls per
    // SW wake (verifyRepoAccess does GET /user + GET /repos/{o}/{r}).
    if (conn.checkedAt) {
      const age = Date.now() - Date.parse(conn.checkedAt);
      if (age < VERIFY_CONNECTION_INTERVAL_MS) return;
    }
  }
  try {
    const client = new GitHubClient(settings);
    const r = await client.verifyRepoAccess();
    // Probe the configured branch. A non-default branch is fine — it's
    // routine to capture into a working branch — but a *missing* branch
    // would 422 every commit.
    let branchOk = true;
    if (settings.branch && settings.branch !== r.default_branch) {
      branchOk = await client.branchExists(settings.branch);
    }
    await setConnection({
      status: 'ok',
      login: r.login,
      checkedAt: new Date().toISOString(),
      error: null,
      defaultBranch: r.default_branch,
      configuredBranchExists: branchOk,
      rateLimitResetAt: null,
    });
    await info('connection verified', {
      login: r.login,
      repo: r.full_name,
      default_branch: r.default_branch,
      configured_branch: settings.branch,
      configured_branch_exists: branchOk,
    });
    if (!branchOk) {
      await warn('configured branch does not exist on remote', {
        configured: settings.branch,
        default_branch: r.default_branch,
        fix: 'open Settings and change branch to ' + r.default_branch,
      });
    }
  } catch (err) {
    const cat = err instanceof GitHubError ? err.category : 'unknown';
    const status: ConnectionState['status'] =
      cat === 'auth'
        ? 'auth-error'
        : cat === 'rate-limit'
          ? 'rate-limited'
          : cat === 'network'
            ? 'network-error'
            : 'auth-error';
    const resetAt =
      err instanceof GitHubError && cat === 'rate-limit' ? err.rateLimitResetAt : null;
    await setConnection({
      status,
      login: null,
      checkedAt: new Date().toISOString(),
      error: describeError(err).message,
      defaultBranch: null,
      configuredBranchExists: null,
      rateLimitResetAt: resetAt,
    });
    await warn('connection check failed', {
      category: cat,
      rateLimitResetAt: resetAt,
      ...describeError(err),
    });
  }
  await broadcastState();
}

async function refreshAccountsList(force: boolean): Promise<void> {
  const settings = await getSettings();
  if (!settings.pat) {
    await setAccounts([...FALLBACK_ACCOUNTS]);
    return;
  }
  if (!force) {
    // Skip while inside a known rate-limit window — accounts.yaml is fetched
    // via authenticated raw.githubusercontent.com, which counts against the
    // same quota.
    const conn = await getConnection();
    if (conn.status === 'rate-limited' && conn.rateLimitResetAt !== null) {
      const nowSec = Math.floor(Date.now() / 1000);
      if (nowSec < conn.rateLimitResetAt) return;
    }
    // Skip if we refreshed recently. accounts.yaml changes rarely — the old
    // code re-fetched it on every SW wake, which during heavy browsing meant
    // a GitHub call every few seconds even when nothing was being captured.
    const lastRefreshed = await getAccountsRefreshedAt();
    if (lastRefreshed) {
      const age = Date.now() - Date.parse(lastRefreshed);
      if (Number.isFinite(age) && age < VERIFY_CONNECTION_INTERVAL_MS) return;
    }
  }
  try {
    const client = new GitHubClient(settings);
    const text = await client.fetchRawText('config/accounts.yaml');
    if (!text) {
      if (force) await warn('accounts.yaml not found in repo; using fallback');
      await setAccounts([...FALLBACK_ACCOUNTS]);
      await setAccountsRefreshedAt(new Date().toISOString());
      return;
    }
    const parsed = parseAccountsYaml(text);
    if (parsed.length === 0) {
      await warn('accounts.yaml parsed empty; keeping fallback');
      await setAccounts([...FALLBACK_ACCOUNTS]);
      await setAccountsRefreshedAt(new Date().toISOString());
      return;
    }
    await setAccounts(parsed);
    await setAccountsRefreshedAt(new Date().toISOString());
    if (force) await info('accounts list refreshed', { count: parsed.length });
  } catch (err) {
    await warn('refresh accounts failed; keeping current list', describeError(err));
  }
  await broadcastState();
}

// --- State broadcast -----------------------------------------------------

async function broadcastState(): Promise<void> {
  const state = await buildState();
  browser.runtime.sendMessage({ type: 'state-changed', state }).catch(() => {});
}

async function buildState(): Promise<ExtensionState> {
  const settings = await getSettings();
  const conn = await getConnection();
  const accounts = await getAccounts();
  const counters = await getCounters();
  let tabCount = 0;
  try {
    const tabs = await browser.tabs.query({ url: ['https://x.com/*', 'https://twitter.com/*'] });
    tabCount = tabs.length;
  } catch {
    // tabs.query is allowed by the manifest but can transiently fail
    // around extension startup; surfacing 0 is fine.
  }
  const refetchQueued = await refetchQueueTotal();
  const mediaCrawlQueued = await mediaCrawlQueueTotal();
  const refetchSess = await getRefetchSession();
  const mediaCrawlSess = await getMediaCrawlSession();
  const autoScrollSess = await getAutoScrollSession();
  return {
    version: EXT_VERSION,
    settings: redactSettings(settings),
    connection: conn,
    accounts,
    counters,
    autoScroll: {
      active: autoScrollSess !== null && autoScrollTimer !== null,
      tabCount,
      scrollCount: autoScrollSess?.scrollCount ?? 0,
      ingestedCount: autoScrollSess?.ingestedCount ?? 0,
      expandedCount: autoScrollSess?.expandedCount ?? 0,
    },
    refetchQueue: {
      total: refetchQueued,
      running: refetchIntervalHandle !== null,
      processed: refetchSess?.processed ?? 0,
      total_at_start: refetchSess?.totalAtStart ?? 0,
    },
    mediaCrawlQueue: {
      total: mediaCrawlQueued,
      running: mediaCrawlIntervalHandle !== null,
      processed: mediaCrawlSess?.processed ?? 0,
      total_at_start: mediaCrawlSess?.totalAtStart ?? 0,
    },
  };
}

function redactSettings(s: Settings): ExtensionState['settings'] {
  return {
    owner: s.owner,
    repo: s.repo,
    branch: s.branch,
    enabled: s.enabled,
    autoCapture: s.autoCapture,
    configuredAt: s.configuredAt,
    autoScrollIntervalSec: s.autoScrollIntervalSec,
    patSet: s.pat.length > 0,
    patSuffix: s.pat.length >= 4 ? s.pat.slice(-4) : '',
  };
}

function isoCompact(iso: string): string {
  // 2025-04-12T14:23:01.000Z → 2025-04-12T142301Z
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.replace(/[:.]/g, '');
  const pad = (n: number, w = 2) => String(n).padStart(w, '0');
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}` +
    `T${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}${pad(d.getUTCSeconds())}Z`
  );
}

function viewerUrl(s: Settings): string {
  return `https://${s.owner}.github.io/${s.repo}/`;
}

/**
 * Walk `node` collecting dotted key paths up to `maxDepth` deep. Array
 * indices collapse to `[0]` (we only descend into the first element). Used
 * to surface the structural skeleton of an unfamiliar GraphQL response
 * without including any data values.
 */
/** Collect every distinct `__typename` value found anywhere in the tree. */
function probeTypenames(node: unknown): string[] {
  const seen = new Set<string>();
  const visited = new WeakSet<object>();
  const stack: unknown[] = [node];
  while (stack.length > 0) {
    const n = stack.pop();
    if (n === null || typeof n !== 'object') continue;
    if (visited.has(n as object)) continue;
    visited.add(n as object);
    if (Array.isArray(n)) {
      for (const v of n) stack.push(v);
    } else {
      const obj = n as Record<string, unknown>;
      if (typeof obj.__typename === 'string') seen.add(obj.__typename);
      for (const v of Object.values(obj)) stack.push(v);
    }
  }
  return [...seen].sort();
}

/**
 * Find the first node anywhere in the tree whose `__typename` matches and
 * return its top-level key list (sorted). Useful for discovering what fields
 * X is actually shipping for a given node type after a schema change.
 */
function probeFirstTypeShape(node: unknown, typename: string): string[] | null {
  const visited = new WeakSet<object>();
  const stack: unknown[] = [node];
  while (stack.length > 0) {
    const n = stack.pop();
    if (n === null || typeof n !== 'object') continue;
    if (visited.has(n as object)) continue;
    visited.add(n as object);
    if (Array.isArray(n)) {
      for (const v of n) stack.push(v);
    } else {
      const obj = n as Record<string, unknown>;
      if (obj.__typename === typename) {
        return Object.keys(obj).sort();
      }
      for (const v of Object.values(obj)) stack.push(v);
    }
  }
  return null;
}

/**
 * Find the first node with `__typename === typename`, then descend through
 * the given key path, and return the top-level keys of whatever is at the
 * end. Returns a marker string when the path leads somewhere non-object so
 * we can tell apart "absent" from "present but not an object".
 */
function probeTypedNested(node: unknown, typename: string, path: string[]): unknown {
  const visited = new WeakSet<object>();
  const stack: unknown[] = [node];
  let found: Record<string, unknown> | null = null;
  while (stack.length > 0) {
    const n = stack.pop();
    if (n === null || typeof n !== 'object') continue;
    if (visited.has(n as object)) continue;
    visited.add(n as object);
    if (Array.isArray(n)) {
      for (const v of n) stack.push(v);
    } else {
      const obj = n as Record<string, unknown>;
      if (obj.__typename === typename) {
        found = obj;
        break;
      }
      for (const v of Object.values(obj)) stack.push(v);
    }
  }
  if (!found) return null;
  let cur: unknown = found;
  for (const seg of path) {
    if (cur === null || typeof cur !== 'object') return `<${cur === null ? 'null' : typeof cur}>`;
    cur = (cur as Record<string, unknown>)[seg];
  }
  if (cur === undefined) return '<missing>';
  if (cur === null) return '<null>';
  if (typeof cur !== 'object') return `<${typeof cur}>`;
  if (Array.isArray(cur)) return `<array len=${cur.length}>`;
  return Object.keys(cur as Record<string, unknown>).sort();
}

function shortenUrl(u: string): string {
  // Activity-tail context is more useful with just the path; full URLs become
  // hard to read at the sidebar's width.
  try {
    const parsed = new URL(u);
    const path = parsed.pathname + (parsed.search ? '?…' : '');
    return parsed.host === 'x.com' || parsed.host === 'twitter.com'
      ? path
      : `${parsed.host}${path}`;
  } catch {
    return u.length > 80 ? `${u.slice(0, 80)}…` : u;
  }
}

// --- Dev helpers exposed on globalThis for the devtools console ----------
// (e.g. `__immArchive.clearAll()` in the background worker's devtools).

(globalThis as Record<string, unknown>).__immArchive = {
  clearAll,
  activity: getActivity,
  accounts: getAccounts,
  buffers: getRunBuffers,
  flushAll: () => flushAll('devtools'),
  state: buildState,
  refetchQueue: getRefetchQueue,
  startRefetch: startRefetchLoop,
  cancelRefetch: cancelRefetchLoop,
  mediaCrawlQueue: getMediaCrawlQueue,
  startMediaCrawl: startMediaCrawlLoop,
  cancelMediaCrawl: cancelMediaCrawlLoop,
};
