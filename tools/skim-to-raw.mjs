#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import readline from 'node:readline';
import { pathToFileURL } from 'node:url';
import ts from 'typescript';

const REPO_ROOT = process.cwd();
const DEFAULT_SKIM_DIR = path.join(REPO_ROOT, '.skim', 'raw');
const DEFAULT_RAW_DIR = path.join(REPO_ROOT, 'raw');
const NORMALIZE_CACHE_DIR = path.join(REPO_ROOT, '.skim', 'cache', 'normalize');
const ACCOUNTS_PATH = path.join(REPO_ROOT, 'config', 'accounts.yaml');

function usage(exitCode = 0) {
  console.log(
    `
Usage:
  node tools/skim-to-raw.mjs --handles DHSgov ICEgov GregoryKBovino RealTomHoman

Options:
  --handles <h...>        Only convert skim files whose search query is from one of these handles.
  --skim-dir <dir>        Directory containing x-skim *.summary.json / *.jsonl. Default: .skim/raw
  --raw-dir <dir>         Destination raw capture root. Default: raw
  --run-prefix <prefix>   Prefix for generated capture_run_id. Default: skim
`.trim()
  );
  process.exit(exitCode);
}

function parseArgs(argv) {
  const options = {
    skimDir: DEFAULT_SKIM_DIR,
    rawDir: DEFAULT_RAW_DIR,
    handles: [],
    runPrefix: 'skim',
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const readValue = () => {
      const value = argv[++index];
      if (!value) throw new Error(`Missing value after ${arg}`);
      return value;
    };
    switch (arg) {
      case '--handles':
        while (argv[index + 1] && !argv[index + 1].startsWith('--')) {
          options.handles.push(stripHandle(argv[++index]));
        }
        break;
      case '--skim-dir':
        options.skimDir = path.resolve(REPO_ROOT, readValue());
        break;
      case '--raw-dir':
        options.rawDir = path.resolve(REPO_ROOT, readValue());
        break;
      case '--run-prefix':
        options.runPrefix = readValue();
        break;
      case '--help':
      case '-h':
        usage(0);
        break;
      default:
        throw new Error(`Unknown option: ${arg}`);
    }
  }
  if (options.handles.length === 0) {
    throw new Error('Provide at least one --handles value.');
  }
  return options;
}

function stripHandle(value) {
  return String(value || '').trim().replace(/^@/, '');
}

async function importNormalizer() {
  await mkdir(NORMALIZE_CACHE_DIR, { recursive: true });
  await transpileTsModule(
    path.join(REPO_ROOT, 'extension', 'src', 'lib', 'config.ts'),
    path.join(NORMALIZE_CACHE_DIR, 'config.js')
  );
  await transpileTsModule(
    path.join(REPO_ROOT, 'extension', 'src', 'lib', 'normalize.ts'),
    path.join(NORMALIZE_CACHE_DIR, 'normalize.js')
  );
  return import(`${pathToFileURL(path.join(NORMALIZE_CACHE_DIR, 'normalize.js')).href}?v=${Date.now()}`);
}

async function transpileTsModule(inPath, outPath) {
  const source = await readFile(inPath, 'utf8');
  const output = ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ES2022,
      importsNotUsedAsValues: ts.ImportsNotUsedAsValues.Remove,
    },
    fileName: inPath,
  });
  await writeFile(outPath, output.outputText, 'utf8');
}

async function readAccounts() {
  const text = await readFile(ACCOUNTS_PATH, 'utf8');
  const accounts = [];
  let current = null;
  for (const line of text.split(/\r?\n/)) {
    const handle = line.match(/^\s*-\s*handle:\s*([A-Za-z0-9_]+)/);
    if (handle) {
      current = { handle: handle[1], category: 'core' };
      accounts.push(current);
      continue;
    }
    const category = line.match(/^\s*category:\s*([A-Za-z_]+)/);
    if (category && current) current.category = category[1];
  }
  return accounts;
}

async function discoverSummaries(skimDir) {
  const entries = await readdir(skimDir, { withFileTypes: true });
  const summaries = [];
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith('.summary.json')) continue;
    const fullPath = path.join(skimDir, entry.name);
    try {
      const summary = JSON.parse(await readFile(fullPath, 'utf8'));
      const jsonl = summary.output_jsonl;
      if (!jsonl || summary.captured_response_count <= 0) continue;
      summaries.push({ summaryPath: fullPath, jsonlPath: jsonl, summary });
    } catch {
      // Ignore malformed summaries; x-skim raw JSONL remains untouched.
    }
  }
  return summaries.sort((a, b) => String(a.summary.run_id).localeCompare(String(b.summary.run_id)));
}

