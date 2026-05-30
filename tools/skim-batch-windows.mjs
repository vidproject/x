#!/usr/bin/env node

import { spawn } from 'node:child_process';
import { createWriteStream } from 'node:fs';
import { cp, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const DEFAULTS = {
  unit: 'month',
  sourceProfile: '.skim/profile-stephen-dhs-backfill',
  profileRoot: '.skim/batch-profiles',
  outDir: '.skim/raw',
  logDir: '.skim',
  concurrency: 4,
  seconds: 90,
  scrolls: 60,
  scrollDelayMs: 700,
  scrollFactor: 1.05,
  allowStyles: true,
  headless: false,
  latest: false,
  includeNativeRetweets: false,
  querySuffix: '',
  stopOnZero: true,
  zeroFailureLimit: 6,
};

function usage(exitCode = 0) {
  console.log(
    `
Usage:
  node tools/skim-batch-windows.mjs --job DHSgov:2018-04-01:2026-05-22 --job StephenM:2021-06-01:2026-05-22

Options:
  --job <handle:from:to[:unit]>  Add date windows for a handle. Unit defaults to ${DEFAULTS.unit}.
  --source-profile <dir>         Logged-in profile to reuse/clone. Default: ${DEFAULTS.sourceProfile}
  --profile-root <dir>           Destination root for the per-worker profile clones.
  --out <dir>                    x-skim output JSONL directory. Default: ${DEFAULTS.outDir}
  --log-dir <dir>                Per-window logs directory. Default: ${DEFAULTS.logDir}
  --concurrency <n>              Parallel windows. Default: ${DEFAULTS.concurrency}
  --seconds <n>                  Per-window max runtime. Default: ${DEFAULTS.seconds}
  --scrolls <n>                  Per-window scroll steps. Default: ${DEFAULTS.scrolls}
  --scroll-delay-ms <n>          Per-scroll delay. Default: ${DEFAULTS.scrollDelayMs}
  --scroll-factor <n>            Viewport heights per scroll. Default: ${DEFAULTS.scrollFactor}
  --block-styles                 Block stylesheets.
  --headless                     Run child skims without visible browser windows.
  --latest                       Use X's Latest search tab.
  --include-native-retweets      Add include:nativeretweets to each search query.
  --retweets-only                Add filter:nativeretweets to each search query.
  --query-suffix <text>          Extra raw search operators to append.
  --allow-zero-responses         Keep going even if a window captures no GraphQL.
  --zero-failure-limit <n>       Stop after this many zero-response windows. Default: ${DEFAULTS.zeroFailureLimit}
`.trim()
  );
  process.exit(exitCode);
}

function parseArgs(argv) {
  const options = { ...DEFAULTS, jobs: [] };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const readValue = () => {
      const value = argv[++index];
      if (!value) throw new Error(`Missing value after ${arg}`);
      return value;
    };
    switch (arg) {
      case '--job':
        options.jobs.push(parseJob(readValue()));
        break;
      case '--source-profile':
        options.sourceProfile = readValue();
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
      case '--concurrency':
        options.concurrency = positiveInteger(arg, readValue());
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
      case '--block-styles':
        options.allowStyles = false;
        break;
      case '--headless':
        options.headless = true;
        break;
      case '--latest':
        options.latest = true;
        break;
      case '--include-native-retweets':
        options.includeNativeRetweets = true;
        break;
      case '--retweets-only':
        options.querySuffix = `${options.querySuffix} filter:nativeretweets`.trim();
        break;
      case '--query-suffix':
        options.querySuffix = `${options.querySuffix} ${readValue()}`.trim();
        break;
      case '--allow-zero-responses':
        options.stopOnZero = false;
        break;
      case '--zero-failure-limit':
        options.zeroFailureLimit = positiveInteger(arg, readValue());
        break;
      case '--help':
      case '-h':
        usage(0);
        break;
      default:
        throw new Error(`Unknown option: ${arg}`);
    }
  }
  if (options.jobs.length === 0) throw new Error('Provide at least one --job.');
  return options;
}

function parseJob(raw) {
  const parts = raw.split(':');
  if (parts.length < 3 || parts.length > 4) {
    throw new Error(`Bad --job ${raw}; expected handle:from:to[:unit].`);
  }
  const [handleRaw, from, to, unit = DEFAULTS.unit] = parts;
  validateDate(from, `${raw} from`);
  validateDate(to, `${raw} to`);
  return { handle: stripHandle(handleRaw), from, to, unit };
}

function stripHandle(value) {
  return String(value || '')
    .trim()
    .replace(/^@/, '');
}

function validateDate(value, label) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || Number.isNaN(Date.parse(`${value}T00:00:00Z`))) {
    throw new Error(`${label} must be yyyy-mm-dd.`);
  }
}

function positiveInteger(name, value) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) throw new Error(`${name} must be positive.`);
  return parsed;
}

function positiveNumber(name, value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) throw new Error(`${name} must be positive.`);
  return parsed;
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
      throw new Error(`Unsupported unit: ${unit}`);
  }
}

