"""Build public list-page data from exported creative review decisions.

Run with:

    uv run python -m scripts.build_review_list_site_data --decisions DECISIONS_JSON
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from scripts.build_creative_site_data import (
    ACCOUNT_CATEGORIES_PATH,
    AUDIO_COMPLETE_STATUSES,
    CATALOG_PATH,
    DATA_DIR,
    KEYFRAME_COMPLETE_STATUSES,
    OCR_COMPLETE_STATUSES,
    REPO_ROOT,
    TAGS_DIR,
    THUMB_COMPLETE_STATUSES,
    TRANSCRIPT_COMPLETE_STATUSES,
    VIDEO_TYPES,
    era_for,
    first_matching,
    intish,
    joined_text,
    read_json,
    relative_thumb,
    sidecar_rows,
    status_counts,
    tag_names,
)

OUT_DIR = DATA_DIR / "list"
CREATIVE_DIR = DATA_DIR / "creative"
DEFAULT_OUT = OUT_DIR / "review-list.json"
SELECTED_DECISIONS = {"superlike", "yes"}
QUEUE_FILES = [
    CREATIVE_DIR / "creative-high-confidence.json",
    CREATIVE_DIR / "creative-candidates.json",
    CREATIVE_DIR / "creative-2016-2020.json",
    CREATIVE_DIR / "creative-2025-plus.json",
]


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


def write_json_stable(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    prior: dict[str, Any] | None = None
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, dict):
            prior = loaded
    if prior is not None:
        old_meta_raw = prior.get("metadata")
        new_meta_raw = payload.get("metadata")
        old_meta: dict[str, Any] = dict(old_meta_raw) if isinstance(old_meta_raw, dict) else {}
        new_meta: dict[str, Any] = dict(new_meta_raw) if isinstance(new_meta_raw, dict) else {}
        comparable_prior = dict(prior)
        comparable_prior["metadata"] = {**old_meta, "generated_at": None}
        comparable_new = dict(payload)
        comparable_new["metadata"] = {**new_meta, "generated_at": None}
        if comparable_prior == comparable_new:
            new_meta["generated_at"] = old_meta.get("generated_at") or new_meta.get("generated_at")
            payload["metadata"] = new_meta
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_decisions(path: Path) -> list[dict[str, Any]]:
    data = read_json(path)
    raw = data.get("decisions") if isinstance(data, dict) else data
    if isinstance(raw, dict):
        rows = list(raw.values())
    elif isinstance(raw, list):
        rows = raw
    else:
        rows = []

    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        decision = str(row.get("decision") or "").strip().lower()
        if decision not in SELECTED_DECISIONS:
            continue
        normalized = dict(row)
        normalized["decision"] = decision
        normalized["tweet_id"] = str(row.get("tweet_id") or "").strip()
        normalized["media_id"] = str(row.get("media_id") or "").strip()
        normalized["item_key"] = str(row.get("item_key") or row.get("key") or "").strip()
        out.append(normalized)
    return sorted(out, key=lambda row: str(row.get("decided_at") or ""))


def dedupe_key(decision: dict[str, Any]) -> str:
    media_id = str(decision.get("media_id") or "").strip()
    if media_id:
        return f"media:{media_id}"
    tweet_id = str(decision.get("tweet_id") or "").strip()
    if tweet_id:
        return f"tweet:{tweet_id}"
    return f"item:{decision.get('item_key') or decision.get('tweet_url') or id(decision)}"


def dedupe_collections(
    decisions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    highlights: list[dict[str, Any]] = []
    relevant: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    seen_tweets: set[str] = set()
    duplicate_count = 0

    def add_rows(rows: list[dict[str, Any]], destination: list[dict[str, Any]]) -> None:
        nonlocal duplicate_count
        for row in rows:
            key = dedupe_key(row)
            tweet_id = str(row.get("tweet_id") or "").strip()
            if key in seen_keys or (tweet_id and tweet_id in seen_tweets):
                duplicate_count += 1
                continue
            seen_keys.add(key)
            if tweet_id:
                seen_tweets.add(tweet_id)
            destination.append(row)

    add_rows([row for row in decisions if row["decision"] == "superlike"], highlights)
    add_rows([row for row in decisions if row["decision"] == "yes"], relevant)
    return highlights, relevant, duplicate_count


def load_creative_items() -> tuple[
    dict[str, dict[str, Any]], dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, Any]]
]:
    by_id: dict[str, dict[str, Any]] = {}
    by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    by_tweet: dict[str, dict[str, Any]] = {}
    for path in QUEUE_FILES:
        if not path.exists():
            continue
        data = read_json(path)
        for item in data.get("items", []) if isinstance(data, dict) else []:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip()
            tweet_id = str(item.get("tweet_id") or "").strip()
            media_ids = [
                str(media.get("media_id") or "").strip()
                for media in item.get("media") or []
                if isinstance(media, dict)
            ]
            if item_id and item_id not in by_id:
                by_id[item_id] = item
            if tweet_id and tweet_id not in by_tweet:
                by_tweet[tweet_id] = item
            for media_id in media_ids:
                if tweet_id and media_id and (tweet_id, media_id) not in by_pair:
                    by_pair[(tweet_id, media_id)] = item
    return by_id, by_pair, by_tweet


def load_catalog() -> dict[str, dict[str, Any]]:
    if not CATALOG_PATH.exists():
        return {}
    df = pl.read_parquet(CATALOG_PATH)
    return {str(row.get("tweet_id") or ""): row for row in df.to_dicts()}


def load_account_categories() -> dict[str, dict[str, Any]]:
    if not ACCOUNT_CATEGORIES_PATH.exists():
        return {}
    data = read_json(ACCOUNT_CATEGORIES_PATH)
    return data.get("categories", {}) if isinstance(data, dict) else {}


def load_indexes() -> dict[str, dict[tuple[str, str], list[dict[str, Any]]]]:
    return {
        "vision": sidecar_rows(TAGS_DIR / "media_vision.parquet"),
        "ocr": sidecar_rows(TAGS_DIR / "image_ocr.parquet"),
        "keyframes": sidecar_rows(TAGS_DIR / "keyframes.parquet"),
        "audio": sidecar_rows(TAGS_DIR / "audio_music.parquet"),
        "transcripts": sidecar_rows(TAGS_DIR / "transcripts.parquet"),
        "photo_thumbnails": sidecar_rows(TAGS_DIR / "photo_thumbnails.parquet"),
    }


def media_insight_map(tweet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for insight in tweet.get("media_insights") or []:
        if not isinstance(insight, dict):
            continue
        media_id = str(insight.get("media_id") or "").strip()
        if media_id:
            out[media_id] = insight
    return out


def media_description(
    *,
    tweet: dict[str, Any],
    media_id: str,
    vision_rows: list[dict[str, Any]],
) -> tuple[str, str]:
    for row in vision_rows:
        text = str(row.get("summary_text") or row.get("description") or "").strip()
        if text:
            return text, str(row.get("model") or "media_vision")
    insight = media_insight_map(tweet).get(media_id, {})
    text = str(insight.get("summary_text") or insight.get("description") or "").strip()
    if text:
        return text, "catalog.media_insights"
    return "", "missing"


def build_media_entries(
    tweet: dict[str, Any],
    *,
    preferred_media_id: str,
    indexes: dict[str, dict[tuple[str, str], list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for media in tweet.get("media") or []:
        if not isinstance(media, dict):
            continue
        media_id = str(media.get("media_id") or "").strip()
        if preferred_media_id and media_id != preferred_media_id:
            continue
        media_type = str(media.get("media_type") or "").strip()
        if media_type not in {"photo", *VIDEO_TYPES}:
            continue
        key = (str(tweet.get("tweet_id") or ""), media_id)
        archive_url = str(media.get("release_asset_url") or "").strip()
        vision_rows = indexes["vision"].get(key, [])
        ocr_rows = indexes["ocr"].get(key, [])
        keyframe_rows = indexes["keyframes"].get(key, [])
        audio_rows = indexes["audio"].get(key, [])
        transcript_rows = indexes["transcripts"].get(key, [])
        thumb_rows = indexes["photo_thumbnails"].get(key, [])
        desc, desc_source = media_description(
            tweet=tweet, media_id=media_id, vision_rows=vision_rows
        )
        complete_ocr = [
            row for row in ocr_rows if str(row.get("status") or "") in OCR_COMPLETE_STATUSES
        ]
        keyframe_row = first_matching(keyframe_rows, KEYFRAME_COMPLETE_STATUSES)
        audio_row = first_matching(audio_rows, AUDIO_COMPLETE_STATUSES)
        transcript_row = first_matching(transcript_rows, TRANSCRIPT_COMPLETE_STATUSES)
        thumb_row = first_matching(thumb_rows, THUMB_COMPLETE_STATUSES)
        thumbnail_url = relative_thumb(
            str(
                (keyframe_row or {}).get("thumbnail_path")
                or (thumb_row or {}).get("thumbnail_path")
                or ""
            )
        )
        entries.append(
            {
                "media_id": media_id,
                "type": "video" if media_type in VIDEO_TYPES else "photo",
                "archive_url": archive_url or None,
                "original_url": media.get("original_url"),
                "thumbnail_url": thumbnail_url,
                "sha256": media.get("sha256"),
                "duration_sec": media.get("duration_sec"),
                "playable": bool(archive_url),
                "analysis": {
                    "description": {
                        "source": desc_source,
                        "text": desc,
                        "sidecar_statuses": status_counts(vision_rows),
                    },
                    "ocr": {
                        "status_counts": status_counts(ocr_rows),
                        "complete_row_count": len(complete_ocr),
                        "text": joined_text(complete_ocr),
                    },
                    "keyframes": {
                        "status": (keyframe_row or {}).get("status"),
                        "frame_count": intish((keyframe_row or {}).get("frame_count")),
                    },
                    "audio": {
                        "status": (audio_row or {}).get("status"),
                        "audio_stream_count": intish((audio_row or {}).get("audio_stream_count")),
                        "duration_sec": (audio_row or {}).get("audio_duration_sec"),
                        "tags": tag_names((audio_row or {}).get("tags")),
                    },
                    "transcript": {
                        "status": (transcript_row or {}).get("status"),
                        "segment_count": intish((transcript_row or {}).get("segment_count")),
                        "text": str((transcript_row or {}).get("text") or ""),
                    },
                },
            }
        )
    return entries


def normalize_from_creative(decision: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    media_id = str(decision.get("media_id") or "").strip()
    media = [
        entry
        for entry in item.get("media") or []
        if not media_id or str(entry.get("media_id") or "") == media_id
    ]
    if not media:
        media = item.get("media") or []
    return {
        "id": str(item.get("id") or decision.get("item_key") or ""),
        "review_key": str(decision.get("item_key") or ""),
        "decision": decision["decision"],
        "decided_at": str(decision.get("decided_at") or ""),
        "decision_queue": str(decision.get("queue") or ""),
        "tweet_id": str(item.get("tweet_id") or decision.get("tweet_id") or ""),
        "media_id": media_id,
        "posted_at": str(item.get("posted_at") or decision.get("posted_at") or ""),
        "era": item.get("era"),
        "account": item.get("account") or {},
        "tweet_url": str(item.get("tweet_url") or decision.get("tweet_url") or ""),
        "tweet_text": str(item.get("tweet_text") or ""),
        "review_state": item.get("review_state"),
        "inclusion_basis": item.get("inclusion_basis"),
        "confidence": item.get("confidence"),
        "score": item.get("score"),
        "preference_score": item.get("preference_score"),
        "preference_categories": item.get("preference_categories") or [],
        "creative_forms": item.get("creative_forms") or [],
        "subjects": item.get("subjects") or [],
        "tags": item.get("tags") or [],
        "media": media,
        "evidence": item.get("evidence") or {},
        "engagement": item.get("engagement") or {},
        "detail_source": "creative-review-data",
        "detail_ready": True,
    }


def normalize_from_catalog(
    decision: dict[str, Any],
    *,
    tweet: dict[str, Any] | None,
    indexes: dict[str, dict[tuple[str, str], list[dict[str, Any]]]],
    account_categories: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    tweet = tweet or {}
    tweet_id = str(decision.get("tweet_id") or tweet.get("tweet_id") or "").strip()
    media_id = str(decision.get("media_id") or "").strip()
    handle = str(decision.get("account_handle") or tweet.get("account_handle") or "").strip()
    account_meta = account_categories.get(handle, {})
    media = (
        build_media_entries(tweet, preferred_media_id=media_id, indexes=indexes) if tweet else []
    )
    desc = "\n\n".join(
        str((entry.get("analysis") or {}).get("description", {}).get("text") or "")
        for entry in media
        if isinstance(entry, dict)
    ).strip()
    return {
        "id": str(decision.get("item_key") or f"{tweet_id}:{media_id}"),
        "review_key": str(decision.get("item_key") or ""),
        "decision": decision["decision"],
        "decided_at": str(decision.get("decided_at") or ""),
        "decision_queue": str(decision.get("queue") or ""),
        "tweet_id": tweet_id,
        "media_id": media_id,
        "posted_at": str(tweet.get("posted_at") or decision.get("posted_at") or ""),
        "era": era_for(str(tweet.get("posted_at") or decision.get("posted_at") or "")),
        "account": {
            "handle": handle,
            "category": account_meta.get("category"),
            "label": account_meta.get("label") or handle,
        },
        "tweet_url": str(tweet.get("tweet_url") or decision.get("tweet_url") or ""),
        "tweet_text": str(tweet.get("text_resolved") or tweet.get("text") or ""),
        "review_state": "selected-export-fallback",
        "inclusion_basis": "selected_by_review_export",
        "confidence": "selected",
        "score": None,
        "preference_score": None,
        "preference_categories": [],
        "creative_forms": [],
        "subjects": [],
        "tags": tag_names(tweet.get("tags")) if tweet else [],
        "media": media,
        "evidence": {
            "summary": desc,
            "notable_text": "",
            "reasons": ["selected in review export; reconstructed from catalog/sidecars"],
            "source_sidecars": ["catalog.parquet", "media sidecars"],
        },
        "engagement": {
            "likes": tweet.get("like_count") or 0,
            "retweets": tweet.get("retweet_count") or 0,
            "quotes": tweet.get("quote_count") or 0,
            "views": tweet.get("view_count") or 0,
        },
        "detail_source": "catalog-sidecar-fallback" if tweet else "decision-export-only",
        "detail_ready": bool(tweet and media),
    }


def build_item(
    decision: dict[str, Any],
    *,
    creative_by_id: dict[str, dict[str, Any]],
    creative_by_pair: dict[tuple[str, str], dict[str, Any]],
    creative_by_tweet: dict[str, dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
    indexes: dict[str, dict[tuple[str, str], list[dict[str, Any]]]],
    account_categories: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    item_key = str(decision.get("item_key") or "")
    tweet_id = str(decision.get("tweet_id") or "")
    media_id = str(decision.get("media_id") or "")
    creative = (
        creative_by_id.get(item_key)
        or creative_by_pair.get((tweet_id, media_id))
        or creative_by_tweet.get(tweet_id)
    )
    if creative:
        return normalize_from_creative(decision, creative)
    return normalize_from_catalog(
        decision,
        tweet=catalog.get(tweet_id),
        indexes=indexes,
        account_categories=account_categories,
    )


def build_payload(decisions_path: Path) -> dict[str, Any]:
    decisions = load_decisions(decisions_path)
    highlights_raw, relevant_raw, duplicate_count = dedupe_collections(decisions)
    creative_by_id, creative_by_pair, creative_by_tweet = load_creative_items()
    catalog = load_catalog()
    indexes = load_indexes()
    account_categories = load_account_categories()

    def build_many(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            build_item(
                row,
                creative_by_id=creative_by_id,
                creative_by_pair=creative_by_pair,
                creative_by_tweet=creative_by_tweet,
                catalog=catalog,
                indexes=indexes,
                account_categories=account_categories,
            )
            for row in rows
        ]

    highlights = build_many(highlights_raw)
    relevant = build_many(relevant_raw)
    decision_counts = Counter(row["decision"] for row in decisions)
    detail_sources = Counter(item.get("detail_source") for item in [*highlights, *relevant])
    account_counts = Counter(
        str((item.get("account") or {}).get("handle") or "unknown")
        for item in [*highlights, *relevant]
    )
    return {
        "metadata": {
            "generated_at": now_iso(),
            "source_commit": source_commit(),
            "source_decisions_file": str(decisions_path.name),
            "selected_decision_counts": dict(decision_counts),
            "duplicate_selected_items_removed": duplicate_count,
            "highlights_count": len(highlights),
            "relevant_items_count": len(relevant),
            "detail_source_counts": dict(detail_sources),
            "top_accounts": dict(account_counts.most_common(16)),
            "dedupe_rule": "Superlikes become Highlights first; duplicate media or duplicate tweets are removed from Relevant Items.",
            "collections": {
                "highlights": "Superlikes, displayed as Highlights",
                "relevant_items": "Likes, displayed as Relevant Items",
            },
        },
        "collections": {
            "highlights": highlights,
            "relevant_items": relevant,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--decisions",
        required=True,
        type=Path,
        help="Creative review decision export JSON.",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_payload(args.decisions)
    write_json_stable(args.out, payload)
    print(
        "review list data built",
        json.dumps(
            {
                "out": str(args.out),
                "highlights": payload["metadata"]["highlights_count"],
                "relevant_items": payload["metadata"]["relevant_items_count"],
                "duplicates_removed": payload["metadata"]["duplicate_selected_items_removed"],
            },
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
