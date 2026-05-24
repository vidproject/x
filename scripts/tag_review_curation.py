"""Curation tagger for the review and media-status filter sets.

Two human/LLM review passes live under `data/tags/` as one JSON file per
candidate:

* `produced_review/*.json`  — one file per candidate video. Files whose
  ``produced`` field is ``true`` are the produced / genre-experiment videos.
* `meme_image_review/*.json` — one file per candidate image. Files whose
  ``is_designed`` field is ``true`` are the designed graphic images.

Four more filter sets are derived deterministically from the analysis
sidecars (no JSON review files needed):

* `review:screened-video`  — tweets whose video / animated-gif media went
  through the vision review pass (``media_vision.parquet``,
  ``model == 'opus-vision-review'``, video media types).
* `review:screened-photo`  — same, for photo media.
* `media-status:transcribed` — tweets with a completed speech-to-text pass
  (``transcripts.parquet``, ``status == 'ok'``).
* `media-status:ocr-done`   — tweets whose image OCR ran to completion
  (``image_ocr.parquet``, ``status`` in ``ok`` / ``no-text``).

This script turns those decisions into viewer tags joined on tweet_id:

    review:produced-video      (produced == true)
    review:meme-image          (is_designed == true)
    review:screened-video      (vision-reviewed video/gif media)
    review:screened-photo      (vision-reviewed photo media)
    media-status:transcribed   (transcript status ok)
    media-status:ocr-done      (image OCR completed)

so the viewer can filter to each set via ``#tags=<tag>``. Combined with
``sort=engagement`` this yields the shareable links.

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
overlay (dedupe-aware), so it is safe to run again after more review files or
analysis rows land. Corrupt / half-written input files (other agents may be
writing them concurrently) are skipped with a warning rather than aborting the
run.

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

MEDIA_VISION_PATH = TAGS_DIR / "media_vision.parquet"
TRANSCRIPTS_PATH = TAGS_DIR / "transcripts.parquet"
IMAGE_OCR_PATH = TAGS_DIR / "image_ocr.parquet"

SIDECAR_PATH = TAGS_DIR / "review_curation.parquet"
CATALOG_PARQUET_PATH = DATA_DIR / "catalog.parquet"

PRODUCED_VIDEO_TAG = "review:produced-video"
MEME_IMAGE_TAG = "review:meme-image"
SCREENED_VIDEO_TAG = "review:screened-video"
SCREENED_PHOTO_TAG = "review:screened-photo"
TRANSCRIBED_TAG = "media-status:transcribed"
OCR_DONE_TAG = "media-status:ocr-done"

# Tag-entry ``source`` values. The human review JSONs are editor decisions;
# the screened-* sets come from the curation review pass; the media-status:*
# sets are derived metadata (matching the existing ``media-status:needs-ocr``
# source so the viewer groups them consistently).
SOURCE_HUMAN = "human"
SOURCE_REVIEW_CURATION = "review-curation"
SOURCE_MEDIA_METADATA = "media-metadata"

TAGGER_VERSION = "review-curation-v2"

# Each entry: (review dir, gate field that must be exactly True, tag, source).
REVIEW_SETS: list[tuple[Path, str, str, str]] = [
    (PRODUCED_REVIEW_DIR, "produced", PRODUCED_VIDEO_TAG, SOURCE_HUMAN),
    (MEME_REVIEW_DIR, "is_designed", MEME_IMAGE_TAG, SOURCE_HUMAN),
]


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_parquet_safe(path: Path, *, columns: list[str] | None = None) -> pl.DataFrame | None:
    """Read a sidecar parquet, returning None (with a warning) if unreadable.

    Other pipeline steps may be writing these files concurrently; a half-written
    or missing file should be skipped rather than aborting the whole tagging
    run.
    """
    if not path.exists():
        LOG.warning("review-curation: input parquet missing", path=str(path))
        return None
    try:
        return pl.read_parquet(path, columns=columns)
    except Exception as err:  # noqa: BLE001 - any read failure should skip, not abort
        LOG.warning("review-curation: skipping unreadable parquet", path=str(path), error=str(err))
        return None


def _add_tag(
    by_tweet: dict[str, dict[str, Any]],
    tweet_id: str,
    tag: str,
    source: str,
    *,
    handle: str = "",
) -> None:
    """Record ``tag`` (with its ``source``) for ``tweet_id`` in the accumulator.

    A tweet can collect more than one tag. Tags are stored as a {tag: source}
    mapping; the first source seen for a given tag wins (the sets are disjoint
    in practice, so this is just a deterministic tie-break).
    """
    slot = by_tweet.setdefault(tweet_id, {"tags": {}, "account_handle": ""})
    slot["tags"].setdefault(tag, source)
    handle = (handle or "").strip()
    if handle and not slot["account_handle"]:
        slot["account_handle"] = handle


def collect_review_json_tweets(by_tweet: dict[str, dict[str, Any]]) -> None:
    """Fold the JSON review-pass decisions into ``by_tweet``.

    Reads every review JSON, keeps only files whose gate field is exactly
    ``True`` (and which are flagged immigration-related), and accumulates the
    matching tag per tweet_id.
    """
    for review_dir, gate_field, tag, source in REVIEW_SETS:
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
            if record.get("immigration_related") is not True:
                skipped += 1
                continue
            tweet_id = str(record.get("tweet_id") or "").strip()
            if not tweet_id:
                skipped += 1
                continue
            handle = str(record.get("account_handle") or record.get("handle") or "").strip()
            _add_tag(by_tweet, tweet_id, tag, source, handle=handle)
            matched += 1
        LOG.info(
            "review-curation: scanned review set",
            dir=str(review_dir),
            tag=tag,
            matched=matched,
            skipped=skipped,
            corrupt=corrupt,
        )


def _collect_parquet_tag(
    by_tweet: dict[str, dict[str, Any]],
    *,
    df: pl.DataFrame | None,
    tag: str,
    source: str,
    predicate: pl.Expr,
) -> int:
    """Apply ``tag`` to every distinct tweet_id in ``df`` matching ``predicate``.

    Returns the number of distinct tweet_ids tagged.
    """
    if df is None:
        return 0
    needed = {"tweet_id", "account_handle"}
    if not needed.issubset(df.columns):
        LOG.warning("review-curation: sidecar missing columns", tag=tag, columns=df.columns)
        return 0
    sub = df.filter(predicate)
    count = 0
    seen: set[str] = set()
    for row in sub.select(["tweet_id", "account_handle"]).iter_rows(named=True):
        tweet_id = str(row.get("tweet_id") or "").strip()
        if not tweet_id or tweet_id in seen:
            continue
        seen.add(tweet_id)
        _add_tag(by_tweet, tweet_id, tag, source, handle=str(row.get("account_handle") or ""))
        count += 1
    LOG.info("review-curation: tagged from sidecar", tag=tag, source=source, distinct=count)
    return count


def collect_media_status_tweets(by_tweet: dict[str, dict[str, Any]]) -> None:
    """Fold the analysis-sidecar-derived filter sets into ``by_tweet``."""
    vision = _read_parquet_safe(
        MEDIA_VISION_PATH, columns=["tweet_id", "account_handle", "model", "media_type"]
    )
    if vision is not None and "model" in vision.columns and "media_type" in vision.columns:
        _collect_parquet_tag(
            by_tweet,
            df=vision,
            tag=SCREENED_VIDEO_TAG,
            source=SOURCE_REVIEW_CURATION,
            predicate=(pl.col("model") == "opus-vision-review")
            & pl.col("media_type").is_in(["video", "animated_gif"]),
        )
        _collect_parquet_tag(
            by_tweet,
            df=vision,
            tag=SCREENED_PHOTO_TAG,
            source=SOURCE_REVIEW_CURATION,
            predicate=(pl.col("model") == "opus-vision-review")
            & (pl.col("media_type") == "photo"),
        )

    transcripts = _read_parquet_safe(
        TRANSCRIPTS_PATH, columns=["tweet_id", "account_handle", "status"]
    )
    if transcripts is not None and "status" in transcripts.columns:
        _collect_parquet_tag(
            by_tweet,
            df=transcripts,
            tag=TRANSCRIBED_TAG,
            source=SOURCE_MEDIA_METADATA,
            predicate=pl.col("status") == "ok",
        )

    ocr = _read_parquet_safe(IMAGE_OCR_PATH, columns=["tweet_id", "account_handle", "status"])
    if ocr is not None and "status" in ocr.columns:
        _collect_parquet_tag(
            by_tweet,
            df=ocr,
            tag=OCR_DONE_TAG,
            source=SOURCE_MEDIA_METADATA,
            predicate=pl.col("status").is_in(["ok", "no-text"]),
        )


def collect_tagged_tweets() -> dict[str, dict[str, Any]]:
    """Return {tweet_id: {"tags": {tag: source, ...}, "account_handle": str}}."""
    by_tweet: dict[str, dict[str, Any]] = {}
    collect_review_json_tweets(by_tweet)
    collect_media_status_tweets(by_tweet)
    return by_tweet


def tag_entries_for(tags: dict[str, str]) -> list[dict[str, Any]]:
    """Build lexical-schema tag-entry structs (sorted for stable output)."""
    return [
        {
            "tag": tag,
            "tentative": None,
            "source": tags[tag],
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


def _compact_overlay_entries(tags: dict[str, str]) -> list[dict[str, Any]]:
    """Catalog/preview tag entries use the compact {tag, source} shape."""
    return [{"tag": tag, "source": tags[tag]} for tag in sorted(tags)]


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


def _count(by_tweet: dict[str, dict[str, Any]], tag: str) -> int:
    return sum(1 for slot in by_tweet.values() if tag in slot["tags"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sidecar-only",
        action="store_true",
        help="Only (re)write the sidecar parquet; skip the catalog/preview overlay.",
    )
    args = parser.parse_args(argv)

    by_tweet = collect_tagged_tweets()
    LOG.info(
        "review-curation: tagged tweets",
        total=len(by_tweet),
        produced_videos=_count(by_tweet, PRODUCED_VIDEO_TAG),
        meme_images=_count(by_tweet, MEME_IMAGE_TAG),
        screened_videos=_count(by_tweet, SCREENED_VIDEO_TAG),
        screened_photos=_count(by_tweet, SCREENED_PHOTO_TAG),
        transcribed=_count(by_tweet, TRANSCRIBED_TAG),
        ocr_done=_count(by_tweet, OCR_DONE_TAG),
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
