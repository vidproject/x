"""Build per-handle Parquet files from raw capture JSON.

Reads every `raw/<handle>/*.json` file written by the Firefox extension,
deduplicates tweets by `tweet_id`, merges engagement history across captures,
and writes `data/<handle>.parquet` atomically. Also rebuilds
`data/manifest.json`.

Bad input (unparseable JSON, missing required keys, schema-version mismatch)
is moved into `raw/_quarantine/` rather than silently dropped. This keeps the
operating-principle "never silently drop data" honest.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from scripts._logging import configure
from scripts._schema import REQUIRED_TWEET_KEYS, TWEET_SCHEMA, empty_dataframe

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "raw"
DATA_DIR = REPO_ROOT / "data"
CONFIG_PATH = REPO_ROOT / "config" / "accounts.yaml"
QUARANTINE_DIR = RAW_DIR / "_quarantine"
SCHEMA_VERSION = 1


# --------------------------------------------------------------------------
# Config


def load_accounts(path: Path = CONFIG_PATH) -> list[dict[str, str]]:
    if not path.exists():
        LOG.warning("accounts.yaml missing; defaulting to empty list", path=str(path))
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("accounts", [])
    out: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        handle = str(entry.get("handle", "")).strip()
        label = str(entry.get("label", handle)).strip()
        if handle:
            out.append({"handle": handle, "label": label})
    return out


# --------------------------------------------------------------------------
# Reading raw captures


def iter_raw_files(handle: str) -> Iterator[Path]:
    handle_dir = RAW_DIR / handle
    if not handle_dir.exists():
        return
    yield from sorted(handle_dir.glob("*.json"))


def quarantine(path: Path, reason: str, detail: str | None = None) -> None:
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    target = QUARANTINE_DIR / f"{path.parent.name}_{path.name}"
    if target.exists():
        # Add timestamp to avoid clobbering.
        target = target.with_name(
            f"{target.stem}.{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}{target.suffix}"
        )
    LOG.error("quarantining raw file", path=str(path), reason=reason, detail=detail)
    shutil.move(str(path), str(target))


def parse_capture_file(path: Path) -> list[dict[str, Any]] | None:
    """Return the list of tweet dicts in this file, or None if it's not a
    valid capture payload. Files lacking the structured envelope but
    containing a top-level list of tweet dicts (legacy/manual format) are
    accepted as well.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        quarantine(path, "json-parse-error", str(e))
        return None

    if isinstance(raw, dict) and "tweets" in raw:
        if raw.get("schema_version") not in (None, SCHEMA_VERSION):
            quarantine(path, "schema-version-mismatch", str(raw.get("schema_version")))
            return None
        tweets = raw.get("tweets")
    elif isinstance(raw, list):
        tweets = raw
    else:
        quarantine(path, "unrecognized-shape", type(raw).__name__)
        return None

    if not isinstance(tweets, list):
        quarantine(path, "tweets-not-a-list", type(tweets).__name__)
        return None

    valid: list[dict[str, Any]] = []
    for entry in tweets:
        if not isinstance(entry, dict):
            continue
        if not REQUIRED_TWEET_KEYS.issubset(entry.keys()):
            missing = sorted(REQUIRED_TWEET_KEYS - entry.keys())
            LOG.warning(
                "skipping tweet missing keys",
                file=str(path),
                tweet_id=entry.get("tweet_id"),
                missing=missing,
            )
            continue
        valid.append(entry)
    return valid


# --------------------------------------------------------------------------
# Dedup / merge


