"""Curation tagger for the produced-video and meme-image review sets.

Two human/LLM review passes live under `data/tags/`:

* `produced_review/*.json`  — one file per candidate video. Files whose
  ``produced`` field is ``true`` are the produced / genre-experiment videos.
* `meme_image_review/*.json` — one file per candidate image. Files whose
  ``is_designed`` field is ``true`` are the designed meme / propaganda images.

This script turns those decisions into two viewer tags joined on tweet_id:

    review:produced-video   (produced == true)
    review:meme-image       (is_designed == true)

so the viewer can filter to each curated set via ``#tags=review:produced-video``
or ``#tags=review:meme-image``. Combined with ``sort=engagement`` this yields
the two shareable links.

Outputs (all purely additive — existing tags are never removed):

1. ``data/tags/review_curation.parquet`` — the canonical sidecar in the
   lexical-tag schema (one row per tagged tweet). This is what
   ``scripts.build_viewer_preview`` joins in on a rebuild and what the
   viewer's full-database path loads.
2. ``data/catalog.parquet`` — the pre-built catalog the default viewer view
   reads is overlaid in place so the tags appear without a full catalog
   rebuild. Re-running ``build_viewer_preview`` reproduces the same result
   from the sidecar.
3. ``data/preview-*.json`` — the preview/fallback payloads' ``tags`` maps are
   overlaid the same way.

Idempotent: re-running rebuilds the sidecar from scratch and re-applies the
overlay (dedupe-aware), so it is safe to run again after more review files
land. Corrupt / half-written review JSONs (other agents may be writing them
concurrently) are skipped with a warning rather than aborting the run.

Run with:  uv run python -m scripts.tag_review_curation
"""

from __future__ import annotations

import argparse
import glob
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from ._logging import configure
from ._schema import LEXICAL_TAG_SCHEMA

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"

PRODUCED_REVIEW_DIR = TAGS_DIR / "produced_review"
MEME_REVIEW_DIR = TAGS_DIR / "meme_image_review"

SIDECAR_PATH = TAGS_DIR / "review_curation.parquet"
CATALOG_PARQUET_PATH = DATA_DIR / "catalog.parquet"

PRODUCED_VIDEO_TAG = "review:produced-video"
MEME_IMAGE_TAG = "review:meme-image"
TAG_SOURCE = "human"
TAGGER_VERSION = "review-curation-v1"

# Each entry: (review dir, gate field that must be exactly True, tag to apply).
REVIEW_SETS: list[tuple[Path, str, str]] = [
    (PRODUCED_REVIEW_DIR, "produced", PRODUCED_VIDEO_TAG),
    (MEME_REVIEW_DIR, "is_designed", MEME_IMAGE_TAG),
]


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def collect_tagged_tweets() -> dict[str, dict[str, Any]]:
    """Return {tweet_id: {"tags": {tag, ...}, "account_handle": str}}.

    Reads every review JSON, keeps only files whose gate field is exactly
    ``True``, and accumulates the matching tag per tweet_id. A single tweet can
    legitimately collect more than one tag (e.g. a designed image that is also
    a produced video), so tags are stored in a set.
    """
    by_tweet: dict[str, dict[str, Any]] = {}
    for review_dir, gate_field, tag in REVIEW_SETS:
        if not review_dir.is_dir():
            LOG.warning("review-curation: dir missing", dir=str(review_dir))
            continue
        matched = skipped = corrupt = 0
        for path in sorted(glob.glob(str(review_dir / "*.json"))):
            try:
                with open(path, encoding="utf-8") as fh:
                    record = json.load(fh)
            except (json.JSONDecodeError, OSError) as err:
                # Files may be mid-write by a concurrent reviewer; skip and
                # let a later re-run pick them up.
                corrupt += 1
                LOG.warning("review-curation: skipping unreadable file", path=path, error=str(err))
                continue
            if record.get(gate_field) is not True:
                skipped += 1
                continue
            tweet_id = str(record.get("tweet_id") or "").strip()
            if not tweet_id:
                skipped += 1
                continue
            slot = by_tweet.setdefault(tweet_id, {"tags": set(), "account_handle": ""})
            slot["tags"].add(tag)
            handle = str(record.get("account_handle") or record.get("handle") or "").strip()
            if handle and not slot["account_handle"]:
                slot["account_handle"] = handle
            matched += 1
        LOG.info(
            "review-curation: scanned review set",
            dir=str(review_dir),
            tag=tag,
            matched=matched,
            skipped=skipped,
            corrupt=corrupt,
        )
    return by_tweet


def tag_entries_for(tags: set[str]) -> list[dict[str, Any]]:
    """Build lexical-schema tag-entry structs (sorted for stable output)."""
    return [
        {
            "tag": tag,
            "tentative": None,
            "source": TAG_SOURCE,
            "span_start": None,
            "span_end": None,
        }
        for tag in sorted(tags)
    ]


