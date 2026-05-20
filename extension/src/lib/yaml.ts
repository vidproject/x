import type { AccountCategory, AccountConfig } from './types.js';

/**
 * Minimal parser for the strict shape used by `config/accounts.yaml`:
 *
 *   accounts:
 *     - handle: DHSgov
 *       label: Department of Homeland Security
 *       category: core
 *
 * We deliberately don't pull in a full YAML library — we control both ends of
 * this file, and a small parser is easier to audit than the alternatives.
 * Unknown keys and comments are tolerated; malformed lines are skipped.
 *
 * Entries missing a `category` default to `core` for backward compatibility
 * with the pre-categorization file shape.
 */
const VALID_CATEGORIES: ReadonlySet<AccountCategory> = new Set([
  'core',
  'government',
  'officials',
  'public_figures',
  'public',
]);

export function parseAccountsYaml(text: string): AccountConfig[] {
  const accounts: AccountConfig[] = [];
  let current: Partial<AccountConfig> = {};
  let inAccounts = false;
  const flush = () => {
    if (current.handle && current.label) {
      if (!current.category) current.category = 'core';
      accounts.push(current as AccountConfig);
    }
  };
  for (const rawLine of text.split('\n')) {
    const line = stripComments(rawLine);
    if (line.trim() === '') continue;
    if (/^accounts\s*:/.test(line)) {
      inAccounts = true;
      continue;
    }
    if (!inAccounts) continue;

    const itemMatch = line.match(/^\s*-\s*handle\s*:\s*(.+)$/);
    if (itemMatch) {
      flush();
      current = { handle: unquote(itemMatch[1] ?? '') };
      continue;
    }
    const handleOnly = line.match(/^\s*handle\s*:\s*(.+)$/);
    if (handleOnly && !line.includes('-')) {
      flush();
      current = { handle: unquote(handleOnly[1] ?? '') };
      continue;
    }
    const labelMatch = line.match(/^\s*label\s*:\s*(.+)$/);
    if (labelMatch && current.handle) {
      current.label = unquote(labelMatch[1] ?? '');
      continue;
    }
    const categoryMatch = line.match(/^\s*category\s*:\s*(.+)$/);
    if (categoryMatch && current.handle) {
      const raw = unquote(categoryMatch[1] ?? '');
      current.category = VALID_CATEGORIES.has(raw as AccountCategory)
        ? (raw as AccountCategory)
        : 'core';
      continue;
    }
  }
  flush();
  return accounts.filter((a) => a.handle.length > 0);
}

function stripComments(line: string): string {
  // Naive but adequate for our format: strip everything after a `#` that isn't
  // inside quotes. Our config never uses inline strings with `#`.
  const idx = line.indexOf('#');
  return idx === -1 ? line : line.slice(0, idx);
}

function unquote(s: string): string {
  const trimmed = s.trim();
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1);
  }
  return trimmed;
}
