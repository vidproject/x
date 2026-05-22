"""Build a review queue for produced-video and genre work on core accounts.

The archive already has several media layers: keyframes, audio/music,
OCR, cheap metadata descriptions, and manual review notes. This script
joins those layers into one lightweight audit
artifact focused on core-account videos, so genre work can proceed without
more broad scraping.

Run with: ``uv run python -m scripts.build_core_video_audit``
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from scripts._logging import configure

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"
ACCOUNT_CATEGORIES_PATH = DATA_DIR / "account_categories.json"
JSON_OUT = TAGS_DIR / "core_video_audit.json"
CSV_OUT = TAGS_DIR / "core_video_audit.csv"
MISSING_TWEET_IDS_OUT = TAGS_DIR / "core_produced_missing_tweet_ids.txt"
MISSING_MEDIA_IDS_OUT = TAGS_DIR / "core_produced_missing_media_ids.txt"
VIDEO_TYPES = {"video", "animated_gif"}
PRODUCED_TAGS = {
    "video:produced",
    "media:music-video",
    "media:montage",
    "media:text-overlay",
    "media:voiceover",
}
GENRE_TAGS = {
    "genre:music-video",
    "genre:psa",
    "genre:advertisement",
    "genre:recruitment",
    "genre:war-movie",
    "genre:utopian",
    "genre:dystopian",
}
GENRE_EXPERIMENT_TAGS = {
    "genre:music-video",
    "genre:war-movie",
    "genre:utopian",
    "genre:dystopian",
}
TAG_ALIASES = {
    "media:produced-video": "video:produced",
}


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def core_handles() -> set[str]:
    data = read_json(ACCOUNT_CATEGORIES_PATH)
    categories = data.get("categories") if isinstance(data, dict) else {}
    return {
        str(handle)
        for handle, meta in categories.items()
        if isinstance(meta, dict) and meta.get("category") == "core"
    }


def tag_values(values: Any) -> list[str]:
    out: list[str] = []
    for entry in values or []:
        tag = entry.get("tag") if isinstance(entry, dict) else str(entry or "")
        tag = str(tag or "").strip()
        tag = TAG_ALIASES.get(tag, tag)
        if tag and tag not in out:
            out.append(tag)
    return out


def load_parquet(path: Path) -> pl.DataFrame:
    if not path.exists():
        return pl.DataFrame()
    try:
        return pl.read_parquet(path)
    except Exception:
        LOG.exception("could not read sidecar", path=str(path))
        return pl.DataFrame()


def load_lexical_tags(handles: set[str]) -> dict[str, set[str]]:
    df = load_parquet(TAGS_DIR / "lexical.parquet")
    if df.is_empty():
        return {}
    if "account_handle" in df.columns:
        df = df.filter(pl.col("account_handle").is_in(sorted(handles)))
    out: dict[str, set[str]] = {}
    for row in df.select(["tweet_id", "tags"]).iter_rows(named=True):
        out.setdefault(str(row["tweet_id"]), set()).update(tag_values(row.get("tags")))
    return out


def load_media_sidecar(name: str, handles: set[str]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    df = load_parquet(TAGS_DIR / f"{name}.parquet")
    if df.is_empty():
        return {}
    if "account_handle" in df.columns:
        df = df.filter(pl.col("account_handle").is_in(sorted(handles)))
    out: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in df.to_dicts():
        key = (str(row.get("tweet_id") or ""), str(row.get("media_id") or ""))
        if key[0] and key[1]:
            out[key].append(row)
    return out


def load_manual_review() -> dict[tuple[str, str], dict[str, Any]]:
    path = TAGS_DIR / "manual_media_review_queue.json"
    if not path.exists():
        return {}
    data = read_json(path)
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for item in data.get("items", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("tweet_id") or ""), str(item.get("media_id") or ""))
        if key[0] and key[1]:
            out[key] = item
    return out


def classify_from_text(text: str) -> set[str]:
    haystack = text.lower()
    tags: set[str] = set()
    produced_words = (
        "polished",
        "produced",
        "edited",
        "cinematic",
        "trailer-style",
        "multi-shot",
        "rapid-cut",
        "b-roll",
        "title-card",
        "end-card",
        "montage",
        "voiceover",
        "voice-over",
        "narration",
        "soundtrack",
        "music bed",
        "background music",
    )
    if any(word in haystack for word in produced_words):
        tags.add("video:produced")
    if any(word in haystack for word in ("montage", "multiple shot", "sequence of clips", "series of clips", "b-roll")):
        tags.add("media:montage")
    if any(word in haystack for word in ("text overlay", "title-card", "end-card", "chyron", "lower-third", "caption")):
        tags.add("media:text-overlay")
    if any(word in haystack for word in ("voiceover", "voice-over", "narration", "narrator", "narrated")):
        tags.add("media:voiceover")
    if any(word in haystack for word in ("music video", "set to music", "music track", "soundtrack", "music bed", "background music", "anthem")):
        tags.update({"media:music-video", "genre:music-video", "audio:music-likely"})
    if any(word in haystack for word in ("psa", "public service announcement", "did you know", "learn more", "hotline")):
        tags.update({"video:produced", "genre:psa"})
    if any(word in haystack for word in ("join.ice.gov", "recruitment", "apply now", "apply today", "hiring", "career")):
        tags.update({"video:produced", "genre:recruitment", "genre:advertisement"})
    if any(word in haystack for word in ("campaign ad", "ad spot", "commercial", "promotional video")):
        tags.update({"video:produced", "genre:advertisement"})
    if any(word in haystack for word in ("war movie", "war film", "action movie", "trailer-style", "combat", "battle")) and "cinematic" in haystack:
        tags.update({"video:produced", "genre:war-movie"})
    if any(word in haystack for word in ("dystopian", "sci-fi", "science fiction", "cyberpunk", "apocalyptic", "hellscape", "surveillance-state")):
        tags.update({"video:produced", "genre:dystopian"})
    if any(word in haystack for word in ("utopian", "aspirational", "bright future", "golden age", "sunlit", "triumphal")):
        tags.update({"video:produced", "genre:utopian"})
    return tags


def sidecar_tags(rows: list[dict[str, Any]]) -> set[str]:
    tags: set[str] = set()
    for row in rows:
        tags.update(tag_values(row.get("tags")))
    return tags


def sidecar_descriptions(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for row in rows:
        for field in ("description", "summary_text", "text"):
            value = str(row.get(field) or "").strip()
            if value and value not in out:
                out.append(value)
    return out


def media_video_items(row: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for media in row.get("media") or []:
        if not isinstance(media, dict):
            continue
        if media.get("media_type") in VIDEO_TYPES:
            out.append(media)
    return out


def missing_steps(
    media: dict[str, Any],
    *,
    keyframe_rows: list[dict[str, Any]],
    audio_rows: list[dict[str, Any]],
    vision_rows: list[dict[str, Any]],
    tags: set[str],
) -> list[str]:
    steps: list[str] = []
    archived = bool(media.get("release_asset_url"))
    if not archived:
        steps.append("archive-media")
    if archived and not any(r.get("status") == "ok" for r in keyframe_rows):
        steps.append("extract-keyframes")
    if archived and not audio_rows:
        steps.append("detect-audio")
    if not vision_rows:
        steps.append("describe-with-vision")
    if (tags & PRODUCED_TAGS) and not (tags & GENRE_TAGS):
        steps.append("assign-produced-video-genre")
    return steps


def bucket_for(tags: set[str], missing: list[str]) -> str:
    if "archive-media" in missing:
        return "missing-media"
    if tags & GENRE_EXPERIMENT_TAGS:
        return "genre-experiment"
    if tags & PRODUCED_TAGS or tags & GENRE_TAGS:
        return "produced-video"
    if "describe-with-vision" in missing or "detect-audio" in missing:
        return "needs-recognition"
    return "ordinary-video"


def priority_for(tags: set[str], missing: list[str], bucket: str, row: dict[str, Any]) -> int:
    score = {
        "genre-experiment": 100,
        "produced-video": 85,
        "needs-recognition": 70,
        "missing-media": 55,
        "ordinary-video": 30,
    }.get(bucket, 30)
    if "assign-produced-video-genre" in missing:
        score += 8
    if "audio:music-likely" in tags and "genre:music-video" not in tags:
        score += 8
    if (row.get("like_count") or 0) > 1000 or (row.get("retweet_count") or 0) > 250:
        score += 5
    return min(score, 100)


def build_item(
    row: dict[str, Any],
    media: dict[str, Any],
    *,
    lexical: dict[str, set[str]],
    vision: dict[tuple[str, str], list[dict[str, Any]]],
    audio: dict[tuple[str, str], list[dict[str, Any]]],
    keyframes: dict[tuple[str, str], list[dict[str, Any]]],
    ocr: dict[tuple[str, str], list[dict[str, Any]]],
    manual: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    tweet_id = str(row.get("tweet_id") or "")
    media_id = str(media.get("media_id") or "")
    key = (tweet_id, media_id)
    vision_rows = vision.get(key, [])
    audio_rows = audio.get(key, [])
    keyframe_rows = keyframes.get(key, [])
    ocr_rows = ocr.get(key, [])
    manual_row = manual.get(key, {})
    descriptions = sidecar_descriptions(vision_rows + ocr_rows)
    if manual_row.get("visual_observation"):
        descriptions.append(str(manual_row["visual_observation"]))
    context = "\n".join(
        [
            str(row.get("text_resolved") or row.get("text") or ""),
            str(media.get("alt_text") or ""),
            "\n".join(descriptions),
        ]
    )
    tags = set(lexical.get(tweet_id, set()))
    tags.update(sidecar_tags(vision_rows + audio_rows))
    tags.update(tag_values(manual_row.get("candidate_visual_tags")))
    tags.update(classify_from_text(context))
    missing = missing_steps(
        media,
        keyframe_rows=keyframe_rows,
        audio_rows=audio_rows,
        vision_rows=vision_rows,
        tags=tags,
    )
    bucket = bucket_for(tags, missing)
    item = {
        "tweet_id": tweet_id,
        "account_handle": row.get("account_handle"),
        "posted_at": row.get("posted_at"),
        "tweet_url": row.get("tweet_url"),
        "tweet_text": row.get("text_resolved") or row.get("text") or "",
        "media_id": media_id,
        "media_type": media.get("media_type"),
        "archive_status": media.get("archive_status"),
        "release_asset_url": media.get("release_asset_url"),
        "duration_sec": media.get("duration_sec"),
        "width": media.get("width"),
        "height": media.get("height"),
        "like_count": row.get("like_count") or 0,
        "retweet_count": row.get("retweet_count") or 0,
        "bucket": bucket,
        "priority": priority_for(tags, missing, bucket, row),
        "tags": sorted(tags),
        "genre_tags": sorted(tag for tag in tags if tag.startswith("genre:")),
        "produced_video_tags": sorted(tags & PRODUCED_TAGS),
        "missing_steps": missing,
        "has_keyframes": any(r.get("status") == "ok" for r in keyframe_rows),
        "has_audio_analysis": bool(audio_rows),
        "has_vision_description": bool(vision_rows),
        "has_ocr": bool(ocr_rows),
        "description": " | ".join(descriptions)[:1200],
    }
    return item


def parquet_paths_for(handles: set[str]) -> list[Path]:
    paths = []
    for handle in sorted(handles):
        path = DATA_DIR / f"{handle}.parquet"
        if path.exists():
            paths.append(path)
    return paths


def build() -> dict[str, Any]:
    handles = core_handles()
    lexical = load_lexical_tags(handles)
    vision = load_media_sidecar("media_vision", handles)
    audio = load_media_sidecar("audio_music", handles)
    keyframes = load_media_sidecar("keyframes", handles)
    ocr = load_media_sidecar("image_ocr", handles)
    manual = load_manual_review()
    items: list[dict[str, Any]] = []
    for path in parquet_paths_for(handles):
        df = pl.read_parquet(path)
        for row in df.to_dicts():
            for media in media_video_items(row):
                items.append(
                    build_item(
                        row,
                        media,
                        lexical=lexical,
                        vision=vision,
                        audio=audio,
                        keyframes=keyframes,
                        ocr=ocr,
                        manual=manual,
                    )
                )
    items.sort(key=lambda i: (-int(i["priority"]), str(i.get("posted_at") or ""), str(i["tweet_id"])))
    bucket_counts = Counter(str(item["bucket"]) for item in items)
    tag_counts = Counter(tag for item in items for tag in item["tags"])
    missing_counts = Counter(step for item in items for step in item["missing_steps"])
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "scope": "core-account videos",
        "core_handles": sorted(handles),
        "summary": {
            "videos": len(items),
            "bucket_counts": dict(sorted(bucket_counts.items())),
            "top_tags": dict(tag_counts.most_common(50)),
            "missing_step_counts": dict(sorted(missing_counts.items())),
        },
        "items": items,
    }


def write_csv(items: list[dict[str, Any]], path: Path) -> None:
    fields = [
        "priority",
        "bucket",
        "account_handle",
        "posted_at",
        "tweet_id",
        "media_id",
        "duration_sec",
        "archive_status",
        "genre_tags",
        "produced_video_tags",
        "missing_steps",
        "tweet_url",
        "release_asset_url",
        "tweet_text",
        "description",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for item in items:
            row = {field: csv_cell(item.get(field)) for field in fields}
            for field in ("genre_tags", "produced_video_tags", "missing_steps"):
                row[field] = ";".join(item.get(field) or [])
            writer.writerow(row)


def csv_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return re.sub(r"\s+", " ", value).strip()


def archive_recovery_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if "archive-media" in (item.get("missing_steps") or [])
        and (set(item.get("produced_video_tags") or []) or set(item.get("genre_tags") or []))
    ]


def write_id_file(path: Path, ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for value in ids:
            f.write(f"{value}\n")


def write_archive_recovery_queues(items: list[dict[str, Any]]) -> None:
    recovery = archive_recovery_items(items)
    tweet_ids = sorted({str(item.get("tweet_id") or "") for item in recovery if item.get("tweet_id")})
    media_ids = sorted({str(item.get("media_id") or "") for item in recovery if item.get("media_id")})
    write_id_file(MISSING_TWEET_IDS_OUT, tweet_ids)
    write_id_file(MISSING_MEDIA_IDS_OUT, media_ids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, default=JSON_OUT)
    parser.add_argument("--csv-out", type=Path, default=CSV_OUT)
    args = parser.parse_args(argv)
    result = build()
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    write_csv(result["items"], args.csv_out)
    write_archive_recovery_queues(result["items"])
    LOG.info(
        "core video audit built",
        videos=result["summary"]["videos"],
        archive_recovery=len(archive_recovery_items(result["items"])),
        json_out=str(args.json_out),
        csv_out=str(args.csv_out),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
