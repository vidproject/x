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
from dataclasses import dataclass
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

# Handle / label used for the consolidated "everyone else" parquet. Tweets
# from accounts not in config/accounts.yaml — typically people the tracked
# accounts replied to, quoted, or retweeted — land in `data/_misc.parquet`
# instead of their own per-handle file. The viewer renders them as part of
# the same unified table; the `reply_to_*`, `quoted_tweet_id`, and
# `retweeted_tweet_id` columns still link them to the tracked tweet that
# brought them in.
MISC_HANDLE = "_misc"
MISC_LABEL = "Miscellaneous (replies / quotes / retweets of non-tracked accounts)"
MISC_CATEGORY = "public"

# Valid over-categories an account in accounts.yaml may declare. The `_misc`
# bucket is always `public` regardless. Listed entries with a missing or
# unrecognised category fall back to `core` for backward compat with the
# pre-categorization file shape.
VALID_CATEGORIES = frozenset({"core", "government", "officials", "public_figures", "public"})


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
        category = str(entry.get("category", "core")).strip() or "core"
        if category not in VALID_CATEGORIES:
            LOG.warning(
                "unknown account category; defaulting to core",
                handle=handle,
                category=category,
            )
            category = "core"
        if handle:
            out.append({"handle": handle, "label": label, "category": category})
    return out


# --------------------------------------------------------------------------
# Reading raw captures


@dataclass
class ParsedCapture:
    tweets: list[dict[str, Any]]
    unavailable_tweets: list[dict[str, Any]]


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


def parse_capture_file(path: Path) -> ParsedCapture | None:
    """Return tweet rows plus unavailable/tombstone events in this file.

    Files lacking the structured envelope but containing a top-level list
    of tweet dicts (legacy/manual format) are accepted as tweet rows only.
    Return None if the file is not a valid capture payload.
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
        unavailable_raw = raw.get("unavailable_tweets") or []
    elif isinstance(raw, list):
        tweets = raw
        unavailable_raw = []
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
    unavailable: list[dict[str, Any]] = []
    if isinstance(unavailable_raw, list):
        for entry in unavailable_raw:
            if not isinstance(entry, dict):
                continue
            tweet_id = str(entry.get("tweet_id") or "").strip()
            if not tweet_id:
                continue
            unavailable.append(entry)
    return ParsedCapture(tweets=valid, unavailable_tweets=unavailable)


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
            for field in (
                "unavailable_detected_at",
                "unavailable_reason",
                "unavailable_text",
                "unavailable_source_url",
            ):
                if cur.get(field) and not row.get(field):
                    preserved[field] = cur[field]
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
            for field in (
                "unavailable_detected_at",
                "unavailable_reason",
                "unavailable_text",
                "unavailable_source_url",
            ):
                if not cur.get(field) and row.get(field):
                    cur[field] = row[field]
            if not cur.get("community_note") and row.get("community_note"):
                cur["community_note"] = row["community_note"]
    return merged


def apply_unavailable_events(
    merged: dict[str, dict[str, Any]], events: Iterable[dict[str, Any]]
) -> int:
    updated = 0
    for event in events:
        tweet_id = str(event.get("tweet_id") or "").strip()
        if not tweet_id:
            continue
        row = merged.get(tweet_id)
        if row is None:
            LOG.warning(
                "unavailable event has no matching archived tweet",
                tweet_id=tweet_id,
                reason=event.get("unavailable_reason"),
            )
            continue
        for field in (
            "unavailable_detected_at",
            "unavailable_reason",
            "unavailable_text",
            "unavailable_source_url",
        ):
            value = event.get(field)
            if value:
                row[field] = value
        updated += 1
    return updated


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
        # Archive metadata pivots on `release_asset_url` — whichever side
        # has the uploaded asset is the authoritative source for ALL the
        # archive fields. Critically, `archive_status` is always truthy on
        # both sides ('pending' from the extension, 'archived' from the
        # archive workflow), so the old field-by-field "non-empty wins"
        # rule silently let the extension's 'pending' clobber the
        # workflow's 'archived'. Anchoring on release_asset_url fixes that.
        archive_fields = (
            "release_asset_url",
            "sha256",
            "bytes",
            "archive_status",
            "archive_attempts",
            "last_attempt_at",
        )
        prev_archived = bool(prev.get("release_asset_url"))
        new_archived = bool(m.get("release_asset_url"))
        if prev_archived and not new_archived:
            for fld in archive_fields:
                merged[fld] = prev.get(fld)
        elif new_archived and not prev_archived:
            for fld in archive_fields:
                merged[fld] = m.get(fld)
        else:
            # Neither archived (both 'pending' / 'failed'), or both
            # archived. Take the new row's values, falling back to prev
            # for missing fields. For attempts, the maximum wins so we
            # don't reset a backoff counter on re-ingest.
            for fld in archive_fields:
                v = m.get(fld)
                if v is None or v == "":
                    v = prev.get(fld)
                merged[fld] = v
            if isinstance(prev.get("archive_attempts"), int) and isinstance(
                m.get("archive_attempts"), int
            ):
                merged["archive_attempts"] = max(
                    int(prev["archive_attempts"]), int(m["archive_attempts"])
                )
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
                "category": a.get("category", "core"),
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
# Users aggregation
#
# Per-author profile snapshots live on every tweet (in the `author` struct).
# `data/users.json` is the latest-non-null aggregation, keyed by handle.
# The viewer loads it once on boot to render avatars + display names inline
# without scanning every tweet at render time.


def aggregate_users() -> dict[str, dict[str, Any]]:
    """Return {handle: latest_user_snapshot} merged across every parquet
    (tracked + _misc). For each field, the most recently captured non-null
    value wins.
    """
    field_keys = (
        "display_name",
        "avatar_url",
        "verified",
        "is_blue_verified",
        "verified_type",
        "description",
        "location",
        "url",
        "followers_count",
        "friends_count",
        "statuses_count",
        "account_created_at",
        "protected",
    )
    # handle -> {observed_at, fields...}
    out: dict[str, dict[str, Any]] = {}
    for parquet_path in sorted(DATA_DIR.glob("*.parquet")):
        try:
            df = pl.read_parquet(parquet_path, columns=["account_handle", "author", "last_seen_at"])
        except Exception:
            LOG.exception("aggregate_users: could not read", path=str(parquet_path))
            continue
        for row in df.iter_rows(named=True):
            handle = str(row.get("account_handle") or "")
            if not handle:
                continue
            author = row.get("author") or {}
            if not isinstance(author, dict):
                continue
            seen = str(row.get("last_seen_at") or "")
            cur = out.get(handle)
            if cur is None:
                cur = {"handle": handle, "observed_at": seen}
                for k in field_keys:
                    cur[k] = author.get(k)
                out[handle] = cur
                continue
            # Field-by-field: prefer non-null from whichever row was
            # captured later. Falls back to current value when newer row's
            # field is null.
            row_seen = seen
            cur_seen = str(cur.get("observed_at") or "")
            if row_seen >= cur_seen:
                cur["observed_at"] = row_seen
                for k in field_keys:
                    v = author.get(k)
                    if v is not None:
                        cur[k] = v
            else:
                for k in field_keys:
                    if cur.get(k) is None:
                        v = author.get(k)
                        if v is not None:
                            cur[k] = v
    return out


def write_users(users: dict[str, dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "users": users,
    }
    tmp = DATA_DIR / "users.tmp.json"
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, DATA_DIR / "users.json")


# --------------------------------------------------------------------------
# Per-handle pipeline


def load_misc_rows_by_handle() -> dict[str, list[dict[str, Any]]]:
    """Read `data/_misc.parquet` and bucket rows by their author handle.

    Used to seed the per-handle merge with archive metadata for non-tracked
    handles — and, when a handle gets promoted into accounts.yaml, to carry
    its existing rows out of `_misc` and into the new per-handle parquet.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    misc_path = DATA_DIR / f"{MISC_HANDLE}.parquet"
    if not misc_path.exists():
        return out
    try:
        df = pl.read_parquet(misc_path)
    except Exception:
        LOG.exception("could not read _misc.parquet; treating as empty")
        return out
    for row in df.to_dicts():
        h = str(row.get("account_handle") or "")
        if not h:
            continue
        out.setdefault(h, []).append(row)
    return out


