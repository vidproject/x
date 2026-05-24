"""Build read-only per-tweet signal bundles for the manual tag-audit campaign.

The tag-audit reviewing subagents must NOT read the big parquets directly (cost,
and to avoid colliding with the agent concurrently rewriting them). Instead this
orchestrator step joins every available signal for a slice of tweet_ids into one
small JSON bundle per tweet under ``data/tags/tag_audit/_bundles/<tweet_id>.json``.

Signals gathered (all read-only):

* catalog.parquet: text, text_resolved, account_handle, tweet_type, mentions,
  hashtags, card title/description, media list (type + alt_text + sha256),
  media_insights descriptions, and the CURRENT tags (tag/tentative/source).
* media_vision.parquet: opus-vision-review descriptions/summaries per media.
* image_ocr.parquet: recovered on-image text.
* transcripts.parquet: speech-to-text text.
* keyframe dirs: a flag of whether keyframes exist on disk (so the subagent
  knows it *can* fall back to viewing frames), plus the media_sha256 dirs.

Usage:
    uv run python -m scripts.build_tag_audit_bundles --ids-file <path>
    uv run python -m scripts.build_tag_audit_bundles --ids 123,456

The ids file is one tweet_id per line (blank lines / `#` comments ignored).
Idempotent: overwrites bundle files for the requested ids.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import polars as pl

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"
AUDIT_DIR = TAGS_DIR / "tag_audit"
BUNDLE_DIR = AUDIT_DIR / "_bundles"
KEYFRAME_DIR = DATA_DIR / "derived" / "keyframes"

CATALOG = DATA_DIR / "catalog.parquet"
MEDIA_VISION = TAGS_DIR / "media_vision.parquet"
IMAGE_OCR = TAGS_DIR / "image_ocr.parquet"
TRANSCRIPTS = TAGS_DIR / "transcripts.parquet"


def _read_ids(args: argparse.Namespace) -> list[str]:
    ids: list[str] = []
    if args.ids:
        ids.extend(s.strip() for s in args.ids.split(",") if s.strip())
    if args.ids_file:
        for line in Path(args.ids_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ids.append(line)
    # de-dupe, keep order
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _tags_to_list(tags: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in tags or []:
        if isinstance(e, dict) and e.get("tag"):
            out.append(
                {
                    "tag": e.get("tag"),
                    "tentative": e.get("tentative"),
                    "source": e.get("source"),
                }
            )
    return out


def _media_to_list(media: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in media or []:
        if not isinstance(m, dict):
            continue
        out.append(
            {
                "media_id": m.get("media_id"),
                "media_type": m.get("media_type"),
                "sha256": m.get("sha256"),
                "duration_sec": m.get("duration_sec"),
                "alt_text": m.get("alt_text"),
            }
        )
    return out


def _insights_to_list(ins: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in ins or []:
        if not isinstance(m, dict):
            continue
        desc = (m.get("description") or "").strip()
        summ = (m.get("summary_text") or "").strip()
        if not desc and not summ:
            continue
        out.append(
            {
                "media_id": m.get("media_id"),
                "media_type": m.get("media_type"),
                "description": desc,
                "summary_text": summ,
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ids", help="comma-separated tweet_ids")
    ap.add_argument("--ids-file", help="file with one tweet_id per line")
    args = ap.parse_args(argv)

    ids = _read_ids(args)
    if not ids:
        raise SystemExit("no tweet_ids given (use --ids or --ids-file)")
    id_set = set(ids)

    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)

    cat = pl.read_parquet(
        CATALOG,
        columns=[
            "tweet_id",
            "account_handle",
            "tweet_type",
            "posted_at",
            "tweet_url",
            "text",
            "text_resolved",
            "mentions",
            "hashtags",
            "card",
            "media",
            "media_insights",
            "tags",
            "possibly_sensitive",
            "community_note",
            "unavailable_reason",
            "unavailable_text",
        ],
    ).filter(pl.col("tweet_id").is_in(list(id_set)))

    # Auxiliary sidecars, filtered to the slice; group by tweet_id.
    def _load_aux(path: Path, cols: list[str]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        if not path.exists():
            return grouped
        try:
            df = pl.read_parquet(path, columns=cols).filter(pl.col("tweet_id").is_in(list(id_set)))
        except Exception as err:
            print(f"WARN: could not read {path}: {err}")
            return grouped
        for row in df.iter_rows(named=True):
            grouped.setdefault(str(row["tweet_id"]), []).append(row)
        return grouped

    vision = _load_aux(
        MEDIA_VISION,
        [
            "tweet_id",
            "media_id",
            "media_type",
            "media_sha256",
            "model",
            "description",
            "summary_text",
            "status",
        ],
    )
    ocr = _load_aux(
        IMAGE_OCR,
        ["tweet_id", "media_id", "media_sha256", "text", "status"],
    )
    trans = _load_aux(
        TRANSCRIPTS,
        ["tweet_id", "media_id", "media_sha256", "text", "language", "status"],
    )

    written = 0
    for row in cat.iter_rows(named=True):
        tid = str(row["tweet_id"])
        media = _media_to_list(row.get("media"))
        # keyframe availability: any media sha with an on-disk dir
        keyframe_shas = []
        for m in media:
            sha = m.get("sha256")
            if sha and (KEYFRAME_DIR / sha).is_dir():
                keyframe_shas.append(sha)
        card = row.get("card") or {}
        bundle = {
            "tweet_id": tid,
            "account_handle": row.get("account_handle"),
            "tweet_type": row.get("tweet_type"),
            "posted_at": row.get("posted_at"),
            "tweet_url": row.get("tweet_url"),
            "text": row.get("text"),
            "text_resolved": row.get("text_resolved"),
            "mentions": list(row.get("mentions") or []),
            "hashtags": list(row.get("hashtags") or []),
            "card_title": (card.get("title") if isinstance(card, dict) else None),
            "card_description": (card.get("description") if isinstance(card, dict) else None),
            "possibly_sensitive": row.get("possibly_sensitive"),
            "unavailable_reason": row.get("unavailable_reason"),
            "unavailable_text": row.get("unavailable_text"),
            "media": media,
            "media_insights": _insights_to_list(row.get("media_insights")),
            "current_tags": _tags_to_list(row.get("tags")),
            "vision_descriptions": [
                {
                    "media_id": v.get("media_id"),
                    "media_type": v.get("media_type"),
                    "model": v.get("model"),
                    "description": (v.get("description") or "").strip(),
                    "summary_text": (v.get("summary_text") or "").strip(),
                }
                for v in vision.get(tid, [])
                if (v.get("description") or v.get("summary_text"))
            ],
            "ocr_text": [
                {
                    "media_id": o.get("media_id"),
                    "text": (o.get("text") or "").strip(),
                    "status": o.get("status"),
                }
                for o in ocr.get(tid, [])
                if (o.get("text") or "").strip()
            ],
            "transcripts": [
                {
                    "media_id": t.get("media_id"),
                    "language": t.get("language"),
                    "text": (t.get("text") or "").strip(),
                    "status": t.get("status"),
                }
                for t in trans.get(tid, [])
                if (t.get("text") or "").strip()
            ],
            "keyframe_shas_on_disk": keyframe_shas,
        }
        (BUNDLE_DIR / f"{tid}.json").write_text(
            json.dumps(bundle, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        written += 1

    missing = id_set - {str(r["tweet_id"]) for r in cat.iter_rows(named=True)}
    print(f"wrote {written} bundles to {BUNDLE_DIR}")
    if missing:
        print(
            f"WARN: {len(missing)} requested ids not found in catalog (first 5): {list(missing)[:5]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
