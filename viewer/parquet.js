// Thin wrapper around hyparquet for loading data/<handle>.parquet files
// straight from GitHub Pages.

import { parquetReadObjects } from 'https://esm.sh/hyparquet@1.18.1?bundle';

/**
 * Load a parquet file into an array of plain JS objects.
 * @param {string} url
 * @param {(loaded: number, total: number) => void} [onProgress]
 * @returns {Promise<Array<Record<string, unknown>>>}
 */
export async function loadParquetRows(url, onProgress) {
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) {
    throw new Error(`fetch ${url}: ${res.status} ${res.statusText}`);
  }
  const total = Number(res.headers.get('content-length')) || 0;
  let bytes;
  if (onProgress && total > 0 && res.body) {
    const reader = res.body.getReader();
    const chunks = [];
    let loaded = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) {
        chunks.push(value);
        loaded += value.length;
        onProgress(loaded, total);
      }
    }
    bytes = new Uint8Array(loaded);
    let off = 0;
    for (const c of chunks) {
      bytes.set(c, off);
      off += c.length;
    }
  } else {
    bytes = new Uint8Array(await res.arrayBuffer());
  }
  const rows = await parquetReadObjects({ file: bytes.buffer });
  return rows;
}
