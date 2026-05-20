"""Remove ``raw/<handle>/`` directories for accounts that aren't tracked
*and* aren't referenced by a tracked account's tweets.

The Firefox extension's old USER_PAGE_ENDPOINTS filter kept every tweet in a
user-page GraphQL response, including replies under a tracked account's
tweets written by random third parties. Those replies landed in
``raw/<random_handle>/<timestamp>.json`` and would have been ingested into a
per-handle parquet on the next ``scripts/ingest.py`` run.

This script:

  1. Loads the tracked-handle list from ``config/accounts.yaml``.
  2. Walks every tweet in every ``raw/<tracked_handle>/*.json`` and collects
     the set of "related" external handles — anyone the tracked accounts
     mentioned, replied to, quoted, or retweeted.
  3. Any ``raw/<handle>/`` whose handle is neither tracked nor in that
     related set is moved into ``raw/_purged/`` (so nothing is destroyed
     irreversibly — the user can review and delete after).

Run with ``--dry-run`` to see what would happen without moving anything.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "raw"
CONFIG_PATH = REPO_ROOT / "config" / "accounts.yaml"
PURGED_DIR = RAW_DIR / "_purged"
QUARANTINE_DIR = RAW_DIR / "_quarantine"


def load_tracked() -> set[str]:
    if not CONFIG_PATH.exists():
        return set()
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return {
        str(a["handle"]).lower()
        for a in data.get("accounts", [])
        if isinstance(a, dict) and a.get("handle")
    }


def collect_related(tracked: set[str]) -> set[str]:
    related: set[str] = set()
    for handle in tracked:
        for parquet_dir in (RAW_DIR / handle,):
            if not parquet_dir.is_dir():
                continue
            for path in parquet_dir.glob("*.json"):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                tweets = payload.get("tweets") if isinstance(payload, dict) else None
                if not isinstance(tweets, list):
                    continue
                for t in tweets:
                    if not isinstance(t, dict):
                        continue
                    for m in t.get("mentions") or []:
                        if isinstance(m, str) and m:
                            related.add(m.lower())
                    rta = t.get("reply_to_account")
                    if isinstance(rta, str) and rta:
                        related.add(rta.lower())
                    # `urls` entries pointing at twitter.com/<handle> imply
                    # the tracked account linked to that handle's content.
                    for u in t.get("urls") or []:
                        if not isinstance(u, dict):
                            continue
                        exp = u.get("expanded") or ""
                        if not isinstance(exp, str):
                            continue
                        for prefix in ("https://x.com/", "https://twitter.com/"):
                            if exp.startswith(prefix):
                                tail = exp[len(prefix) :]
                                if "/" in tail:
                                    h = tail.split("/", 1)[0]
                                    if h and h.lower() not in {"i", "search", "explore"}:
                                        related.add(h.lower())
    return related


def directories_to_purge(tracked: set[str], related: set[str]) -> list[Path]:
    if not RAW_DIR.is_dir():
        return []
    keep = tracked | related
    out: list[Path] = []
    for child in sorted(RAW_DIR.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("_"):
            continue  # _quarantine, _purged, etc.
        if child.name.lower() in keep:
            continue
        out.append(child)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the directories that would be purged but don't move anything.",
    )
    args = parser.parse_args(argv)

    tracked = load_tracked()
    if not tracked:
        print("no tracked accounts found in config/accounts.yaml; refusing to purge", file=sys.stderr)
        return 1
    related = collect_related(tracked)
    targets = directories_to_purge(tracked, related)
    if not targets:
        print(f"nothing to purge ({len(tracked)} tracked, {len(related)} related)")
        return 0

    if args.dry_run:
        for t in targets:
            print(f"would purge {t}")
        print(f"\n{len(targets)} directories would be moved to {PURGED_DIR}/")
        return 0

    PURGED_DIR.mkdir(parents=True, exist_ok=True)
    moved = 0
    for t in targets:
        dest = PURGED_DIR / t.name
        if dest.exists():
            # Avoid clobbering an earlier purge of the same handle.
            dest = PURGED_DIR / f"{t.name}.{moved}"
        shutil.move(str(t), str(dest))
        print(f"purged {t.name} -> {dest.relative_to(REPO_ROOT)}")
        moved += 1
    print(f"\nmoved {moved} unrelated-handle directories to {PURGED_DIR}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
