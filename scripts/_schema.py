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

USER_SNAPSHOT_STRUCT = pl.Struct(
    [
        pl.Field("display_name", pl.Utf8),
        pl.Field("avatar_url", pl.Utf8),
        pl.Field("verified", pl.Boolean),
        pl.Field("is_blue_verified", pl.Boolean),
        pl.Field("verified_type", pl.Utf8),
        pl.Field("description", pl.Utf8),
        pl.Field("location", pl.Utf8),
        pl.Field("url", pl.Utf8),
        pl.Field("followers_count", pl.Int64),
        pl.Field("friends_count", pl.Int64),
        pl.Field("statuses_count", pl.Int64),
        pl.Field("account_created_at", pl.Utf8),
        pl.Field("protected", pl.Boolean),
    ]
)

CARD_STRUCT = pl.Struct(
    [
        pl.Field("name", pl.Utf8),
        pl.Field("card_url", pl.Utf8),
        pl.Field("vendor_url", pl.Utf8),
        pl.Field("title", pl.Utf8),
        pl.Field("description", pl.Utf8),
        pl.Field("image_url", pl.Utf8),
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
    "unavailable_detected_at": pl.Utf8,
    "unavailable_reason": pl.Utf8,
    "unavailable_text": pl.Utf8,
    "unavailable_source_url": pl.Utf8,
    "tweet_url": pl.Utf8,
    "tweet_type": pl.Utf8,
    "conversation_id": pl.Utf8,
    "reply_to_tweet_id": pl.Utf8,
    "reply_to_account": pl.Utf8,
    "reply_to_account_id": pl.Utf8,
    "quoted_tweet_id": pl.Utf8,
    "retweeted_tweet_id": pl.Utf8,
    "text": pl.Utf8,
    "text_resolved": pl.Utf8,
    "lang": pl.Utf8,
    "possibly_sensitive": pl.Boolean,
    "source": pl.Utf8,
    "place_full_name": pl.Utf8,
    "hashtags": pl.List(pl.Utf8),
    "mentions": pl.List(pl.Utf8),
    "urls": pl.List(URL_STRUCT),
    "card": CARD_STRUCT,
    "media": pl.List(MEDIA_STRUCT),
    "like_count": pl.Int64,
    "retweet_count": pl.Int64,
    "reply_count": pl.Int64,
    "quote_count": pl.Int64,
    "view_count": pl.Int64,
    "bookmark_count": pl.Int64,
    "engagement_history": pl.List(ENGAGEMENT_STRUCT),
    "author": USER_SNAPSHOT_STRUCT,
    "community_note": COMMUNITY_NOTE_STRUCT,
    "is_truncated": pl.Boolean,
    "wayback_url": pl.Utf8,
    "wayback_submitted_at": pl.Utf8,
    "capture_source": pl.Utf8,
    "capture_run_id": pl.Utf8,
    "schema_version": pl.Int64,
}

RETWEET_EDGE_SCHEMA: dict[str, Any] = {
    "retweeter_handle": pl.Utf8,
    "retweeter_account_id": pl.Utf8,
    "retweeter_category": pl.Utf8,
    "retweet_tweet_id": pl.Utf8,
    "retweet_url": pl.Utf8,
    "original_tweet_id": pl.Utf8,
    "original_author_handle": pl.Utf8,
    "original_author_account_id": pl.Utf8,
    "original_author_category": pl.Utf8,
    "first_captured_at": pl.Utf8,
    "last_seen_at": pl.Utf8,
    "seen_count": pl.Int64,
    "capture_run_ids": pl.List(pl.Utf8),
    "endpoints": pl.List(pl.Utf8),
    "source_urls": pl.List(pl.Utf8),
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


# --------------------------------------------------------------------------
# Sidecar tag parquets
#
# Tag layers (lexical regex, CLIP image labels, OCR, etc.) write to
# separate files under `data/tags/`, keyed by tweet_id (or media_id
# for image layers). The canonical tweet parquets are never modified
# by the taggers — that's the "Capture honestly" principle. Viewers
# join the sidecars in on tweet_id at load time.

TAG_ENTRY_STRUCT = pl.Struct(
    [
        pl.Field("tag", pl.Utf8),
        # Set when the tagger was uncertain (low-confidence regex match,
        # vision-model output below threshold, etc.). Omitted / null on
        # confirmed tags. Renders as a dashed pill in the viewer and
        # invites a suggestion via GitHub Discussions.
        pl.Field("tentative", pl.Boolean),
        # Where the tag came from. One of:
        #   "auto"       — written by an auto-tagger script
        #   "human"      — applied by an editor with PAT write access
        #   "suggestion" — accepted from a GitHub-Discussion suggestion
        pl.Field("source", pl.Utf8),
        # Character offsets in the tweet's `text_resolved` (falling back
        # to `text`) where the rule matched. Null for tags that aren't
        # tied to a specific span (composite tags, structural tags from
        # tweet_type, etc.). Useful for highlighting in the viewer.
        pl.Field("span_start", pl.Int64),
        pl.Field("span_end", pl.Int64),
    ]
)

LEXICAL_TAG_SCHEMA: dict[str, Any] = {
    "tweet_id": pl.Utf8,
    "account_handle": pl.Utf8,
    "tagger_version": pl.Utf8,
    "tagged_at": pl.Utf8,
    "tags": pl.List(TAG_ENTRY_STRUCT),
}


def empty_lexical_tag_dataframe() -> pl.DataFrame:
    """Return an empty DataFrame with the lexical-tag sidecar schema."""
    return pl.DataFrame(schema=LEXICAL_TAG_SCHEMA)


MEDIA_VISION_SCHEMA: dict[str, Any] = {
    "tweet_id": pl.Utf8,
    "account_handle": pl.Utf8,
    "media_id": pl.Utf8,
    "media_type": pl.Utf8,
    "media_sha256": pl.Utf8,
    "input_hash": pl.Utf8,
    "generated_at": pl.Utf8,
    "model": pl.Utf8,
    "model_version": pl.Utf8,
    "prompt_hash": pl.Utf8,
    "description": pl.Utf8,
    "summary_text": pl.Utf8,
    "confidence": pl.Float64,
    "cost_estimate_usd": pl.Float64,
    "status": pl.Utf8,
    "tags": pl.List(TAG_ENTRY_STRUCT),
    "source_fields": pl.List(pl.Utf8),
    "error": pl.Utf8,
}


def empty_media_vision_dataframe() -> pl.DataFrame:
    """Return an empty DataFrame with the media-recognition sidecar schema."""
    return pl.DataFrame(schema=MEDIA_VISION_SCHEMA)


# --------------------------------------------------------------------------
# Image OCR sidecar (Layer 3b)
#
# One row per OCR attempt over archived still images and extracted video
# keyframes. The lexical tagger concatenates rows by tweet_id and treats the
# recovered text as another deterministic text source.

IMAGE_OCR_SCHEMA: dict[str, Any] = {
    "tweet_id": pl.Utf8,
    "account_handle": pl.Utf8,
    "media_id": pl.Utf8,
    "media_type": pl.Utf8,
    "media_sha256": pl.Utf8,
    "source_kind": pl.Utf8,
    "source_path": pl.Utf8,
    "input_hash": pl.Utf8,
    "ocr_engine": pl.Utf8,
    "ocr_version": pl.Utf8,
    "ocr_at": pl.Utf8,
    "text": pl.Utf8,
    "confidence": pl.Float64,
    "status": pl.Utf8,
    "cost_estimate_usd": pl.Float64,
    "error": pl.Utf8,
}


def empty_image_ocr_dataframe() -> pl.DataFrame:
    """Return an empty DataFrame with the image-OCR sidecar schema."""
    return pl.DataFrame(schema=IMAGE_OCR_SCHEMA)


# --------------------------------------------------------------------------
# Audio sidecar (Layer 3a)
#
# One row per archived video / animated-gif media item. This is a cheap,
# ffmpeg-only classifier boundary: it records whether an audio stream exists
# and a conservative music-likelihood score without downloading any model.

AUDIO_MUSIC_SCHEMA: dict[str, Any] = {
    "tweet_id": pl.Utf8,
    "account_handle": pl.Utf8,
    "media_id": pl.Utf8,
    "media_type": pl.Utf8,
    "media_sha256": pl.Utf8,
    "release_asset_url": pl.Utf8,
    "input_hash": pl.Utf8,
    "generated_at": pl.Utf8,
    "detector": pl.Utf8,
    "detector_version": pl.Utf8,
    "audio_duration_sec": pl.Float64,
    "sample_duration_sec": pl.Float64,
    "audio_stream_count": pl.Int64,
    "codec": pl.Utf8,
    "channels": pl.Int64,
    "sample_rate": pl.Int64,
    "music_score": pl.Float64,
    "speech_score": pl.Float64,
    "non_silent_ratio": pl.Float64,
    "zero_crossing_rate": pl.Float64,
    "rms_mean": pl.Float64,
    "rms_variance": pl.Float64,
    "status": pl.Utf8,
    "tags": pl.List(TAG_ENTRY_STRUCT),
    "features_json": pl.Utf8,
    "cost_estimate_usd": pl.Float64,
    "error": pl.Utf8,
}


def empty_audio_music_dataframe() -> pl.DataFrame:
    """Return an empty DataFrame with the audio-recognition sidecar schema."""
    return pl.DataFrame(schema=AUDIO_MUSIC_SCHEMA)


# --------------------------------------------------------------------------
# News-mentions sidecar
#
# One row per core tweet scanned against a deterministic, local news-corpus
# export. The script never calls a search API during normal operation or tests:
# callers provide an article JSON/JSONL/CSV file, and exact status-URL matches
# become `news:*` tags the viewer can merge like other sidecar tags.

NEWS_ARTICLE_MENTION_STRUCT = pl.Struct(
    [
        pl.Field("source", pl.Utf8),
        pl.Field("title", pl.Utf8),
        pl.Field("url", pl.Utf8),
        pl.Field("published_at", pl.Utf8),
        pl.Field("match_type", pl.Utf8),
        pl.Field("matched_fields", pl.List(pl.Utf8)),
        pl.Field("matched_terms", pl.List(pl.Utf8)),
        pl.Field("confidence", pl.Float64),
        pl.Field("confirmed", pl.Boolean),
    ]
)

NEWS_MENTIONS_SCHEMA: dict[str, Any] = {
    "tweet_id": pl.Utf8,
    "account_handle": pl.Utf8,
    "tweet_url": pl.Utf8,
    "posted_at": pl.Utf8,
    "input_hash": pl.Utf8,
    "generated_at": pl.Utf8,
    "detector": pl.Utf8,
    "detector_version": pl.Utf8,
    "mention_count": pl.Int64,
    "articles": pl.List(NEWS_ARTICLE_MENTION_STRUCT),
    "status": pl.Utf8,
    "tags": pl.List(TAG_ENTRY_STRUCT),
    "cost_estimate_usd": pl.Float64,
    "error": pl.Utf8,
}


def empty_news_mentions_dataframe() -> pl.DataFrame:
    """Return an empty DataFrame with the news-mentions sidecar schema."""
    return pl.DataFrame(schema=NEWS_MENTIONS_SCHEMA)


# --------------------------------------------------------------------------
# Keyframe sidecar (Layer 2)
#
# One row per archived video. Records the metadata of evenly-spaced
# keyframes extracted by ffmpeg for downstream OCR / CLIP / vision-LLM
# layers to consume. The frame JPEGs themselves live under
# `data/derived/keyframes/<media_sha256>/` and are gitignored — they are
# deterministic from the archived video + the extractor version + frame
# indices, and downstream layers re-extract on demand within the same CI
# run if the derived dir was cleared.
#
# The sidecar is the cache boundary: a row's presence with `status == "ok"`
# and the right `extractor_version` lets layers 3a/3b/4 skip re-extraction
# (or, if the derived dir is missing, drive a re-run by sha256). Each frame
# carries its own sha256 so OCR / CLIP layers can key their own caches off
# the frame bytes, not the parent video.

KEYFRAME_STRUCT = pl.Struct(
    [
        pl.Field("index", pl.Int64),
        pl.Field("timestamp_sec", pl.Float64),
        pl.Field("path", pl.Utf8),
        pl.Field("sha256", pl.Utf8),
        pl.Field("width", pl.Int64),
        pl.Field("height", pl.Int64),
        pl.Field("bytes", pl.Int64),
    ]
)

KEYFRAMES_SCHEMA: dict[str, Any] = {
    "tweet_id": pl.Utf8,
    "account_handle": pl.Utf8,
    "media_id": pl.Utf8,
    "media_sha256": pl.Utf8,
    "release_asset_url": pl.Utf8,
    "thumbnail_path": pl.Utf8,
    "thumbnail_sha256": pl.Utf8,
    "thumbnail_width": pl.Int64,
    "thumbnail_height": pl.Int64,
    "thumbnail_bytes": pl.Int64,
    "video_duration_sec": pl.Float64,
    "video_width": pl.Int64,
    "video_height": pl.Int64,
    "frame_count": pl.Int64,
    "frames": pl.List(KEYFRAME_STRUCT),
    "generated_at": pl.Utf8,
    "extractor_version": pl.Utf8,
    "status": pl.Utf8,
    "cost_estimate_usd": pl.Float64,
    "error": pl.Utf8,
}


def empty_keyframes_dataframe() -> pl.DataFrame:
    """Return an empty DataFrame with the keyframe sidecar schema."""
    return pl.DataFrame(schema=KEYFRAMES_SCHEMA)
