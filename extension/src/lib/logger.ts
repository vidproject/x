import { appendActivity } from './storage.js';
import type { LogEvent, LogLevel } from './types.js';

let broadcast: ((ev: LogEvent) => void) | null = null;

export function setBroadcaster(fn: (ev: LogEvent) => void): void {
  broadcast = fn;
}

function nowISO(): string {
  return new Date().toISOString();
}

async function emit(level: LogLevel, msg: string, context: Record<string, unknown>): Promise<void> {
  const ev: LogEvent = { ts: nowISO(), level, msg, context: redact(context) };
  // Console for devtools.
  const line = `[imm-archive] ${level.toUpperCase()} ${msg}`;
  if (level === 'error') console.error(line, ev.context);
  else if (level === 'warn') console.warn(line, ev.context);
  else console.log(line, ev.context);
  // Persistent tail.
  try {
    await appendActivity(ev);
  } catch (err) {
    console.warn('[imm-archive] failed to append activity', err);
  }
  // Live broadcast (e.g. to sidebar).
  try {
    broadcast?.(ev);
  } catch {
    // Sidebar may not be open; that's fine.
  }
}

export function info(msg: string, context: Record<string, unknown> = {}): Promise<void> {
  return emit('info', msg, context);
}
export function warn(msg: string, context: Record<string, unknown> = {}): Promise<void> {
  return emit('warn', msg, context);
}
export function error(msg: string, context: Record<string, unknown> = {}): Promise<void> {
  return emit('error', msg, context);
}

export function describeError(err: unknown): { message: string; stack: string | null } {
  if (err instanceof Error) {
    return { message: err.message, stack: err.stack ?? null };
  }
  try {
    return { message: String(err), stack: null };
  } catch {
    return { message: '<unprintable error>', stack: null };
  }
}

/**
 * Strip anything that looks like a PAT or other long secret from a context
 * object before persisting it. Defensive: callers should not log tokens, but
 * if they slip through we want to redact them.
 */
function redact(ctx: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(ctx)) {
    if (k.toLowerCase().includes('token') || k.toLowerCase() === 'pat' || k === 'authorization') {
      out[k] = '<redacted>';
    } else if (typeof v === 'string' && /github_pat_[A-Za-z0-9_]{30,}/.test(v)) {
      out[k] = v.replace(/github_pat_[A-Za-z0-9_]+/g, 'github_pat_<redacted>');
    } else if (typeof v === 'string' && v.length > 4096) {
      out[k] = `${v.slice(0, 4096)}…<truncated>`;
    } else {
      out[k] = v;
    }
  }
  return out;
}