function buildTasks(options) {
  const tasks = [];
  for (const job of options.jobs) {
    for (const window of makeWindows(job.from, job.to, job.unit)) {
      tasks.push({ handle: job.handle, window, unit: job.unit });
    }
  }
  return tasks;
}

function safeLabel(value) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
}

function taskId(task) {
  return `${safeLabel(task.handle)}-${task.window.from}-to-${task.window.to}`;
}

async function cloneProfile(sourceProfile, destProfile) {
  await mkdir(path.dirname(destProfile), { recursive: true });
  await cp(sourceProfile, destProfile, {
    recursive: true,
    force: true,
    filter: (src) => {
      const base = path.basename(src);
      if (base.startsWith('Singleton') || base === 'lockfile') return false;
      if (base === 'Crashpad' || base === 'Crash Reports') return false;
      if (base === 'Cache' || base === 'Code Cache' || base === 'ShaderCache') return false;
      if (base === 'GrShaderCache' || base === 'GPUCache') return false;
      return true;
    },
  });
}

function commandForTask(task, profileDir, options) {
  const query = `from:${task.handle} since:${task.window.from} until:${task.window.to}${
    options.includeNativeRetweets ? ' include:nativeretweets' : ''
  }${options.querySuffix ? ` ${options.querySuffix}` : ''
  }`;
  const url = `https://x.com/search?q=${encodeURIComponent(query)}${
    options.latest ? '&src=typed_query&f=live' : ''
  }`;
  const args = [
    'tools/x-skim.mjs',
    '--url',
    url,
    '--profile-dir',
    profileDir,
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
  if (options.stopOnZero) args.push('--fail-on-zero-responses');
  if (options.allowStyles) args.push('--allow-styles');
  if (options.headless) args.push('--headless');
  return { query, args };
}

async function runTask(task, profileDir, index, total, options) {
  const id = taskId(task);
  const outLog = path.join(options.logDir, `skim-batch-${id}.out.log`);
  const errLog = path.join(options.logDir, `skim-batch-${id}.err.log`);
  const command = commandForTask(task, profileDir, options);
  console.log(`[${index + 1}/${total}] ${command.query}`);
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
      console.log(`  ${id}: status=${status}${signal ? ` signal=${signal}` : ''}`);
      resolve({ ...task, id, status, signal: signal ?? null, outLog, errLog });
    });
  });
}

// Clone the logged-in source profile once per worker slot and reuse it across
// every window that worker runs. Cloning a (large) browser profile per window
// was the dominant cost of a deep backfill with hundreds of windows; a worker
// runs its windows sequentially, so one profile per worker is safe. With a
// single worker we skip cloning entirely and drive the source profile directly.
async function buildProfilePool(poolSize, options) {
  const pool = [];
  for (let slot = 0; slot < poolSize; slot += 1) {
    if (poolSize === 1) {
      pool.push(options.sourceProfile);
      continue;
    }
    const dir = path.join(options.profileRoot, `pool-${slot}`);
    await cloneProfile(options.sourceProfile, dir);
    pool.push(dir);
  }
  return pool;
}

async function runQueue(tasks, options) {
  const results = [];
  let nextIndex = 0;
  let stop = false;
  let zeroFailures = 0;

  const poolSize = Math.min(options.concurrency, tasks.length);
  const pool = await buildProfilePool(poolSize, options);

  async function worker(slot) {
    while (!stop && nextIndex < tasks.length) {
      const index = nextIndex;
      nextIndex += 1;
      const result = await runTask(tasks[index], pool[slot], index, tasks.length, options);
      results.push(result);
      if (result.status === 2) {
        zeroFailures += 1;
      }
      if (options.stopOnZero && zeroFailures >= options.zeroFailureLimit) {
        stop = true;
      }
    }
  }

  await Promise.all(Array.from({ length: poolSize }, (_, slot) => worker(slot)));
  return { results, stoppedEarly: stop, nextIndex, zeroFailures };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\..+/, 'Z');
  options.sourceProfile = path.resolve(process.cwd(), options.sourceProfile);
  options.profileRoot = path.resolve(process.cwd(), options.profileRoot, stamp);
  options.outDir = path.resolve(process.cwd(), options.outDir);
  options.logDir = path.resolve(process.cwd(), options.logDir);
  await mkdir(options.profileRoot, { recursive: true });
  await mkdir(options.logDir, { recursive: true });

  const tasks = buildTasks(options);
  console.log(
    `Prepared ${tasks.length} one-window skim tasks; concurrency=${options.concurrency}.`
  );
  const report = await runQueue(tasks, options);
  const reportPath = path.join(options.logDir, `skim-batch-${stamp}.summary.json`);
  await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  console.log(
    `Batch complete. stoppedEarly=${report.stoppedEarly}; completed=${report.results.length}/${tasks.length}`
  );
  console.log(`Report: ${reportPath}`);
  process.exitCode =
    report.stoppedEarly || report.results.some((result) => result.status !== 0) ? 1 : 0;
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
