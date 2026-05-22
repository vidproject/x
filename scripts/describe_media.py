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
import re
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
GENERATED_DATA_PARQUETS = frozenset({"catalog.parquet"})
OUT_PATH = TAGS_DIR / "media_vision.parquet"
MANIFEST_PATH = TAGS_DIR / "manifest.json"

MODEL = "metadata"
MODEL_VERSION = "media-metadata-v4"
PROMPT = (
    "Describe public X media from canonical capture metadata. "
    "Do not infer visual content beyond alt text, captured metadata, or "
    "curated manual media-review observations. Include tweet card and link "
    "context when available, labeling it as metadata rather than visual fact."
)
PROMPT_HASH = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()[:16]
MANUAL_REVIEW_QUEUE_PATH = TAGS_DIR / "manual_media_review_queue.json"
LEGACY_MANUAL_TAG_ALIASES = {
    "media:produced-video": "video:produced",
    "shape:lineup": "genre:lineup",
    "branch:army": "military:army",
    "branch:navy": "military:navy",
    "branch:air-force": "military:air-force",
    "branch:space-force": "military:space-force",
    "branch:marines": "military:marines",
    "branch:coast-guard": "military:coast-guard",
    "branch:national-guard": "military:national-guard",
    "video:ad": "genre:advertisement",
    "video:music-video": "genre:music-video",
    "video:psa": "genre:psa",
    # Namespace-migration aliases (media:* production attrs -> video:/genre:)
    "media:montage": "video:montage",
    "media:text-overlay": "video:text-overlay",
    "media:voiceover": "video:voiceover",
    "media:music-video": "genre:music-video",
    "media:short-video": "video:short",
    # Namespace-migration aliases (media:* status flags -> media-status:*)
    "media:archived": "media-status:archived",
    "media:described": "media-status:described",
    "media:has-alt-text": "media-status:has-alt-text",
    "media:needs-vision": "media-status:needs-vision",
    "media:needs-ocr": "media-status:needs-ocr",
    "media:graphic-content": "media-status:graphic-content",
}


def normalize_tag_name(tag: str) -> str:
    return LEGACY_MANUAL_TAG_ALIASES.get(tag, tag)


def discover_canonical_parquets() -> list[Path]:
    return sorted(
        p
        for p in DATA_DIR.glob("*.parquet")
        if p.is_file() and p.name not in GENERATED_DATA_PARQUETS
    )


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def input_hash_for(
    tweet: dict[str, Any],
    media: dict[str, Any],
    manual_review: dict[str, Any] | None = None,
) -> str:
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
        "card": card_hash_payload(tweet),
        "manual_review": manual_review_hash_payload(manual_review),
        "model_version": MODEL_VERSION,
        "prompt_hash": PROMPT_HASH,
    }
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def manual_review_hash_payload(manual_review: dict[str, Any] | None) -> dict[str, Any] | None:
    if not manual_review:
        return None
    return {
        "visual_observation": manual_review.get("visual_observation"),
        "candidate_visual_tags": manual_review.get("candidate_visual_tags"),
        "deterministic_signal_missing": manual_review.get("deterministic_signal_missing"),
    }


def card_hash_payload(tweet: dict[str, Any]) -> dict[str, Any] | None:
    card = tweet.get("card")
    if not isinstance(card, dict):
        return None
    return {
        "name": card.get("name"),
        "title": card.get("title"),
        "description": card.get("description"),
        "card_url": card.get("card_url"),
        "vendor_url": card.get("vendor_url"),
        "image_url": card.get("image_url"),
    }


def tag_entry(
    tag: str,
    *,
    tentative: bool = False,
    source: str = "media-metadata",
) -> dict[str, Any]:
    return {
        "tag": tag,
        "tentative": True if tentative else None,
        "source": source,
        "span_start": None,
        "span_end": None,
    }