function handleFromSearchUrl(rawUrl) {
  if (!rawUrl) return null;
  try {
    const url = new URL(rawUrl);
    const query = url.searchParams.get('q') ?? '';
    const match = query.match(/\bfrom:([A-Za-z0-9_]{1,15})\b/i);
    return match?.[1] ?? null;
  } catch {
    const decoded = safeDecode(rawUrl);
    const match = decoded.match(/\bfrom:([A-Za-z0-9_]{1,15})\b/i);
    return match?.[1] ?? null;
  }
}

function safeDecode(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return String(value);
  }
}

async function* iterJsonl(pathName) {
  const rl = readline.createInterface({
    input: createReadStream(pathName, { encoding: 'utf8' }),
    crlfDelay: Infinity,
  });
  for await (const line of rl) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      yield JSON.parse(trimmed);
    } catch {
      // Keep walking; a malformed line should not sink the whole conversion.
    }
  }
}

function retweetEdgeKey(edge) {
  return [
    String(edge.retweeter_handle || '').toLowerCase(),
    String(edge.retweet_tweet_id || ''),
    String(edge.original_tweet_id || ''),
  ].join('|');
}

function inferRetweetedHandle(text) {
  const match = String(text || '').match(/^RT @([A-Za-z0-9_]{1,15}):/);
  return match?.[1] ?? null;
}

function buildRetweetEdges(tweets, accounts, capturedAt, endpoint, sourceUrl, runId) {
  const categoryByHandle = new Map(
    accounts.map((account) => [account.handle.toLowerCase(), account.category])
  );
  const byId = new Map(tweets.map((tweet) => [tweet.tweet_id, tweet]));
  const out = new Map();
  for (const tweet of tweets) {
    if (tweet.tweet_type !== 'retweet' || !tweet.retweeted_tweet_id) continue;
    const retweeterCategory = categoryByHandle.get(tweet.account_handle.toLowerCase());
    if (!retweeterCategory) continue;
    const original = byId.get(tweet.retweeted_tweet_id);
    const originalHandle =
      original?.account_handle ?? inferRetweetedHandle(tweet.text_resolved || tweet.text);
    const edge = {
      retweeter_handle: tweet.account_handle,
      retweeter_account_id: tweet.account_id ?? null,
      retweeter_category: retweeterCategory,
      retweet_tweet_id: tweet.tweet_id,
      retweet_url: tweet.tweet_url,
      original_tweet_id: tweet.retweeted_tweet_id,
      original_author_handle: originalHandle,
      original_author_account_id: original?.account_id ?? null,
      original_author_category: originalHandle
        ? categoryByHandle.get(originalHandle.toLowerCase()) ?? 'public'
        : null,
      captured_at: capturedAt,
      capture_run_id: runId,
      endpoint,
      source_url: sourceUrl,
    };
    out.set(retweetEdgeKey(edge), edge);
  }
  return [...out.values()];
}

function pushMap(map, key, value) {
  const arr = map.get(key) ?? [];
  arr.push(value);
  map.set(key, arr);
}

function compactTimestamp(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return new Date().toISOString().replace(/[-:.]/g, '').slice(0, 15);
  return date.toISOString().replace(/[-:.]/g, '').slice(0, 15);
}

function shortHash(value) {
  return createHash('sha256').update(value).digest('hex').slice(0, 8).toUpperCase();
}

