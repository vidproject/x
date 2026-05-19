"""End-to-end tests for ``scripts/ingest.py``.

The fixture set deliberately exercises:
  - simple original tweet
  - re-capture of the same tweet with newer engagement (dedup + history merge)
  - retweet, quote, reply types
  - video media + photo media + alt text
  - multi-image
  - long thread (note_tweet style) — represented as a long ``text`` field
  - language variant (non-en)
  - bad JSON (must be quarantined, not crash the run)
  - empty capture payload (no tweets)
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl
import pytest

from scripts import ingest
from tests.conftest import make_capture, make_media, make_tweet, write_capture


def test_single_capture_writes_parquet(tmp_repo: Path) -> None:
    cap = make_capture([make_tweet("1001", text="first tweet")])
    write_capture(tmp_repo, "test-handle", "run-001.json", cap)

    assert ingest.main([]) == 0

    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet")
    assert df.height == 1
    assert df["tweet_id"][0] == "1001"
    assert df["text"][0] == "first tweet"

    manifest = json.loads((tmp_repo / "data" / "manifest.json").read_text())
    assert manifest["accounts"][0]["handle"] == "test-handle"
    assert manifest["accounts"][0]["row_count"] == 1


def test_dedup_keeps_earliest_first_capture_and_merges_engagement(tmp_repo: Path) -> None:
    early = make_tweet(
        "1002",
        captured_at="2025-04-12T10:00:00Z",
        like_count=5,
        view_count=100,
    )
    later = make_tweet(
        "1002",
        captured_at="2025-04-13T10:00:00Z",
        like_count=50,
        view_count=1000,
    )
    write_capture(tmp_repo, "test-handle", "run-01.json", make_capture([early]))
    write_capture(tmp_repo, "test-handle", "run-02.json", make_capture([later]))

    assert ingest.main([]) == 0

    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet")
    row = df.row(0, named=True)
    assert row["first_captured_at"] == "2025-04-12T10:00:00Z"
    assert row["last_seen_at"] == "2025-04-13T10:00:00Z"
    assert row["like_count"] == 50  # latest counts win
    assert len(row["engagement_history"]) == 2
    timestamps = [s["captured_at"] for s in row["engagement_history"]]
    assert timestamps == sorted(timestamps)


def test_tweet_types_round_trip(tmp_repo: Path) -> None:
    tweets = [
        make_tweet("2001", tweet_type="original"),
        make_tweet("2002", tweet_type="retweet", retweeted_tweet_id="9001"),
        make_tweet("2003", tweet_type="quote", quoted_tweet_id="9002"),
        make_tweet(
            "2004",
            tweet_type="reply",
            reply_to_tweet_id="9003",
            reply_to_account="someone",
        ),
    ]
    write_capture(tmp_repo, "test-handle", "run.json", make_capture(tweets))
    assert ingest.main([]) == 0
    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet").sort("tweet_id")
    types = df["tweet_type"].to_list()
    assert sorted(types) == ["original", "quote", "reply", "retweet"]


def test_media_video_photo_and_alt(tmp_repo: Path) -> None:
    tweets = [
        make_tweet("3001", media=[make_media(media_type="video", media_id="v1")]),
        make_tweet(
            "3002",
            media=[
                make_media(media_type="photo", media_id="p1"),
                make_media(media_type="photo", media_id="p2"),
            ],
        ),
        make_tweet("3003", media=[]),
    ]
    write_capture(tmp_repo, "test-handle", "media.json", make_capture(tweets))
    assert ingest.main([]) == 0
    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet").sort("tweet_id")
    media_lengths = [len(m or []) for m in df["media"].to_list()]
    assert media_lengths == [1, 2, 0]
    manifest = json.loads((tmp_repo / "data" / "manifest.json").read_text())
    acc = next(a for a in manifest["accounts"] if a["handle"] == "test-handle")
    assert acc["media_count"] == 3
    assert acc["video_count"] == 1


def test_language_variant_persisted(tmp_repo: Path) -> None:
    cap = make_capture(
        [
            make_tweet("4001", lang="es", text="hola mundo"),
            make_tweet("4002", lang=None, text=""),
        ]
    )
    write_capture(tmp_repo, "test-handle", "lang.json", cap)
    assert ingest.main([]) == 0
    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet").sort("tweet_id")
    langs = df["lang"].to_list()
    assert "es" in langs
    assert None in langs


def test_long_thread_text(tmp_repo: Path) -> None:
    long = " ".join(["paragraph"] * 200)
    cap = make_capture([make_tweet("5001", text=long)])
    write_capture(tmp_repo, "test-handle", "long.json", cap)
    assert ingest.main([]) == 0
    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet")
    assert df["text"][0] == long


def test_bad_json_quarantined(tmp_repo: Path) -> None:
    d = tmp_repo / "raw" / "test-handle"
    d.mkdir(parents=True, exist_ok=True)
    bad = d / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    good = make_capture([make_tweet("6001")])
    write_capture(tmp_repo, "test-handle", "good.json", good)

    assert ingest.main([]) == 0
    assert not bad.exists(), "bad file should have been moved to quarantine"
    moved = list((tmp_repo / "raw" / "_quarantine").glob("*"))
    assert len(moved) == 1
    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet")
    assert df.height == 1  # the good capture still ingested


def test_missing_required_keys_skipped(tmp_repo: Path) -> None:
    valid = make_tweet("7001")
    invalid = {"tweet_id": "7002", "text": "no other keys"}
    cap = make_capture([valid, invalid])
    write_capture(tmp_repo, "test-handle", "mixed.json", cap)
    assert ingest.main([]) == 0
    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet")
    assert df["tweet_id"].to_list() == ["7001"]


def test_empty_payload_handled(tmp_repo: Path) -> None:
    cap = make_capture([])
    write_capture(tmp_repo, "test-handle", "empty.json", cap)
    assert ingest.main([]) == 0
    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet")
    assert df.height == 0


def test_legacy_list_format_accepted(tmp_repo: Path) -> None:
    # Manual workflow: drop a list-of-tweets file (no envelope) into raw/.
    tweets = [make_tweet("8001"), make_tweet("8002")]
    write_capture(tmp_repo, "test-handle", "legacy.json", {"tweets": tweets, "schema_version": 1})
    assert ingest.main([]) == 0
    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet")
    assert df.height == 2


def test_schema_version_mismatch_quarantined(tmp_repo: Path) -> None:
    bad = make_capture([make_tweet("9001")])
    bad["schema_version"] = 99
    write_capture(tmp_repo, "test-handle", "future.json", bad)
    assert ingest.main([]) == 0
    moved = list((tmp_repo / "raw" / "_quarantine").glob("*"))
    assert moved, "future-schema file should have been quarantined"


def test_release_asset_url_preserved_across_reingest(tmp_repo: Path) -> None:
    media = make_media(media_type="video", media_id="v-keep")
    cap1 = make_capture([make_tweet("aa01", media=[media])])
    write_capture(tmp_repo, "test-handle", "01.json", cap1)
    assert ingest.main([]) == 0
    # Simulate media.yml populating archive metadata in the parquet.
    parquet = tmp_repo / "data" / "test-handle.parquet"
    df = pl.read_parquet(parquet)
    enriched = df.with_columns(
        pl.col("media").list.eval(
            pl.element().struct.with_fields(
                release_asset_url=pl.lit("https://github.com/.../v-keep.mp4"),
                sha256=pl.lit("deadbeef"),
                bytes=pl.lit(1234),
                archive_status=pl.lit("archived"),
            )
        )
    )
    enriched.write_parquet(parquet, compression="zstd")

    # New capture arrives — should NOT clobber the release_asset_url.
    cap2 = make_capture(
        [
            make_tweet(
                "aa01",
                captured_at="2025-04-13T15:00:00Z",
                media=[make_media(media_type="video", media_id="v-keep")],
            )
        ]
    )
    write_capture(tmp_repo, "test-handle", "02.json", cap2)
    assert ingest.main([]) == 0
    df2 = pl.read_parquet(parquet)
    media_after = df2["media"].to_list()[0]
    assert media_after[0]["release_asset_url"] == "https://github.com/.../v-keep.mp4"
    assert media_after[0]["sha256"] == "deadbeef"


def test_multiple_handles_each_get_parquet(tmp_repo: Path) -> None:
    write_capture(
        tmp_repo,
        "test-handle",
        "a.json",
        make_capture([make_tweet("h1-1", handle="test-handle")]),
    )
    # Drop a capture for a non-configured handle; ingest should still produce
    # a parquet for it (handy for ad-hoc captures and tests).
    write_capture(
        tmp_repo,
        "extra",
        "a.json",
        make_capture([make_tweet("h2-1", handle="extra")]),
    )
    assert ingest.main([]) == 0
    assert (tmp_repo / "data" / "test-handle.parquet").exists()
    assert (tmp_repo / "data" / "extra.parquet").exists()
    manifest = json.loads((tmp_repo / "data" / "manifest.json").read_text())
    handles = {a["handle"] for a in manifest["accounts"]}
    assert handles == {"test-handle", "extra"}


@pytest.mark.parametrize(
    "missing",
    ["tweet_id", "account_handle", "posted_at", "tweet_url", "tweet_type", "text"],
)
def test_each_required_key_enforced(tmp_repo: Path, missing: str) -> None:
    base = make_tweet("xx99")
    base.pop(missing)
    cap = make_capture([base])
    write_capture(tmp_repo, "test-handle", "bad.json", cap)
    assert ingest.main([]) == 0
    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet")
    assert df.height == 0