def describe_media_item(
    tweet: dict[str, Any],
    media: dict[str, Any],
    *,
    generated_at: str,
    manual_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    media_type = str(media.get("media_type") or "media")
    media_id = str(media.get("media_id") or "")
    alt_text = clean_text(str(media.get("alt_text") or ""))
    context = clean_text(tweet_text(tweet))
    card_context = card_context_parts(tweet)
    archived = bool(media.get("release_asset_url"))
    dimensions = dimension_text(media)
    duration = duration_text(media)
    byte_count = bytes_text(media)
    archive_url = clean_text(str(media.get("release_asset_url") or ""))
    original_url = clean_text(str(media.get("original_url") or ""))
    visual_observation = clean_text(str((manual_review or {}).get("visual_observation") or ""))
    tweet_excerpt = clean_text(str((manual_review or {}).get("tweet_text_excerpt") or ""))

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
    for label, value, field in card_context:
        parts.append(f"{label}: {truncate(value, 260)}")
        source_fields.append(field)
    if original_url:
        parts.append(f"source URL: {truncate(original_url, 180)}")
        source_fields.append("original_url")
    if archive_url:
        parts.append(f"archive URL: {truncate(archive_url, 180)}")
        source_fields.append("release_asset_url")
    if tweet_excerpt and tweet_excerpt not in context:
        parts.append(f"review tweet excerpt: {truncate(tweet_excerpt, 260)}")
        source_fields.append("manual_media_review_queue")
    if visual_observation:
        parts.append(f"visual observation: {visual_observation}")
        source_fields.append("manual_media_review_queue")

    needs_vision = (
        media_type in {"photo", "video", "animated_gif"} and not alt_text and not visual_observation
    )
    if needs_vision:
        parts.append("needs OCR, transcript, or frame-level vision before content claims")

    description = "; ".join(parts) + "."
    tags = [tag_entry("media-status:described"), tag_entry(f"media:{tag_slug_for(media_type)}")]
    if archived:
        tags.append(tag_entry("media-status:archived"))
    if alt_text:
        tags.append(tag_entry("media-status:has-alt-text"))
    if needs_vision:
        tags.append(tag_entry("media-status:needs-vision", tentative=True))
    duration_seconds = numeric(media.get("duration_sec"))
    if media_type in {"video", "animated_gif"} and 0 < duration_seconds <= 30:
        tags.append(tag_entry("video:short"))
    tag_derivation_text = " ".join(
        p
        for p in [
            alt_text,
            context,
            " ".join(value for _, value, _ in card_context),
            original_url,
            tweet_excerpt,
            visual_observation,
        ]
        if p
    )
    for tag in derive_description_tags(tag_derivation_text, media_type=media_type):
        tags.append(tag_entry(tag, source="media-description"))
    if manual_review:
        tags.extend(candidate_visual_tag_entries(manual_review, media_type=media_type))
    tags = dedupe_tag_entries(tags)

    status = (
        "manual-review"
        if visual_observation
        else "metadata-alt"
        if alt_text
        else "metadata-context"
        if context or card_context or original_url
        else "metadata-only"
    )
    confidence = (
        0.92
        if visual_observation
        else 0.68
        if alt_text
        else 0.45
        if status == "metadata-context"
        else 0.35
    )
    return {
        "tweet_id": str(tweet.get("tweet_id") or ""),
        "account_handle": str(tweet.get("account_handle") or ""),
        "media_id": media_id,
        "media_type": media_type,
        "media_sha256": str(media.get("sha256") or ""),
        "input_hash": input_hash_for(tweet, media, manual_review),
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


def derive_description_tags(text: str, *, media_type: str) -> list[str]:
    """Infer media-production tags from description text.

    This is intentionally conservative: it consumes text already present in
    alt text, manual review, OCR/vision descriptions, or tweet context. It
    does not inspect pixels by itself.
    """
    haystack = text.lower()
    media_form_haystack = without_negated_media_form_claims(haystack)
    is_video = media_type in {"video", "animated_gif"}
    # Speech / press-conference indicators. When present, this clip is oratory,
    # not a music video, even if the description mentions incidental music.
    has_speech_indicator = re_search(
        r"\b(podium|remarks|speech|address|press\s+conference|press\s+briefing|"
        r"press\s+gaggle|oval\s+office|rose\s+garden|delivers?\s+remarks|"
        r"delivered\s+remarks|interview|spoke\s+to\s+reporters)\b",
        haystack,
    )
    out: list[str] = []

    def add(tag: str) -> None:
        if tag not in out:
            out.append(tag)

    if re_search(
        r"\b(text[- ]only|text overlay|title[- ]card|caption|chyron|lower-third|headline|press-release card|graphic text)\b",
        haystack,
    ):
        add("video:text-overlay")
    musical_score = (
        r"\b(?:musical|orchestral|cinematic|dramatic|film|movie|trailer)\s+score\b"
    )
    if is_video and re_search(
        r"\b(polished|produced|edited|multi-shot|multi shot|rapid[- ]cut|b-roll|screencast|"
        r"recruitment|psa|public service announcement|commercial|cinematic|trailer[- ]style|"
        r"title[- ]card|end[- ]card|color[- ]graded|soundtrack|music bed)\b|"
        + musical_score,
        media_form_haystack,
    ):
        add("video:produced")
    if is_video and re_search(
        r"\bmontage\b|\bmultiple shots?\b|\bsequence of clips?\b|\bseries of clips?\b|"
        r"\bb-roll\b|\brapid[- ]cut\b|\bcuts between\b",
        media_form_haystack,
    ):
        add("video:montage")
    # genre:music-video requires STRONG, explicit music-video evidence.
    # Generic / metaphorical music wording ("background music", "soundtrack",
    # "musical score", "beat drops", "the soundtrack of America") is NOT enough
    # and is intentionally excluded — it over-tagged speeches and incidental
    # mentions. Speech / press-conference clips never get music-video.
    if (
        is_video
        and not has_speech_indicator
        and re_search(
            r"\b(?:official\s+)?music\s+video\b"
            r"|\bset\s+to\s+(?:music|the\s+song|the\s+track)\b"
            r"|\bofficial\s+(?:video|audio)\s+for\b"
            r"|\b(?:lyric|lyrics)\s+video\b",
            media_form_haystack,
        )
    ):
        add("genre:music-video")
    if is_video and re_search(r"\bvoiceover\b|\bvoice-over\b|\bnarration\b|\bnarrator\b|\bnarrated\b", haystack):
        add("video:voiceover")
    if is_video and re_search(
        r"\b(cnn|fox news|msnbc|cbs news|abc news|nbc news|newsmax|lower-third|chyron|broadcast)\b",
        haystack,
    ):
        add("video:news-clip")
    if is_video and re_search(
        r"\b(psa|public service announcement|did you know|learn more|hotline)\b", haystack
    ):
        add("genre:psa")
    if is_video and re_search(
        r"\b(podium|remarks|speech|address|press conference|press briefing)\b", haystack
    ):
        add("video:speech")
    if is_video and re_search(
        r"\b(recruitment|commercial|campaign ad|ad spot|apply now|apply today)\b", haystack
    ):
        if re_search(r"\b(recruitment|apply now|apply today)\b", haystack):
            add("genre:recruitment")
        add("genre:advertisement")
    if is_video and re_search(
        r"\bwar[- ]movie\b|\bwar[- ]film\b|\baction[- ]movie\b|"
        r"\b(?:cinematic|trailer[- ]style|dramatic)\b.{0,80}\b(?:battle|combat|military|raid|operation)\b|"
        r"\b(?:battle|combat|military|raid|operation)\b.{0,80}\b(?:cinematic|trailer[- ]style|dramatic)\b",
        haystack,
    ):
        add("genre:war-movie")
    if is_video and re_search(
        r"\bdystopian\b|\bsci[- ]?fi\b|\bscience[- ]fiction\b|\bcyberpunk\b|"
        r"\b(?:bleak|dark|apocalyptic|hellscape|surveillance[- ]state|futuristic)\b.{0,80}\b(?:city|scene|vision|aesthetic|future)\b",
        haystack,
    ):
        add("genre:dystopian")
    if is_video and re_search(
        r"\butopian\b|\bidealized\b|\baspirational\b|\bbright future\b|\bgolden age\b|"
        r"\b(?:sunlit|heroic|triumphal)\b.{0,80}\b(?:montage|vision|future|aesthetic)\b",
        haystack,
    ):
        add("genre:utopian")
    return out


NEGATED_MEDIA_FORM_PATTERN = re.compile(
    r"\b(?:not|no|without|never|isn['’]?t|is\s+not|does\s+not\s+appear\s+to\s+be)\b"
    r".{0,90}\b(?:music\s+videos?|music[- ](?:led|driven)\s+(?:clips?|montages?|videos?)|"
    r"produced[- ]videos?|produced\s+videos?|montages?|soundtracks?|music\s+beds?)\b",
    re.I,
)


def without_negated_media_form_claims(text: str) -> str:
    """Drop negated media-form phrases before positive media-form matching."""
    return NEGATED_MEDIA_FORM_PATTERN.sub(" ", text)


def re_search(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.I) is not None


def candidate_visual_tag_entries(
    manual_review: dict[str, Any],
    *,
    media_type: str,
) -> list[dict[str, Any]]:
    raw = manual_review.get("candidate_visual_tags")
    if not isinstance(raw, list):
        return []
    is_video = media_type in {"video", "animated_gif"}
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in raw:
        tag = str(value or "").strip()
        tag = normalize_tag_name(tag)
        if not tag or tag in seen:
            continue
        # Do not apply video:* tags to still-image review rows. Those notes
        # are useful for future taxonomy work but not a deterministic tag.
        if (tag.startswith("video:") or tag.startswith("genre:")) and not is_video:
            continue
        seen.add(tag)
        entries.append(tag_entry(tag, source="manual-media-review"))
    return entries


def dedupe_tag_entries(tags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    priority = {
        "manual-media-review": 4,
        "media-description": 3,
        "media-metadata": 2,
    }
    out: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    for entry in tags:
        tag = normalize_tag_name(str(entry.get("tag") or ""))
        if not tag:
            continue
        entry = {**entry, "tag": tag}
        if tag not in index:
            index[tag] = len(out)
            out.append(entry)
            continue
        current = out[index[tag]]
        current_priority = priority.get(str(current.get("source") or ""), 0)
        next_priority = priority.get(str(entry.get("source") or ""), 0)
        if next_priority > current_priority:
            out[index[tag]] = entry
    if any(entry["tag"].startswith("military:") for entry in out) and "topic:military" not in index:
        out.append(tag_entry("topic:military", source="media-description"))
    return out


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
            if isinstance(row.get("tags"), list):
                row["tags"] = dedupe_tag_entries(row["tags"])
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
    manual_reviews = load_manual_review_queue()
    rows: list[dict[str, Any]] = []
    stats = Counter[str]()
    generated = 0

    for path in parquets:
        df = pl.read_parquet(path)
        for tweet in df.iter_rows(named=True):
            for media in media_candidates(tweet, include_pending=include_pending):
                key = (str(tweet.get("tweet_id") or ""), str(media.get("media_id") or ""))
                manual_review = manual_reviews.get(key)
                draft = describe_media_item(
                    tweet,
                    media,
                    generated_at=generated_at,
                    manual_review=manual_review,
                )
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


def load_manual_review_queue() -> dict[tuple[str, str], dict[str, Any]]:
    if not MANUAL_REVIEW_QUEUE_PATH.exists():
        return {}
    data = json.loads(MANUAL_REVIEW_QUEUE_PATH.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for item in data.get("items", []):
        if not isinstance(item, dict):
            continue
        tweet_id = str(item.get("tweet_id") or "")
        media_id = str(item.get("media_id") or "")
        if tweet_id and media_id:
            out[(tweet_id, media_id)] = item
    return out


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


def card_context_parts(tweet: dict[str, Any]) -> list[tuple[str, str, str]]:
    card = tweet.get("card")
    if not isinstance(card, dict):
        return []
    out: list[tuple[str, str, str]] = []
    for label, key in (
        ("card title", "title"),
        ("card description", "description"),
        ("card vendor URL", "vendor_url"),
        ("card URL", "card_url"),
    ):
        value = clean_text(str(card.get(key) or ""))
        if value:
            out.append((label, value, f"card.{key}"))
    return out


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
