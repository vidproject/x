/**
 * page-hook.ts — runs in the page's main world (NOT the content-script world).
 *
 * Monkey-patches `window.fetch` and `XMLHttpRequest.prototype.send` so it can
 * observe (but not modify) responses to X's internal GraphQL endpoints. When a
 * matching response arrives, the body is JSON-parsed and forwarded to the
 * content script via `window.postMessage`. The content script then forwards
 * it to the background service worker.
 *
 * Care is taken not to break the page: we clone responses, swallow our own
 * errors, and never alter the data flowing back to X's app code.
 */

(() => {
  const TAG = '__imm_archive_hook__';
  const w = window as unknown as Record<string, unknown>;
  if (w[TAG]) return;
  w[TAG] = true;

  const TARGET = 'IMM_ARCHIVE_CAPTURE';
  const READY = 'IMM_ARCHIVE_HOOK_READY';

  // Tell the content script the hook actually loaded in the page world.
  // The content script forwards this to the background as a log event so the
  // sidebar's activity tail makes the bootstrap visible.
  try {
    window.postMessage(
      { source: READY, at: new Date().toISOString(), url: location.href },
      location.origin
    );
  } catch {
    // ignore
  }

  // Tweet-bearing endpoints we want to observe. Matched as substrings in the
  // request URL path so we don't have to maintain the rotating queryId values
  // X bakes into URLs.
  const NEEDLES = [
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
  ];

  function endpointOf(url: string): string | null {
    try {
      const path = new URL(url, location.origin).pathname;
      for (const n of NEEDLES) {
        if (path.includes(`/${n}`)) return n;
      }
    } catch {
      // ignore
    }
    return null;
  }

  function post(endpoint: string, url: string, body: unknown): void {
    try {
      window.postMessage(
        {
          source: TARGET,
          endpoint,
          url,
          response: body,
          observed_at: new Date().toISOString(),
        },
        location.origin
      );
    } catch {
      // ignore — we never want to disturb the host page
    }
  }

  function safeParse(text: string): unknown | null {
    try {
      return JSON.parse(text);
    } catch {
      return null;
    }
  }

  // --- fetch ----------------------------------------------------------------
  const origFetch = window.fetch.bind(window);
  window.fetch = async function (input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const endpoint = url ? endpointOf(url) : null;
    const res = await origFetch(input, init);
    if (endpoint) {
      try {
        const clone = res.clone();
        clone
          .text()
          .then((text) => {
            const parsed = safeParse(text);
            if (parsed !== null) post(endpoint, url, parsed);
          })
          .catch(() => {});
      } catch {
        // ignore
      }
    }
    return res;
  };

  // --- XMLHttpRequest -------------------------------------------------------
  const XHR = XMLHttpRequest.prototype;
  const origOpen = XHR.open;
  const origSend = XHR.send;
  XHR.open = function (
    this: XMLHttpRequest,
    method: string,
    url: string | URL,
    async?: boolean,
    user?: string | null,
    password?: string | null
  ) {
    (this as unknown as Record<string, unknown>).__immArchiveUrl = String(url);
    return origOpen.call(this, method, url, async ?? true, user ?? null, password ?? null);
  } as typeof XHR.open;

  XHR.send = function (this: XMLHttpRequest, body?: Document | XMLHttpRequestBodyInit | null) {
    const u = (this as unknown as Record<string, unknown>).__immArchiveUrl as string | undefined;
    const endpoint = u ? endpointOf(u) : null;
    if (endpoint) {
      this.addEventListener('load', () => {
        try {
          if (
            (this.responseType === '' || this.responseType === 'text') &&
            typeof this.responseText === 'string'
          ) {
            const parsed = safeParse(this.responseText);
            if (parsed !== null) post(endpoint, u!, parsed);
          } else if (this.responseType === 'json') {
            const r = this.response as unknown;
            if (r !== null) post(endpoint, u!, r);
          }
        } catch {
          // ignore
        }
      });
    }
    return origSend.call(this, body ?? null);
  } as typeof XHR.send;
})();
