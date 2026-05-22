"""Build small JSON preview datasets for the static viewer.

The full archive lives in per-account Parquet files. Those are efficient for
storage, but expensive for a browser that only needs the first page. This
script writes small, cacheable JSON slices of the newest rows so the viewer can
boot without downloading every Parquet file.
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


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def preview_path(data_dir: Path, limit: int) -> Path:
    return data_dir / f"preview-{limit}.json"


def canonical_parquet_paths(data_dir: Path) -> list[Path]:
    return sorted(p for p in data_dir.glob("*.parquet") if p.is_file())


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
        poster = _string_or_none(row.get("thumbnail_path")) or poster_path_from_frames(row.get("frames"))
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


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
    args = parser.parse_args(argv)
    write_previews(args.data_dir, generated_at=args.generated_at)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
