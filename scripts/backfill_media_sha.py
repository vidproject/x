"""Backfill missing ``sha256`` / ``bytes`` on already-published media assets.

Some canonical rows carry a ``release_asset_url`` (the media *is* archived to
a GitHub release) yet have an empty ``sha256`` — the hash was never written
back into the parquet ``media`` struct. ``scripts.extract_video_frames`` and
the photo-thumbnail extractor both require ``sha256`` as their cache key /
on-disk directory name, so those rows are silently skipped and never get
keyframes.

This script closes that gap without re-uploading anything: it streams each
already-published ``release_asset_url``, computes the sha256 + byte length,
and patches the canonical ``data/<handle>.parquet`` ``media`` struct using the
same ``update_media_in_df`` / ``write_parquet`` helpers ``archive_media``
uses. It only ever fills an *empty* ``sha256`` (``--force`` re-checks all),
never rewrites a recorded one, so it is a pure capture-completion pass.

Idempotent: rows that already have a sha are cache hits on re-run.

Run with::

    uv run python -m scripts.backfill_media_sha --tweet-ids-file FILE
    uv run python -m scripts.backfill_media_sha --handle ICEgov --max-items 50
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from scripts._logging import configure
from scripts.archive_media import update_media_in_df, write_parquet

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"

HTTP_TIMEOUT_SECS = 120.0
MAX_BYTES = 600 * 1024 * 1024
VISUAL_TYPES = {"video", "animated_gif", "photo"}


def discover_parquets(handle: str | None) -> list[Path]:
    paths = sorted(
        p for p in DATA_DIR.glob("*.parquet") if p.is_file() and p.name != "catalog.parquet"
    )
    if handle:
        paths = [p for p in paths if p.stem == handle]
    return paths


def stream_hash(url: str, http: httpx.Client) -> tuple[str, int]:
    """Return (sha256, bytes) for ``url`` without holding the whole file."""
    h = hashlib.sha256()
    total = 0
    with http.stream("GET", url, timeout=HTTP_TIMEOUT_SECS, follow_redirects=True) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_bytes(chunk_size=1 << 20):
            total += len(chunk)
            if total > MAX_BYTES:
                raise ValueError(f"asset exceeds {MAX_BYTES} bytes")
            h.update(chunk)
    return h.hexdigest(), total


def backfill_handle(
    path: Path,
    http: httpx.Client,
    *,
    only_tweet_ids: set[str] | None,
    max_items: int | None,
    force: bool,
    media_types: set[str],
    dry_run: bool,
) -> dict[str, int]:
    df = pl.read_parquet(path)
    if df.height == 0:
        return {}
    updates: dict[str, dict[str, dict[str, Any]]] = {}
    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    stats: dict[str, int] = {"attempted": 0, "filled": 0, "failed": 0, "skipped": 0}

    for row in df.iter_rows(named=True):
        tid = str(row.get("tweet_id") or "")
        if only_tweet_ids is not None and tid not in only_tweet_ids:
            continue
        for m in row.get("media") or []:
            if not isinstance(m, dict):
                continue
            if str(m.get("media_type") or "") not in media_types:
                continue
            url = str(m.get("release_asset_url") or "")
            sha = str(m.get("sha256") or "")
            if not url:
                continue
            if sha and not force:
                stats["skipped"] += 1
                continue
            if max_items is not None and stats["attempted"] >= max_items:
                continue
            stats["attempted"] += 1
            mid = str(m.get("media_id") or "")
            try:
                new_sha, nbytes = stream_hash(url, http)
            except Exception as e:
                stats["failed"] += 1
                LOG.warning(
                    "backfill: hash failed",
                    handle=path.stem,
                    tweet_id=tid,
                    media_id=mid,
                    err=str(e)[:200],
                )
                continue
            if sha and sha != new_sha:
                LOG.warning(
                    "backfill: recorded sha differs from asset; leaving as-is",
                    handle=path.stem,
                    tweet_id=tid,
                    media_id=mid,
                )
                continue
            updates.setdefault(tid, {})[mid] = {
                "sha256": new_sha,
                "bytes": nbytes,
                "last_attempt_at": now_iso,
            }
            stats["filled"] += 1
            LOG.info(
                "backfill: filled sha",
                handle=path.stem,
                tweet_id=tid,
                media_id=mid,
                bytes=nbytes,
            )

    if updates and not dry_run:
        df = update_media_in_df(df, updates)
        write_parquet(df, path)
        LOG.info("backfill: wrote parquet", path=str(path), tweets=len(updates))
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handle", help="Restrict to one data/<handle>.parquet.")
    parser.add_argument("--tweet-ids-file", type=Path, help="Only these tweet_ids.")
    parser.add_argument("--max-items", type=int, help="Cap assets hashed per handle.")
    parser.add_argument("--force", action="store_true", help="Re-hash even rows with a sha.")
    parser.add_argument(
        "--photos", action="store_true", help="Include photo media (default: video/gif only)."
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    only: set[str] | None = None
    if args.tweet_ids_file:
        only = {
            line.strip()
            for line in args.tweet_ids_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    media_types = {"video", "animated_gif"}
    if args.photos:
        media_types = VISUAL_TYPES

    parquets = discover_parquets(args.handle)
    totals: dict[str, int] = {"attempted": 0, "filled": 0, "failed": 0, "skipped": 0}
    with httpx.Client(
        timeout=HTTP_TIMEOUT_SECS,
        follow_redirects=True,
        headers={"user-agent": "imm-archive-sha-backfill/1.0"},
    ) as http:
        for path in parquets:
            stats = backfill_handle(
                path,
                http,
                only_tweet_ids=only,
                max_items=args.max_items,
                force=args.force,
                media_types=media_types,
                dry_run=args.dry_run,
            )
            for k, v in stats.items():
                totals[k] = totals.get(k, 0) + v
    LOG.info("backfill: complete", **totals)
    return 0


if __name__ == "__main__":
    sys.exit(main())
