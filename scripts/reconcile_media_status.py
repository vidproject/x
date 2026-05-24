"""Reconcile the media-status:described / media-status:needs-vision filter set.

The metadata pass (``scripts.describe_media``) historically tagged *every*
media row ``media-status:described`` while *also* tagging visual placeholders
``media-status:needs-vision``. The two are meant to be mutually exclusive:

* ``media-status:described``    -- the item has real descriptive content;
* ``media-status:needs-vision`` -- a photo / video / animated-gif still lacks
  any descriptive content and awaits a vision pass.

An item counts as genuinely described when it has ANY of:

* a real vision-review row in ``media_vision.parquet``
  (``model == 'opus-vision-review'``);
* alt text (the ``media-status:has-alt-text`` row tag, which
  ``describe_media`` sets from the captured ``alt_text``);
* a manual visual observation in ``manual_media_review_queue.json``.

These are exactly the signals ``describe_media`` already uses to decide
``needs_vision`` (plus the later vision-review overlay, which the metadata
pass cannot see on its own). This script makes the labels honest everywhere
the viewer reads them:

1. ``data/tags/media_vision.parquet`` -- each media row gets
   ``media-status:described`` XOR ``media-status:needs-vision`` (per media).
   Fixing the source keeps a later ``describe_media`` / ``build_viewer_preview``
   rebuild honest at the per-media level.
2. ``data/catalog.parquet`` -- the pre-built catalog the default viewer view
   reads is overlaid in place (tweet ``tags`` and ``media_insights[].tags``).
3. ``data/preview-*.json`` -- the preview / fallback payloads' ``tags`` and
   ``media_insights`` maps are overlaid the same way.

Tweet-level rule (so the two tags are mutually exclusive per tweet, which a
naive union of per-media tags is not): a tweet is ``described`` when it has at
least one genuinely-described media AND none of its visual media still needs
vision; it is ``needs-vision`` when ANY of its visual media is not yet
genuinely described. A mixed tweet (some media described, some not) keeps
``needs-vision`` because real work remains.

Idempotent: re-running recomputes the genuine set from the current sidecars
and reapplies the same correction, so it is safe to run again after more
vision-review descriptions land (Phase 2 re-runs it after each batch).

Run with:  uv run python -m scripts.reconcile_media_status
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

import polars as pl

from ._logging import configure
from ._schema import MEDIA_VISION_SCHEMA

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"

MEDIA_VISION_PATH = TAGS_DIR / "media_vision.parquet"
MANUAL_REVIEW_QUEUE_PATH = TAGS_DIR / "manual_media_review_queue.json"
CATALOG_PARQUET_PATH = DATA_DIR / "catalog.parquet"

DESCRIBED_TAG = "media-status:described"
NEEDS_VISION_TAG = "media-status:needs-vision"
HAS_ALT_TAG = "media-status:has-alt-text"
VISION_REVIEW_MODEL = "opus-vision-review"
VISUAL_TYPES = {"photo", "video", "animated_gif"}

# Match the source the metadata pass writes so the viewer groups these the same.
STATUS_SOURCE = "media-metadata"

CATALOG_TAG_DTYPE = pl.List(
    pl.Struct(
        [
            pl.Field("tag", pl.Utf8),
            pl.Field("tentative", pl.Boolean),
            pl.Field("source", pl.Utf8),
        ]
    )
)


def _tag_names(tags: Any) -> set[str]:
    out: set[str] = set()
    for entry in tags or []:
        if isinstance(entry, dict) and entry.get("tag"):
            out.add(str(entry["tag"]))
        elif isinstance(entry, str):
            out.add(entry)
    return out


def load_visual_observations() -> set[tuple[str, str]]:
    """(tweet_id, media_id) pairs that have a manual visual observation."""
    if not MANUAL_REVIEW_QUEUE_PATH.exists():
        return set()
    data = json.loads(MANUAL_REVIEW_QUEUE_PATH.read_text(encoding="utf-8"))
    out: set[tuple[str, str]] = set()
    for item in data.get("items", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("visual_observation") or "").strip():
            out.add((str(item.get("tweet_id") or ""), str(item.get("media_id") or "")))
    return out


def compute_genuine_media(
    df: pl.DataFrame, visual_observations: set[tuple[str, str]]
) -> set[tuple[str, str]]:
    """Return the (tweet_id, media_id) keys that are genuinely described.

    Genuine = a vision-review row exists, OR the row carries the has-alt-text
    tag, OR a manual visual observation exists for that media.
    """
    genuine: set[tuple[str, str]] = set()
    for row in df.iter_rows(named=True):
        key = (str(row.get("tweet_id") or ""), str(row.get("media_id") or ""))
        if str(row.get("model") or "") == VISION_REVIEW_MODEL:
            genuine.add(key)
            continue
        if HAS_ALT_TAG in _tag_names(row.get("tags")):
            genuine.add(key)
            continue
        if key in visual_observations:
            genuine.add(key)
    return genuine


def compute_review_descriptions(df: pl.DataFrame) -> dict[tuple[str, str], dict[str, Any]]:
    """Map (tweet_id, media_id) -> the vision-review prose for that media.

    Only ``opus-vision-review`` rows carry a real description. The catalog and
    preview ``media_insights`` cache a copy of this prose for the viewer; this
    map lets the overlay refresh those copies so newly-applied descriptions
    appear without a full ``build_viewer_preview`` rebuild.
    """
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in df.iter_rows(named=True):
        if str(row.get("model") or "") != VISION_REVIEW_MODEL:
            continue
        key = (str(row.get("tweet_id") or ""), str(row.get("media_id") or ""))
        out[key] = {
            "description": row.get("description"),
            "summary_text": row.get("summary_text"),
            "model": row.get("model"),
            "model_version": row.get("model_version"),
            "status": row.get("status"),
            "confidence": row.get("confidence"),
        }
    return out


def compute_tweet_status(
    df: pl.DataFrame, genuine: set[tuple[str, str]]
) -> tuple[set[str], set[str]]:
    """Return (described_tweets, needs_vision_tweets), mutually exclusive.

    described    : at least one genuine media AND no visual media still pending.
    needs_vision : any visual media not yet genuine.
    """
    by_tweet: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in df.iter_rows(named=True):
        tid = str(row.get("tweet_id") or "")
        mid = str(row.get("media_id") or "")
        by_tweet[tid].append((mid, str(row.get("media_type") or "")))

    described: set[str] = set()
    needs_vision: set[str] = set()
    for tid, medias in by_tweet.items():
        any_genuine = any((tid, mid) in genuine for mid, _ in medias)
        pending_visual = any(
            mtype in VISUAL_TYPES and (tid, mid) not in genuine for mid, mtype in medias
        )
        if pending_visual:
            needs_vision.add(tid)
        elif any_genuine:
            described.add(tid)
    return described, needs_vision


def fix_row_tags(tags: Any, *, described: bool, needs_vision: bool) -> list[dict[str, Any]]:
    """Rewrite a single media row's tag list so described XOR needs-vision holds.

    All other tags are preserved in order; the two media-status tags are
    removed and re-added per the desired state (described as a firm tag,
    needs-vision as tentative, matching ``describe_media``).
    """
    out: list[dict[str, Any]] = []
    for entry in tags or []:
        if isinstance(entry, dict):
            name = str(entry.get("tag") or "")
            normalized = dict(entry)
        elif isinstance(entry, str):
            name = entry
            normalized = {"tag": entry}
        else:
            continue
        if name in (DESCRIBED_TAG, NEEDS_VISION_TAG):
            continue
        out.append(normalized)
    if described:
        out.insert(0, _full_tag(DESCRIBED_TAG, tentative=None))
    if needs_vision:
        out.append(_full_tag(NEEDS_VISION_TAG, tentative=True))
    return out


def _full_tag(tag: str, *, tentative: bool | None) -> dict[str, Any]:
    """Full media_vision-schema tag struct (span fields included)."""
    return {
        "tag": tag,
        "tentative": True if tentative else None,
        "source": STATUS_SOURCE,
        "span_start": None,
        "span_end": None,
    }


def _compact_tag(tag: str, *, tentative: bool | None) -> dict[str, Any]:
    """Compact catalog/preview tag struct (no span fields)."""
    out: dict[str, Any] = {"tag": tag}
    if tentative:
        out["tentative"] = True
    out["source"] = STATUS_SOURCE
    return out


def reconcile_media_vision(
    genuine: set[tuple[str, str]],
) -> tuple[int, int]:
    """Fix per-media described/needs-vision tags in media_vision.parquet."""
    df = pl.read_parquet(MEDIA_VISION_PATH)
    rows = df.to_dicts()
    described = needs = 0
    for row in rows:
        key = (str(row.get("tweet_id") or ""), str(row.get("media_id") or ""))
        media_type = str(row.get("media_type") or "")
        is_genuine = key in genuine
        is_visual = media_type in VISUAL_TYPES
        row_needs = is_visual and not is_genuine
        # A non-visual media row (none today) still gets "described"; a visual
        # row is described only when genuine.
        row_described = (not is_visual) or is_genuine
        row["tags"] = fix_row_tags(row.get("tags"), described=row_described, needs_vision=row_needs)
        described += int(row_described)
        needs += int(row_needs)
    out = pl.DataFrame(rows, schema=MEDIA_VISION_SCHEMA, strict=False)
    tmp = MEDIA_VISION_PATH.with_suffix(".parquet.tmp")
    out.write_parquet(tmp, compression="zstd")
    os.replace(tmp, MEDIA_VISION_PATH)
    LOG.info(
        "reconcile: rewrote media_vision per-media tags",
        rows=len(rows),
        described=described,
        needs_vision=needs,
    )
    return described, needs


def _overlay_tweet_tags(
    existing: Any, *, described: bool, needs_vision: bool, compact: bool
) -> list[dict[str, Any]]:
    """Strip both media-status tags from a tweet tag list, re-add the right one."""
    out: list[dict[str, Any]] = []
    for entry in existing or []:
        if isinstance(entry, dict):
            name = str(entry.get("tag") or "")
            normalized = dict(entry)
        elif isinstance(entry, str):
            name = entry
            normalized = {"tag": entry}
        else:
            continue
        if name in (DESCRIBED_TAG, NEEDS_VISION_TAG):
            continue
        out.append(normalized)
    mk = _compact_tag if compact else _full_tag
    if described:
        out.append(mk(DESCRIBED_TAG, tentative=None))
    if needs_vision:
        out.append(mk(NEEDS_VISION_TAG, tentative=True))
    return out


def overlay_catalog(
    described_tweets: set[str],
    needs_vision_tweets: set[str],
    genuine: set[tuple[str, str]],
    descriptions: dict[tuple[str, str], dict[str, Any]],
) -> int:
    df = pl.read_parquet(CATALOG_PARQUET_PATH)
    if "tweet_id" not in df.columns:
        LOG.warning("reconcile: catalog has no tweet_id column")
        return 0
    ids = df["tweet_id"].to_list()
    existing_tags = df["tags"].to_list() if "tags" in df.columns else [None] * df.height
    existing_mi = (
        df["media_insights"].to_list() if "media_insights" in df.columns else [None] * df.height
    )
    new_tags: list[Any] = []
    new_mi: list[Any] = []
    touched = 0
    for tweet_id, tags, insights in zip(ids, existing_tags, existing_mi, strict=True):
        tid = str(tweet_id)
        d = tid in described_tweets
        n = tid in needs_vision_tweets
        if d or n or _has_media_status(tags):
            fixed = _overlay_tweet_tags(tags, described=d, needs_vision=n, compact=False)
            if fixed != tags:
                touched += 1
            new_tags.append(fixed)
        else:
            new_tags.append(tags)
        new_mi.append(_fix_insight_list(insights, tid, genuine, descriptions))
    df = df.with_columns(
        pl.Series("tags", new_tags, dtype=CATALOG_TAG_DTYPE),
        pl.Series("media_insights", new_mi, dtype=df.schema["media_insights"]),
    )
    tmp = CATALOG_PARQUET_PATH.with_suffix(".parquet.tmp")
    df.write_parquet(tmp)
    os.replace(tmp, CATALOG_PARQUET_PATH)
    LOG.info("reconcile: overlaid catalog", path=str(CATALOG_PARQUET_PATH), touched=touched)
    return touched


def _has_media_status(tags: Any) -> bool:
    names = _tag_names(tags)
    return DESCRIBED_TAG in names or NEEDS_VISION_TAG in names


def _fix_insight_list(
    insights: Any,
    tweet_id: str,
    genuine: set[tuple[str, str]],
    descriptions: dict[tuple[str, str], dict[str, Any]],
) -> Any:
    """Fix media-status tags + refresh prose in a tweet's media_insights (per media)."""
    if not insights:
        return insights
    out = []
    for insight in insights:
        ins = dict(insight)
        mid = str(ins.get("media_id") or "")
        mtype = str(ins.get("media_type") or "")
        is_visual = mtype in VISUAL_TYPES
        is_genuine = (tweet_id, mid) in genuine
        row_described = (not is_visual) or is_genuine
        row_needs = is_visual and not is_genuine
        ins["tags"] = _overlay_tweet_tags(
            ins.get("tags"), described=row_described, needs_vision=row_needs, compact=False
        )
        prose = descriptions.get((tweet_id, mid))
        if prose:
            for field in ("description", "summary_text"):
                if field in ins and prose.get(field) is not None:
                    ins[field] = prose[field]
        out.append(ins)
    return out


