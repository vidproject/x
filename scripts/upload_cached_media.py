"""Upload media bytes from a local cache and patch canonical parquet rows.

This is the recovery path for VPS fetches that saved bytes locally before the
normal release upload/parquet patch step completed. It reads backup manifests
under ``.skim/media-backup`` (or another cache root), uploads only current rows
that still lack managed release assets, and applies the same media-struct
patching helpers as ``scripts.archive_media``.

Run with::

    uv run python -m scripts.upload_cached_media --token-file PATH
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import urllib.parse
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import polars as pl

from scripts._logging import configure
from scripts._misc_scope import load_account_categories, row_is_in_media_scope
from scripts.archive_media import (
    GitHubReleaseClient,
    ReleaseShard,
    ReleaseShardSet,
    asset_name_for,
    content_type_for,
    media_release_name,
    media_release_tag,
    rate_limit_wait_seconds,
    update_media_in_df,
    write_parquet,
)

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
DEFAULT_CACHE_ROOT = REPO_ROOT / ".skim" / "media-backup"
API_UPLOAD_TIMEOUT = httpx.Timeout(connect=30, read=1800, write=1800, pool=30)
UPLOAD_DELAY_SEC = float(os.environ.get("CACHE_UPLOAD_DELAY", "0.1"))


@dataclass(frozen=True)
class CachedMedia:
    media_id: str
    tweet_id: str
    account_handle: str
    media_type: str
    original_url: str
    sha256: str
    bytes: int
    content_type: str
    path: Path
    run_id: str


@dataclass
class UploadCandidate:
    parquet_path: Path
    tweet_id: str
    media: dict[str, Any]
    cached: CachedMedia


def iter_manifest_rows(cache_root: Path) -> Iterable[tuple[Path, dict[str, Any]]]:
    for index_path in sorted(cache_root.glob("*/index.jsonl")):
        with index_path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as e:
                    LOG.warning(
                        "cache-upload: skipping malformed manifest row",
                        path=str(index_path),
                        line=line_no,
                        err=str(e),
                    )
                    continue
                yield index_path, row


def local_object_path(index_path: Path, row: dict[str, Any]) -> Path | None:
    sha = str(row.get("sha256") or "").strip()
    if not sha:
        return None
    local_name = Path(str(row.get("local_path") or "")).name
    if not local_name:
        return None
    return index_path.parent / "objects" / sha[:2] / local_name


def load_cache(cache_root: Path) -> dict[str, CachedMedia]:
    cache: dict[str, CachedMedia] = {}
    stats = defaultdict(int)
    for index_path, row in iter_manifest_rows(cache_root):
        if row.get("status") != "ok":
            stats["non_ok"] += 1
            continue
        media_id = str(row.get("media_id") or "").strip()
        tweet_id = str(row.get("tweet_id") or "").strip()
        sha = str(row.get("sha256") or "").strip()
        path = local_object_path(index_path, row)
        if not media_id or not tweet_id or not sha or path is None:
            stats["incomplete"] += 1
            continue
        if not path.exists():
            stats["missing_file"] += 1
            continue
        size = path.stat().st_size
        expected_size = int(row.get("bytes") or 0)
        if expected_size and size != expected_size:
            stats["size_mismatch"] += 1
            LOG.warning(
                "cache-upload: object size mismatch; skipping",
                media_id=media_id,
                path=str(path),
                expected=expected_size,
                actual=size,
            )
            continue
        cache[media_id] = CachedMedia(
            media_id=media_id,
            tweet_id=tweet_id,
            account_handle=str(row.get("account_handle") or ""),
            media_type=str(row.get("media_type") or ""),
            original_url=str(row.get("original_url") or ""),
            sha256=sha,
            bytes=size,
            content_type=str(row.get("content_type") or ""),
            path=path,
            run_id=str(row.get("run_id") or index_path.parent.name),
        )
        stats["ok"] += 1
    LOG.info("cache-upload: loaded cache", cache_items=len(cache), **dict(stats))
    return cache


def discover_parquets(handle: str | None) -> list[Path]:
    paths = sorted(
        p for p in DATA_DIR.glob("*.parquet") if p.is_file() and p.name != "catalog.parquet"
    )
    if handle:
        paths = [p for p in paths if p.stem == handle]
    return paths


def media_source_urls(media: dict[str, Any]) -> set[str]:
    urls: set[str] = set()
    for key in ("original_url", "url", "media_url_https", "expanded_url"):
        value = str(media.get(key) or "").strip()
        if value:
            urls.add(value)
    return urls


def collect_candidates(
    cache: dict[str, CachedMedia],
    *,
    handle: str | None,
    max_items: int | None,
) -> list[UploadCandidate]:
    candidates: list[UploadCandidate] = []
    misc_categories = load_account_categories()
    for parquet_path in discover_parquets(handle):
        df = pl.read_parquet(parquet_path)
        for row in df.iter_rows(named=True):
            if not row_is_in_media_scope(
                row,
                handle=parquet_path.stem,
                categories=misc_categories,
            ):
                continue
            tweet_id = str(row.get("tweet_id") or "")
            for media in row.get("media") or []:
                if not isinstance(media, dict):
                    continue
                media_id = str(media.get("media_id") or "")
                cached = cache.get(media_id)
                if cached is None:
                    continue
                if (
                    cached.tweet_id
                    and cached.tweet_id != tweet_id
                    and cached.original_url not in media_source_urls(media)
                ):
                    continue
                if media.get("release_asset_url") and media.get("archive_status") == "archived":
                    continue
                candidates.append(UploadCandidate(parquet_path, tweet_id, media, cached))
                if max_items is not None and len(candidates) >= max_items:
                    return candidates
    return candidates


def stream_upload_asset(
    gh: GitHubReleaseClient,
    upload_url: str,
    *,
    name: str,
    content_type: str,
    path: Path,
) -> dict[str, Any]:
    base = re.sub(r"\{\?.*\}$", "", upload_url)
    size = path.stat().st_size
    last_response: httpx.Response | None = None
    for _attempt in range(20):
        with path.open("rb") as f:
            response = gh.session.post(
                base,
                params={"name": name},
                content=f,
                headers={"Content-Type": content_type, "Content-Length": str(size)},
                timeout=API_UPLOAD_TIMEOUT,
            )
        last_response = response
        wait = rate_limit_wait_seconds(response)
        if wait is not None:
            LOG.warning("cache-upload: upload rate-limited; backing off", name=name, wait_s=wait)
            time.sleep(wait)
            continue
        response.raise_for_status()
        return cast(dict[str, Any], response.json())
    assert last_response is not None
    last_response.raise_for_status()
    return cast(dict[str, Any], last_response.json())


def predictable_download_url(gh: GitHubReleaseClient, tag: str, asset_name: str) -> str:
    quoted_name = urllib.parse.quote(asset_name, safe="")
    return f"https://github.com/{gh.owner}/{gh.repo}/releases/download/{tag}/{quoted_name}"


def load_release_shards(
    handle: str, gh: GitHubReleaseClient, *, skip_asset_list: bool
) -> ReleaseShardSet:
    if not skip_asset_list:
        return ReleaseShardSet.load(handle, gh)
    shards: list[ReleaseShard] = []
    shard_index = 1
    while True:
        tag = media_release_tag(handle, shard_index)
        if shard_index == 1:
            release = gh.get_or_create_release(tag, media_release_name(handle, shard_index))
        else:
            release = gh.get_release(tag)
            if release is None:
                break
        shards.append(ReleaseShard(shard_index, release, {}))
        shard_index += 1
    return ReleaseShardSet(handle, gh, shards)


def upload_from_cache(
    release_shards: ReleaseShardSet,
    *,
    name: str,
    content_type: str,
    path: Path,
    skip_asset_list: bool,
) -> dict[str, Any]:
    while True:
        existing = release_shards.find_asset(name)
        if existing and existing.get("browser_download_url"):
            return existing
        shard = release_shards._upload_shard()
        try:
            uploaded = stream_upload_asset(
                release_shards.gh,
                str(shard.release["upload_url"]),
                name=name,
                content_type=content_type,
                path=path,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 422:
                raise
            if skip_asset_list:
                return {
                    "browser_download_url": predictable_download_url(
                        release_shards.gh,
                        shard.tag,
                        name,
                    ),
                    "size": path.stat().st_size,
                }
            existing = release_shards.refresh_and_find_asset(name)
            if existing and existing.get("browser_download_url"):
                return existing
            if release_shards._refresh_shard(shard).is_full:
                continue
            raise
        if not uploaded.get("browser_download_url"):
            raise ValueError("release asset upload response missing browser_download_url")
        shard.assets[name] = uploaded
        return uploaded


def patch_parquets(
    candidates: list[UploadCandidate],
    *,
    gh: GitHubReleaseClient,
    dry_run: bool,
    stitch_only: bool,
    skip_asset_list: bool,
) -> dict[str, int]:
    by_handle: dict[str, ReleaseShardSet] = {}
    updates_by_path: dict[Path, dict[str, dict[str, dict[str, Any]]]] = defaultdict(dict)
    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    stats = {
        "uploaded": 0,
        "stitched": 0,
        "skipped_missing": 0,
        "failed": 0,
        "candidates": len(candidates),
    }

    for candidate in candidates:
        media = candidate.media
        cached = candidate.cached
        handle = candidate.parquet_path.stem
        media_id = str(media.get("media_id") or "")
        attempts = int(media.get("archive_attempts") or 0)
        try:
            asset_name = asset_name_for(media)
            content_type = cached.content_type or content_type_for(Path(asset_name).suffix)
            if handle not in by_handle:
                by_handle[handle] = load_release_shards(
                    handle,
                    gh,
                    skip_asset_list=skip_asset_list,
                )
            release_shards = by_handle[handle]
            existing = release_shards.find_asset(asset_name)
            if stitch_only and not existing:
                stats["skipped_missing"] += 1
                continue
            if dry_run:
                url = str(existing.get("browser_download_url")) if existing else ""
                uploaded = {"browser_download_url": url, "size": cached.bytes}
            elif existing and existing.get("browser_download_url"):
                uploaded = existing
                stats["stitched"] += 1
            else:
                uploaded = upload_from_cache(
                    release_shards,
                    name=asset_name,
                    content_type=content_type,
                    path=cached.path,
                    skip_asset_list=skip_asset_list,
                )
                stats["uploaded"] += 1
                time.sleep(UPLOAD_DELAY_SEC)
            url = str(uploaded.get("browser_download_url") or "")
            if not dry_run and not url:
                raise ValueError("missing browser_download_url")
            per_tweet = updates_by_path[candidate.parquet_path].setdefault(candidate.tweet_id, {})
            per_tweet[media_id] = {
                "release_asset_url": url or media.get("release_asset_url"),
                "sha256": cached.sha256,
                "bytes": int(uploaded.get("size") or cached.bytes),
                "archive_status": "archived",
                "archive_attempts": attempts if existing else attempts + 1,
                "last_attempt_at": now_iso,
            }
            LOG.info(
                "cache-upload: archived",
                handle=handle,
                tweet_id=candidate.tweet_id,
                media_id=media_id,
                asset=asset_name,
                bytes=cached.bytes,
                dry_run=dry_run,
            )
        except Exception as e:
            stats["failed"] += 1
            LOG.warning(
                "cache-upload: failed",
                handle=handle,
                tweet_id=candidate.tweet_id,
                media_id=media_id,
                err=str(e)[:500],
            )

    if dry_run:
        return stats

    for parquet_path, updates in updates_by_path.items():
        if not updates:
            continue
        df = pl.read_parquet(parquet_path)
        df = update_media_in_df(df, updates)
        write_parquet(df, parquet_path)
        LOG.info("cache-upload: wrote parquet", path=str(parquet_path), tweets=len(updates))
    return stats


def load_gcm_token(username: str | None) -> str:
    query = "protocol=https\nhost=github.com\n"
    if username:
        query += f"username={username}\n"
    query += "\n"
    result = subprocess.run(
        ["git", "credential", "fill"],
        input=query,
        text=True,
        capture_output=True,
        check=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("password="):
            token = line.split("=", 1)[1].strip()
            if token:
                return token
    raise SystemExit("GCM did not return a password/token")


def load_token(path: Path | None, *, use_gcm: bool, gcm_username: str | None) -> str:
    if use_gcm:
        return load_gcm_token(gcm_username)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token.strip()
    if path is not None:
        return path.read_text(encoding="utf-8").strip()
    raise SystemExit("missing GH_TOKEN/GITHUB_TOKEN or --token-file")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
    parser.add_argument("--token-file", type=Path, help="Read a GitHub token from this file.")
    parser.add_argument(
        "--use-gcm",
        action="store_true",
        help="Read the GitHub HTTPS credential from Git Credential Manager.",
    )
    parser.add_argument("--gcm-username", default="vidproject")
    parser.add_argument("--owner", default=os.environ.get("GITHUB_REPOSITORY_OWNER", "vidproject"))
    parser.add_argument(
        "--repo", default=(os.environ.get("GITHUB_REPOSITORY", "").split("/")[-1] or "x")
    )
    parser.add_argument("--handle", help="Restrict to one data/<handle>.parquet.")
    parser.add_argument("--max-items", type=int, help="Cap items this invocation uploads/patches.")
    parser.add_argument(
        "--stitch-only",
        action="store_true",
        help="Patch parquet for assets that already exist on Releases; do not upload new assets.",
    )
    parser.add_argument(
        "--skip-asset-list",
        action="store_true",
        help="Avoid listing release assets before upload; duplicate 422s become deterministic URLs.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    cache = load_cache(args.cache_root)
    candidates = collect_candidates(cache, handle=args.handle, max_items=args.max_items)
    LOG.info("cache-upload: candidates", count=len(candidates), dry_run=args.dry_run)
    if not candidates:
        return 0

    token = load_token(args.token_file, use_gcm=args.use_gcm, gcm_username=args.gcm_username)
    gh = GitHubReleaseClient(args.owner, args.repo, token)
    try:
        stats = patch_parquets(
            candidates,
            gh=gh,
            dry_run=args.dry_run,
            stitch_only=args.stitch_only,
            skip_asset_list=args.skip_asset_list,
        )
    finally:
        gh.close()
    LOG.info("cache-upload: done", **stats)
    return 1 if stats.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
