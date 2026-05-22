// Thin wrapper around hyparquet for loading data/<handle>.parquet files
// straight from GitHub Pages.
//
// Hyparquet's core ships SNAPPY + GZIP support only; the ingest pipeline
// writes ZSTD-compressed parquet (much better ratio for this data) so we
// pull the codec table from `hyparquet-compressors`, which adds ZSTD,
// BROTLI, and LZ4_RAW behind the same API.

import { asyncBufferFromUrl, parquetReadObjects } from 'https://esm.sh/hyparquet@1.18.1?bundle';
import { compressors } from 'https://esm.sh/hyparquet-compressors@1?bundle';

/**
 * Load a parquet file into an array of plain JS objects.
 * @param {string} url
 * @param {((loaded: number, total: number) => void)|{rowStart?: number, rowEnd?: number, columns?: string[], byteLength?: number, onProgress?: (loaded: number, total: number) => void}} [options]
 * @returns {Promise<Array<Record<string, unknown>>>}
 */
export async function loadParquetRows(url, options) {
  const opts = typeof options === 'function' ? { onProgress: options } : options || {};
  const hasRange = Number.isFinite(opts.rowStart) || Number.isFinite(opts.rowEnd) || opts.columns;
  if (!opts.onProgress || hasRange) {
    try {
      const file = await parquetFile(url, opts.byteLength);
      return await parquetReadObjects({
        file,
        compressors,
        ...(Number.isFinite(opts.rowStart) ? { rowStart: Math.max(0, Math.floor(opts.rowStart)) } : {}),
        ...(Number.isFinite(opts.rowEnd) ? { rowEnd: Math.max(0, Math.floor(opts.rowEnd)) } : {}),
        ...(Array.isArray(opts.columns) && opts.columns.length > 0 ? { columns: opts.columns } : {}),
      });
    } catch (err) {
      if (hasRange) throw err;
      // Fall back to the historical whole-file path for hosts that do not
      // support HEAD/range requests. Row-range calls must stay range-only.
      console.warn('[viewer] async parquet read failed; retrying whole-file fetch', err);
    }
  }
  return loadParquetRowsWhole(url, opts.onProgress);
}

const parquetFiles = new Map();

async function parquetFile(url, byteLength) {
  if (!parquetFiles.has(url)) {
    const promise = asyncBufferFromUrl({
      url,
      ...(Number.isFinite(byteLength) && byteLength > 0
        ? { byteLength: Math.floor(byteLength) }
        : {}),
      requestInit: { cache: 'no-store' },
    }).catch((err) => {
      parquetFiles.delete(url);
      throw err;
    });
    parquetFiles.set(url, promise);
  }
  return parquetFiles.get(url);
}

async function loadParquetRowsWhole(url, onProgress) {
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
  const rows = await parquetReadObjects({ file: bytes.buffer, compressors });
  return rows;
}
