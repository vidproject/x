"""Build ready-only creative review JSON for the standalone swipe site.

The PDF dossier is intentionally broad. This generator is stricter: it keeps
only rows whose review media are fully prepared for Amy's yes/no/superlike
pass. Prepared means archived media plus the relevant analysis sidecars:

* photos: thumbnail, OCR, and a genuine visual description;
* videos/GIFs: keyframes, keyframe OCR, audio detection, transcript when audio
  is present, thumbnail, and a genuine visual description.

Rows that match the creative-content rules but are missing any required sidecar
are written to ``creative-not-ready.json`` instead of being mixed into the
review queues.

Run with:

    uv run python -m scripts.build_creative_site_data
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from scripts._logging import configure

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"
OUT_DIR = DATA_DIR / "creative"
CATALOG_PATH = DATA_DIR / "catalog.parquet"
ACCOUNT_CATEGORIES_PATH = DATA_DIR / "account_categories.json"
PRODUCED_CSV = TAGS_DIR / "produced_videos.csv"
MEME_CSV = TAGS_DIR / "meme_images.csv"
MANUAL_REVIEW_QUEUE = TAGS_DIR / "manual_media_review_queue.json"

VIDEO_TYPES = {"video", "animated_gif"}
VISUAL_TYPES = {"photo", "video", "animated_gif"}
VISION_REVIEW_MODEL = "opus-vision-review"
HAS_ALT_TAG = "media-status:has-alt-text"

OCR_COMPLETE_STATUSES = {"ok", "no-text"}
KEYFRAME_COMPLETE_STATUSES = {"ok"}
THUMB_COMPLETE_STATUSES = {"ok"}
AUDIO_COMPLETE_STATUSES = {"ok", "silent-audio", "no-audio-stream"}
TRANSCRIPT_COMPLETE_STATUSES = {"ok", "empty-transcript", "no-audio-stream"}

WHOLLY_CREATIVE_RE = re.compile(
    r"\b(?:ai-generated|synthetic|pixel[- ]art|animation|animated|cgi|poster|meme|"
    r"parody|cartoon|illustration|illustrated|comic|collage|photoshop|vintage|"
    r"wpa|wwii|propaganda|stylized|mirthnuke|nice agents|busted\s*&\s*booted|"
    r"remigrate|wanted|booking card|rogues-gallery|trading card)\b",
    re.I,
)
CREATIVE_USE_RE = re.compile(
    r"\b(?:asmr|set to music|music bed|soundtrack|montage|rapid[- ]cut|fast cuts?|"
    r"color[- ]graded|cinematic|trailer[- ]style|war[- ]movie|slow[- ]motion|"
    r"title card|text overlay|lower-third|reticle|vhs|glitch|neon|gothic|"
    r"motion[- ]blur|stylized|animated|cgi|pixel[- ]art|voiceover|voice-over)\b",
    re.I,
)
REAL_ENFORCEMENT_RE = re.compile(
    r"\b(?:detainee|detainees|handcuff(?:ed|s)?|shackle(?:d|s)?|chain(?:ed|s)?|"
    r"belly chains?|custody|detention|deport(?:ed|ation|ing)?|self-deport|"
    r"illegal alien|illegal aliens|migrant|migrants|arrest(?:ed|s)?|raid|"
    r"removal flight|deportation flight|ice air|ero|hsi|cbp|border patrol)\b",
    re.I,
)
ROUTINE_EXCLUDE_RE = re.compile(
    r"\b(?:fox news|newsmax|cnn|msnbc|cbs news|abc news|nbc news|newsnation|"
    r"rebroadcast|television segment|cable-news|interview|press briefing|"
    r"press conference|speech excerpt|single continuous shot|last week at dhs|"
    r"standard news package|ordinary statistics card|routine statistics card)\b",
    re.I,
)
PLAIN_INFOGRAPHIC_RE = re.compile(
    r"\b(?:infographic|statistics card|enforcement update|single-day statistics)\b",
    re.I,
)


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def source_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_account_categories() -> dict[str, dict[str, Any]]:
    if not ACCOUNT_CATEGORIES_PATH.exists():
        return {}
    data = read_json(ACCOUNT_CATEGORIES_PATH)
    return data.get("categories", {}) if isinstance(data, dict) else {}


def load_catalog() -> dict[str, dict[str, Any]]:
    df = pl.read_parquet(CATALOG_PATH)
    return {str(row["tweet_id"]): row for row in df.to_dicts()}


def csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def tag_names(values: Any) -> list[str]:
    out: list[str] = []
    for entry in values or []:
        tag = entry.get("tag") if isinstance(entry, dict) else str(entry or "")
        tag = str(tag or "").strip()
        if tag and tag not in out:
            out.append(tag)
    return out


def tag_name_set(values: Any) -> set[str]:
    return set(tag_names(values))


def sidecar_rows(path: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    if not path.exists():
        return {}
    df = pl.read_parquet(path)
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in df.to_dicts():
        key = (str(row.get("tweet_id") or ""), str(row.get("media_id") or ""))
        if key[0] and key[1]:
            out.setdefault(key, []).append(row)
    return out


def load_manual_observations() -> dict[tuple[str, str], str]:
    if not MANUAL_REVIEW_QUEUE.exists():
        return {}
    data = read_json(MANUAL_REVIEW_QUEUE)
    out: dict[tuple[str, str], str] = {}
    for item in data.get("items", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        observation = str(item.get("visual_observation") or "").strip()
        if not observation:
            continue
        key = (str(item.get("tweet_id") or ""), str(item.get("media_id") or ""))
        if key[0] and key[1]:
            out[key] = observation
    return out


def text_blob(*parts: Any) -> str:
    flat: list[str] = []
    for part in parts:
        if isinstance(part, list):
            flat.extend(str(x or "") for x in part)
        elif isinstance(part, dict):
            flat.append(json.dumps(part, ensure_ascii=False, sort_keys=True))
        else:
            flat.append(str(part or ""))
    return "\n".join(flat)


def item_year(value: str) -> int:
    try:
        return int(str(value)[:4])
    except ValueError:
        return 0


def era_for(posted_at: str) -> str | None:
    year = item_year(posted_at)
    if year >= 2025:
        return "2025_plus"
    if 2016 <= year <= 2020:
        return "2016_2020"
    return None


def relative_thumb(path: str | None) -> str | None:
    if not path:
        return None
    clean = str(path).replace("\\", "/")
    if clean.startswith("data/"):
        return f"../{clean}"
    return clean


def first_matching(rows: list[dict[str, Any]], statuses: set[str]) -> dict[str, Any] | None:
    for row in rows:
        if str(row.get("status") or "") in statuses:
            return row
    return rows[0] if rows else None


def intish(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(row.get("status") or "missing") for row in rows))


def joined_text(rows: list[dict[str, Any]]) -> str:
    parts = [str(row.get("text") or "") for row in rows if str(row.get("text") or "").strip()]
    return "\n\n".join(parts)


def choose_description(
    key: tuple[str, str],
    *,
    vision_rows: list[dict[str, Any]],
    manual_observations: dict[tuple[str, str], str],
    curated_description: str,
) -> tuple[bool, str, str]:
    """Return (has_genuine_description, source, full_text)."""
    for row in vision_rows:
        if str(row.get("model") or "") == VISION_REVIEW_MODEL:
            return (
                True,
                "opus-vision-review",
                str(row.get("summary_text") or row.get("description") or ""),
            )
    if curated_description.strip():
        return True, "curated-review", curated_description.strip()
    if key in manual_observations:
        return True, "manual-visual-observation", manual_observations[key]
    for row in vision_rows:
        if HAS_ALT_TAG in tag_name_set(row.get("tags")):
            return (
                True,
                "captured-alt-text",
                str(row.get("summary_text") or row.get("description") or ""),
            )
    for row in vision_rows:
        desc = str(row.get("summary_text") or row.get("description") or "")
        if desc.strip():
            return False, "metadata-placeholder", desc
    return False, "missing", ""


def media_ready_entry(
    tweet: dict[str, Any],
    media: dict[str, Any],
    *,
    indexes: dict[str, dict[tuple[str, str], list[dict[str, Any]]]],
    manual_observations: dict[tuple[str, str], str],
    curated_description: str,
) -> dict[str, Any]:
    tweet_id = str(tweet.get("tweet_id") or "")
    media_id = str(media.get("media_id") or "")
    media_type = str(media.get("media_type") or "")
    key = (tweet_id, media_id)

    archive_url = str(media.get("release_asset_url") or "")
    checks: dict[str, bool | str | int | None] = {}
    blockers: list[str] = []

    def require(name: str, ok: bool, blocker: str) -> None:
        checks[name] = ok
        if not ok:
            blockers.append(blocker)

    require("archived", bool(archive_url), "missing archived media asset")

    vision_rows = indexes["vision"].get(key, [])
    has_desc, desc_source, description = choose_description(
        key,
        vision_rows=vision_rows,
        manual_observations=manual_observations,
        curated_description=curated_description,
    )
    require("description", has_desc, "missing genuine visual description")

    ocr_rows = indexes["ocr"].get(key, [])
    complete_ocr = [
        row for row in ocr_rows if str(row.get("status") or "") in OCR_COMPLETE_STATUSES
    ]
    ocr_text = joined_text(complete_ocr)

    thumb: str | None = None
    keyframe_row: dict[str, Any] | None = None
    audio_row: dict[str, Any] | None = None
    transcript_row: dict[str, Any] | None = None

    if media_type in VIDEO_TYPES:
        keyframe_rows = indexes["keyframes"].get(key, [])
        keyframe_row = first_matching(keyframe_rows, KEYFRAME_COMPLETE_STATUSES)
        frame_count = intish((keyframe_row or {}).get("frame_count"))
        keyframes_ok = bool(keyframe_row and keyframe_row.get("status") == "ok" and frame_count > 0)
        require("keyframes", keyframes_ok, "missing extracted video keyframes")
        checks["keyframe_count"] = frame_count
        thumb = relative_thumb(str((keyframe_row or {}).get("thumbnail_path") or ""))
        require("thumbnail", bool(thumb), "missing video thumbnail")
        require(
            "ocr",
            bool(keyframes_ok and len(complete_ocr) >= frame_count),
            "missing complete OCR for extracted keyframes",
        )

        audio_rows = indexes["audio"].get(key, [])
        audio_row = first_matching(audio_rows, AUDIO_COMPLETE_STATUSES)
        audio_status = str((audio_row or {}).get("status") or "")
        require("audio_analysis", audio_status in AUDIO_COMPLETE_STATUSES, "missing audio analysis")
        has_audio = audio_status == "ok" and intish((audio_row or {}).get("audio_stream_count")) > 0
        checks["has_audio"] = has_audio

        transcript_rows = indexes["transcripts"].get(key, [])
        transcript_row = first_matching(transcript_rows, TRANSCRIPT_COMPLETE_STATUSES)
        transcript_status = str((transcript_row or {}).get("status") or "")
        transcript_ok = (not has_audio) or transcript_status in TRANSCRIPT_COMPLETE_STATUSES
        require("transcript", transcript_ok, "missing transcript for video with audio")
    elif media_type == "photo":
        thumb_rows = indexes["photo_thumbnails"].get(key, [])
        thumb_row = first_matching(thumb_rows, THUMB_COMPLETE_STATUSES)
        thumb = relative_thumb(str((thumb_row or {}).get("thumbnail_path") or ""))
        require("thumbnail", bool(thumb), "missing photo thumbnail")
        require("ocr", bool(complete_ocr), "missing photo OCR")
    else:
        require("visual_media_type", False, f"unsupported media type {media_type}")

    transcript_text = str((transcript_row or {}).get("text") or "")
    audio_tags = tag_names((audio_row or {}).get("tags"))
    ready = not blockers
    return {
        "media_id": media_id,
        "type": "video" if media_type in VIDEO_TYPES else media_type,
        "archive_url": archive_url or None,
        "original_url": media.get("original_url"),
        "thumbnail_url": thumb,
        "sha256": media.get("sha256"),
        "duration_sec": media.get("duration_sec"),
        "playable": bool(archive_url),
        "readiness": {
            "ready": ready,
            "blockers": sorted(set(blockers)),
            "checks": checks,
        },
        "analysis": {
            "description": {
                "source": desc_source,
                "text": description,
                "sidecar_statuses": status_counts(vision_rows),
            },
            "ocr": {
                "status_counts": status_counts(ocr_rows),
                "complete_row_count": len(complete_ocr),
                "text": ocr_text,
            },
            "keyframes": {
                "status": (keyframe_row or {}).get("status"),
                "frame_count": intish((keyframe_row or {}).get("frame_count")),
            },
            "audio": {
                "status": (audio_row or {}).get("status"),
                "audio_stream_count": intish((audio_row or {}).get("audio_stream_count")),
                "duration_sec": (audio_row or {}).get("audio_duration_sec"),
                "tags": audio_tags,
            },
            "transcript": {
                "status": (transcript_row or {}).get("status"),
                "segment_count": intish((transcript_row or {}).get("segment_count")),
                "text": transcript_text,
            },
        },
    }


def media_entries(
    tweet: dict[str, Any],
    *,
    preferred_media_id: str | None,
    media_types: set[str],
    indexes: dict[str, dict[tuple[str, str], list[dict[str, Any]]]],
    manual_observations: dict[tuple[str, str], str],
    curated_description: str,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for media in tweet.get("media") or []:
        if not isinstance(media, dict):
            continue
        media_id = str(media.get("media_id") or "")
        if preferred_media_id and media_id != preferred_media_id:
            continue
        media_type = str(media.get("media_type") or "")
        if media_type not in media_types:
            continue
        entries.append(
            media_ready_entry(
                tweet,
                media,
                indexes=indexes,
                manual_observations=manual_observations,
                curated_description=curated_description,
            )
        )
    return entries


def engagement_from(tweet: dict[str, Any], row: dict[str, Any] | None = None) -> dict[str, int]:
    row = row or {}
    return {
        "likes": int(float(row.get("like_count") or tweet.get("like_count") or 0)),
        "retweets": int(float(row.get("retweet_count") or tweet.get("retweet_count") or 0)),
        "quotes": int(float(tweet.get("quote_count") or 0)),
        "views": int(float(tweet.get("view_count") or 0)),
    }


def classify_inclusion(
    blob: str, *, allow_routine: bool = False
) -> tuple[str | None, int, list[str]]:
    creative = bool(WHOLLY_CREATIVE_RE.search(blob))
    real = bool(REAL_ENFORCEMENT_RE.search(blob))
    creative_use = bool(CREATIVE_USE_RE.search(blob))
    routine = bool(ROUTINE_EXCLUDE_RE.search(blob))
    plain_infographic = bool(PLAIN_INFOGRAPHIC_RE.search(blob)) and not creative
    reasons: list[str] = []
    if creative:
        reasons.append("wholly creative media signal")
    if real:
        reasons.append("real enforcement/detainee subject signal")
    if creative_use:
        reasons.append("creative treatment signal")
    if routine and not allow_routine:
        reasons.append("routine news/speech signal")
    if plain_infographic:
        reasons.append("plain infographic/statistics signal")
    if creative and not plain_infographic:
        return "wholly_creative_media", 90 if not routine else 72, reasons
    if real and creative_use and not (routine and not allow_routine):
        return "real_footage_creative_use", 84, reasons
    return None, 0, reasons


def creative_forms(blob: str, tags: list[str]) -> list[str]:
    forms: list[str] = []
    checks = [
        ("animation", r"\b(?:animation|animated|pixel[- ]art|cgi)\b"),
        ("ai-or-synthetic", r"\b(?:ai-generated|synthetic|cgi)\b"),
        ("poster-or-meme", r"\b(?:poster|meme|parody|cartoon|illustration|collage)\b"),
        ("music-led", r"\b(?:set to music|music bed|soundtrack|music-video)\b"),
        ("montage", r"\b(?:montage|rapid[- ]cut|fast cuts?|b-roll)\b"),
        ("cinematic", r"\b(?:cinematic|trailer[- ]style|war[- ]movie|color[- ]graded)\b"),
        ("text-overlay", r"\b(?:title card|text overlay|lower-third|caption|reticle)\b"),
        ("asmr", r"\basmr\b"),
    ]
    for name, pattern in checks:
        if re.search(pattern, blob, re.I) and name not in forms:
            forms.append(name)
    for tag in tags:
        if tag.startswith("genre:"):
            forms.append(tag.removeprefix("genre:"))
        elif tag in {"video:montage", "video:text-overlay", "video:voiceover"}:
            forms.append(tag.removeprefix("video:"))
    return sorted(set(forms))


def subjects(blob: str) -> list[str]:
    checks = [
        ("detainees", r"\b(?:detainee|detainees|handcuff|shackle|chain|custody|detention)\b"),
        ("deportation", r"\b(?:deport|removal flight|ice air|self-deport)\b"),
        ("arrest-or-raid", r"\b(?:arrest|raid|operation|hsi|ero|border patrol|cbp)\b"),
        ("immigration-language", r"\b(?:illegal alien|migrant|cbp home)\b"),
    ]
    return [label for label, pattern in checks if re.search(pattern, blob, re.I)]


def aggregate_readiness(media: list[dict[str, Any]]) -> dict[str, Any]:
    blockers: list[str] = []
    for entry in media:
        blockers.extend(entry["readiness"]["blockers"])
    return {
        "ready": bool(media) and not blockers,
        "blockers": sorted(set(blockers)),
        "media_count": len(media),
    }


def queue_for_item(*, era: str | None, review_state: str, score: int, confidence: str) -> str:
    if era == "2016_2020":
        return "historical_2016_2020"
    if review_state == "curated" or confidence == "high" or score >= 90:
        return "high_confidence"
    return "candidates"


def base_item(
    tweet: dict[str, Any],
    *,
    source_rows: list[str],
    review_state: str,
    confidence: str,
    basis: str,
    score: int,
    reason_list: list[str],
    evidence_summary: str,
    notable_text: str,
    account_categories: dict[str, dict[str, Any]],
    media: list[dict[str, Any]],
    tags: list[str],
    row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    handle = str(tweet.get("account_handle") or (row or {}).get("account_handle") or "")
    account_meta = account_categories.get(handle, {})
    blob = text_blob(
        evidence_summary, notable_text, tweet.get("text_resolved") or tweet.get("text"), tags
    )
    era = era_for(str(tweet.get("posted_at") or (row or {}).get("posted_at") or ""))
    readiness = aggregate_readiness(media)
    queue = queue_for_item(era=era, review_state=review_state, score=score, confidence=confidence)
    return {
        "id": f"{tweet.get('tweet_id') or (row or {}).get('tweet_id')}:{','.join(m['media_id'] for m in media)}",
        "tweet_id": str(tweet.get("tweet_id") or (row or {}).get("tweet_id") or ""),
        "posted_at": str(tweet.get("posted_at") or (row or {}).get("posted_at") or ""),
        "era": era,
        "queue": queue,
        "account": {
            "handle": handle,
            "category": account_meta.get("category"),
            "label": account_meta.get("label") or handle,
        },
        "tweet_url": str(tweet.get("tweet_url") or (row or {}).get("tweet_url") or ""),
        "tweet_text": str(tweet.get("text_resolved") or tweet.get("text") or ""),
        "review_state": review_state,
        "inclusion_basis": basis,
        "confidence": confidence,
        "score": score,
        "creative_forms": creative_forms(blob, tags),
        "subjects": subjects(blob),
        "tags": sorted(set(tags)),
        "media": media,
        "readiness": readiness,
        "evidence": {
            "summary": evidence_summary,
            "notable_text": notable_text,
            "reasons": reason_list,
            "source_sidecars": source_rows,
        },
        "engagement": engagement_from(tweet, row),
    }


def build_from_curated(
    *,
    catalog: dict[str, dict[str, Any]],
    account_categories: dict[str, dict[str, Any]],
    indexes: dict[str, dict[tuple[str, str], list[dict[str, Any]]]],
    manual_observations: dict[tuple[str, str], str],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in csv_rows(PRODUCED_CSV):
        tweet = catalog.get(str(row.get("tweet_id") or ""))
        if not tweet:
            continue
        curated_description = text_blob(
            row.get("summary"), row.get("script"), row.get("notable_text")
        )
        blob = text_blob(row, tweet.get("text_resolved") or tweet.get("text"), tweet.get("tags"))
        basis, score, reasons = classify_inclusion(blob, allow_routine=True)
        if not basis:
            continue
        tags = tag_names(tweet.get("tags")) + [t for t in row.get("genre_tags", "").split(";") if t]
        media = media_entries(
            tweet,
            preferred_media_id=None,
            media_types=VIDEO_TYPES,
            indexes=indexes,
            manual_observations=manual_observations,
            curated_description=curated_description,
        )
        if not media:
            continue
        out[str(row["tweet_id"])] = base_item(
            tweet,
            source_rows=["produced_videos.csv"],
            review_state="curated",
            confidence="high",
            basis=basis,
            score=score + 20,
            reason_list=reasons,
            evidence_summary=row.get("summary", ""),
            notable_text=row.get("notable_text", ""),
            account_categories=account_categories,
            media=media,
            tags=tags,
            row=row,
        )
    for row in csv_rows(MEME_CSV):
        tweet = catalog.get(str(row.get("tweet_id") or ""))
        if not tweet:
            continue
        curated_description = str(row.get("description") or "")
        blob = text_blob(row, tweet.get("text_resolved") or tweet.get("text"), tweet.get("tags"))
        basis, score, reasons = classify_inclusion(blob, allow_routine=True)
        if not basis:
            continue
        media_id = str(row.get("media_id") or "")
        tags = tag_names(tweet.get("tags"))
        media = media_entries(
            tweet,
            preferred_media_id=media_id,
            media_types=VISUAL_TYPES,
            indexes=indexes,
            manual_observations=manual_observations,
            curated_description=curated_description,
        )
        if not media:
            continue
        out[f"{row['tweet_id']}:{media_id}"] = base_item(
            tweet,
            source_rows=["meme_images.csv"],
            review_state="curated",
            confidence="high",
            basis=basis,
            score=score + 15,
            reason_list=reasons,
            evidence_summary=row.get("description", ""),
            notable_text=row.get("notable_text", ""),
            account_categories=account_categories,
            media=media,
            tags=tags,
            row={"account_handle": row.get("handle"), **row},
        )
    return out


def build_computed_candidates(
    *,
    catalog: dict[str, dict[str, Any]],
    account_categories: dict[str, dict[str, Any]],
    indexes: dict[str, dict[tuple[str, str], list[dict[str, Any]]]],
    manual_observations: dict[tuple[str, str], str],
    existing_tweet_ids: set[str],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for tweet_id, tweet in catalog.items():
        era = era_for(str(tweet.get("posted_at") or ""))
        if era not in {"2025_plus", "2016_2020"} or tweet_id in existing_tweet_ids:
            continue
        tags = tag_names(tweet.get("tags"))
        descriptions = []
        for insight in tweet.get("media_insights") or []:
            if isinstance(insight, dict):
                descriptions.append(
                    str(insight.get("summary_text") or insight.get("description") or "")
                )
        blob = text_blob(tweet.get("text_resolved") or tweet.get("text"), descriptions, tags)
        basis, score, reasons = classify_inclusion(blob, allow_routine=False)
        if not basis:
            continue
        media = media_entries(
            tweet,
            preferred_media_id=None,
            media_types=VISUAL_TYPES,
            indexes=indexes,
            manual_observations=manual_observations,
            curated_description="",
        )
        if not media:
            continue
        confidence = "medium" if era == "2025_plus" else "low"
        if era == "2016_2020":
            reasons.append("older-era candidate; annotations are thinner")
        out[tweet_id] = base_item(
            tweet,
            source_rows=["catalog.parquet", "media_insights", "tags"],
            review_state="candidate" if era == "2025_plus" else "needs_review",
            confidence=confidence,
            basis=basis,
            score=score,
            reason_list=reasons,
            evidence_summary="\n\n".join(descriptions),
            notable_text="",
            account_categories=account_categories,
            media=media,
            tags=tags,
        )
    return out


def sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            item.get("era") != "2025_plus",
            item.get("queue") != "high_confidence",
            -int(item.get("score") or 0),
            -int((item.get("engagement") or {}).get("likes") or 0)
            - int((item.get("engagement") or {}).get("retweets") or 0),
            item.get("posted_at") or "",
        ),
    )


def queue_payload(
    queue: str,
    items: list[dict[str, Any]],
    *,
    commit: str,
    source_total: int,
    not_ready_total: int,
) -> dict[str, Any]:
    counts = Counter(item["inclusion_basis"] for item in items)
    accounts = Counter(item["account"]["handle"] for item in items)
    return {
        "metadata": {
            "queue": queue,
            "generated_at": now_iso(),
            "source_commit": commit,
            "item_count": len(items),
            "source_candidate_count": source_total,
            "not_ready_count": not_ready_total,
            "all_items_ready": all(item["readiness"]["ready"] for item in items),
            "basis_counts": dict(sorted(counts.items())),
            "top_accounts": dict(accounts.most_common(12)),
            "review_actions": ["yes", "no", "superlike", "back"],
            "inclusion_rules": [
                "wholly creative media object",
                "or real enforcement/detainee/deportation footage used with creative treatment",
                "exclude routine news clips, speeches, raw arrests, and plain statistics cards",
            ],
            "readiness_rules": [
                "archived GitHub release media required",
                "photos require thumbnail, OCR, and genuine visual description",
                "videos require keyframes, keyframe OCR, audio analysis, thumbnail, and genuine visual description",
                "videos with audio require a completed transcript or completed empty-transcript result",
            ],
        },
        "items": items,
    }


def not_ready_payload(items: list[dict[str, Any]], *, commit: str) -> dict[str, Any]:
    blockers = Counter()
    for item in items:
        blockers.update(item["readiness"]["blockers"])
    return {
        "metadata": {
            "queue": "not_ready",
            "generated_at": now_iso(),
            "source_commit": commit,
            "item_count": len(items),
            "blocker_counts": dict(blockers.most_common()),
        },
        "items": items,
    }


def write_json_stable(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    prior = None
    if path.exists():
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prior = None
    if isinstance(prior, dict):
        old_meta = prior.get("metadata") if isinstance(prior.get("metadata"), dict) else {}
        comparable_prior = {**prior, "metadata": {**old_meta, "generated_at": None}}
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        comparable_new = {**payload, "metadata": {**meta, "generated_at": None}}
        if comparable_prior == comparable_new:
            payload["metadata"]["generated_at"] = (
                old_meta.get("generated_at") or payload["metadata"]["generated_at"]
            )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_indexes() -> dict[str, dict[tuple[str, str], list[dict[str, Any]]]]:
    return {
        "vision": sidecar_rows(TAGS_DIR / "media_vision.parquet"),
        "ocr": sidecar_rows(TAGS_DIR / "image_ocr.parquet"),
        "keyframes": sidecar_rows(TAGS_DIR / "keyframes.parquet"),
        "audio": sidecar_rows(TAGS_DIR / "audio_music.parquet"),
        "transcripts": sidecar_rows(TAGS_DIR / "transcripts.parquet"),
        "photo_thumbnails": sidecar_rows(TAGS_DIR / "photo_thumbnails.parquet"),
    }


def main() -> int:
    catalog = load_catalog()
    account_categories = load_account_categories()
    indexes = load_indexes()
    manual_observations = load_manual_observations()
    curated = build_from_curated(
        catalog=catalog,
        account_categories=account_categories,
        indexes=indexes,
        manual_observations=manual_observations,
    )
    computed = build_computed_candidates(
        catalog=catalog,
        account_categories=account_categories,
        indexes=indexes,
        manual_observations=manual_observations,
        existing_tweet_ids={key.split(":", 1)[0] for key in curated},
    )
    all_items = sort_items(list(curated.values()) + list(computed.values()))
    ready_items = [item for item in all_items if item["readiness"]["ready"]]
    not_ready = [item for item in all_items if not item["readiness"]["ready"]]
    high_confidence = [item for item in ready_items if item["queue"] == "high_confidence"]
    candidates = [item for item in ready_items if item["queue"] == "candidates"]
    historical = [item for item in ready_items if item["queue"] == "historical_2016_2020"]
    current_ready = [item for item in ready_items if item.get("era") == "2025_plus"]

    commit = source_commit()
    review_datasets = {
        "high_confidence": ("creative-high-confidence.json", high_confidence),
        "candidates": ("creative-candidates.json", candidates),
        "historical_2016_2020": ("creative-2016-2020.json", historical),
    }
    all_datasets = {
        **review_datasets,
        "2025_plus": ("creative-2025-plus.json", current_ready),
    }
    for queue, (filename, items) in all_datasets.items():
        write_json_stable(
            OUT_DIR / filename,
            queue_payload(
                queue,
                items,
                commit=commit,
                source_total=len(all_items),
                not_ready_total=len(not_ready),
            ),
        )
    write_json_stable(
        OUT_DIR / "creative-not-ready.json", not_ready_payload(not_ready, commit=commit)
    )
    write_json_stable(
        OUT_DIR / "manifest.json",
        {
            "metadata": {
                "generated_at": now_iso(),
                "source_commit": commit,
                "source_candidate_count": len(all_items),
                "ready_count": len(ready_items),
                "not_ready_count": len(not_ready),
                "datasets": {
                    queue: filename for queue, (filename, _items) in review_datasets.items()
                },
                "additional_datasets": {
                    "2025_plus": "creative-2025-plus.json",
                },
                "not_ready": "creative-not-ready.json",
                "default_queue": "high_confidence",
                "review_actions": ["yes", "no", "superlike", "back"],
            }
        },
    )
    LOG.info(
        "creative site data built",
        high_confidence=len(high_confidence),
        candidates=len(candidates),
        historical=len(historical),
        not_ready=len(not_ready),
        output=str(OUT_DIR),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
