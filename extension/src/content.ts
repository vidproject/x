/**
 * content.ts — content-script context.
 *
 * 1. Injects `page-hook.js` into the page world so we can observe GraphQL
 *    responses (content scripts run in an isolated world and can't see the
 *    page's fetch/XHR by default).
 * 2. Listens for `window.postMessage` events from the page hook and forwards
 *    them to the background service worker.
 *
 * The hook runs at document_start so it patches fetch/XHR before X's app
 * code starts using them.
 */

const TARGET = 'IMM_ARCHIVE_CAPTURE';

function inject(): void {
  try {
    const src = browser.runtime.getURL('page-hook.js');
    const script = document.createElement('script');
    script.src = src;
    script.async = false;
    script.dataset.immArchive = '1';
    (document.head || document.documentElement).appendChild(script);
    script.onload = () => script.remove();
  } catch (err) {
    console.warn('[imm-archive] failed to inject page hook', err);
  }
}

inject();

window.addEventListener('message', (event: MessageEvent) => {
  if (event.source !== window) return;
  const data = event.data as unknown;
  if (!data || typeof data !== 'object') return;
  const d = data as Record<string, unknown>;
  if (d.source !== TARGET) return;
  const endpoint = typeof d.endpoint === 'string' ? d.endpoint : null;
  const url = typeof d.url === 'string' ? d.url : null;
  const response = d.response;
  if (!endpoint || !url || response === undefined) return;
  browser.runtime.sendMessage({ type: 'graphql-capture', endpoint, url, response }).catch((err) => {
    // The background worker may be asleep; sendMessage should wake it.
    console.warn('[imm-archive] sendMessage failed', err);
  });
});
