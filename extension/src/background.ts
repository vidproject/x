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
  USER_PAGE_ENDPOINTS,
  VERIFY_CONNECTION_INTERVAL_MS,
} from './lib/config.js';
import { GitHubClient, GitHubError, toBase64 } from './lib/github.js';
import { describeError, error as logErr, info, setBroadcaster, warn } from './lib/logger.js';
import { normalize } from './lib/normalize.js';
import { newRunId, shortRunId } from './lib/runids.js';
import {
  bumpCounter,
  clearActivity,
  clearAll,
  clearRunBuffer,
  dequeueRefetch,
  engagementSig,
  enqueueRefetch,
  getAccounts,
  getActivity,
  getCommittedIndex,
  getConnection,
  getCounters,
  getRefetchQueue,
  getRunBuffer,
  getRunBuffers,
  getSettings,
  nextRefetchTarget,
  pruneCommittedIndex,
  refetchQueueTotal,
  setAccounts,
  setBufferedCount,
  setCommittedIndex,
  setConnection,
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
// scrolling. The auto-scroll loop therefore runs on an in-SW interval,
// re-armed on every wake. Keep the handle module-scoped so a fresh
// SW instance can clear a stale one if it ever leaks.
let autoScrollTimer: ReturnType<typeof setInterval> | null = null;

async function ensureAutoScrollAlarm(): Promise<void> {
  const s = await getSettings();
  await configureAutoScroll(s.autoScroll, s.autoScrollIntervalSec);
}

async function configureAutoScroll(on: boolean, intervalSec: number): Promise<void> {
  if (autoScrollTimer !== null) {
    clearInterval(autoScrollTimer);
    autoScrollTimer = null;
  }
  if (!on) return;
  const clamped = Math.min(
    AUTO_SCROLL_MAX_SEC,
    Math.max(AUTO_SCROLL_MIN_SEC, Math.round(intervalSec))
  );
  autoScrollTimer = setInterval(() => {
    void autoScrollTick().catch((err) => {
      void warn('auto-scroll tick failed', describeError(err));
    });
  }, clamped * 1000);
  await info('auto-scroll loop armed', { interval_sec: clamped });
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
  for (const tab of tabs) {
    if (typeof tab.id !== 'number') continue;
    if (tab.discarded) continue;
    try {
      await browser.scripting.executeScript({
        target: { tabId: tab.id },
        world: 'MAIN' as 'ISOLATED',
        func: () => {
          // Dispatch End key — many X surfaces (lists, search, replies) bind
          // pagination triggers to it via React handlers, not just on the
          // intersection observer. Belt-and-braces: also explicitly scroll.
          const opts: KeyboardEventInit = {
            key: 'End',
            code: 'End',
            keyCode: 35,
            which: 35,
            bubbles: true,
            cancelable: true,
          };
          const target = (document.activeElement as HTMLElement | null) ?? document.body;
          target.dispatchEvent(new KeyboardEvent('keydown', opts));
          target.dispatchEvent(new KeyboardEvent('keyup', opts));
          window.scrollTo({
            top: document.documentElement.scrollHeight,
            behavior: 'auto',
          });
        },
      });
    } catch {
      // Tabs that disallow scripting (about:, discarded, mid-navigation)
      // just get skipped — they'll be eligible on a later tick.
    }
  }
}

browser.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'flush-sweep') {
    void flushIdleBuffers();
  } else if (alarm.name === 'verify-connection') {
    void verifyConnection(false);
  } else if (alarm.name === 'refetch-tick') {
    void refetchTick();
  }
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

