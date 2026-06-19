"""Import agent-reviewed creative visual descriptions.

The creative review site only accepts rows with a genuine visual description.
External/manual descriptions live in the existing review overlay directories:

* videos/GIFs -> ``data/tags/produced_review/<tweet_id>.json``;
* photos -> ``data/tags/meme_image_review/<media_id>.json``.

This helper imports a neutral JSON array of reviewed media descriptions into
those directories so ``scripts.apply_review_descriptions`` can overlay them into
``data/tags/media_vision.parquet``.

Run with:

    uv run python -m scripts.import_creative_visual_reviews tmp/.../agent_A_descriptions.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import polars as pl

from scripts._logging import configure

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"
CATALOG_PATH = DATA_DIR / "catalog.parquet"
PRODUCED_REVIEW_DIR = TAGS_DIR / "produced_review"
MEME_REVIEW_DIR = TAGS_DIR / "meme_image_review"
VIDEO_TYPES = {"video", "animated_gif"}


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_catalog() -> dict[str, dict[str, Any]]:
    df = pl.read_parquet(CATALOG_PATH)
    return {str(row.get("tweet_id") or ""): row for row in df.to_dicts()}


def media_by_id(tweet: dict[str, Any], media_id: str) -> dict[str, Any]:
    for media in tweet.get("media") or []:
        if isinstance(media, dict) and str(media.get("media_id") or "") == media_id:
            return media
    return {}


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def write_json_stable(path: Path, payload: dict[str, Any]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return True


def video_payload(
    rec: dict[str, Any], tweet: dict[str, Any], media: dict[str, Any]
) -> dict[str, Any]:
    desc = clean_text(rec.get("description"))
    notable = clean_text(rec.get("notable_text"))
    return {
        "tweet_id": str(rec.get("tweet_id") or ""),
        "account_handle": str(tweet.get("account_handle") or ""),
        "posted_at": str(tweet.get("posted_at") or ""),
        "tweet_url": str(tweet.get("tweet_url") or ""),
        "genre_tags": [],
        "duration_sec": media.get("duration_sec"),
        "like_count": int(float(tweet.get("like_count") or 0)),
        "retweet_count": int(float(tweet.get("retweet_count") or 0)),
        "produced": None,
        "content_type": "reviewed visual media",
        "set_to_music": None,
        "summary": desc,
        "script": [],
        "notable_text": notable,
    }


def image_payload(
    rec: dict[str, Any], tweet: dict[str, Any], media: dict[str, Any]
) -> dict[str, Any]:
    return {
        "media_id": str(rec.get("media_id") or ""),
        "tweet_id": str(rec.get("tweet_id") or ""),
        "handle": str(tweet.get("account_handle") or ""),
        "posted_at": str(tweet.get("posted_at") or "")[:10],
        "tweet_url": str(tweet.get("tweet_url") or ""),
        "content_type": str(media.get("media_type") or rec.get("media_type") or "photo"),
        "description": clean_text(rec.get("description")),
        "notable_text": clean_text(rec.get("notable_text")),
        "status": "ok",
    }


def should_import(rec: dict[str, Any]) -> bool:
    desc = clean_text(rec.get("description"))
    if not desc:
        return False
    lower = desc.lower()
    blocked_phrases = (
        "lacks a usable local frame",
        "lacks a usable visual",
        "no usable local frame",
        "no usable visual",
        "missing-visual placeholder",
        "only the tweet text",
        "not enough visual information",
        "unusable visual",
    )
    return not any(phrase in lower for phrase in blocked_phrases)


def import_records(paths: list[Path], *, dry_run: bool = False) -> dict[str, int]:
    catalog = load_catalog()
    stats = {
        "records": 0,
        "written": 0,
        "skipped": 0,
        "missing_tweet": 0,
        "missing_media": 0,
    }
    video_seen: set[str] = set()
    for path in paths:
        data = read_json(path)
        if not isinstance(data, list):
            raise ValueError(f"{path} must contain a JSON array")
        for rec in data:
            stats["records"] += 1
            if not isinstance(rec, dict) or not should_import(rec):
                stats["skipped"] += 1
                continue
            tweet_id = str(rec.get("tweet_id") or "")
            media_id = str(rec.get("media_id") or "")
            tweet = catalog.get(tweet_id)
            if not tweet:
                stats["missing_tweet"] += 1
                continue
            media = media_by_id(tweet, media_id)
            if not media:
                stats["missing_media"] += 1
                continue
            media_type = str(rec.get("media_type") or media.get("media_type") or "")
            if media_type in VIDEO_TYPES:
                if tweet_id in video_seen:
                    continue
                video_seen.add(tweet_id)
                out = PRODUCED_REVIEW_DIR / f"{tweet_id}.json"
                payload = video_payload(rec, tweet, media)
            else:
                out = MEME_REVIEW_DIR / f"{media_id}.json"
                payload = image_payload(rec, tweet, media)
            if dry_run:
                LOG.info("import-review: would write", path=str(out))
                continue
            if write_json_stable(out, payload):
                stats["written"] += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_paths", nargs="+", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    stats = import_records(args.json_paths, dry_run=args.dry_run)
    LOG.info("import-review: complete", **stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