def overlay_previews(
    described_tweets: set[str],
    needs_vision_tweets: set[str],
    genuine: set[tuple[str, str]],
    descriptions: dict[tuple[str, str], dict[str, Any]],
) -> int:
    total = 0
    for path in sorted(glob.glob(str(DATA_DIR / "preview-*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (json.JSONDecodeError, OSError) as err:
            LOG.warning("reconcile: skipping preview", path=path, error=str(err))
            continue
        tags_map = payload.get("tags")
        if not isinstance(tags_map, dict):
            tags_map = {}
        mi_map = payload.get("media_insights")
        if not isinstance(mi_map, dict):
            mi_map = {}
        rows = payload.get("rows")
        row_ids = {str(r.get("tweet_id") or "") for r in rows} if isinstance(rows, list) else None

        touched = 0
        # Tweet-level tags: fix any tweet present in this slice that is in either
        # set or that currently carries a media-status tag.
        candidate_ids = set(tags_map) | described_tweets | needs_vision_tweets
        for tweet_id in candidate_ids:
            if row_ids is not None and tweet_id not in row_ids:
                continue
            d = tweet_id in described_tweets
            n = tweet_id in needs_vision_tweets
            current = tags_map.get(tweet_id)
            if not (d or n or _has_media_status(current)):
                continue
            fixed = _overlay_tweet_tags(current, described=d, needs_vision=n, compact=True)
            if fixed != current:
                tags_map[tweet_id] = fixed
                touched += 1
        # Per-media insight tags + prose.
        for tweet_id, insights in mi_map.items():
            fixed = _fix_insight_compact(insights, tweet_id, genuine, descriptions)
            if fixed != insights:
                mi_map[tweet_id] = fixed
                touched += 1

        payload["tags"] = tags_map
        payload["media_insights"] = mi_map
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        LOG.info("reconcile: overlaid preview", path=path, touched=touched)
        total += touched
    return total


def _fix_insight_compact(
    insights: Any,
    tweet_id: str,
    genuine: set[tuple[str, str]],
    descriptions: dict[tuple[str, str], dict[str, Any]],
) -> Any:
    if not isinstance(insights, list):
        return insights
    out = []
    for insight in insights:
        if not isinstance(insight, dict):
            out.append(insight)
            continue
        ins = dict(insight)
        mid = str(ins.get("media_id") or "")
        mtype = str(ins.get("media_type") or "")
        is_visual = mtype in VISUAL_TYPES
        is_genuine = (tweet_id, mid) in genuine
        row_described = (not is_visual) or is_genuine
        row_needs = is_visual and not is_genuine
        ins["tags"] = _overlay_tweet_tags(
            ins.get("tags"), described=row_described, needs_vision=row_needs, compact=True
        )
        prose = descriptions.get((tweet_id, mid))
        if prose:
            for field in (
                "description",
                "summary_text",
                "model",
                "model_version",
                "status",
                "confidence",
            ):
                if field in ins and prose.get(field) is not None:
                    ins[field] = prose[field]
        out.append(ins)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sidecar-only",
        action="store_true",
        help="Only fix media_vision.parquet; skip the catalog/preview overlay.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report counts; write nothing.")
    args = parser.parse_args(argv)

    if not MEDIA_VISION_PATH.exists():
        LOG.error("reconcile: media_vision missing", path=str(MEDIA_VISION_PATH))
        return 1

    df = pl.read_parquet(MEDIA_VISION_PATH)
    visual_observations = load_visual_observations()
    genuine = compute_genuine_media(df, visual_observations)
    descriptions = compute_review_descriptions(df)
    described_tweets, needs_vision_tweets = compute_tweet_status(df, genuine)
    LOG.info(
        "reconcile: computed status",
        genuine_media=len(genuine),
        described_tweets=len(described_tweets),
        needs_vision_tweets=len(needs_vision_tweets),
        overlap=len(described_tweets & needs_vision_tweets),
        review_descriptions=len(descriptions),
    )
    if args.dry_run:
        LOG.info("reconcile: dry run, nothing written")
        return 0

    reconcile_media_vision(genuine)
    if not args.sidecar_only:
        overlay_catalog(described_tweets, needs_vision_tweets, genuine, descriptions)
        overlay_previews(described_tweets, needs_vision_tweets, genuine, descriptions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
