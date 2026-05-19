"""Canonical Parquet schema for `data/<handle>.parquet`.

Kept here so ingest, detect_deletions, and update_readme all agree on the
column types. Mirrors `extension/src/lib/types.ts::CanonicalTweet`.
"""

from __future__ import annotations

from typing import Any

import polars as pl

URL_STRUCT = pl.Struct(
    [
        pl.Field("short", pl.Utf8),
        pl.Field("expanded", pl.Utf8),
        pl.Field("display", pl.Utf8),
    ]
)

MEDIA_STRUCT = pl.Struct(
    [
        pl.Field("media_id", pl.Utf8),
        pl.Field("media_type", pl.Utf8),
        pl.Field("original_url", pl.Utf8),
        pl.Field("release_asset_url", pl.Utf8),
        pl.Field("sha256", pl.Utf8),
        pl.Field("bytes", pl.Int64),
        pl.Field("duration_sec", pl.Float64),
        pl.Field("width", pl.Int64),
        pl.Field("height", pl.Int64),
        pl.Field("alt_text", pl.Utf8),
        pl.Field("archive_status", pl.Utf8),
        pl.Field("archive_attempts", pl.Int64),
        pl.Field("last_attempt_at", pl.Utf8),
    ]
)

ENGAGEMENT_STRUCT = pl.Struct(
    [
        pl.Field("captured_at", pl.Utf8),
        pl.Field("likes", pl.Int64),
        pl.Field("retweets", pl.Int64),
        pl.Field("replies", pl.Int64),
        pl.Field("quotes", pl.Int64),
        pl.Field("views", pl.Int64),
        pl.Field("bookmarks", pl.Int64),
    ]
)

COMMUNITY_NOTE_STRUCT = pl.Struct(
    [
        pl.Field("note_id", pl.Utf8),
        pl.Field("title", pl.Utf8),
        pl.Field("short_title", pl.Utf8),
        pl.Field("summary", pl.Utf8),
        pl.Field("destination_url", pl.Utf8),
        pl.Field("observed_at", pl.Utf8),
    ]
)

TWEET_SCHEMA: dict[str, Any] = {
    "tweet_id": pl.Utf8,
    "account_handle": pl.Utf8,
    "account_id": pl.Utf8,
    "posted_at": pl.Utf8,
    "first_captured_at": pl.Utf8,
    "last_seen_at": pl.Utf8,
    "deletion_detected_at": pl.Utf8,
    "tweet_url": pl.Utf8,
    "tweet_type": pl.Utf8,
    "reply_to_tweet_id": pl.Utf8,
    "reply_to_account": pl.Utf8,
    "quoted_tweet_id": pl.Utf8,
    "retweeted_tweet_id": pl.Utf8,
    "text": pl.Utf8,
    "text_resolved": pl.Utf8,
    "lang": pl.Utf8,
    "hashtags": pl.List(pl.Utf8),
    "mentions": pl.List(pl.Utf8),
    "urls": pl.List(URL_STRUCT),
    "media": pl.List(MEDIA_STRUCT),
    "like_count": pl.Int64,
    "retweet_count": pl.Int64,
    "reply_count": pl.Int64,
    "quote_count": pl.Int64,
    "view_count": pl.Int64,
    "bookmark_count": pl.Int64,
    "engagement_history": pl.List(ENGAGEMENT_STRUCT),
    "community_note": COMMUNITY_NOTE_STRUCT,
    "is_truncated": pl.Boolean,
    "wayback_url": pl.Utf8,
    "wayback_submitted_at": pl.Utf8,
    "capture_source": pl.Utf8,
    "capture_run_id": pl.Utf8,
    "schema_version": pl.Int64,
}


def empty_dataframe() -> pl.DataFrame:
    """Return an empty DataFrame with the canonical schema."""
    return pl.DataFrame(schema=TWEET_SCHEMA)


REQUIRED_TWEET_KEYS: frozenset[str] = frozenset(
    {
        "tweet_id",
        "account_handle",
        "posted_at",
        "tweet_url",
        "tweet_type",
        "text",
        "schema_version",
    }
)