def write_sidecar(by_tweet: dict[str, dict[str, Any]]) -> pl.DataFrame:
    tagged_at = _now_iso()
    rows = [
        {
            "tweet_id": tweet_id,
            "account_handle": slot["account_handle"],
            "tagger_version": TAGGER_VERSION,
            "tagged_at": tagged_at,
            "tags": tag_entries_for(slot["tags"]),
        }
        for tweet_id, slot in sorted(by_tweet.items())
    ]
    df = pl.DataFrame(rows, schema=LEXICAL_TAG_SCHEMA)
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(SIDECAR_PATH)
    LOG.info("review-curation: wrote sidecar", path=str(SIDECAR_PATH), rows=df.height)
    return df


def _compact_overlay_entries(tags: set[str]) -> list[dict[str, Any]]:
    """Catalog/preview tag entries use the compact {tag, source} shape."""
    return [{"tag": tag, "source": TAG_SOURCE} for tag in sorted(tags)]


def _merge_tag_lists(
    existing: list[dict[str, Any]] | None, additions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Append additions to existing tags, dropping duplicates by tag name.

    Existing entries always win (we never rewrite an already-present tag), so
    this is safe to run repeatedly and never disturbs other taggers' output.
    """
    out: list[dict[str, Any]] = []
    present: set[str] = set()
    for entry in existing or []:
        if isinstance(entry, dict):
            name = str(entry.get("tag") or "")
        elif isinstance(entry, str):
            entry, name = {"tag": entry}, entry
        else:
            continue
        out.append(entry)
        if name:
            present.add(name)
    for entry in additions:
        if entry["tag"] not in present:
            out.append(entry)
            present.add(entry["tag"])
    return out


def overlay_catalog(by_tweet: dict[str, dict[str, Any]]) -> int:
    if not CATALOG_PARQUET_PATH.exists():
        LOG.warning("review-curation: catalog parquet missing", path=str(CATALOG_PARQUET_PATH))
        return 0
    df = pl.read_parquet(CATALOG_PARQUET_PATH)
    if "tweet_id" not in df.columns:
        LOG.warning("review-curation: catalog has no tweet_id column")
        return 0
    additions = {tid: _compact_overlay_entries(slot["tags"]) for tid, slot in by_tweet.items()}
    existing_tags = df["tags"].to_list() if "tags" in df.columns else [None] * df.height
    ids = df["tweet_id"].to_list()
    new_tags: list[list[dict[str, Any]]] = []
    touched = 0
    for tweet_id, current in zip(ids, existing_tags, strict=True):
        adds = additions.get(str(tweet_id))
        if not adds:
            new_tags.append(current)
            continue
        merged = _merge_tag_lists(current, adds)
        new_tags.append(merged)
        if merged != current:
            touched += 1
    # The catalog `tags` column is List(Struct({tag, tentative, source})); build
    # the replacement Series with that exact dtype so the parquet schema is
    # preserved.
    catalog_tag_dtype = pl.List(
        pl.Struct(
            [
                pl.Field("tag", pl.Utf8),
                pl.Field("tentative", pl.Boolean),
                pl.Field("source", pl.Utf8),
            ]
        )
    )
    df = df.with_columns(pl.Series("tags", new_tags, dtype=catalog_tag_dtype))
    df.write_parquet(CATALOG_PARQUET_PATH)
    LOG.info("review-curation: overlaid catalog", path=str(CATALOG_PARQUET_PATH), touched=touched)
    return touched


def overlay_previews(by_tweet: dict[str, dict[str, Any]]) -> int:
    additions = {tid: _compact_overlay_entries(slot["tags"]) for tid, slot in by_tweet.items()}
    total_touched = 0
    for path in sorted(glob.glob(str(DATA_DIR / "preview-*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (json.JSONDecodeError, OSError) as err:
            LOG.warning("review-curation: skipping preview", path=path, error=str(err))
            continue
        rows = payload.get("rows")
        row_ids = {str(r.get("tweet_id") or "") for r in rows} if isinstance(rows, list) else None
        tags_map = payload.get("tags")
        if not isinstance(tags_map, dict):
            tags_map = {}
        touched = 0
        for tweet_id, adds in additions.items():
            # Only annotate tweets actually present in this preview slice.
            if row_ids is not None and tweet_id not in row_ids:
                continue
            merged = _merge_tag_lists(tags_map.get(tweet_id), adds)
            if merged != tags_map.get(tweet_id):
                tags_map[tweet_id] = merged
                touched += 1
        payload["tags"] = tags_map
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        LOG.info("review-curation: overlaid preview", path=path, touched=touched)
        total_touched += touched
    return total_touched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sidecar-only",
        action="store_true",
        help="Only (re)write the sidecar parquet; skip the catalog/preview overlay.",
    )
    args = parser.parse_args(argv)

    by_tweet = collect_tagged_tweets()
    produced = sum(1 for s in by_tweet.values() if PRODUCED_VIDEO_TAG in s["tags"])
    meme = sum(1 for s in by_tweet.values() if MEME_IMAGE_TAG in s["tags"])
    LOG.info(
        "review-curation: tagged tweets",
        total=len(by_tweet),
        produced_videos=produced,
        meme_images=meme,
    )
    if not by_tweet:
        LOG.warning("review-curation: nothing to tag; leaving outputs untouched")
        return 0

    write_sidecar(by_tweet)
    if not args.sidecar_only:
        overlay_catalog(by_tweet)
        overlay_previews(by_tweet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
