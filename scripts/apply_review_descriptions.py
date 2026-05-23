"""Overlay the sterile review descriptions into the media-vision sidecar.

Two Opus review passes live under ``data/tags/``:

* ``produced_review/*.json``   - one file per candidate video, keyed by
  ``tweet_id``. The human-readable account is in ``summary`` and a timestamped
  shot list in ``script`` ([{"timestamp", "scene"}, ...]).
* ``meme_image_review/*.json`` - one file per candidate image, keyed by
  ``media_id``. The account is in ``description``.

Those descriptions were written from the archived keyframes / full images.
This script writes them into the canonical media-description sidecar
``data/tags/media_vision.parquet`` -- the very ``description`` /
``summary_text`` fields that ``scripts.describe_media`` produces and that
``scripts.build_viewer_preview`` carries into the catalog ``media_insights``
the viewer renders. In other words, it drops the real descriptions exactly
where the programmatically generated ones go, replacing the metadata-only
placeholders.

Join keys:

* image reviews -> media_vision row by ``media_id``;
* video reviews -> media_vision rows for that ``tweet_id`` whose media_type is
  ``video`` / ``animated_gif``.

Every id, ``input_hash``, ``media_sha256``, ``tags`` and ``source_fields``
value is preserved untouched, so a later ``describe_media`` run keeps these
rows on a cache hit (the ``input_hash`` still matches the metadata draft)
rather than regenerating the placeholder. Only the prose, model/version,
status, confidence and timestamp are rewritten. Idempotent: re-running
reproduces the same overlay from the current review JSONs.

Run with:  uv run python -m scripts.apply_review_descriptions
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from ._logging import configure
from ._schema import MEDIA_VISION_SCHEMA

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"
MEDIA_VISION_PATH = TAGS_DIR / "media_vision.parquet"
PRODUCED_REVIEW_DIR = TAGS_DIR / "produced_review"
MEME_REVIEW_DIR = TAGS_DIR / "meme_image_review"

REVIEW_MODEL = "opus-vision-review"
REVIEW_MODEL_VERSION = "review-v1"
REVIEW_STATUS = "manual-review"
REVIEW_CONFIDENCE = 0.9
VIDEO_TYPES = {"video", "animated_gif"}


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _flatten_script(script: object) -> str:
    """Render the timestamped shot list as one searchable line."""
    if not isinstance(script, list):
        return ""
    out: list[str] = []
    for shot in script:
        if not isinstance(shot, dict):
            continue
        ts = str(shot.get("timestamp") or "").strip()
        scene = str(shot.get("scene") or "").strip()
        if scene:
            out.append(f"[{ts}] {scene}" if ts else scene)
    return "  ".join(out)


def load_image_descriptions() -> dict[str, str]:
    """media_id -> sterile image description."""
    out: dict[str, str] = {}
    for path in sorted(glob.glob(str(MEME_REVIEW_DIR / "*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (json.JSONDecodeError, OSError) as err:
            LOG.warning("apply-desc: skip unreadable image review", path=path, error=str(err))
            continue
        media_id = str(rec.get("media_id") or "").strip()
        desc = str(rec.get("description") or "").strip()
        if media_id and desc:
            out[media_id] = desc
    return out


def load_video_descriptions() -> dict[str, tuple[str, str]]:
    """tweet_id -> (description, summary_text-with-script)."""
    out: dict[str, tuple[str, str]] = {}
    for path in sorted(glob.glob(str(PRODUCED_REVIEW_DIR / "*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (json.JSONDecodeError, OSError) as err:
            LOG.warning("apply-desc: skip unreadable video review", path=path, error=str(err))
            continue
        tweet_id = str(rec.get("tweet_id") or "").strip()
        summary = str(rec.get("summary") or "").strip()
        if not (tweet_id and summary):
            continue
        script = _flatten_script(rec.get("script"))
        summary_text = f"{summary}  {script}".strip() if script else summary
        out[tweet_id] = (summary, summary_text)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report coverage; do not write.")
    args = parser.parse_args(argv)

    if not MEDIA_VISION_PATH.exists():
        LOG.error("apply-desc: media_vision missing", path=str(MEDIA_VISION_PATH))
        return 1

    df = pl.read_parquet(MEDIA_VISION_PATH)
    images = load_image_descriptions()
    videos = load_video_descriptions()
    LOG.info("apply-desc: loaded reviews", images=len(images), videos=len(videos))

    now = _now_iso()
    rows = df.to_dicts()
    img_hits = vid_hits = 0
    seen_media_ids: set[str] = set()
    seen_video_tweets: set[str] = set()
    for row in rows:
        media_id = str(row.get("media_id") or "")
        tweet_id = str(row.get("tweet_id") or "")
        media_type = str(row.get("media_type") or "")
        new_desc: str | None = None
        new_summary: str | None = None
        if media_id and media_id in images:
            new_desc = new_summary = images[media_id]
            img_hits += 1
            seen_media_ids.add(media_id)
        elif media_type in VIDEO_TYPES and tweet_id in videos:
            new_desc, new_summary = videos[tweet_id]
            vid_hits += 1
            seen_video_tweets.add(tweet_id)
        if new_desc is None:
            continue
        if args.dry_run:
            continue
        row["description"] = new_desc
        row["summary_text"] = new_summary
        row["model"] = REVIEW_MODEL
        row["model_version"] = REVIEW_MODEL_VERSION
        row["status"] = REVIEW_STATUS
        row["confidence"] = REVIEW_CONFIDENCE
        row["generated_at"] = now

    missing_images = sorted(set(images) - seen_media_ids)
    missing_videos = sorted(set(videos) - seen_video_tweets)
    LOG.info(
        "apply-desc: overlay coverage",
        image_rows=img_hits,
        video_rows=vid_hits,
        images_without_media_row=len(missing_images),
        videos_without_media_row=len(missing_videos),
    )
    if missing_images:
        LOG.warning("apply-desc: image reviews with no media_vision row", count=len(missing_images),
                    sample=missing_images[:5])
    if missing_videos:
        LOG.warning("apply-desc: video reviews with no media_vision row", count=len(missing_videos),
                    sample=missing_videos[:5])

    if args.dry_run:
        LOG.info("apply-desc: dry run, nothing written")
        return 0

    out = pl.DataFrame(rows, schema=MEDIA_VISION_SCHEMA, strict=False)
    tmp = MEDIA_VISION_PATH.with_suffix(".parquet.tmp")
    out.write_parquet(tmp, compression="zstd")
    os.replace(tmp, MEDIA_VISION_PATH)
    LOG.info("apply-desc: wrote media_vision", rows=out.height, path=str(MEDIA_VISION_PATH))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
