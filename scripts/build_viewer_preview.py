"""Build lightweight JSON boot data for the static viewer.

The full archive lives in per-account Parquet files. Those are efficient for
storage, but expensive for a browser that only needs a current page plus global
facets. This script writes:

* data/catalog.parquet: compact metadata for the whole archive, with Parquet
  row locators so the browser can lazily hydrate full rows.
* data/catalog.json: tiny summary and poster map for the catalog.
* data/preview-*.json: legacy newest-row slices kept for compatibility.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from scripts._logging import configure

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIRNAME = "tags"
PREVIEW_LIMITS = (20, 50, 100, 200)
SCHEMA_VERSION = 1
CATALOG_FILENAME = "catalog.json"
CATALOG_PARQUET_FILENAME = "catalog.parquet"
CATALOG_SCALAR_COLUMNS = (
    "tweet_id",
    "account_handle",
    "account_id",
    "posted_at",
    "first_captured_at",
    "last_seen_at",
    "deletion_detected_at",
    "unavailable_detected_at",
    "unavailable_reason",
    "unavailable_text",
    "unavailable_source_url",
    "tweet_url",
    "tweet_type",
    "conversation_id",
    "reply_to_tweet_id",
    "reply_to_account",
    "reply_to_account_id",
    "quoted_tweet_id",
    "retweeted_tweet_id",
    "text",
    "text_resolved",
    "lang",
    "possibly_sensitive",
    "source",
    "place_full_name",
    "hashtags",
    "mentions",
    "like_count",
    "retweet_count",
    "reply_count",
    "quote_count",
    "view_count",
    "bookmark_count",
    "is_truncated",
    "wayback_url",
    "wayback_submitted_at",
    "capture_source",
)
MEDIA_CATALOG_KEYS = (
    "media_id",
    "media_type",
    "release_asset_url",
    "sha256",
    "duration_sec",
    "alt_text",
)
URL_CATALOG_KEYS = ("short", "expanded", "display")
CARD_CATALOG_KEYS = ("name", "card_url", "vendor_url", "title", "description", "image_url")
MEDIA_INSIGHT_CATALOG_KEYS = (
    "media_id",
    "media_type",
    "description",
    "summary_text",
    "tags",
)
COMMUNITY_NOTE_CATALOG_KEYS = (
    "note_id",
    "title",
    "short_title",
    "summary",
    "destination_url",
    "observed_at",
)


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def preview_path(data_dir: Path, limit: int) -> Path:
    return data_dir / f"preview-{limit}.json"


def catalog_path(data_dir: Path) -> Path:
    return data_dir / CATALOG_FILENAME


def catalog_parquet_path(data_dir: Path) -> Path:
    return data_dir / CATALOG_PARQUET_FILENAME


def canonical_parquet_paths(data_dir: Path) -> list[Path]:
    return sorted(
        p for p in data_dir.glob("*.parquet") if p.is_file() and p.name != CATALOG_PARQUET_FILENAME
    )


def load_preview_rows(data_dir: Path, limit: int) -> list[dict[str, Any]]:
    frames: list[pl.DataFrame] = []
    for path in canonical_parquet_paths(data_dir):
        try:
            df = pl.read_parquet(path)
        except Exception:
            LOG.exception("preview: could not read parquet", path=str(path))
            continue
        if df.height > 0:
            frames.append(df)
    if not frames:
        return []

    combined = pl.concat(frames, how="diagonal_relaxed")
    sort_cols = [c for c in ("posted_at", "last_seen_at", "tweet_id") if c in combined.columns]
    if sort_cols:
        combined = combined.sort(sort_cols, descending=[True] * len(sort_cols), nulls_last=True)
    return [_json_safe(row) for row in combined.head(limit).to_dicts()]


def build_preview_payload(
    data_dir: Path = DATA_DIR,
    *,
    limit: int = max(PREVIEW_LIMITS),
    generated_at: str | None = None,
) -> dict[str, Any]:
    rows = load_preview_rows(data_dir, limit)
    row_ids = _tweet_ids(rows)
    tags_dir = data_dir / TAGS_DIRNAME
    return {
        "generated_at": generated_at or now_iso(),
        "schema_version": SCHEMA_VERSION,
        "limit": limit,
        "row_count": len(rows),
        "rows": rows,
        "tags": load_tag_slices(tags_dir, row_ids),
        "media_insights": load_media_insight_slices(tags_dir, row_ids),
        "news_mentions": load_news_mention_slices(tags_dir, row_ids),
        "poster_by_sha": load_keyframe_posters(tags_dir, row_ids),
    }


def write_previews(
    data_dir: Path = DATA_DIR,
    *,
    limits: Iterable[int] = PREVIEW_LIMITS,
    generated_at: str | None = None,
) -> list[Path]:
    normalized_limits = tuple(sorted({int(limit) for limit in limits if int(limit) > 0}))
    if not normalized_limits:
        return []
    max_limit = max(normalized_limits)
    payload = build_preview_payload(data_dir, limit=max_limit, generated_at=generated_at)
    written: list[Path] = []
    for limit in normalized_limits:
        sliced = slice_preview_payload(payload, limit)
        path = preview_path(data_dir, limit)
        atomic_write_json(path, sliced)
        written.append(path)
        LOG.info("preview: wrote viewer preview", path=str(path), rows=sliced["row_count"])
    return written


def build_catalog_payload(
    data_dir: Path = DATA_DIR,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    rows = load_catalog_rows(data_dir)
    row_ids = _tweet_ids(rows)
    tags_dir = data_dir / TAGS_DIRNAME
    apply_catalog_overlays(
        rows,
        tag_map=load_tag_slices(tags_dir, row_ids),
        media_insight_map=load_media_insight_slices(tags_dir, row_ids),
        news_mention_map=load_news_mention_slices(tags_dir, row_ids),
    )
    return {
        "generated_at": generated_at or now_iso(),
        "schema_version": SCHEMA_VERSION,
        "row_count": len(rows),
        "date_range": catalog_date_range(rows),
        "rows": rows,
        "poster_by_sha": load_keyframe_posters(tags_dir, row_ids),
    }


def write_catalog(
    data_dir: Path = DATA_DIR,
    *,
    generated_at: str | None = None,
) -> Path:
    payload = build_catalog_payload(data_dir, generated_at=generated_at)
    parquet = catalog_parquet_path(data_dir)
    write_catalog_parquet(parquet, payload["rows"])
    summary = {key: value for key, value in payload.items() if key not in {"rows"}}
    summary["parquet"] = f"data/{parquet.name}"
    path = catalog_path(data_dir)
    atomic_write_json(path, summary)
    LOG.info(
        "catalog: wrote viewer catalog",
        path=str(path),
        parquet=str(parquet),
        rows=payload["row_count"],
    )
    return path


def write_catalog_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.parquet")
    df = pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()
    df.write_parquet(tmp, compression="zstd")
    os.replace(tmp, path)


def load_catalog_rows(data_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in canonical_parquet_paths(data_dir):
        try:
            df = pl.read_parquet(path)
        except Exception:
            LOG.exception("catalog: could not read parquet", path=str(path))
            continue
        if df.height == 0:
            continue
        if hasattr(df, "with_row_index"):
            df = df.with_row_index("__row_index")
        else:
            df = df.with_row_count("__row_index")
        source_handle = path.stem
        parquet_url = f"data/{path.name}"
        parquet_byte_length = path.stat().st_size
        for row in df.iter_rows(named=True):
            rows.append(
                compact_catalog_row(
                    row,
                    source_handle=source_handle,
                    parquet_url=parquet_url,
                    parquet_byte_length=parquet_byte_length,
                )
            )
    rows.sort(key=catalog_sort_key, reverse=True)
    return rows


def compact_catalog_row(
    row: dict[str, Any],
    *,
    source_handle: str,
    parquet_url: str,
    parquet_byte_length: int | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in CATALOG_SCALAR_COLUMNS:
        if key not in row:
            continue
        value = compact_scalar(key, row.get(key), out.get("text"))
        if value is not None:
            out[key] = _json_safe(value)
    out["urls"] = compact_dict_list(row.get("urls"), URL_CATALOG_KEYS)
    out["media"] = compact_dict_list(row.get("media"), MEDIA_CATALOG_KEYS)
    card = compact_dict(row.get("card"), CARD_CATALOG_KEYS)
    if card:
        out["card"] = card
    note = compact_dict(row.get("community_note"), COMMUNITY_NOTE_CATALOG_KEYS)
    if note:
        out["community_note"] = note
    out["__catalog"] = {
        "handle": source_handle,
        "parquet": parquet_url,
        "row_index": int(row.get("__row_index") or 0),
    }
    if parquet_byte_length is not None:
        out["__catalog"]["byte_length"] = int(parquet_byte_length)
    out["__hydrated"] = False
    return _json_safe(out)


def apply_catalog_overlays(
    rows: list[dict[str, Any]],
    *,
    tag_map: dict[str, list[dict[str, Any]]],
    media_insight_map: dict[str, list[dict[str, Any]]],
    news_mention_map: dict[str, dict[str, Any]],
) -> None:
    for row in rows:
        tweet_id = str(row.get("tweet_id") or "")
        tags = list(tag_map.get(tweet_id) or [])
        news = news_mention_map.get(tweet_id)
        if news:
            tags.extend(news.get("tags") or [])
            row["news_mentions"] = news.get("articles") or []
            row["news_mention_count"] = int(news.get("mention_count") or 0)
            row["news_mention_status"] = news.get("status") or ""
            row["news_mention_detector"] = news.get("detector") or ""
        insights = media_insight_map.get(tweet_id) or []
        if insights:
            row["media_insights"] = compact_media_insights(insights)
            for insight in insights:
                tags.extend(insight.get("tags") or [])
        if tags:
            row["tags"] = dedupe_tag_entries(tags)


def dedupe_tag_entries(entries: Iterable[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, bool, str]] = set()
    for entry in entries:
        if isinstance(entry, str):
            normalized: dict[str, Any] = {"tag": entry}
        elif isinstance(entry, dict):
            normalized = dict(entry)
        else:
            continue
        tag = str(normalized.get("tag") or "")
        if not tag:
            continue
        normalized = compact_tag_entry({**normalized, "tag": tag})
        key = (
            normalized["tag"],
            bool(normalized.get("tentative")),
            str(normalized.get("source") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(_json_safe(normalized))
    return out


def compact_tag_entry(entry: dict[str, Any]) -> dict[str, Any]:
    out = {"tag": str(entry.get("tag") or "")}
    if entry.get("tentative"):
        out["tentative"] = True
    source = str(entry.get("source") or "")
    if source:
        out["source"] = source
    return out


def compact_media_insights(insights: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for insight in insights:
        compacted = compact_dict(insight, MEDIA_INSIGHT_CATALOG_KEYS)
        if isinstance(compacted.get("tags"), list):
            compacted["tags"] = dedupe_tag_entries(compacted["tags"])
        if compacted:
            out.append(compacted)
    return out


def compact_scalar(key: str, value: Any, text: Any = None) -> Any:
    if value in (None, "", [], {}):
        return None
    if key == "text_resolved" and value == text:
        return None
    return value


def compact_dict_list(value: Any, keys: Iterable[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        compacted = compact_dict(item, keys)
        if compacted:
            out.append(compacted)
    return out


def compact_dict(value: Any, keys: Iterable[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out = {
        key: _json_safe(value.get(key)) for key in keys if value.get(key) not in (None, "", [], {})
    }
    return out


def catalog_sort_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("posted_at") or ""),
        str(row.get("last_seen_at") or ""),
        str(row.get("tweet_id") or ""),
    )


def catalog_date_range(rows: list[dict[str, Any]]) -> dict[str, str]:
    days = sorted({str(row.get("posted_at") or "")[:10] for row in rows if row.get("posted_at")})
    return {
        "start": days[0] if days else "",
        "end": days[-1] if days else "",
    }


def slice_preview_payload(payload: dict[str, Any], limit: int) -> dict[str, Any]:
    rows = list(payload.get("rows") or [])[:limit]
    row_ids = _tweet_ids(rows)
    return {
        **payload,
        "limit": limit,
        "row_count": len(rows),
        "rows": rows,
        "tags": _filter_map(payload.get("tags"), row_ids),
        "media_insights": _filter_map(payload.get("media_insights"), row_ids),
        "news_mentions": _filter_map(payload.get("news_mentions"), row_ids),
        "poster_by_sha": filter_posters(payload.get("poster_by_sha"), rows),
    }


def load_tag_slices(tags_dir: Path, row_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for layer in ("lexical", "audio_music"):
        path = tags_dir / f"{layer}.parquet"
        if not path.exists():
            continue
        try:
            df = pl.read_parquet(path, columns=["tweet_id", "tags"])
        except Exception:
            LOG.exception("preview: could not read tag sidecar", path=str(path))
            continue
        for row in df.iter_rows(named=True):
            tweet_id = str(row.get("tweet_id") or "")
            if tweet_id not in row_ids:
                continue
            entries = row.get("tags") or []
            if isinstance(entries, list) and entries:
                out.setdefault(tweet_id, []).extend(_json_safe(entries))
    return out


def load_media_insight_slices(tags_dir: Path, row_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for layer in ("media_vision", "media_llm"):
        path = tags_dir / f"{layer}.parquet"
        if not path.exists():
            continue
        try:
            df = pl.read_parquet(path)
        except Exception:
            LOG.exception("preview: could not read media sidecar", path=str(path))
            continue
        for row in df.iter_rows(named=True):
            tweet_id = str(row.get("tweet_id") or "")
            if tweet_id in row_ids:
                out.setdefault(tweet_id, []).append(_json_safe(row))
    return out


def load_news_mention_slices(tags_dir: Path, row_ids: set[str]) -> dict[str, dict[str, Any]]:
    path = tags_dir / "news_mentions.parquet"
    if not path.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    try:
        df = pl.read_parquet(path)
    except Exception:
        LOG.exception("preview: could not read news sidecar", path=str(path))
        return out
    for row in df.iter_rows(named=True):
        tweet_id = str(row.get("tweet_id") or "")
        if tweet_id not in row_ids or int(row.get("mention_count") or 0) <= 0:
            continue
        out[tweet_id] = _json_safe(
            {
                "mention_count": int(row.get("mention_count") or 0),
                "status": row.get("status") or "mentioned",
                "detector": row.get("detector") or "",
                "detector_version": row.get("detector_version") or "",
                "generated_at": row.get("generated_at") or "",
                "articles": row.get("articles") or [],
                "tags": row.get("tags") or [],
            }
        )
    return out


def load_keyframe_posters(tags_dir: Path, row_ids: set[str]) -> dict[str, str]:
    path = tags_dir / "keyframes.parquet"
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    try:
        df = pl.read_parquet(path)
    except Exception:
        LOG.exception("preview: could not read keyframe sidecar", path=str(path))
        return out
    for row in df.iter_rows(named=True):
        if str(row.get("tweet_id") or "") not in row_ids:
            continue
        if str(row.get("status") or "") != "ok":
            continue
        sha = str(row.get("media_sha256") or "")
        if not sha or sha in out:
            continue
        poster = _string_or_none(row.get("thumbnail_path")) or poster_path_from_frames(
            row.get("frames")
        )
        if poster:
            out[sha] = poster
    return out


def poster_path_from_frames(frames: Any) -> str | None:
    if not isinstance(frames, list) or not frames:
        return None
    usable = [
        frame
        for frame in frames
        if isinstance(frame, dict) and isinstance(frame.get("path"), str) and frame.get("path")
    ]
    if not usable:
        return None
    frame = usable[len(usable) // 2]
    return str(frame["path"]).replace("\\", "/").removeprefix("./")


def filter_posters(value: Any, rows: list[dict[str, Any]]) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        return {}
    shas: set[str] = set()
    for row in rows:
        media = row.get("media")
        if not isinstance(media, list):
            continue
        for item in media:
            if not isinstance(item, dict):
                continue
            sha = str(item.get("sha256") or "")
            if sha:
                shas.add(sha)
    return {sha: str(path) for sha, path in value.items() if sha in shas}


DEFAULT_VOLATILE_KEYS = ("generated_at",)


def strip_volatile_keys(value: Any, keys: frozenset[str]) -> Any:
    """Recursively drop ``keys`` so two payloads can be compared for meaningful
    equality while ignoring run-stamped fields like ``generated_at``."""
    if isinstance(value, dict):
        return {k: strip_volatile_keys(v, keys) for k, v in value.items() if k not in keys}
    if isinstance(value, list):
        return [strip_volatile_keys(v, keys) for v in value]
    return value


def stabilize_volatile(
    path: Path,
    payload: dict[str, Any],
    *,
    volatile_keys: Iterable[str] = DEFAULT_VOLATILE_KEYS,
) -> dict[str, Any]:
    """Return ``payload`` unless the committed file at ``path`` already holds the
    same content (ignoring ``volatile_keys``), in which case return the existing
    parsed payload so the file is rewritten byte-for-byte and git sees no diff.

    This keeps idempotent re-runs (the common case in CI) from churning a
    committed artifact — and thus from triggering a redundant Pages deploy —
    purely because a timestamp field advanced.
    """
    keys = frozenset(volatile_keys)
    if not path.exists():
        return payload
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return payload
    if not isinstance(existing, dict):
        return payload
    if strip_volatile_keys(existing, keys) == strip_volatile_keys(_json_safe(payload), keys):
        return existing
    return payload


def atomic_write_json(
    path: Path,
    payload: dict[str, Any],
    *,
    volatile_keys: Iterable[str] = DEFAULT_VOLATILE_KEYS,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = stabilize_volatile(path, payload, volatile_keys=volatile_keys)
    tmp = path.with_suffix(".tmp.json")
    tmp.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def _filter_map(value: Any, row_ids: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items() if str(k) in row_ids}


def _tweet_ids(rows: Iterable[dict[str, Any]]) -> set[str]:
    return {str(row.get("tweet_id") or "") for row in rows if row.get("tweet_id")}


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--generated-at", default=None)
    parser.add_argument("--no-previews", action="store_true", help="Only write data/catalog.json")
    args = parser.parse_args(argv)
    write_catalog(args.data_dir, generated_at=args.generated_at)
    if not args.no_previews:
        write_previews(args.data_dir, generated_at=args.generated_at)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
