import { describeError } from './logger.js';
import type { Settings } from './types.js';

const API_BASE = 'https://api.github.com';
const RAW_BASE = 'https://raw.githubusercontent.com';

export interface PutFileOptions {
  path: string;
  contentBase64: string;
  message: string;
  expectedSha?: string | null;
}

export interface PutFileResult {
  path: string;
  sha: string;
  url: string;
}

export class GitHubError extends Error {
  status: number;
  body: unknown;
  retriable: boolean;
  category: 'auth' | 'rate-limit' | 'conflict' | 'not-found' | 'server' | 'network' | 'unknown';
  /** When the GitHub rate-limit window for this token resets, as a Unix
   * epoch in seconds. Populated from `X-RateLimit-Reset` on 403/429, or
   * derived from `Retry-After` for secondary limits. null when unknown. */
  rateLimitResetAt: number | null;

  constructor(opts: {
    status: number;
    body: unknown;
    message: string;
    retriable: boolean;
    category: GitHubError['category'];
    rateLimitResetAt?: number | null;
  }) {
    super(opts.message);
    this.name = 'GitHubError';
    this.status = opts.status;
    this.body = opts.body;
    this.retriable = opts.retriable;
    this.category = opts.category;
    this.rateLimitResetAt = opts.rateLimitResetAt ?? null;
  }
}

export class GitHubClient {
  private readonly settings: Settings;

  constructor(settings: Settings) {
    this.settings = settings;
  }

  /** Returns the authenticated user's login. Throws on auth failure. */
  async whoami(): Promise<string> {
    const res = await this.fetchJson('GET', '/user');
    return (res as { login?: string }).login ?? '<unknown>';
  }

  /** Returns true if the given branch exists on the configured repo. */
  async branchExists(branch: string): Promise<boolean> {
    try {
      await this.fetchJson(
        'GET',
        `/repos/${this.settings.owner}/${this.settings.repo}/branches/${encodeURIComponent(branch)}`
      );
      return true;
    } catch (err) {
      if (err instanceof GitHubError && err.category === 'not-found') return false;
      throw err;
    }
  }

  /** Verifies the PAT can see the configured repo. */
  async verifyRepoAccess(): Promise<{ login: string; full_name: string; default_branch: string }> {
    const me = await this.fetchJson('GET', '/user');
    const repo = await this.fetchJson('GET', `/repos/${this.settings.owner}/${this.settings.repo}`);
    const r = repo as { full_name: string; default_branch: string };
    return {
      login: (me as { login: string }).login,
      full_name: r.full_name,
      default_branch: r.default_branch,
    };
  }

  /**
   * Fetch a text file from raw.githubusercontent.com authenticated with the
   * PAT. Returns null if the file doesn't exist. Used for config/accounts.yaml.
   */
  async fetchRawText(pathInRepo: string): Promise<string | null> {
    const url = `${RAW_BASE}/${this.settings.owner}/${this.settings.repo}/${this.settings.branch}/${pathInRepo}`;
    const res = await fetch(url, {
      method: 'GET',
      headers: { Authorization: `Bearer ${this.settings.pat}` },
    });
    if (res.status === 404) return null;
    if (!res.ok) throw await this.makeError(res, `GET raw ${pathInRepo}`);
    return res.text();
  }

  /**
   * Look up an existing file's SHA via the Contents API, or null if not found.
   * Used when retrying a PUT after a 422 sha mismatch.
   */
  async getFileSha(path: string): Promise<string | null> {
    try {
      const res = await this.fetchJson(
        'GET',
        `/repos/${this.settings.owner}/${this.settings.repo}/contents/${encodePath(path)}?ref=${encodeURIComponent(this.settings.branch)}`
      );
      const r = res as { sha?: string };
      return r.sha ?? null;
    } catch (err) {
      if (err instanceof GitHubError && err.category === 'not-found') return null;
      throw err;
    }
  }

