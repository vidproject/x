"""Download tweet media to GitHub Release assets, populating parquet rows.

For every media item with ``archive_status == "pending"`` (or ``"failed"``
within the retry budget), this script:

  1. Downloads the bytes from ``original_url`` via httpx.
  2. Computes sha256 and byte length.
  3. Uploads them to a per-handle GitHub Release as an asset.
  4. Patches the parquet row so ``release_asset_url``, ``sha256``, ``bytes``,
     ``archive_status="archived"``, ``archive_attempts``, and
     ``last_attempt_at`` reflect the result.

The script is idempotent: re-running only touches items still missing an
asset url, and existing assets aren't re-uploaded. Failures bump
``archive_attempts``; after :data:`MAX_ATTEMPTS` we mark ``"expired"`` so the
viewer can surface that the original URL has aged out (twimg's signed URLs
are short-lived).

Run via ``uv run python -m scripts.archive_media`` locally, or via the
``archive-media`` workflow in CI (needs ``GITHUB_TOKEN`` to write releases).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import polars as pl

from scripts._logging import configure
from scripts._schema import TWEET_SCHEMA

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
API_BASE = "https://api.github.com"

# After this many consecutive failures we stop retrying a media item and mark
# it ``expired`` — typically twimg signed URLs that have aged out.
MAX_ATTEMPTS = 4

# Soft cap on each invocation: keeps a scheduled run bounded even when the
# archive falls behind, so CI minutes don't blow up. Override with --max-items.
DEFAULT_MAX_ITEMS = 200

# httpx fetch is fairly tolerant — we want a generous timeout for large videos.
DOWNLOAD_TIMEOUT = httpx.Timeout(connect=10, read=120, write=30, pool=10)

# GitHub release asset upload happens on a different host.
UPLOAD_TIMEOUT = httpx.Timeout(connect=10, read=300, write=300, pool=10)


def media_release_tag(handle: str) -> str:
    return f"media-{handle}"


def media_release_name(handle: str) -> str:
    return f"Media archive — @{handle}"


# --------------------------------------------------------------------------
# GitHub Releases helpers


class GitHubReleaseClient:
    def __init__(self, owner: str, repo: str, token: str) -> None:
        self.owner = owner
        self.repo = repo
        self.session = httpx.Client(
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "imm-archive-media/1.0",
            },
            timeout=httpx.Timeout(connect=10, read=60, write=30, pool=10),
        )

    def close(self) -> None:
        self.session.close()

    def get_or_create_release(self, tag: str, name: str) -> dict[str, Any]:
        r = self.session.get(f"{API_BASE}/repos/{self.owner}/{self.repo}/releases/tags/{tag}")
        if r.status_code == 200:
            return cast(dict[str, Any], r.json())
        if r.status_code != 404:
            r.raise_for_status()
        # Create.
        body = {
            "tag_name": tag,
            "name": name,
            "body": (
                "Media assets archived from public X posts. "
                "Auto-managed by scripts/archive_media.py."
            ),
            "draft": False,
            "prerelease": False,
        }
        c = self.session.post(
            f"{API_BASE}/repos/{self.owner}/{self.repo}/releases",
            json=body,
        )
        c.raise_for_status()
        return cast(dict[str, Any], c.json())

    def list_existing_assets(self, release_id: int) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        page = 1
        while True:
            r = self.session.get(
                f"{API_BASE}/repos/{self.owner}/{self.repo}/releases/{release_id}/assets",
                params={"per_page": 100, "page": page},
            )
            if r.status_code == 403:
                LOG.warning(
                    "archive: release asset listing forbidden; uploads will continue without duplicate preflight",
                    release_id=release_id,
                )
                return out
            r.raise_for_status()
            items = cast(list[dict[str, Any]], r.json())
            if not items:
                break
            for a in items:
                out[a["name"]] = a
            if len(items) < 100:
                break
            page += 1
        return out

    def upload_asset(
        self,
        upload_url: str,
        name: str,
        content_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        # The release API exposes a templated `upload_url` of the form
        # "https://uploads.github.com/.../assets{?name,label}". Strip the
        # template suffix and pass `name` ourselves.
        base = re.sub(r"\{\?.*\}$", "", upload_url)
        r = self.session.post(
            base,
            params={"name": name},
            content=data,
            headers={"Content-Type": content_type},
            timeout=UPLOAD_TIMEOUT,
        )
        r.raise_for_status()
        return cast(dict[str, Any], r.json())

    def browser_download_url(self, tag: str, asset_name: str) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/releases/download/{tag}/{asset_name}"


# --------------------------------------------------------------------------
# Per-tweet processing


def extension_for(media_type: str | None, url: str) -> str:
    if media_type == "photo":
        m = re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", url, re.IGNORECASE)
        return f".{m.group(1).lower()}" if m else ".jpg"
    if media_type in {"video", "animated_gif"}:
        m = re.search(r"\.(mp4|m4v|webm|mov)(\?|$)", url, re.IGNORECASE)
        return f".{m.group(1).lower()}" if m else ".mp4"
    return ".bin"


def content_type_for(ext: str) -> str:
    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
    }
    return mapping.get(ext, "application/octet-stream")


def asset_name_for(media: dict[str, Any]) -> str:
    media_id = str(media.get("media_id") or "").strip()
    if not media_id:
        raise ValueError("media has no media_id")
    # Replace any chars GitHub rejects in asset names with `-`.
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", media_id)
    return safe + extension_for(media.get("media_type"), str(media.get("original_url") or ""))


def fetch_bytes(url: str, http: httpx.Client) -> bytes:
    r = http.get(url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT)
    r.raise_for_status()
    return r.content


def candidates_from_row(
    row: dict[str, Any],
    *,
    tweet_ids: set[str] | None = None,
    media_ids: set[str] | None = None,
) -> Iterable[tuple[int, dict[str, Any]]]:
    tid = str(row.get("tweet_id") or "")
    if tweet_ids is not None and tid not in tweet_ids:
        return
    media = row.get("media") or []
    for idx, m in enumerate(media):
        if not isinstance(m, dict):
            continue
        mid = str(m.get("media_id") or "")
        if media_ids is not None and mid not in media_ids:
            continue
        if m.get("release_asset_url"):
            continue  # already archived
        if not m.get("original_url"):
            continue
        attempts = int(m.get("archive_attempts") or 0)
        status = m.get("archive_status") or "pending"
        if status == "expired":
            continue
        if status == "failed" and attempts >= MAX_ATTEMPTS:
            continue
        yield idx, m


# --------------------------------------------------------------------------
# Parquet read/write


def load_parquet(path: Path) -> pl.DataFrame:
    return pl.read_parquet(path)


def write_parquet(df: pl.DataFrame, path: Path) -> None:
    tmp = path.with_suffix(".tmp.parquet")
    df.write_parquet(tmp, compression="zstd", statistics=True)
    os.replace(tmp, path)


def update_media_in_df(
    df: pl.DataFrame, updates: dict[str, dict[str, dict[str, Any]]]
) -> pl.DataFrame:
    """Apply ``updates[tweet_id][media_id] = patch`` into the dataframe.

    Mutates the ``media`` list-of-struct column in place by row, then rebuilds
    the column with the parquet schema. Polars makes piecewise struct mutation
    awkward; the cleanest path for the volumes we deal with (~10k rows max
    per handle) is to round-trip through python dicts.
    """
    if not updates:
        return df
    rows = df.to_dicts()
    for row in rows:
        tid = str(row.get("tweet_id") or "")
        if tid not in updates:
            continue
        media = row.get("media") or []
        per_media = updates[tid]
        for m in media:
            mid = str((m or {}).get("media_id") or "")
            patch = per_media.get(mid)
            if patch:
                m.update(patch)
        row["media"] = media
    return pl.DataFrame(rows, schema=TWEET_SCHEMA, strict=False)


# --------------------------------------------------------------------------
# Main


def archive_one_handle(
    handle: str,
    parquet_path: Path,
    gh: GitHubReleaseClient,
    http: httpx.Client,
    max_items: int,
    *,
    tweet_ids: set[str] | None = None,
    media_ids: set[str] | None = None,
) -> tuple[int, int, int]:
    """Returns (archived, failed, skipped) counts for this handle."""
    df = load_parquet(parquet_path)
    if df.height == 0:
        return 0, 0, 0

    # Collect candidates.
    todo: list[tuple[str, dict[str, Any]]] = []  # (tweet_id, media)
    for r in df.iter_rows(named=True):
        for _idx, m in candidates_from_row(r, tweet_ids=tweet_ids, media_ids=media_ids):
            todo.append((str(r["tweet_id"]), m))
            if len(todo) >= max_items:
                break
        if len(todo) >= max_items:
            break
    if not todo:
        return 0, 0, 0

    LOG.info("archive: pending", handle=handle, count=len(todo))
    release = gh.get_or_create_release(media_release_tag(handle), media_release_name(handle))
    existing_assets = gh.list_existing_assets(release["id"])

    updates: dict[str, dict[str, dict[str, Any]]] = {}
    archived = failed = skipped = 0
    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    for tweet_id, media in todo:
        try:
            asset_name = asset_name_for(media)
        except ValueError:
            skipped += 1
            continue
        per = updates.setdefault(tweet_id, {})
        mid = str(media.get("media_id"))
        attempts = int(media.get("archive_attempts") or 0)
        existing = existing_assets.get(asset_name)
        if existing and existing.get("browser_download_url"):
            # Asset already on the release; just stitch up the parquet so the
            # next run isn't waste work.
            per[mid] = {
                "release_asset_url": existing["browser_download_url"],
                "bytes": int(existing.get("size") or 0) or media.get("bytes"),
                "archive_status": "archived",
                "last_attempt_at": now_iso,
                "archive_attempts": attempts,
            }
            archived += 1
            continue
        try:
            data = fetch_bytes(str(media["original_url"]), http)
        except Exception as e:
            failed += 1
            per[mid] = {
                "archive_status": "failed",
                "archive_attempts": attempts + 1,
                "last_attempt_at": now_iso,
            }
            if attempts + 1 >= MAX_ATTEMPTS:
                per[mid]["archive_status"] = "expired"
            LOG.warning(
                "archive: fetch failed",
                handle=handle,
                tweet_id=tweet_id,
                media_id=mid,
                err=str(e),
            )
            continue
        sha = hashlib.sha256(data).hexdigest()
        ext = Path(asset_name).suffix
        ct = content_type_for(ext)
        try:
            uploaded = gh.upload_asset(release["upload_url"], asset_name, ct, data)
        except Exception as e:
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 422:
                per[mid] = {
                    "release_asset_url": gh.browser_download_url(media_release_tag(handle), asset_name),
                    "sha256": sha,
                    "bytes": len(data),
                    "archive_status": "archived",
                    "archive_attempts": attempts + 1,
                    "last_attempt_at": now_iso,
                }
                archived += 1
                LOG.info(
                    "archive: asset already exists; stitched parquet URL",
                    handle=handle,
                    tweet_id=tweet_id,
                    media_id=mid,
                )
                continue
            failed += 1
            per[mid] = {
                "archive_status": "failed",
                "archive_attempts": attempts + 1,
                "last_attempt_at": now_iso,
                "sha256": sha,
                "bytes": len(data),
            }
            LOG.warning(
                "archive: upload failed",
                handle=handle,
                tweet_id=tweet_id,
                media_id=mid,
                err=str(e),
            )
            continue
        per[mid] = {
            "release_asset_url": uploaded.get("browser_download_url"),
            "sha256": sha,
            "bytes": len(data),
            "archive_status": "archived",
            "archive_attempts": attempts + 1,
            "last_attempt_at": now_iso,
        }
        archived += 1
        # twimg can rate-limit; a tiny pause is friendly.
        time.sleep(0.05)

    if updates:
        df = update_media_in_df(df, updates)
        write_parquet(df, parquet_path)
    return archived, failed, skipped


def discover_handles(only: str | None) -> list[str]:
    if only:
        return [only]
    return sorted(p.stem for p in DATA_DIR.glob("*.parquet") if p.name != "catalog.parquet")


def load_id_file(path: Path | None) -> set[str] | None:
    if not path:
        return None
    ids: set[str] = set()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            ids.add(value.split(",", 1)[0].strip())
    return ids


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--handle", help="Archive only this handle.")
    p.add_argument("--tweet-ids-file", type=Path, help="Archive only tweets listed in this newline/CSV file.")
    p.add_argument("--media-ids-file", type=Path, help="Archive only media IDs listed in this newline/CSV file.")
    p.add_argument(
        "--max-items",
        type=int,
        default=DEFAULT_MAX_ITEMS,
        help="Soft cap on media downloaded per invocation.",
    )
    p.add_argument(
        "--owner",
        default=os.environ.get("GITHUB_REPOSITORY_OWNER", "vidproject"),
        help="GitHub repo owner. Defaults to $GITHUB_REPOSITORY_OWNER.",
    )
    p.add_argument(
        "--repo",
        default=(os.environ.get("GITHUB_REPOSITORY", "").split("/")[-1] or "x"),
        help="GitHub repo name. Defaults to the trailing segment of $GITHUB_REPOSITORY.",
    )
    args = p.parse_args(argv)

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        LOG.error("archive: missing GH_TOKEN/GITHUB_TOKEN; cannot upload assets")
        return 2

    handles = discover_handles(args.handle)
    if not handles:
        LOG.info("archive: no parquet files found; nothing to do")
        return 0

    gh = GitHubReleaseClient(args.owner, args.repo, token)
    tweet_ids = load_id_file(args.tweet_ids_file)
    media_ids = load_id_file(args.media_ids_file)
    http = httpx.Client(
        headers={"User-Agent": "imm-archive-media/1.0"},
        follow_redirects=True,
    )
    totals = {"archived": 0, "failed": 0, "skipped": 0}
    remaining = args.max_items
    try:
        for handle in handles:
            if remaining <= 0:
                break
            path = DATA_DIR / f"{handle}.parquet"
            if not path.exists():
                continue
            a, f, s = archive_one_handle(
                handle,
                path,
                gh,
                http,
                remaining,
                tweet_ids=tweet_ids,
                media_ids=media_ids,
            )
            totals["archived"] += a
            totals["failed"] += f
            totals["skipped"] += s
            remaining -= a + f
            LOG.info("archive: handle done", handle=handle, **{"archived": a, "failed": f})
    finally:
        gh.close()
        http.close()
    LOG.info("archive: complete", **totals, handles=len(handles))
    return 0


if __name__ == "__main__":
    sys.exit(main())
