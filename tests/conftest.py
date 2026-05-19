"""Shared pytest fixtures for ingest-pipeline tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


def make_tweet(
    tweet_id: str,
    *,
    handle: str = "test-handle",
    posted_at: str = "2025-04-12T14:23:01Z",
    captured_at: str = "2025-04-12T14:25:33Z",
    text: str = "hello world",
    tweet_type: str = "original",
    like_count: int = 1,
    retweet_count: int = 0,
    reply_count: int = 0,
    quote_count: int = 0,
    view_count: int | None = 10,
    media: list[dict[str, Any]] | None = None,
    hashtags: list[str] | None = None,
    mentions: list[str] | None = None,
    urls: list[dict[str, Any]] | None = None,
    lang: str | None = "en",
    reply_to_tweet_id: str | None = None,
    reply_to_account: str | None = None,
    quoted_tweet_id: str | None = None,
    retweeted_tweet_id: str | None = None,
    deletion_detected_at: str | None = None,
) -> dict[str, Any]:
    return {
        "tweet_id": tweet_id,
        "account_handle": handle,
        "account_id": "1234",
        "posted_at": posted_at,
        "first_captured_at": captured_at,
        "last_seen_at": captured_at,
        "deletion_detected_at": deletion_detected_at,
        "tweet_url": f"https://x.com/{handle}/status/{tweet_id}",
        "tweet_type": tweet_type,
        "reply_to_tweet_id": reply_to_tweet_id,
        "reply_to_account": reply_to_account,
        "quoted_tweet_id": quoted_tweet_id,
        "retweeted_tweet_id": retweeted_tweet_id,
        "text": text,
        "text_resolved": text,
        "lang": lang,
        "hashtags": hashtags or [],
        "mentions": mentions or [],
        "urls": urls or [],
        "media": media or [],
        "like_count": like_count,
        "retweet_count": retweet_count,
        "reply_count": reply_count,
        "quote_count": quote_count,
        "view_count": view_count,
        "bookmark_count": None,
        "engagement_history": [
            {
                "captured_at": captured_at,
                "likes": like_count,
                "retweets": retweet_count,
                "replies": reply_count,
                "quotes": quote_count,
                "views": view_count,
                "bookmarks": None,
            }
        ],
        "wayback_url": None,
        "wayback_submitted_at": None,
        "capture_source": "extension",
        "capture_run_id": "TESTRUN0001",
        "schema_version": 1,
    }


def make_media(
    *, media_type: str = "video", media_id: str | None = None, duration_sec: float = 12.0
) -> dict[str, Any]:
    return {
        "media_id": media_id or f"media-{media_type}",
        "media_type": media_type,
        "original_url": f"https://video.twimg.com/{media_id or media_type}.mp4",
        "release_asset_url": None,
        "sha256": None,
        "bytes": None,
        "duration_sec": duration_sec if media_type != "photo" else None,
        "width": 1920,
        "height": 1080,
        "alt_text": "a still from a press conference" if media_type == "photo" else None,
        "archive_status": "pending",
        "archive_attempts": 0,
        "last_attempt_at": None,
    }


def make_capture(tweets: list[dict[str, Any]], run_id: str = "TESTRUN0001") -> dict[str, Any]:
    handle = "test-handle"
    if tweets and isinstance(tweets[0], dict) and isinstance(tweets[0].get("account_handle"), str):
        handle = tweets[0]["account_handle"]
    return {
        "schema_version": 1,
        "capture_run_id": run_id,
        "account_handle": handle,
        "captured_at": "2025-04-12T14:30:00Z",
        "endpoint": "UserTweets",
        "user_agent": "test-runner/1.0",
        "source_url": "https://x.com/test-handle",
        "tweets": tweets,
    }


@pytest.fixture
def tmp_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Set up an isolated repo-shaped tmp dir and re-point the ingest module
    constants at it. Yields the tmp dir as the new REPO_ROOT.
    """
    (tmp_path / "raw").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "seen").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "accounts.yaml").write_text(
        "accounts:\n  - handle: test-handle\n    label: Test Handle\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "# Test\n\n<!-- COVERAGE:START -->\n<!-- COVERAGE:END -->\n",
        encoding="utf-8",
    )

    from scripts import detect_deletions, ingest, update_readme

    monkeypatch.setattr(ingest, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ingest, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(ingest, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(ingest, "CONFIG_PATH", tmp_path / "config" / "accounts.yaml")
    monkeypatch.setattr(ingest, "QUARANTINE_DIR", tmp_path / "raw" / "_quarantine")

    monkeypatch.setattr(detect_deletions, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(detect_deletions, "SEEN_DIR", tmp_path / "seen")
    monkeypatch.setattr(detect_deletions, "DATA_DIR", tmp_path / "data")

    monkeypatch.setattr(update_readme, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(update_readme, "MANIFEST_PATH", tmp_path / "data" / "manifest.json")
    monkeypatch.setattr(update_readme, "README_PATH", tmp_path / "README.md")

    yield tmp_path


def write_capture(repo: Path, handle: str, name: str, capture: dict[str, Any]) -> Path:
    d = repo / "raw" / handle
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    path.write_text(json.dumps(capture, indent=2), encoding="utf-8")
    return path


def write_seen(repo: Path, handle: str, name: str, payload: dict[str, Any]) -> Path:
    d = repo / "seen" / handle
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
