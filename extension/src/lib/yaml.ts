import type { AccountConfig } from './types.js';

/**
 * Minimal parser for the strict shape used by `config/accounts.yaml`:
 *
 *   accounts:
 *     - handle: DHSgov
 *       label: Department of Homeland Security
 *
 * We deliberately don't pull in a full YAML library — we control both ends of
 * this file, and a 50-line parser is easier to audit than the alternatives.
 * Unknown keys and comments are tolerated; malformed lines are skipped.
 */
export function parseAccountsYaml(text: string): AccountConfig[] {
  const accounts: AccountConfig[] = [];
  let current: Partial<AccountConfig> = {};
  let inAccounts = false;
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
      if (current.handle && current.label) accounts.push(current as AccountConfig);
      current = { handle: unquote(itemMatch[1] ?? '') };
      continue;
    }
    const handleOnly = line.match(/^\s*handle\s*:\s*(.+)$/);
    if (handleOnly && !line.includes('-')) {
      if (current.handle && current.label) accounts.push(current as AccountConfig);
      current = { handle: unquote(handleOnly[1] ?? '') };
      continue;
    }
    const labelMatch = line.match(/^\s*label\s*:\s*(.+)$/);
    if (labelMatch && current.handle) {
      current.label = unquote(labelMatch[1] ?? '');
      continue;
    }
  }
  if (current.handle && current.label) accounts.push(current as AccountConfig);
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
