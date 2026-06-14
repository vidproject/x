"""Campaign helper: fetch full-resolution photos for visual review.

Read-only with respect to the tracked data: given one or more ``media_id``
values, looks up each photo's ``release_asset_url`` in
``photo_thumbnails.parquet`` and downloads the full-resolution JPEG into a
gitignored scratch directory (``data/derived/fullres_photos/``) so the
campaign driver can View it and write a factual description.

The 96px thumbnails recorded in ``photo_thumbnails.parquet`` are too small to
describe reliably; the release asset is the archived original. Nothing here is
committed: the scratch dir mirrors how keyframes are treated (deterministic
from the release asset, re-fetch on demand).

Prints, for each requested media_id, the local path on success or a SKIP line
on failure (no asset url, or fetch error) so un-fetchable items can be noted
and never invented.

Run with::

    uv run python -m scripts._campaign_fetch_photo 3_123 3_456
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
import polars as pl

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"
PHOTO_THUMBNAILS_PATH = TAGS_DIR / "photo_thumbnails.parquet"
SCRATCH_DIR = DATA_DIR / "derived" / "fullres_photos"

HTTP_TIMEOUT_SECS = 60.0
MAX_PHOTO_BYTES = 64 * 1024 * 1024


def _canonical_parquets() -> list[Path]:
    return sorted(
        p for p in DATA_DIR.glob("*.parquet") if p.is_file() and p.name != "catalog.parquet"
    )


def _asset_urls(media_ids: list[str]) -> dict[str, str]:
    """media_id -> release_asset_url.

    Most photos carry their asset url in the canonical per-handle parquets'
    ``media`` list; only a subset are mirrored into photo_thumbnails.parquet.
    Check the thumbnails sidecar first (cheap), then fall back to scanning the
    canonical parquets for any still-unresolved id.
    """
    want = set(media_ids)
    out: dict[str, str] = {}
    if PHOTO_THUMBNAILS_PATH.exists():
        df = pl.read_parquet(PHOTO_THUMBNAILS_PATH, columns=["media_id", "release_asset_url"])
        for row in df.filter(pl.col("media_id").is_in(list(want))).iter_rows(named=True):
            mid = str(row.get("media_id") or "")
            url = str(row.get("release_asset_url") or "")
            if mid and url:
                out[mid] = url
    missing = want - set(out)
    if not missing:
        return out
    for path in _canonical_parquets():
        if not missing:
            break
        try:
            df = pl.read_parquet(path, columns=["media"])
        except Exception:
            continue
        for row in df.iter_rows(named=True):
            media = row.get("media") or []
            if not isinstance(media, list):
                continue
            for item in media:
                if not isinstance(item, dict):
                    continue
                mid = str(item.get("media_id") or "")
                if mid in missing:
                    url = str(item.get("release_asset_url") or "")
                    if url:
                        out[mid] = url
                        missing.discard(mid)
    return out


def fetch_one(media_id: str, url: str, http: httpx.Client) -> Path | None:
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(url.split("?", 1)[0]).suffix or ".jpg"
    dest = SCRATCH_DIR / f"{media_id}{suffix}"
    try:
        with http.stream("GET", url, timeout=HTTP_TIMEOUT_SECS, follow_redirects=True) as resp:
            resp.raise_for_status()
            total = 0
            with dest.open("wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=1 << 20):
                    total += len(chunk)
                    if total > MAX_PHOTO_BYTES:
                        dest.unlink(missing_ok=True)
                        raise ValueError(f"photo exceeds {MAX_PHOTO_BYTES} bytes")
                    fh.write(chunk)
        if total == 0:
            dest.unlink(missing_ok=True)
            return None
        return dest
    except Exception as e:
        print(f"SKIP {media_id}: {e}", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media_ids", nargs="+", help="One or more media_id values.")
    args = parser.parse_args(argv)

    urls = _asset_urls(args.media_ids)
    http = httpx.Client(
        follow_redirects=True,
        headers={"user-agent": "imm-archive-campaign/1.0"},
    )
    try:
        for mid in args.media_ids:
            url = urls.get(mid)
            if not url:
                print(f"SKIP {mid}: no release_asset_url in photo_thumbnails.parquet")
                continue
            path = fetch_one(mid, url, http)
            if path is not None:
                print(f"{mid}\t{path}")
            else:
                print(f"SKIP {mid}: fetch failed")
    finally:
        http.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
