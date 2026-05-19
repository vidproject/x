"""Tests for ``scripts/detect_deletions.py``.

Deletion detection is deliberately conservative:
  * needs the last 3 seen-set files
  * each set must have at least 50 ids (else it's treated as a partial scan)
  * 7-day grace period from last_seen_at
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from scripts import detect_deletions, ingest
from tests.conftest import make_capture, make_tweet, write_capture, write_seen


def _seed_parquet(repo: Path, last_seen: str) -> None:
    tweets = [
        make_tweet(
            f"tw-{i:03d}",
            captured_at=last_seen,
            posted_at="2025-01-01T00:00:00Z",
        )
        for i in range(80)
    ]
    for t in tweets:
        t["last_seen_at"] = last_seen
        t["first_captured_at"] = last_seen
    write_capture(repo, "test-handle", "seed.json", make_capture(tweets))
    ingest.main([])


def _seen(repo: Path, name: str, ts: str, ids: list[str]) -> None:
    write_seen(
        repo,
        "test-handle",
        name,
        {
            "schema_version": 1,
            "capture_run_id": name,
            "account_handle": "test-handle",
            "captured_at": ts,
            "tweet_ids_observed": ids,
        },
    )


def test_no_deletion_when_tweet_still_in_recent_scan(tmp_repo: Path) -> None:
    _seed_parquet(tmp_repo, last_seen="2025-04-01T10:00:00Z")
    ids = [f"tw-{i:03d}" for i in range(80)]
    for i, ts in enumerate(
        ["2025-04-15T10:00:00Z", "2025-04-16T10:00:00Z", "2025-04-17T10:00:00Z"]
    ):
        _seen(tmp_repo, f"r{i}.json", ts, ids)
    now = datetime(2025, 4, 18, tzinfo=UTC)
    detect_deletions.mark_deletions("test-handle", now=now)
    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet")
    assert df.filter(pl.col("deletion_detected_at").is_not_null()).height == 0


def test_deletion_marked_after_grace_period(tmp_repo: Path) -> None:
    _seed_parquet(tmp_repo, last_seen="2025-04-01T10:00:00Z")
    survivor_ids = [f"tw-{i:03d}" for i in range(80) if i != 5]
    # Pad each seen set above the MIN_SCAN_SIZE threshold.
    pad = [f"pad-{j:04d}" for j in range(80)]
    for i, ts in enumerate(
        ["2025-04-15T10:00:00Z", "2025-04-16T10:00:00Z", "2025-04-17T10:00:00Z"]
    ):
        _seen(tmp_repo, f"r{i}.json", ts, survivor_ids + pad)
    now = datetime(2025, 4, 18, tzinfo=UTC)
    detect_deletions.mark_deletions("test-handle", now=now)
    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet")
    marked = df.filter(pl.col("deletion_detected_at").is_not_null())
    assert marked.height == 1
    assert marked["tweet_id"][0] == "tw-005"


def test_partial_scans_do_not_trigger_deletion(tmp_repo: Path) -> None:
    _seed_parquet(tmp_repo, last_seen="2025-04-01T10:00:00Z")
    # Three undersized scans (10 ids each) — should not be enough to declare
    # deletion.
    survivor_ids = [f"tw-{i:03d}" for i in range(10)]
    for i, ts in enumerate(
        ["2025-04-15T10:00:00Z", "2025-04-16T10:00:00Z", "2025-04-17T10:00:00Z"]
    ):
        _seen(tmp_repo, f"r{i}.json", ts, survivor_ids)
    now = datetime(2025, 4, 18, tzinfo=UTC)
    detect_deletions.mark_deletions("test-handle", now=now)
    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet")
    assert df.filter(pl.col("deletion_detected_at").is_not_null()).height == 0


def test_within_grace_period_no_deletion(tmp_repo: Path) -> None:
    _seed_parquet(tmp_repo, last_seen="2025-04-17T08:00:00Z")
    survivor_ids = [f"tw-{i:03d}" for i in range(80) if i != 7]
    pad = [f"pad-{j:04d}" for j in range(80)]
    for i, ts in enumerate(
        ["2025-04-17T10:00:00Z", "2025-04-18T10:00:00Z", "2025-04-19T10:00:00Z"]
    ):
        _seen(tmp_repo, f"r{i}.json", ts, survivor_ids + pad)
    now = datetime(2025, 4, 19, 12, tzinfo=UTC)
    detect_deletions.mark_deletions("test-handle", now=now)
    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet")
    # tw-007 is missing but only 2 days have passed → still within grace.
    assert df.filter(pl.col("deletion_detected_at").is_not_null()).height == 0
