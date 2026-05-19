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
  const response = d.response;
  if (!endpoint || !url || response === undefined) return;
  browser.runtime.sendMessage({ type: 'graphql-capture', endpoint, url, response }).catch((err) => {
    console.warn('[imm-archive] sendMessage failed', err);
  });
});
