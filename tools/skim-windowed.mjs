#!/usr/bin/env node

import { spawn } from 'node:child_process';
import { createWriteStream } from 'node:fs';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';

const DEFAULTS = {
  from: '2016-01-01',
  to: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
  unit: 'month',
  outDir: '.skim/raw',
  logDir: '.skim',
  profileRoot: '.skim',
  seconds: 240,
  scrolls: 120,
  scrollDelayMs: 1000,
  scrollFactor: 0.9,
  allowStyles: true,
  allowImages: false,
  allowMedia: false,
  allowFonts: false,
  latest: false,
  failOnZeroResponses: true,
  headless: false,
  dryRun: false,
  stopOnError: false,
  offset: 0,
  limit: Infinity,
};

function usage(exitCode = 0) {
  console.log(
    `
Usage:
  node tools/skim-windowed.mjs --handles StephenM DHSgov --from 2021-01-01 --to 2026-05-22 --unit month

Options:
  --handles <h...>             Handles to search.
  --from <yyyy-mm-dd>          Inclusive window start. Default: ${DEFAULTS.from}
  --to <yyyy-mm-dd>            Exclusive window end. Default: tomorrow
  --unit <day|week|month|quarter|half|year>
  --offset <n>                 Skip the first n generated windows.
  --limit <n>                  Run at most n generated windows.
  --profile-map <h=dir,...>    Reuse known logged-in profiles per handle.
  --profile-root <dir>         Default profile parent. Default: ${DEFAULTS.profileRoot}
  --out <dir>                  x-skim output JSONL directory. Default: ${DEFAULTS.outDir}
  --log-dir <dir>              Per-window logs directory. Default: ${DEFAULTS.logDir}
  --seconds <n>                Per-window max runtime. Default: ${DEFAULTS.seconds}
  --scrolls <n>                Per-window scroll steps. Default: ${DEFAULTS.scrolls}
  --scroll-delay-ms <n>        Per-scroll delay. Default: ${DEFAULTS.scrollDelayMs}
  --scroll-factor <n>          Viewport heights per scroll. Default: ${DEFAULTS.scrollFactor}
  --allow-styles               Let stylesheets load. Default: on
  --block-styles               Block stylesheets.
  --allow-images               Let images load. Default: off
  --allow-media                Let audio/video load. Default: off
  --allow-fonts                Let fonts load. Default: off
  --latest                     Use X's Latest search tab. Default: plain search.
  --allow-zero-responses       Do not fail a window that captures no GraphQL.
  --headless                   Run Chrome headless.
  --dry-run                    Print planned commands only.
  --stop-on-error              Stop after the first failed x-skim command.
`.trim()
  );
  process.exit(exitCode);
}

function parseArgs(argv) {
  const options = { ...DEFAULTS, handles: [], profileMap: new Map() };
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
      case '--from':
        options.from = readValue();
        break;
      case '--to':
        options.to = readValue();
        break;
      case '--unit':
        options.unit = readValue();
        break;
      case '--offset':
        options.offset = positiveInteger(arg, readValue());
        break;
      case '--limit':
        options.limit = positiveInteger(arg, readValue());
        break;
      case '--profile-map':
        options.profileMap = parseProfileMap(readValue());
        break;
      case '--profile-root':
        options.profileRoot = readValue();
        break;
      case '--out':
        options.outDir = readValue();
        break;
      case '--log-dir':
        options.logDir = readValue();
        break;
      case '--seconds':
        options.seconds = positiveInteger(arg, readValue());
        break;
      case '--scrolls':
        options.scrolls = positiveInteger(arg, readValue());
        break;
      case '--scroll-delay-ms':
        options.scrollDelayMs = positiveInteger(arg, readValue());
        break;
      case '--scroll-factor':
        options.scrollFactor = positiveNumber(arg, readValue());
        break;
      case '--allow-styles':
        options.allowStyles = true;
        break;
      case '--block-styles':
        options.allowStyles = false;
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
      case '--latest':
        options.latest = true;
        break;
      case '--allow-zero-responses':
        options.failOnZeroResponses = false;
        break;
      case '--headless':
        options.headless = true;
        break;
      case '--dry-run':
        options.dryRun = true;
        break;
      case '--stop-on-error':
        options.stopOnError = true;
        break;
      case '--help':
      case '-h':
        usage(0);
        break;
      default:
        throw new Error(`Unknown option: ${arg}`);
    }
  }
  if (options.handles.length === 0) throw new Error('Provide --handles.');
  validateDate(options.from, '--from');
  validateDate(options.to, '--to');
  if (Date.parse(`${options.from}T00:00:00Z`) >= Date.parse(`${options.to}T00:00:00Z`)) {
    throw new Error('--from must be before --to.');
  }
  return options;
}

function stripHandle(value) {
  return String(value || '').trim().replace(/^@/, '');
}

function positiveInteger(name, value) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed < 0) {
    throw new Error(`${name} must be a non-negative integer.`);
  }
  return parsed;
}