def ingest_handle_rows(
    handle: str, *, seed_extra: list[dict[str, Any]] | None = None
) -> tuple[int, int, dict[str, dict[str, Any]]]:
    """Return (files_read, tweets_seen, merged_rows_by_id) for a handle.

    Seeds the merge with the existing per-handle parquet (if any) plus any
    rows supplied via ``seed_extra`` (typically the handle's slice of an
    existing ``_misc.parquet``) so archive metadata — ``release_asset_url``,
    ``sha256``, ``wayback_url``, ``deletion_detected_at`` — survives across
    re-ingests AND across promotion/demotion between tracked and misc.
    """
    files_read = 0
    tweets_seen = 0
    captures: list[dict[str, Any]] = []
    unavailable_events: list[dict[str, Any]] = []

    existing_path = DATA_DIR / f"{handle}.parquet"
    if existing_path.exists():
        try:
            existing_df = pl.read_parquet(existing_path)
            captures.extend(existing_df.to_dicts())
        except Exception:
            LOG.exception("could not read existing parquet; rebuilding", handle=handle)

    if seed_extra:
        captures.extend(seed_extra)

    for raw_path in iter_raw_files(handle):
        if raw_path.parent.name == "_quarantine":
            continue
        parsed = parse_capture_file(raw_path)
        if parsed is None:
            continue
        files_read += 1
        tweets_seen += len(parsed.tweets)
        captures.extend(parsed.tweets)
        unavailable_events.extend(parsed.unavailable_tweets)
    merged = merge_tweets(captures)
    updated = apply_unavailable_events(merged, unavailable_events)
    if updated:
        LOG.info("applied unavailable tweet events", handle=handle, updated=updated)
    return files_read, tweets_seen, merged