def merge_engagement(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_ts: dict[str, dict[str, Any]] = {}
    for snap in (*existing, *incoming):
        if not isinstance(snap, dict):
            continue
        ts = snap.get("captured_at")
        if not isinstance(ts, str):
            continue
        by_ts[ts] = snap
    return [by_ts[k] for k in sorted(by_ts)]


def merge_tweets(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Reduce many captures of the same tweet into one canonical row.

    Strategy: keep the earliest `first_captured_at`, the latest `last_seen_at`,
    union `engagement_history` and `media`, and otherwise take the most
    recently captured row's field values (so engagement counts and `text`
    reflect the latest observed state).

    Re-captures are append-only with respect to media and community notes —
    once we've archived a photo, video, or community note for a tweet,
    subsequent captures cannot drop it. This protects the archive against
    X reducing the data it returns in a later payload (deleted media,
    revoked Community Note, etc.) while still letting fresh engagement
    counts and full-text refetches win.
    """
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        tid = str(row["tweet_id"])
        cur = merged.get(tid)
        if cur is None:
            merged[tid] = dict(row)
            continue
        cur_last = str(cur.get("last_seen_at") or cur.get("first_captured_at") or "")
        row_last = str(row.get("last_seen_at") or row.get("first_captured_at") or "")
        if row_last >= cur_last:
            firsts = [
                v
                for v in (cur.get("first_captured_at"), row.get("first_captured_at"))
                if isinstance(v, str) and v
            ]
            preserved: dict[str, Any] = {
                "first_captured_at": min(firsts) if firsts else None,
                "engagement_history": merge_engagement(
                    cur.get("engagement_history") or [],
                    row.get("engagement_history") or [],
                ),
            }
            # Preserve a previously-set deletion timestamp; never overwrite
            # with null on re-ingest.
            if cur.get("deletion_detected_at") and not row.get("deletion_detected_at"):
                preserved["deletion_detected_at"] = cur["deletion_detected_at"]
            # Preserve a previously-set wayback url if the new row doesn't carry it.
            if cur.get("wayback_url") and not row.get("wayback_url"):
                preserved["wayback_url"] = cur["wayback_url"]
                preserved["wayback_submitted_at"] = cur.get("wayback_submitted_at")
            preserved["media"] = union_media(cur.get("media"), row.get("media"))
            preserved["text"], preserved["text_resolved"], preserved["is_truncated"] = pick_text(
                cur, row
            )
            # Once we've seen a Community Note attached, hold onto it even
            # if a later payload omits the pivot block. If the new row has
            # one and old didn't, the default (row wins) is correct.
            if cur.get("community_note") and not row.get("community_note"):
                preserved["community_note"] = cur["community_note"]
            merged[tid] = {**row, **preserved}
        else:
            cur["last_seen_at"] = max(
                str(cur.get("last_seen_at") or ""),
                str(row.get("last_seen_at") or row.get("first_captured_at") or ""),
            )
            cur["engagement_history"] = merge_engagement(
                cur.get("engagement_history") or [],
                row.get("engagement_history") or [],
            )
            # Even when row is older, it may carry media we don't yet have
            # (e.g. a backfilled older raw file rebuilding history).
            cur["media"] = union_media(cur.get("media"), row.get("media"))
            cur["text"], cur["text_resolved"], cur["is_truncated"] = pick_text(cur, row)
            if not cur.get("community_note") and row.get("community_note"):
                cur["community_note"] = row["community_note"]
    return merged


def pick_text(cur: dict[str, Any], row: dict[str, Any]) -> tuple[str | None, str | None, bool]:
    """Pick the better text body between two captures of the same tweet.

    A non-truncated capture always wins over a truncated one. When both have
    the same truncation status, the longer text wins (covers the case where
    X widened a long-tweet inline, or where we got more entities expanded).
    """
    cur_trunc = bool(cur.get("is_truncated"))
    row_trunc = bool(row.get("is_truncated"))
    cur_text = cur.get("text") or ""
    row_text = row.get("text") or ""
    if cur_trunc and not row_trunc:
        winner = row
    elif (row_trunc and not cur_trunc) or len(cur_text) >= len(row_text):
        winner = cur
    else:
        winner = row
    # Once we've ever seen the full body, the tweet is no longer truncated.
    is_truncated = cur_trunc and row_trunc
    return winner.get("text"), winner.get("text_resolved"), is_truncated


def union_media(
    cur: list[dict[str, Any]] | None, new: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Union two media lists by ``media_id``, preferring fields from whichever
    row carries a value (with archive metadata from the previous row winning
    when present, and the newer row's CDN URL winning when populated).

    No matching media_id from either side is ever dropped — once we've seen a
    photo or video for a tweet it stays in the archive forever, even if X
    later stops returning it. This is the property the user's data depends on.
    """
    cur = cur or []
    new = new or []
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def key(m: dict[str, Any]) -> str | None:
        mid = m.get("media_id")
        return str(mid) if mid else None

    for m in cur:
        if not isinstance(m, dict):
            continue
        k = key(m)
        if not k:
            continue
        by_id[k] = dict(m)
        order.append(k)
    for m in new:
        if not isinstance(m, dict):
            continue
        k = key(m)
        if not k:
            continue
        if k not in by_id:
            by_id[k] = dict(m)
            order.append(k)
            continue
        prev = by_id[k]
        merged: dict[str, Any] = dict(prev)
        # Latest non-empty CDN url / dimensions / alt text wins.
        for fld in (
            "media_type",
            "original_url",
            "duration_sec",
            "width",
            "height",
            "alt_text",
        ):
            v = m.get(fld)
            if v is not None and v != "":
                merged[fld] = v
        # Archive metadata: prefer whichever side has it. The archive
        # workflow writes these into the parquet, so on re-ingest the
        # extension-side capture (which has them null) must not clobber.
        for fld in (
            "release_asset_url",
            "sha256",
            "bytes",
            "archive_status",
            "archive_attempts",
            "last_attempt_at",
        ):
            prev_v = prev.get(fld)
            new_v = m.get(fld)
            if prev_v and not new_v:
                merged[fld] = prev_v
            elif new_v:
                merged[fld] = new_v
        by_id[k] = merged
    return [by_id[k] for k in order]


# --------------------------------------------------------------------------
# Writing


def atomic_write_parquet(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.parquet")
    df.write_parquet(tmp, compression="zstd", statistics=True)
    os.replace(tmp, path)


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce a tweet dict to exactly the keys/types in TWEET_SCHEMA, filling
    missing fields with `None`/defaults so polars can build a DataFrame with a
    stable schema even when the source has gaps.
    """
    out: dict[str, Any] = {}
    for col in TWEET_SCHEMA:
        out[col] = row.get(col)
    # Default-empty lists for list-typed cols so polars doesn't infer Null.
    for list_col in ("hashtags", "mentions", "urls", "media", "engagement_history"):
        if out[list_col] is None:
            out[list_col] = []
    # Default counters to 0 so aggregations don't trip over None.
    for int_col in ("like_count", "retweet_count", "reply_count", "quote_count"):
        if out[int_col] is None:
            out[int_col] = 0
    if out["is_truncated"] is None:
        out["is_truncated"] = False
    out["schema_version"] = SCHEMA_VERSION
    return out


def build_dataframe(rows: list[dict[str, Any]]) -> pl.DataFrame:
    if not rows:
        return empty_dataframe()
    normalized = [normalize_row(r) for r in rows]
    return pl.DataFrame(normalized, schema=TWEET_SCHEMA, strict=False)


# --------------------------------------------------------------------------
# Manifest


def build_manifest(accounts: list[dict[str, str]]) -> dict[str, Any]:
    out_accounts: list[dict[str, Any]] = []
    for a in accounts:
        handle = a["handle"]
        path = DATA_DIR / f"{handle}.parquet"
        if not path.exists():
            continue
        df = pl.read_parquet(path)
        row_count = df.height
        first_post = df.select(pl.col("posted_at").min()).item() if row_count else None
        latest_post = df.select(pl.col("posted_at").max()).item() if row_count else None
        latest_capture = df.select(pl.col("last_seen_at").max()).item() if row_count else None
        deleted = df.filter(pl.col("deletion_detected_at").is_not_null()).height if row_count else 0
        media_count = df.select(pl.col("media").list.len().sum()).item() if row_count else 0
        video_count = (
            df.with_columns(
                pl.col("media")
                .list.eval(pl.element().struct.field("media_type") == "video")
                .list.sum()
                .alias("vids")
            )["vids"].sum()
            if row_count
            else 0
        )
        out_accounts.append(
            {
                "handle": handle,
                "label": a["label"],
                "parquet": f"data/{handle}.parquet",
                "parquet_bytes": path.stat().st_size,
                "row_count": row_count,
                "first_post_at": first_post,
                "latest_post_at": latest_post,
                "latest_capture_at": latest_capture,
                "deleted_count": deleted,
                "media_count": int(media_count or 0),
                "video_count": int(video_count or 0),
                "releases": [],
            }
        )
    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema_version": SCHEMA_VERSION,
        "accounts": out_accounts,
    }


def write_manifest(manifest: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DATA_DIR / "manifest.tmp.json"
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    os.replace(tmp, DATA_DIR / "manifest.json")


# --------------------------------------------------------------------------
# Per-handle pipeline


def ingest_handle(handle: str) -> tuple[int, int, int]:
    """Return (files_read, tweets_seen, tweets_after_dedup).

    Seeds the merge with the existing parquet (if any) so archive metadata
    written by other workflows — ``release_asset_url``, ``sha256``,
    ``wayback_url``, ``deletion_detected_at`` — survives a re-ingest. The
    archive workflows always update parquet, never raw, so without this seed
    a re-ingest would clobber their work.
    """
    files_read = 0
    tweets_seen = 0
    captures: list[dict[str, Any]] = []

    existing_path = DATA_DIR / f"{handle}.parquet"
    if existing_path.exists():
        try:
            existing_df = pl.read_parquet(existing_path)
            captures.extend(existing_df.to_dicts())
        except Exception:
            LOG.exception("could not read existing parquet; rebuilding", handle=handle)

    for raw_path in iter_raw_files(handle):
        if raw_path.parent.name == "_quarantine":
            continue
        tweets = parse_capture_file(raw_path)
        if tweets is None:
            continue
        files_read += 1
        tweets_seen += len(tweets)
        captures.extend(tweets)
    merged = merge_tweets(captures)
    df = build_dataframe(list(merged.values()))
    df = df.sort("posted_at", descending=True)
    out_path = DATA_DIR / f"{handle}.parquet"
    atomic_write_parquet(df, out_path)
    LOG.info(
        "ingested handle",
        handle=handle,
        files_read=files_read,
        tweets_seen=tweets_seen,
        rows_written=df.height,
        out=str(out_path.relative_to(REPO_ROOT)),
    )
    return files_read, tweets_seen, df.height


# --------------------------------------------------------------------------
# CLI


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--handle", help="Restrict to a single handle (for debugging).")
    p.add_argument("--accounts", type=Path, default=CONFIG_PATH)
    args = p.parse_args(argv)

    accounts = load_accounts(args.accounts)
    if args.handle:
        accounts = [a for a in accounts if a["handle"] == args.handle]
        # Also handle "ephemeral" handles that exist in raw/ but not config
        # (e.g. test-handle in tests).
        if not accounts and (RAW_DIR / args.handle).exists():
            accounts = [{"handle": args.handle, "label": args.handle}]

    # Also include any handle directories present in raw/ that aren't in the
    # configured list — useful for tests pushing raw/test-handle/.
    seen_handles = {a["handle"] for a in accounts}
    if RAW_DIR.exists():
        for d in sorted(RAW_DIR.iterdir()):
            if d.is_dir() and not d.name.startswith("_") and d.name not in seen_handles:
                accounts.append({"handle": d.name, "label": d.name})
                seen_handles.add(d.name)

    if not accounts:
        LOG.warning("no accounts configured; nothing to ingest")
        write_manifest(build_manifest([]))
        return 0

    totals = {"files": 0, "tweets": 0, "rows": 0}
    for a in accounts:
        try:
            f, t, r = ingest_handle(a["handle"])
            totals["files"] += f
            totals["tweets"] += t
            totals["rows"] += r
        except Exception:
            LOG.exception("ingest failed for handle", handle=a["handle"])
            return 2

    manifest = build_manifest(accounts)
    write_manifest(manifest)
    LOG.info("ingest complete", **totals, accounts=len(accounts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