async function handleMessage(msg: RuntimeMessage): Promise<unknown> {
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
    case 'toggle-auto-scroll': {
      const s = await updateSettings({ autoScroll: msg.on });
      await configureAutoScroll(s.autoScroll, s.autoScrollIntervalSec);
      await info('auto-scroll toggled', { on: msg.on, interval_sec: s.autoScrollIntervalSec });
      return buildState();
    }
    case 'set-auto-scroll-interval': {
      const seconds = Math.min(
        AUTO_SCROLL_MAX_SEC,
        Math.max(AUTO_SCROLL_MIN_SEC, Math.round(msg.seconds))
      );
      const s = await updateSettings({ autoScrollIntervalSec: seconds });
      await configureAutoScroll(s.autoScroll, s.autoScrollIntervalSec);
      return buildState();
    }
    case 'start-refetch':
      await startRefetchLoop();
      return buildState();
    case 'cancel-refetch':
      await cancelRefetchLoop();
      return buildState();
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

  // On user-scoped endpoints (UserTweets, UserTweetsAndReplies, TweetDetail,
  // …) we keep every tweet in the response, not just those authored by
  // tracked handles — that way when a tracked account retweets / quotes /
  // replies to a non-tracked account, the referenced tweet's content is
  // archived too, bucketed under its actual author. For general-purpose
  // endpoints (HomeTimeline, SearchTimeline, …) we still filter so casual
  // browsing doesn't pull in arbitrary content from the user's feed.
  const allowed = USER_PAGE_ENDPOINTS.has(endpoint)
    ? new Set<string>() // empty = no handle filter
    : new Set(accounts.map((a) => a.handle.toLowerCase()));
  const capturedAt = new Date().toISOString();

  let normalized;
  try {
    normalized = normalize(response, {
      capturedAt,
      runId: 'pending',
      endpoint,
      allowedHandles: allowed,
    });
  } catch (err) {
    await quarantine('normalize-threw', endpoint, url, response, err);
    return;
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

  // Cross-session dedup: drop tweets we've already committed with identical
  // engagement counts. Likes/RTs/replies/quotes still trigger a recapture
  // so engagement_history grows over time. view_count is intentionally
  // excluded — it churns too fast to be useful as a freshness signal.
  const committedIdx = await getCommittedIndex();
  let dedupedSkipped = 0;
  const liveTweets: typeof normalized.tweets = [];
  for (const t of normalized.tweets) {
    const prev = committedIdx[t.account_handle]?.[t.tweet_id];
    const sig = engagementSig(t.like_count, t.retweet_count, t.reply_count, t.quote_count);
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

  // Refetch queue housekeeping: a tweet that comes back here with
  // is_truncated=false means we successfully grabbed its full body (e.g.
  // from a TweetDetail capture), so drop it from the queue. The reverse —
  // is_truncated=true — gets enqueued so the user can drive a refetch loop.
  for (const t of liveTweets) {
    if (t.is_truncated) {
      await enqueueRefetch(t.account_handle, t.tweet_id);
    } else {
      await dequeueRefetch(t.account_handle, t.tweet_id);
    }
  }

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
          sig: engagementSig(t.like_count, t.retweet_count, t.reply_count, t.quote_count),
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
      });
      await logErr('flush failed', {
        handle,
        reason,
        category: err.category,
        status: err.status,
        message: err.message,
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
// tweet every auto-scroll-interval seconds, so the user doesn't get a
// tab-storm.

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
  await browser.alarms.clear('refetch-tick');
  await browser.alarms.create('refetch-tick', {
    when: Date.now() + 100,
    periodInMinutes: Math.max(seconds / 60, 0.5), // 30s minimum per MV3
  });
  await info('refetch loop started', { queued: total, interval_sec: seconds });
  await broadcastState();
}

async function cancelRefetchLoop(): Promise<void> {
  await browser.alarms.clear('refetch-tick');
  const tabId = await getRefetchTabId();
  await setRefetchTabId(null);
  await info('refetch loop cancelled', { had_tab: tabId !== null });
  await broadcastState();
}

async function refetchTick(): Promise<void> {
  const target = await nextRefetchTarget();
  if (!target) {
    await browser.alarms.clear('refetch-tick');
    await setRefetchTabId(null);
    await info('refetch loop complete');
    await broadcastState();
    return;
  }
  const url = `https://x.com/${target.handle}/status/${target.tweetId}`;
  let tabId = await getRefetchTabId();
  // Confirm the tab still exists; recreate if the user closed it.
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
    await info('refetch tick', {
      handle: target.handle,
      tweet_id: target.tweetId,
      remaining: await refetchQueueTotal(),
    });
  } catch (err) {
    await warn('refetch navigate failed; dropping target', {
      handle: target.handle,
      tweet_id: target.tweetId,
      ...describeError(err),
    });
    // Skip this one so we don't get stuck.
    await dequeueRefetch(target.handle, target.tweetId);
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
    });
    await broadcastState();
    return;
  }
  const conn = await getConnection();
  if (!force && conn.status === 'ok' && conn.checkedAt) {
    const age = Date.now() - Date.parse(conn.checkedAt);
    if (age < VERIFY_CONNECTION_INTERVAL_MS) return;
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
    await setConnection({
      status,
      login: null,
      checkedAt: new Date().toISOString(),
      error: describeError(err).message,
      defaultBranch: null,
      configuredBranchExists: null,
    });
    await warn('connection check failed', { category: cat, ...describeError(err) });
  }
  await broadcastState();
}

async function refreshAccountsList(force: boolean): Promise<void> {
  const settings = await getSettings();
  if (!settings.pat) {
    await setAccounts([...FALLBACK_ACCOUNTS]);
    return;
  }
  try {
    const client = new GitHubClient(settings);
    const text = await client.fetchRawText('config/accounts.yaml');
    if (!text) {
      if (force) await warn('accounts.yaml not found in repo; using fallback');
      await setAccounts([...FALLBACK_ACCOUNTS]);
      return;
    }
    const parsed = parseAccountsYaml(text);
    if (parsed.length === 0) {
      await warn('accounts.yaml parsed empty; keeping fallback');
      await setAccounts([...FALLBACK_ACCOUNTS]);
      return;
    }
    await setAccounts(parsed);
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
  const refetchAlarm = await browser.alarms.get('refetch-tick');
  return {
    version: EXT_VERSION,
    settings: redactSettings(settings),
    connection: conn,
    accounts,
    counters,
    autoScroll: {
      active: settings.autoScroll && autoScrollTimer !== null,
      tabCount,
    },
    refetchQueue: {
      total: refetchQueued,
      running: refetchAlarm !== undefined,
      lastTickAt: null,
    },
  };
}

function redactSettings(s: Settings): ExtensionState['settings'] {
  return {
    owner: s.owner,
    repo: s.repo,
    branch: s.branch,
    autoCapture: s.autoCapture,
    configuredAt: s.configuredAt,
    autoScroll: s.autoScroll,
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
};