def discover_handles(restrict_to: str | None) -> set[str]:
    """Every handle we have any data for: raw dirs, per-handle parquets,
    or rows in `_misc.parquet`. Optionally restricted to a single handle for
    debugging."""
    if restrict_to:
        return {restrict_to}
    handles: set[str] = set()
    if RAW_DIR.exists():
        for d in RAW_DIR.iterdir():
            if d.is_dir() and not d.name.startswith("_"):
                handles.add(d.name)
    for p in DATA_DIR.glob("*.parquet"):
        h = p.stem
        if not h.startswith("_"):
            handles.add(h)
    handles.update(load_misc_rows_by_handle().keys())
    return handles


# --------------------------------------------------------------------------
# CLI


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--handle", help="Restrict to a single handle (for debugging).")
    p.add_argument("--accounts", type=Path, default=CONFIG_PATH)
    args = p.parse_args(argv)

    tracked_accounts = load_accounts(args.accounts)
    tracked_handles = {a["handle"] for a in tracked_accounts}
    tracked_labels = {a["handle"]: a["label"] for a in tracked_accounts}

    # Bucket existing _misc rows so we can re-seed each handle's merge with
    # the slice that previously lived in misc. Empty when the file doesn't
    # exist yet (first run after this refactor).
    misc_by_handle = load_misc_rows_by_handle()

    handles = discover_handles(args.handle)
    # On a --handle run we still want sane behavior even when the handle has
    # no raw/ dir and no parquet yet (e.g. it's purely a misc reference). The
    # set produced by discover_handles will include it iff it lives somewhere.
    if not handles:
        LOG.warning("no handles found; nothing to ingest")
        write_manifest(build_manifest([]))
        return 0

    totals = {"files": 0, "tweets": 0, "rows": 0}
    misc_rows: list[dict[str, Any]] = []
    tracked_results: dict[str, dict[str, dict[str, Any]]] = {}

    for handle in sorted(handles):
        seed = misc_by_handle.get(handle) if handle not in tracked_handles else None
        if handle in tracked_handles and handle in misc_by_handle:
            # The handle is newly tracked since the last run — its rows are
            # currently in _misc.parquet. Seed the merge with them so they
            # migrate over without losing any archive metadata.
            seed = misc_by_handle.get(handle)
        try:
            f, t, merged = ingest_handle_rows(handle, seed_extra=seed)
        except Exception:
            LOG.exception("ingest failed for handle", handle=handle)
            return 2
        totals["files"] += f
        totals["tweets"] += t
        totals["rows"] += len(merged)
        if handle in tracked_handles:
            tracked_results[handle] = merged
        else:
            misc_rows.extend(merged.values())

    # --- Write tracked parquets ------------------------------------------
    for handle, merged in tracked_results.items():
        df = build_dataframe(list(merged.values())).sort("posted_at", descending=True)
        out_path = DATA_DIR / f"{handle}.parquet"
        atomic_write_parquet(df, out_path)
        LOG.info(
            "ingested handle (tracked)",
            handle=handle,
            rows_written=df.height,
            out=str(out_path.relative_to(REPO_ROOT)),
        )

    # --- Write consolidated _misc parquet --------------------------------
    if not args.handle or args.handle not in tracked_handles:
        # Skip the misc rewrite when the user asked for just one tracked
        # handle: we don't have a complete view of misc rows in that path.
        misc_df = build_dataframe(misc_rows).sort("posted_at", descending=True)
        misc_path = DATA_DIR / f"{MISC_HANDLE}.parquet"
        if misc_df.height > 0:
            atomic_write_parquet(misc_df, misc_path)
            LOG.info(
                "ingested _misc",
                rows_written=misc_df.height,
                authors=len({str(r.get("account_handle") or "") for r in misc_rows}),
            )
        elif misc_path.exists():
            misc_path.unlink()

        # --- Remove legacy per-handle parquets for non-tracked handles ----
        # This is what collapses the previous 200-ish per-author files into
        # the single _misc bucket on the next ingest.
        for parquet_path in sorted(DATA_DIR.glob("*.parquet")):
            h = parquet_path.stem
            if h.startswith("_") or h in tracked_handles:
                continue
            parquet_path.unlink()
            LOG.info("collapsed legacy per-handle parquet into _misc", handle=h)

    # --- Manifest --------------------------------------------------------
    tracked_categories = {a["handle"]: a["category"] for a in tracked_accounts}
    manifest_accounts: list[dict[str, str]] = [
        {
            "handle": h,
            "label": tracked_labels.get(h, h),
            "category": tracked_categories.get(h, "core"),
        }
        for h in tracked_handles
    ]
    if (DATA_DIR / f"{MISC_HANDLE}.parquet").exists():
        manifest_accounts.append(
            {"handle": MISC_HANDLE, "label": MISC_LABEL, "category": MISC_CATEGORY}
        )
    manifest = build_manifest(manifest_accounts)
    write_manifest(manifest)

    # Aggregate per-author user snapshots into data/users.json so the
    # viewer can render avatars + display names inline without scanning
    # every tweet at load time.
    users = aggregate_users()
    write_users(users)

    LOG.info(
        "ingest complete",
        **totals,
        tracked=len(tracked_results),
        misc=len(misc_rows),
        users=len(users),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
