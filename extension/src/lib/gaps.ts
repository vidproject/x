// Coverage-gap detection: turn an account's per-month post counts (from the
// archive manifest) into date ranges that are empty or unusually sparse — the
// holes a researcher would want to patch — plus the recent frontier of tweets
// posted since the last capture. Each gap carries a ready-to-open X search
// (`from:<handle> since:<d> until:<d>`) so the sidebar can drive a targeted,
// date-bounded re-scan instead of re-walking the whole timeline.

import type { ArchiveSnapshotAccount, CoverageGap } from './types.js';

const X_SEARCH_BASE = 'https://x.com/search';

export interface GapOptions {
  now?: Date;
  /** A month below `sparseRatio * median(active months)` counts as sparse. */
  sparseRatio?: number;
  /** Never flag a month sparse if it has at least this many posts. */
  sparseFloor?: number;
  /** Only flag sparse months once typical monthly volume is this high; below
   * it, an account is too low-volume to call any month "sparse" with meaning. */
  minMedianForSparse?: number;
  /** Skip the intra-month recent frontier if the last post is newer than this. */
  recentFrontierMinAgeDays?: number;
}

function monthKey(d: Date): string {
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

function parseMonth(ym: string): Date {
  const parts = ym.split('-');
  const y = Number(parts[0]);
  const m = Number(parts[1] ?? '1');
  return new Date(Date.UTC(y, m - 1, 1));
}

function addMonths(d: Date, n: number): Date {
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + n, 1));
}

function addDays(d: Date, n: number): Date {
  return new Date(d.getTime() + n * 86_400_000);
}

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function median(nums: number[]): number {
  if (nums.length === 0) return 0;
  const s = [...nums].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  if (s.length % 2) return s[mid] ?? 0;
  return ((s[mid - 1] ?? 0) + (s[mid] ?? 0)) / 2;
}

export function gapSearchUrl(handle: string, fromDate: string, toDate: string): string {
  const q = `from:${handle} since:${fromDate} until:${toDate}`;
  return `${X_SEARCH_BASE}?q=${encodeURIComponent(q)}&f=live`;
}

/** Find empty/sparse month ranges (plus the recent frontier) for one account. */
export function findCoverageGaps(
  account: ArchiveSnapshotAccount,
  options: GapOptions = {}
): CoverageGap[] {
  const now = options.now ?? new Date();
  const sparseRatio = options.sparseRatio ?? 0.25;
  const sparseFloor = options.sparseFloor ?? 3;
  const minMedianForSparse = options.minMedianForSparse ?? 8;
  const recentFrontierMinAgeDays = options.recentFrontierMinAgeDays ?? 2;

  const months = account.months ?? {};
  if (!account.first_post_at) return [];
  const startMonth = parseMonth(account.first_post_at.slice(0, 7));
  const endMonth = parseMonth(monthKey(now));

  const seq: { ym: string; count: number }[] = [];
  for (let d = startMonth; d <= endMonth; d = addMonths(d, 1)) {
    const ym = monthKey(d);
    seq.push({ ym, count: months[ym] ?? 0 });
  }
  if (seq.length === 0) return [];

  const activeCounts = seq.filter((s) => s.count > 0).map((s) => s.count);
  const med = median(activeCounts);
  const sparseThreshold =
    med >= minMedianForSparse ? Math.max(sparseFloor, Math.round(sparseRatio * med)) : 0;

  const latestPostMonth = account.latest_post_at ? account.latest_post_at.slice(0, 7) : null;
  const isHole = (s: { ym: string; count: number }): boolean =>
    s.count === 0 || (sparseThreshold > 0 && s.count < sparseThreshold);

  const gaps: CoverageGap[] = [];
  let run: { ym: string; count: number }[] = [];
  const flush = (): void => {
    const first = run[0];
    const last = run[run.length - 1];
    if (!first || !last) return;
    const fromMonth = first.ym;
    const toMonth = last.ym;
    const fromDate = `${fromMonth}-01`;
    const toDate = isoDate(addMonths(parseMonth(toMonth), 1)); // exclusive
    const captured = run.reduce((acc, s) => acc + s.count, 0);
    const allEmpty = run.every((s) => s.count === 0);
    const isRecent = latestPostMonth !== null && fromMonth > latestPostMonth;
    const kind: CoverageGap['kind'] = isRecent ? 'recent' : allEmpty ? 'empty' : 'sparse';
    gaps.push({
      handle: account.handle,
      fromMonth,
      toMonth,
      fromDate,
      toDate,
      monthCount: run.length,
      capturedInRange: captured,
      kind,
      searchUrl: gapSearchUrl(account.handle, fromDate, toDate),
    });
    run = [];
  };
  for (const s of seq) {
    if (isHole(s)) run.push(s);
    else flush();
  }
  flush();

  // Intra-month recent frontier: if the last post sits inside the current month
  // (so the trailing-empty-month logic produced nothing for it) but is a couple
  // days stale, offer a search from the last post through today to pull anything
  // posted since the last scan.
  if (account.latest_post_at) {
    const last = new Date(account.latest_post_at);
    const ageDays = (now.getTime() - last.getTime()) / 86_400_000;
    if (ageDays >= recentFrontierMinAgeDays && monthKey(last) === monthKey(now)) {
      const fromDate = isoDate(last);
      const toDate = isoDate(addDays(now, 1));
      gaps.push({
        handle: account.handle,
        fromMonth: monthKey(now),
        toMonth: monthKey(now),
        fromDate,
        toDate,
        monthCount: 0,
        capturedInRange: months[monthKey(now)] ?? 0,
        kind: 'recent',
        searchUrl: gapSearchUrl(account.handle, fromDate, toDate),
      });
    }
  }

  return gaps;
}

/** Rank gaps worst-first across accounts: recent + empty before sparse, then by span. */
export function rankGaps(gaps: CoverageGap[]): CoverageGap[] {
  const weight: Record<CoverageGap['kind'], number> = { recent: 0, empty: 1, sparse: 2 };
  return [...gaps].sort((a, b) => weight[a.kind] - weight[b.kind] || b.monthCount - a.monthCount);
}