  /**
   * PUT a file to the repo. Handles 422 (sha required) by re-fetching the
   * existing sha and retrying once. Retries network / 5xx / rate-limit errors
   * with exponential backoff up to maxAttempts.
   */
  async putFile(opts: PutFileOptions): Promise<PutFileResult> {
    const path = opts.path;
    const url = `/repos/${this.settings.owner}/${this.settings.repo}/contents/${encodePath(path)}`;
    let sha: string | null = opts.expectedSha ?? null;
    const maxAttempts = 5;

    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      const body: Record<string, unknown> = {
        message: opts.message,
        content: opts.contentBase64,
        branch: this.settings.branch,
      };
      if (sha) body.sha = sha;
      try {
        const res = await this.fetchJson('PUT', url, body);
        const out = res as { content: { sha: string; html_url: string; path: string } };
        return { path: out.content.path, sha: out.content.sha, url: out.content.html_url };
      } catch (err) {
        if (!(err instanceof GitHubError)) throw err;
        if (err.status === 422 && !sha) {
          // File already exists; fetch its sha and retry once.
          sha = await this.getFileSha(path);
          if (!sha) throw err;
          continue;
        }
        if (!err.retriable || attempt === maxAttempts) throw err;
        await sleep(backoffMs(attempt, err));
      }
    }
    throw new Error(`putFile: exhausted attempts for ${path}`);
  }

  private async fetchJson(method: string, path: string, body?: unknown): Promise<unknown> {
    const url = path.startsWith('http') ? path : `${API_BASE}${path}`;
    const init: RequestInit = {
      method,
      headers: {
        Authorization: `Bearer ${this.settings.pat}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        ...(body ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    };
    let res: Response;
    try {
      res = await fetch(url, init);
    } catch (netErr) {
      throw new GitHubError({
        status: 0,
        body: null,
        message: `network: ${describeError(netErr).message}`,
        retriable: true,
        category: 'network',
      });
    }
    if (res.status === 204) return null;
    let parsed: unknown = null;
    const text = await res.text();
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = text;
      }
    }
    if (!res.ok) throw await this.makeErrorFromParsed(res, parsed, `${method} ${path}`);
    return parsed;
  }

  private async makeError(res: Response, label: string): Promise<GitHubError> {
    let parsed: unknown = null;
    try {
      const text = await res.text();
      if (text) parsed = JSON.parse(text);
    } catch {
      // ignore
    }
    return this.makeErrorFromParsed(res, parsed, label);
  }

  private makeErrorFromParsed(res: Response, body: unknown, label: string): GitHubError {
    const msg =
      typeof body === 'object' && body && 'message' in body
        ? String((body as { message: unknown }).message)
        : res.statusText;
    let category: GitHubError['category'] = 'unknown';
    let retriable = false;
    if (res.status === 401 || res.status === 403) {
      // 403 can be either auth or rate limit; check headers.
      const rate = res.headers.get('x-ratelimit-remaining');
      if (rate === '0' || /rate limit/i.test(msg)) {
        category = 'rate-limit';
        retriable = true;
      } else {
        category = 'auth';
      }
    } else if (res.status === 404) {
      category = 'not-found';
    } else if (res.status === 409 || res.status === 422) {
      category = 'conflict';
    } else if (res.status >= 500) {
      category = 'server';
      retriable = true;
    } else if (res.status === 429) {
      category = 'rate-limit';
      retriable = true;
    }
    return new GitHubError({
      status: res.status,
      body,
      message: `${label} → ${res.status}: ${msg}`,
      retriable,
      category,
      rateLimitResetAt: category === 'rate-limit' ? parseRateLimitReset(res.headers) : null,
    });
  }
}

/**
 * Best-effort parse of when the current rate-limit window ends. GitHub's
 * primary limit reports `X-RateLimit-Reset` as a Unix epoch in seconds.
 * Secondary / abuse limits use `Retry-After`, which is either an HTTP-date
 * or a delta in seconds — we normalize both to epoch seconds.
 */
function parseRateLimitReset(headers: Headers): number | null {
  const reset = headers.get('x-ratelimit-reset');
  if (reset) {
    const n = Number.parseInt(reset, 10);
    if (Number.isFinite(n) && n > 0) return n;
  }
  const retryAfter = headers.get('retry-after');
  if (retryAfter) {
    const delta = Number.parseInt(retryAfter, 10);
    if (Number.isFinite(delta) && delta >= 0) {
      return Math.floor(Date.now() / 1000) + delta;
    }
    const asDate = Date.parse(retryAfter);
    if (Number.isFinite(asDate)) return Math.floor(asDate / 1000);
  }
  return null;
}

function encodePath(path: string): string {
  // Encode segments individually so slashes remain.
  return path.split('/').map(encodeURIComponent).join('/');
}

function backoffMs(attempt: number, err: GitHubError): number {
  // Exponential with jitter; bump higher for rate-limit responses.
  const base = err.category === 'rate-limit' ? 5000 : 500;
  const jitter = Math.floor(Math.random() * 400);
  return Math.min(60_000, base * 2 ** (attempt - 1) + jitter);
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export function toBase64(bytes: string): string {
  // bytes is a UTF-8 string of JSON; encode to base64 the safe way.
  const utf8 = new TextEncoder().encode(bytes);
  let bin = '';
  for (const b of utf8) bin += String.fromCharCode(b);
  return btoa(bin);
}