function positiveNumber(name, value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive number.`);
  }
  return parsed;
}

function validateDate(value, name) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || Number.isNaN(Date.parse(`${value}T00:00:00Z`))) {
    throw new Error(`${name} must be yyyy-mm-dd.`);
  }
}

function parseProfileMap(raw) {
  const map = new Map();
  for (const part of raw.split(',')) {
    const [handle, ...rest] = part.split('=');
    const profile = rest.join('=');
    if (!handle || !profile) throw new Error(`Bad --profile-map entry: ${part}`);
    map.set(stripHandle(handle), profile);
  }
  return map;
}

function makeWindows(from, to, unit) {
  const windows = [];
  let cursor = parseDate(from);
  const end = parseDate(to);
  while (cursor < end) {
    const next = addUnit(cursor, unit);
    const windowEnd = next < end ? next : end;
    windows.push({ from: formatDate(cursor), to: formatDate(windowEnd) });
    cursor = windowEnd;
  }
  return windows;
}

function parseDate(value) {
  return new Date(`${value}T00:00:00.000Z`);
}

function formatDate(date) {
  return date.toISOString().slice(0, 10);
}

function addUnit(date, unit) {
  const next = new Date(date.getTime());
  switch (unit) {
    case 'day':
      next.setUTCDate(next.getUTCDate() + 1);
      return next;
    case 'week':
      next.setUTCDate(next.getUTCDate() + 7);
      return next;
    case 'month':
      next.setUTCMonth(next.getUTCMonth() + 1);
      return next;
    case 'quarter':
      next.setUTCMonth(next.getUTCMonth() + 3);
      return next;
    case 'half':
      next.setUTCMonth(next.getUTCMonth() + 6);
      return next;
    case 'year':
      next.setUTCFullYear(next.getUTCFullYear() + 1);
      return next;
    default:
      throw new Error(`Unsupported --unit: ${unit}`);
  }
}

function profileDirFor(handle, options) {
  return (
    options.profileMap.get(handle) ??
    path.join(options.profileRoot, `profile-windowed-${handle.toLowerCase()}`)
  );
}

function buildCommand(handle, window, options) {
  const query = `from:${handle} since:${window.from} until:${window.to}`;
  const url = `https://x.com/search?q=${encodeURIComponent(query)}${
    options.latest ? '&src=typed_query&f=live' : ''
  }`;
  const args = [
    'tools/x-skim.mjs',
    '--url',
    url,
    '--profile-dir',
    profileDirFor(handle, options),
    '--out',
    options.outDir,
    '--seconds',
    String(options.seconds),
    '--scrolls',
    String(options.scrolls),
    '--scroll-delay-ms',
    String(options.scrollDelayMs),
    '--scroll-factor',
    String(options.scrollFactor),
  ];
  if (options.allowStyles) args.push('--allow-styles');
  if (options.allowImages) args.push('--allow-images');
  if (options.allowMedia) args.push('--allow-media');
  if (options.allowFonts) args.push('--allow-fonts');
  if (options.headless) args.push('--headless');
  if (options.failOnZeroResponses) args.push('--fail-on-zero-responses');
  return { query, url, args };
}

function safeLabel(value) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

async function runWindow(job, index, total, options) {
  const { handle, window } = job;
  const command = buildCommand(handle, window, options);
  const label = `${safeLabel(handle)}-${window.from}-to-${window.to}`;
  const outLog = path.join(options.logDir, `skim-windowed-${label}.out.log`);
  const errLog = path.join(options.logDir, `skim-windowed-${label}.err.log`);

  console.log(`[${index + 1}/${total}] ${command.query}`);
  console.log(`  logs: ${outLog} / ${errLog}`);
  if (options.dryRun) {
    console.log(`  node ${command.args.map(shellQuote).join(' ')}`);
    return { status: 0, skipped: true };
  }

  await mkdir(options.logDir, { recursive: true });
  const out = createWriteStream(outLog, { flags: 'w' });
  const err = createWriteStream(errLog, { flags: 'w' });
  const child = spawn(process.execPath, command.args, {
    cwd: process.cwd(),
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  child.stdout.pipe(out);
  child.stderr.pipe(err);
  return await new Promise((resolve) => {
    child.on('exit', (code, signal) => {
      out.end();
      err.end();
      const status = code ?? (signal ? 1 : 0);
      console.log(`  finished: status=${status}${signal ? ` signal=${signal}` : ''}`);
      resolve({ status, signal });
    });
  });
}

function shellQuote(value) {
  if (/^[A-Za-z0-9_./:=?-]+$/.test(value)) return value;
  return JSON.stringify(value);
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const windows = makeWindows(options.from, options.to, options.unit);
  const jobs = [];
  for (const handle of options.handles) {
    for (const window of windows) jobs.push({ handle, window });
  }
  const selected = jobs.slice(options.offset, Number.isFinite(options.limit) ? options.offset + options.limit : undefined);
  console.log(
    `Prepared ${selected.length}/${jobs.length} windowed skim jobs (${options.unit}, ${options.from}..${options.to}).`
  );

  let failures = 0;
  for (let index = 0; index < selected.length; index += 1) {
    const result = await runWindow(selected[index], index, selected.length, options);
    if (result.status !== 0) {
      failures += 1;
      if (options.stopOnError) break;
    }
  }
  console.log(`Windowed skim complete. failures=${failures}`);
  process.exitCode = failures ? 1 : 0;
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