async function convertSummary(item, options, normalizer, accounts) {
  const sourceHandle = handleFromSearchUrl(item.summary.target_url);
  if (!sourceHandle) return null;
  const wanted = new Set(options.handles.map((h) => h.toLowerCase()));
  if (!wanted.has(sourceHandle.toLowerCase())) return null;

  const tracked = new Set(accounts.map((account) => account.handle.toLowerCase()));
  const core = new Set(
    accounts
      .filter((account) => account.category === 'core')
      .map((account) => account.handle.toLowerCase())
  );
  const groupedTweets = new Map();
  const groupedUnavailable = new Map();
  const groupedEdges = new Map();
  const endpoints = new Set();
  let capturedAt = item.summary.finished_at ?? item.summary.started_at ?? new Date().toISOString();
  let responses = 0;
  let built = 0;

  for await (const record of iterJsonl(item.jsonlPath)) {
    if (!record?.body) continue;
    const endpoint = String(record.endpoint || 'SearchTimeline');
    const recordCapturedAt = String(record.captured_at || capturedAt);
    const sourceUrl = record.page_url ?? record.url ?? item.summary.target_url;
    endpoints.add(endpoint);
    capturedAt = recordCapturedAt > capturedAt ? recordCapturedAt : capturedAt;
    responses += 1;

    const normalized = normalizer.normalize(record.body, {
      capturedAt: recordCapturedAt,
      runId: `${options.runPrefix}-${record.run_id || item.summary.run_id}`,
      endpoint,
      allowedHandles: new Set(),
      sourceUrl,
    });
    const related = normalizer.filterRelated(normalized.tweets, tracked, core);
    const captureRunId = `${options.runPrefix}-${record.run_id || item.summary.run_id}`;
    const edges = buildRetweetEdges(related, accounts, recordCapturedAt, endpoint, sourceUrl, captureRunId);
    for (const tweet of related) {
      pushMap(groupedTweets, tweet.account_handle, { ...tweet, capture_run_id: captureRunId });
      built += 1;
    }
    for (const unavailable of normalized.unavailable_tweets ?? []) {
      const h = unavailable.account_handle || sourceHandle;
      pushMap(groupedUnavailable, h, unavailable);
    }
    for (const edge of edges) pushMap(groupedEdges, edge.retweeter_handle, edge);
  }

  const written = [];
  const handles = new Set([
    ...groupedTweets.keys(),
    ...groupedUnavailable.keys(),
    ...groupedEdges.keys(),
  ]);
  for (const handle of handles) {
    const tweets = groupedTweets.get(handle) ?? [];
    const unavailable = groupedUnavailable.get(handle) ?? [];
    const retweetEdges = groupedEdges.get(handle) ?? [];
    if (tweets.length === 0 && unavailable.length === 0 && retweetEdges.length === 0) continue;
    const runId = `${options.runPrefix}-${item.summary.run_id}-${handle}`;
    const payload = {
      schema_version: 1,
      capture_run_id: runId,
      account_handle: handle,
      captured_at: capturedAt,
      endpoint: [...endpoints].sort().join(','),
      user_agent: 'x-skim',
      source_url: item.summary.target_url,
      tweets,
      unavailable_tweets: unavailable,
      retweet_edges: retweetEdges,
      converted_from: {
        kind: 'x-skim',
        summary: path.relative(REPO_ROOT, item.summaryPath).replace(/\\/g, '/'),
        jsonl: path.relative(REPO_ROOT, item.jsonlPath).replace(/\\/g, '/'),
        source_handle: sourceHandle,
      },
    };
    const dir = path.join(options.rawDir, handle);
    await mkdir(dir, { recursive: true });
    const filename = `${compactTimestamp(capturedAt)}-SKIM-${shortHash(runId)}.json`;
    const outPath = path.join(dir, filename);
    await writeFile(outPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
    written.push({ handle, outPath, tweets: tweets.length, unavailable: unavailable.length });
  }
  return {
    sourceHandle,
    runId: item.summary.run_id,
    responses,
    built,
    written,
  };
}

async function main() {
  let options;
  try {
    options = parseArgs(process.argv.slice(2));
  } catch (error) {
    console.error(error.message);
    usage(1);
  }

  const [normalizer, accounts, summaries] = await Promise.all([
    importNormalizer(),
    readAccounts(),
    discoverSummaries(options.skimDir),
  ]);
  const results = [];
  for (const summary of summaries) {
    const result = await convertSummary(summary, options, normalizer, accounts);
    if (result) results.push(result);
  }
  const totals = {
    summaries: results.length,
    responses: results.reduce((sum, result) => sum + result.responses, 0),
    normalized_tweets: results.reduce((sum, result) => sum + result.built, 0),
    raw_files: results.reduce((sum, result) => sum + result.written.length, 0),
  };
  console.log(JSON.stringify({ totals, results }, null, 2));
}

main().catch((error) => {
  console.error(error.stack ?? error.message);
  process.exit(1);
});
