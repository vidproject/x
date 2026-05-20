"""Build a cheap media-recognition sidecar from archived media metadata.

The first pass is intentionally local and zero-cost. It describes each
archived media item using stable capture facts already in the parquet rows:
media type, dimensions, duration, archive status, alt text, and tweet context.
It writes ``data/tags/media_vision.parquet`` so the viewer can search and show
media descriptions without mutating the canonical tweet parquets.

This sidecar is also the cache boundary for later OCR, transcript, CLIP, or
vision-model passes. Rows carry ``input_hash``, model metadata, status, and
cost fields, so a later implementation can skip unchanged media and enforce a
per-run budget.

Run with: ``uv run python -m scripts.describe_media``
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from scripts._logging import configure
from scripts._schema import MEDIA_VISION_SCHEMA, empty_media_vision_dataframe

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"
OUT_PATH = TAGS_DIR / "media_vision.parquet"
MANIFEST_PATH = TAGS_DIR / "manifest.json"

MODEL = "metadata"
MODEL_VERSION = "media-metadata-v1"
PROMPT = (
    "Describe public X media from canonical capture metadata. "
    "Do not infer visual content beyond alt text or captured metadata."
)
PROMPT_HASH = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()[:16]


def discover_canonical_parquets() -> list[Path]:
    return sorted(p for p in DATA_DIR.glob("*.parquet") if p.is_file())


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def input_hash_for(tweet: dict[str, Any], media: dict[str, Any]) -> str:
    payload = {
        "tweet_id": str(tweet.get("tweet_id") or ""),
        "media_id": str(media.get("media_id") or ""),
        "media_type": str(media.get("media_type") or ""),
        "sha256": str(media.get("sha256") or ""),
        "release_asset_url": str(media.get("release_asset_url") or ""),
        "original_url": str(media.get("original_url") or ""),
        "duration_sec": media.get("duration_sec"),
        "width": media.get("width"),
        "height": media.get("height"),
        "alt_text": str(media.get("alt_text") or ""),
        "text": tweet_text(tweet)[:1200],
        "model_version": MODEL_VERSION,
        "prompt_hash": PROMPT_HASH,
    }
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def tag_entry(tag: str, *, tentative: bool = False) -> dict[str, Any]:
    return {
        "tag": tag,
        "tentative": True if tentative else None,
        "source": "media-metadata",
        "span_start": None,
        "span_end": None,
    }


def describe_media_item(
    tweet: dict[str, Any],
    media: dict[str, Any],
    *,
    generated_at: str,
) -> dict[str, Any]:
    media_type = str(media.get("media_type") or "media")
    media_id = str(media.get("media_id") or "")
    alt_text = clean_text(str(media.get("alt_text") or ""))
    context = clean_text(tweet_text(tweet))
    archived = bool(media.get("release_asset_url"))
    dimensions = dimension_text(media)
    duration = duration_text(media)
    byte_count = bytes_text(media)

    source_fields = ["media_type", "archive_status"]
    parts = [kind_label(media_type)]
    if dimensions:
        parts.append(dimensions)
        source_fields.extend(["width", "height"])
    if duration:
        parts.append(duration)
        source_fields.append("duration_sec")
    if byte_count:
        parts.append(byte_count)
        source_fields.append("bytes")
    parts.append("archived" if archived else "not archived")
    if alt_text:
        parts.append(f"alt text: {alt_text}")
        source_fields.append("alt_text")
    if context:
        parts.append(f"tweet context: {truncate(context, 260)}")
        source_fields.append("text_resolved")

    needs_vision = media_type in {"photo", "video", "animated_gif"} and not alt_text
    if needs_vision:
        parts.append("needs OCR, transcript, or frame-level vision before content claims")

    description = "; ".join(parts) + "."
    tags = [tag_entry("media:described"), tag_entry(f"media:{tag_slug_for(media_type)}")]
    if archived:
        tags.append(tag_entry("media:archived"))
    if alt_text:
        tags.append(tag_entry("media:has-alt-text"))
    if needs_vision:
        tags.append(tag_entry("media:needs-vision", tentative=True))
    duration_seconds = numeric(media.get("duration_sec"))
    if media_type in {"video", "animated_gif"} and 0 < duration_seconds <= 30:
        tags.append(tag_entry("media:short-video"))

    status = "metadata-alt" if alt_text else "metadata-only"
    confidence = 0.68 if alt_text else 0.35
    return {
        "tweet_id": str(tweet.get("tweet_id") or ""),
        "account_handle": str(tweet.get("account_handle") or ""),
        "media_id": media_id,
        "media_type": media_type,
        "media_sha256": str(media.get("sha256") or ""),
        "input_hash": input_hash_for(tweet, media),
        "generated_at": generated_at,
        "model": MODEL,
        "model_version": MODEL_VERSION,
        "prompt_hash": PROMPT_HASH,
        "description": description,
        "summary_text": " ".join(p for p in [description, alt_text, context] if p),
        "confidence": confidence,
        "cost_estimate_usd": 0.0,
        "status": status,
        "tags": tags,
        "source_fields": sorted(set(source_fields)),
        "error": None,
    }


def media_candidates(tweet: dict[str, Any], *, include_pending: bool) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    media = tweet.get("media")
    if not isinstance(media, list):
        return out
    for item in media:
        if not isinstance(item, dict):
            continue
        if not str(item.get("media_id") or ""):
            continue
        if item.get("release_asset_url") or include_pending:
            out.append(item)
    return out


def load_existing(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    if not path.exists():
        return {}
    df = pl.read_parquet(path)
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in df.iter_rows(named=True):
        key = (str(row.get("tweet_id") or ""), str(row.get("media_id") or ""))
        if all(key):
            out[key] = row
    return out


def build_rows(
    parquets: list[Path],
    *,
    generated_at: str,
    include_pending: bool,
    force: bool,
    max_items: int | None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    existing = load_existing(OUT_PATH)
    rows: list[dict[str, Any]] = []
    stats = Counter[str]()
    generated = 0

    for path in parquets:
        df = pl.read_parquet(path)
        for tweet in df.iter_rows(named=True):
            for media in media_candidates(tweet, include_pending=include_pending):
                key = (str(tweet.get("tweet_id") or ""), str(media.get("media_id") or ""))
                draft = describe_media_item(tweet, media, generated_at=generated_at)
                cached = existing.get(key)
                if (
                    cached
                    and not force
                    and str(cached.get("input_hash") or "") == draft["input_hash"]
                ):
                    rows.append(cached)
                    stats["cache_hits"] += 1
                    continue
                if max_items is not None and generated >= max_items:
                    stats["skipped_max_items"] += 1
                    continue
                rows.append(draft)
                generated += 1
                stats["generated"] += 1
    stats["rows"] = len(rows)
    return rows, dict(stats)


def write_parquet(rows: list[dict[str, Any]], path: Path) -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    df = (
        pl.DataFrame(rows, schema=MEDIA_VISION_SCHEMA, strict=False)
        if rows
        else empty_media_vision_dataframe()
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.write_parquet(tmp, compression="zstd")
    os.replace(tmp, path)


def update_manifest(rows: list[dict[str, Any]], stats: dict[str, int], generated_at: str) -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    layers = manifest.get("layers")
    if not isinstance(layers, dict):
        layers = {}
    status_counts: dict[str, int] = dict(Counter(str(r.get("status") or "") for r in rows))
    tag_counts: Counter[str] = Counter()
    for row in rows:
        for entry in row.get("tags") or []:
            if isinstance(entry, dict) and entry.get("tag"):
                tag_counts[str(entry["tag"])] += 1
    layers["media_vision"] = {
        "generated_at": generated_at,
        "model": MODEL,
        "model_version": MODEL_VERSION,
        "prompt_hash": PROMPT_HASH,
        "row_count": len(rows),
        "cost_estimate_usd": 0.0,
        "status_counts": status_counts,
        "tag_frequency": dict(sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        **stats,
    }
    manifest["layers"] = layers
    tmp = MANIFEST_PATH.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, MANIFEST_PATH)


def tweet_text(tweet: dict[str, Any]) -> str:
    return str(tweet.get("text_resolved") or tweet.get("text") or "")


def clean_text(text: str) -> str:
    return " ".join(text.split())


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def numeric(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def dimension_text(media: dict[str, Any]) -> str:
    width = int(numeric(media.get("width")))
    height = int(numeric(media.get("height")))
    return f"{width}x{height}" if width and height else ""


def duration_text(media: dict[str, Any]) -> str:
    duration = numeric(media.get("duration_sec"))
    return f"{duration:.1f}s" if duration else ""


def bytes_text(media: dict[str, Any]) -> str:
    byte_count = numeric(media.get("bytes"))
    if not byte_count:
        return ""
    return f"{byte_count / 1024 / 1024:.2f} MiB"


def kind_label(media_type: str) -> str:
    if media_type == "animated_gif":
        return "animated GIF"
    return media_type or "media"


def tag_slug_for(media_type: str) -> str:
    if media_type == "animated_gif":
        return "animated-gif"
    return media_type or "item"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handle", help="Restrict to one data/<handle>.parquet file.")
    parser.add_argument(
        "--include-pending",
        action="store_true",
        help="Also describe media that has not been archived yet.",
    )
    parser.add_argument("--force", action="store_true", help="Ignore the existing sidecar cache.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Report planned rows without writing."
    )
    parser.add_argument(
        "--max-items",
        type=int,
        help="Maximum number of uncached media items to describe in this run.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    parquets = discover_canonical_parquets()
    if args.handle:
        parquets = [p for p in parquets if p.stem == args.handle]
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows, stats = build_rows(
        parquets,
        generated_at=generated_at,
        include_pending=bool(args.include_pending),
        force=bool(args.force),
        max_items=args.max_items,
    )
    if args.dry_run:
        LOG.info("media recognition dry run", rows=len(rows), **stats)
        return 0
    write_parquet(rows, OUT_PATH)
    update_manifest(rows, stats, generated_at)
    LOG.info(
        "media recognition sidecar complete",
        sidecar_rows=len(rows),
        out=str(OUT_PATH.relative_to(REPO_ROOT)),
        **stats,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
