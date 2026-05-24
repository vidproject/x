"""Tiny downscaled thumbnails for archived photos.

For every archived photo, fetch the GitHub Release asset, downscale it to a
small JPEG with ffmpeg, and write it under ``data/thumbnails/photo/`` (mirroring
the video posters from ``scripts.extract_video_frames``). The committed
thumbnail makes the photo cheaply inspectable locally — turning the photo
``media:needs-vision`` backlog into something drainable by review without
re-fetching the full-resolution asset each time.

The output sidecar is ``data/tags/photo_thumbnails.parquet``. Canonical tweet
parquets stay untouched.

Run with::

    uv run python -m scripts.extract_photo_thumbnails --max-items 200
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from scripts._logging import configure
from scripts._schema import PHOTO_THUMBNAIL_SCHEMA, empty_photo_thumbnail_dataframe

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"
THUMBNAILS_DIR = DATA_DIR / "thumbnails" / "photo"
OUT_PATH = TAGS_DIR / "photo_thumbnails.parquet"
MANIFEST_PATH = TAGS_DIR / "manifest.json"

EXTRACTOR_VERSION = "photo-thumbnail-v1"
DEFAULT_THUMB_WIDTH = 160
HTTP_TIMEOUT_SECS = 60.0
MAX_PHOTO_BYTES = 60 * 1024 * 1024
CACHEABLE_STATUSES = {"ok"}


@dataclass(frozen=True)
class PhotoCandidate:
    tweet_id: str
    account_handle: str
    media_id: str
    media_sha256: str
    release_asset_url: str


@dataclass
class ThumbResult:
    status: str
    thumbnail_path: str | None = None
    thumbnail_sha256: str | None = None
    thumbnail_width: int = 0
    thumbnail_height: int = 0
    thumbnail_bytes: int = 0
    error: str | None = None


def discover_candidates(parquets: list[Path]) -> Iterator[PhotoCandidate]:
    for path in parquets:
        try:
            df = pl.read_parquet(path)
        except Exception:
            LOG.exception("photo-thumb: could not read parquet", path=str(path))
            continue
        for tweet in df.iter_rows(named=True):
            media = tweet.get("media") or []
            if not isinstance(media, list):
                continue
            for item in media:
                if not isinstance(item, dict):
                    continue
                if str(item.get("media_type") or "") != "photo":
                    continue
                asset_url = str(item.get("release_asset_url") or "")
                sha = str(item.get("sha256") or "")
                media_id = str(item.get("media_id") or "")
                if not asset_url or not sha or not media_id:
                    continue
                yield PhotoCandidate(
                    tweet_id=str(tweet.get("tweet_id") or ""),
                    account_handle=str(tweet.get("account_handle") or ""),
                    media_id=media_id,
                    media_sha256=sha,
                    release_asset_url=asset_url,
                )


def input_hash_for(cand: PhotoCandidate) -> str:
    payload = {
        "extractor_version": EXTRACTOR_VERSION,
        "media_sha256": cand.media_sha256,
        "release_asset_url": cand.release_asset_url,
        "width": DEFAULT_THUMB_WIDTH,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_existing_index(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    df = pl.read_parquet(path)
    out: dict[str, dict[str, Any]] = {}
    for row in df.iter_rows(named=True):
        sha = str(row.get("media_sha256") or "")
        if sha:
            out[sha] = row
    return out


def is_cache_hit(cached: dict[str, Any]) -> bool:
    if not cached:
        return False
    if str(cached.get("extractor_version") or "") != EXTRACTOR_VERSION:
        return False
    if str(cached.get("status") or "") not in CACHEABLE_STATUSES:
        return False
    thumb = str(cached.get("thumbnail_path") or "")
    return bool(thumb) and (REPO_ROOT / thumb).exists()


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def fetch_to_tempfile(url: str, http: httpx.Client) -> Path:
    with http.stream("GET", url, timeout=HTTP_TIMEOUT_SECS, follow_redirects=True) as resp:
        resp.raise_for_status()
        total = 0
        suffix = Path(url.split("?", 1)[0]).suffix or ".jpg"
        with tempfile.NamedTemporaryFile(
            prefix="imm-archive-photo-", suffix=suffix, delete=False
        ) as fh:
            tmp_path = Path(fh.name)
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                total += len(chunk)
                if total > MAX_PHOTO_BYTES:
                    tmp_path.unlink(missing_ok=True)
                    raise ValueError(f"photo exceeds {MAX_PHOTO_BYTES} bytes")
                fh.write(chunk)
        return tmp_path


def downscale(src: Path, dest: Path, *, width: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_output(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(src),
            "-vf",
            f"scale={width}:-2:flags=area",
            "-frames:v",
            "1",
            "-y",
            str(dest),
        ],
        timeout=120,
    )


def probe_dimensions(path: Path) -> tuple[int, int]:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ],
        timeout=60,
    )
    streams = json.loads(out).get("streams") or []
    if not streams:
        return (0, 0)
    return (int(streams[0].get("width") or 0), int(streams[0].get("height") or 0))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_candidate(
    cand: PhotoCandidate, *, http: httpx.Client, thumbnail_root: Path, width: int
) -> ThumbResult:
    if not ffmpeg_available():
        return ThumbResult(status="skipped-no-ffmpeg", error="ffmpeg / ffprobe not on PATH")
    try:
        local = fetch_to_tempfile(cand.release_asset_url, http)
    except Exception as e:
        return ThumbResult(
            status="photo-too-large" if isinstance(e, ValueError) else "fetch-failed", error=str(e)
        )
    dest = thumbnail_root / f"{cand.media_sha256}.jpg"
    try:
        try:
            downscale(local, dest, width=width)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return ThumbResult(status="ffmpeg-failed", error=str(e))
        if not dest.exists() or dest.stat().st_size == 0:
            return ThumbResult(status="ffmpeg-failed", error="empty thumbnail")
        w, h = probe_dimensions(dest)
        rel = dest.relative_to(REPO_ROOT).as_posix()
        return ThumbResult(
            status="ok",
            thumbnail_path=rel,
            thumbnail_sha256=_sha256_file(dest),
            thumbnail_width=w,
            thumbnail_height=h,
            thumbnail_bytes=dest.stat().st_size,
        )
    finally:
        with contextlib.suppress(OSError):
            local.unlink(missing_ok=True)


def build_row(cand: PhotoCandidate, result: ThumbResult, *, generated_at: str) -> dict[str, Any]:
    return {
        "tweet_id": cand.tweet_id,
        "account_handle": cand.account_handle,
        "media_id": cand.media_id,
        "media_sha256": cand.media_sha256,
        "release_asset_url": cand.release_asset_url,
        "input_hash": input_hash_for(cand),
        "generated_at": generated_at,
        "extractor_version": EXTRACTOR_VERSION,
        "thumbnail_path": result.thumbnail_path,
        "thumbnail_sha256": result.thumbnail_sha256,
        "thumbnail_width": result.thumbnail_width,
        "thumbnail_height": result.thumbnail_height,
        "thumbnail_bytes": result.thumbnail_bytes,
        "status": result.status,
        "cost_estimate_usd": 0.0,
        "error": result.error,
    }


def write_parquet(rows: list[dict[str, Any]], path: Path) -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    df = (
        pl.DataFrame(rows, schema=PHOTO_THUMBNAIL_SCHEMA, strict=False)
        if rows
        else empty_photo_thumbnail_dataframe()
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.write_parquet(tmp, compression="zstd")
    os.replace(tmp, path)


def _layer_without_timestamp(layer: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in layer.items() if k != "generated_at"}


def update_manifest(rows: list[dict[str, Any]], stats: dict[str, int], generated_at: str) -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    layers = manifest.get("layers")
    if not isinstance(layers, dict):
        layers = {}
    status_counts = dict(Counter(str(r.get("status") or "") for r in rows))
    new_layer = {
        "generated_at": generated_at,
        "extractor_version": EXTRACTOR_VERSION,
        "row_count": len(rows),
        "thumbnails": sum(1 for r in rows if str(r.get("status")) == "ok"),
        "cost_estimate_usd": 0.0,
        "status_counts": status_counts,
        **stats,
    }
    prior = layers.get("photo_thumbnails")
    if (
        isinstance(prior, dict)
        and prior.get("generated_at")
        and _layer_without_timestamp(prior) == _layer_without_timestamp(new_layer)
    ):
        new_layer["generated_at"] = prior["generated_at"]
    layers["photo_thumbnails"] = new_layer
    manifest["layers"] = layers
    tmp = MANIFEST_PATH.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, MANIFEST_PATH)


def discover_canonical_parquets() -> list[Path]:
    return sorted(
        p for p in DATA_DIR.glob("*.parquet") if p.is_file() and p.name != "catalog.parquet"
    )


def run(
    *,
    parquets: list[Path] | None = None,
    max_items: int | None = None,
    force: bool = False,
    dry_run: bool = False,
    width: int = DEFAULT_THUMB_WIDTH,
    out_path: Path | None = None,
    thumbnail_root: Path | None = None,
    only_tweet_ids: set[str] | None = None,
    extractor: Callable[[PhotoCandidate], ThumbResult] | None = None,
) -> dict[str, int]:
    if out_path is None:
        out_path = OUT_PATH
    if thumbnail_root is None:
        thumbnail_root = THUMBNAILS_DIR
    parquets = parquets if parquets is not None else discover_canonical_parquets()
    existing = load_existing_index(out_path)
    rows: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    http: httpx.Client | None = None
    runner: Callable[[PhotoCandidate], ThumbResult]
    if extractor is None:
        http = httpx.Client(
            timeout=HTTP_TIMEOUT_SECS,
            follow_redirects=True,
            headers={"user-agent": "imm-archive-photo-thumb/1.0"},
        )
        assert thumbnail_root is not None
        _root = thumbnail_root
        _http = http

        def _real(c: PhotoCandidate) -> ThumbResult:
            return extract_candidate(c, http=_http, thumbnail_root=_root, width=width)

        runner = _real
    else:
        runner = extractor

    try:
        seen_sha: set[str] = set()
        for cand in discover_candidates(parquets):
            if only_tweet_ids is not None and cand.tweet_id not in only_tweet_ids:
                stats["skipped_not_in_filter"] += 1
                continue
            cached = existing.get(cand.media_sha256)
            if not force and is_cache_hit(cached or {}):
                row = {**(cached or {})}
                row["tweet_id"] = cand.tweet_id
                row["account_handle"] = cand.account_handle
                row["media_id"] = cand.media_id
                rows.append(row)
                stats["cache_hits"] += 1
                continue
            if cand.media_sha256 in seen_sha:
                fresh = next(
                    (r for r in reversed(rows) if r["media_sha256"] == cand.media_sha256), None
                )
                if fresh:
                    row = {**fresh}
                    row["tweet_id"] = cand.tweet_id
                    row["account_handle"] = cand.account_handle
                    row["media_id"] = cand.media_id
                    rows.append(row)
                    stats["intra_run_dedup"] += 1
                    continue
            if max_items is not None and stats["attempted"] >= max_items:
                stats["skipped_max_items"] += 1
                continue
            stats["attempted"] += 1
            result = runner(cand)
            stats[f"status_{result.status}"] += 1
            if result.status in CACHEABLE_STATUSES:
                stats["extracted"] += 1
            rows.append(build_row(cand, result, generated_at=generated_at))
            seen_sha.add(cand.media_sha256)
    finally:
        if http is not None:
            http.close()

    stats["rows"] = len(rows)
    if not dry_run:
        write_parquet(rows, out_path)
        update_manifest(rows, dict(stats), generated_at)
    return dict(stats)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handle", help="Restrict to one data/<handle>.parquet file.")
    parser.add_argument("--max-items", type=int, help="Maximum uncached photos to process.")
    parser.add_argument("--force", action="store_true", help="Ignore cache and re-extract.")
    parser.add_argument("--dry-run", action="store_true", help="Report planned rows; do not write.")
    parser.add_argument(
        "--width",
        type=int,
        default=DEFAULT_THUMB_WIDTH,
        help=f"Thumbnail width (default {DEFAULT_THUMB_WIDTH}).",
    )
    parser.add_argument(
        "--tweet-ids-file",
        type=Path,
        help="Only process photos whose tweet_id is listed (one per line) in this file.",
    )
    args = parser.parse_args(argv)

    parquets = discover_canonical_parquets()
    if args.handle:
        parquets = [p for p in parquets if p.stem == args.handle]

    only_tweet_ids: set[str] | None = None
    if args.tweet_ids_file:
        only_tweet_ids = {
            line.strip()
            for line in args.tweet_ids_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    stats = run(
        parquets=parquets,
        max_items=args.max_items,
        force=args.force,
        dry_run=args.dry_run,
        width=args.width,
        only_tweet_ids=only_tweet_ids,
    )
    LOG.info("photo thumbnail extraction complete", **stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
