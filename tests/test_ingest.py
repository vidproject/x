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


def test_archive_status_not_clobbered_by_pending_re_ingest(tmp_repo: Path) -> None:
    """Regression: the archive workflow writes archive_status='archived'
    into the parquet; the next extension capture re-supplies the same
    media_id with archive_status='pending'. The merge must keep 'archived'
    (anchored on `release_asset_url` presence) — otherwise the archive
    workflow re-uploads the same bytes on every run."""
    media_v1 = make_media(media_type="video", media_id="vid-1")
    write_capture(
        tmp_repo,
        "test-handle",
        "01.json",
        make_capture([make_tweet("a1", media=[media_v1])]),
    )
    assert ingest.main([]) == 0

    # Simulate archive workflow populating the row.
    parquet = tmp_repo / "data" / "test-handle.parquet"
    df = pl.read_parquet(parquet)
    enriched = df.with_columns(
        pl.col("media").list.eval(
            pl.element().struct.with_fields(
                release_asset_url=pl.lit("https://github.com/.../vid-1.mp4"),
                sha256=pl.lit("deadbeef"),
                bytes=pl.lit(987_654),
                archive_status=pl.lit("archived"),
                archive_attempts=pl.lit(1),
                last_attempt_at=pl.lit("2025-04-12T15:00:00Z"),
            )
        )
    )
    enriched.write_parquet(parquet, compression="zstd")

    # Extension re-captures the same tweet — its media struct has
    # release_asset_url=None and archive_status='pending'.
    write_capture(
        tmp_repo,
        "test-handle",
        "02.json",
        make_capture(
            [
                make_tweet(
                    "a1",
                    captured_at="2025-04-13T15:00:00Z",
                    media=[make_media(media_type="video", media_id="vid-1")],
                )
            ]
        ),
    )
    assert ingest.main([]) == 0

    df_after = pl.read_parquet(parquet)
    m = df_after["media"].to_list()[0][0]
    assert m["release_asset_url"] == "https://github.com/.../vid-1.mp4"
    assert m["sha256"] == "deadbeef"
    assert m["archive_status"] == "archived", (
        f"expected 'archived' to survive re-ingest, got {m['archive_status']!r}"
    )
    assert m["archive_attempts"] == 1


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


def test_tracked_handle_gets_own_parquet_non_tracked_consolidates_into_misc(
    tmp_repo: Path,
) -> None:
    write_capture(
        tmp_repo,
        "test-handle",
        "a.json",
        make_capture([make_tweet("h1-1", handle="test-handle")]),
    )
    # A non-configured handle's capture should be folded into _misc.parquet
    # rather than getting its own per-handle file. Two non-tracked handles
    # to confirm they share the same bucket.
    write_capture(
        tmp_repo,
        "extra",
        "a.json",
        make_capture([make_tweet("h2-1", handle="extra", text="reply A")]),
    )
    write_capture(
        tmp_repo,
        "another",
        "a.json",
        make_capture([make_tweet("h3-1", handle="another", text="reply B")]),
    )
    assert ingest.main([]) == 0
    assert (tmp_repo / "data" / "test-handle.parquet").exists()
    assert not (tmp_repo / "data" / "extra.parquet").exists()
    assert not (tmp_repo / "data" / "another.parquet").exists()
    misc = pl.read_parquet(tmp_repo / "data" / "_misc.parquet")
    assert misc.height == 2
    assert set(misc["account_handle"].to_list()) == {"extra", "another"}
    manifest = json.loads((tmp_repo / "data" / "manifest.json").read_text())
    handles = {a["handle"] for a in manifest["accounts"]}
    assert handles == {"test-handle", "_misc"}


def test_handle_starting_with_underscore_is_ingested_not_dropped(
    tmp_repo: Path,
) -> None:
    """X usernames may begin with an underscore (e.g. ``_aktrades``). The
    raw-directory walk used to skip anything whose name started with ``_``,
    which silently dropped every tweet from such handles. Sentinel dirs
    (``_quarantine``, ``_purged``) are matched by exact name now, not by
    prefix, so real handles get ingested into ``_misc.parquet`` like any
    other non-tracked author."""
    write_capture(
        tmp_repo,
        "test-handle",
        "a.json",
        make_capture([make_tweet("tracked-1", handle="test-handle")]),
    )
    write_capture(
        tmp_repo,
        "_aktrades",
        "a.json",
        make_capture([make_tweet("under-1", handle="_aktrades", text="reply from _aktrades")]),
    )
    # A directory that genuinely is a sentinel must still be skipped.
    (tmp_repo / "raw" / "_quarantine").mkdir()

    assert ingest.main([]) == 0
    misc = pl.read_parquet(tmp_repo / "data" / "_misc.parquet")
    assert misc.height == 1
    assert misc["account_handle"][0] == "_aktrades"
    assert misc["tweet_id"][0] == "under-1"
    # No per-handle parquet for the underscore author — it consolidates into _misc.
    assert not (tmp_repo / "data" / "_aktrades.parquet").exists()


def test_legacy_per_handle_parquet_for_untracked_is_collapsed_into_misc(
    tmp_repo: Path,
) -> None:
    """Once the consolidation lands, a stale per-handle parquet for a
    handle that's not in accounts.yaml gets folded into _misc on the next
    ingest. Confirms data is preserved across the migration."""
    # Pre-seed: build an "extra.parquet" the old layout would have produced.
    write_capture(
        tmp_repo,
        "extra",
        "a.json",
        make_capture([make_tweet("legacy-1", handle="extra", text="from legacy parquet")]),
    )
    assert ingest.main([]) == 0
    # First run already routes it to _misc since the new code path doesn't
    # write per-handle files for untracked authors. Hand-create the legacy
    # parquet to simulate a repo state from before this change.
    legacy_path = tmp_repo / "data" / "extra.parquet"
    (tmp_repo / "raw" / "extra").rename(tmp_repo / "raw" / "_extra-stash")
    misc_df = pl.read_parquet(tmp_repo / "data" / "_misc.parquet")
    misc_df.write_parquet(legacy_path, compression="zstd")
    (tmp_repo / "data" / "_misc.parquet").unlink()
    # Restore raw dir for the next ingest to find.
    (tmp_repo / "raw" / "_extra-stash").rename(tmp_repo / "raw" / "extra")

    # Now run ingest again — the legacy extra.parquet should get consolidated.
    assert ingest.main([]) == 0
    assert not legacy_path.exists(), "legacy untracked parquet should have been removed"
    misc = pl.read_parquet(tmp_repo / "data" / "_misc.parquet")
    assert misc.height == 1
    assert misc["account_handle"][0] == "extra"
    assert misc["text"][0] == "from legacy parquet"


def test_promoting_handle_to_tracked_migrates_rows_out_of_misc(tmp_repo: Path) -> None:
    """When an account is added to accounts.yaml, its previously-misc rows
    should migrate to its own parquet (with archive metadata intact)."""
    # First run: "extra" is not tracked, lands in _misc.
    write_capture(
        tmp_repo,
        "extra",
        "a.json",
        make_capture([make_tweet("e1", handle="extra", text="first sighting")]),
    )
    assert ingest.main([]) == 0
    misc = pl.read_parquet(tmp_repo / "data" / "_misc.parquet")
    assert misc.height == 1

    # Promote: add "extra" to accounts.yaml.
    (tmp_repo / "config" / "accounts.yaml").write_text(
        "accounts:\n"
        "  - handle: test-handle\n    label: Test Handle\n"
        "  - handle: extra\n    label: Promoted Account\n",
        encoding="utf-8",
    )
    assert ingest.main([]) == 0
    assert (tmp_repo / "data" / "extra.parquet").exists()
    extra_df = pl.read_parquet(tmp_repo / "data" / "extra.parquet")
    assert extra_df.height == 1
    assert extra_df["text"][0] == "first sighting"
    # _misc should no longer contain the migrated handle's rows.
    misc_after = (
        pl.read_parquet(tmp_repo / "data" / "_misc.parquet")
        if (tmp_repo / "data" / "_misc.parquet").exists()
        else None
    )
    assert misc_after is None or "extra" not in misc_after["account_handle"].to_list()


def test_media_never_dropped_when_later_payload_returns_fewer(tmp_repo: Path) -> None:
    """An earlier capture had a 4-photo tweet; a later capture returns
    just 1 photo (X stripped some media after rate-limit churn). The merged
    parquet must still contain all 4 media_ids — re-captures are append-only
    for media so the archive can't be silently shrunken by X."""
    photos_full = [make_media(media_type="photo", media_id=f"p{i}") for i in range(4)]
    cap_full = make_capture(
        [make_tweet("media01", captured_at="2025-04-12T10:00:00Z", media=photos_full)]
    )
    write_capture(tmp_repo, "test-handle", "01-full.json", cap_full)
    assert ingest.main([]) == 0

    cap_partial = make_capture(
        [
            make_tweet(
                "media01",
                captured_at="2025-04-13T10:00:00Z",
                media=[make_media(media_type="photo", media_id="p0")],
            )
        ]
    )
    write_capture(tmp_repo, "test-handle", "02-partial.json", cap_partial)
    assert ingest.main([]) == 0

    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet")
    media = df["media"].to_list()[0]
    media_ids = sorted(m["media_id"] for m in media)
    assert media_ids == ["p0", "p1", "p2", "p3"]


def test_refetched_full_text_replaces_truncated(tmp_repo: Path) -> None:
    """First capture is the 280-char head of a long tweet (is_truncated=True);
    refetch via TweetDetail returns the full body (is_truncated=False). The
    merged row must hold the full body and clear the truncation flag."""
    head = "Lorem ipsum dolor sit amet… https://t.co/abc"
    full = "Lorem ipsum dolor sit amet, consectetur adipiscing elit, " * 10
    cap_head = make_capture(
        [
            make_tweet(
                "trunc01",
                captured_at="2025-04-12T10:00:00Z",
                text=head,
                is_truncated=True,
            )
        ]
    )
    cap_full = make_capture(
        [
            make_tweet(
                "trunc01",
                captured_at="2025-04-13T10:00:00Z",
                text=full,
                is_truncated=False,
            )
        ]
    )
    write_capture(tmp_repo, "test-handle", "01.json", cap_head)
    write_capture(tmp_repo, "test-handle", "02.json", cap_full)
    assert ingest.main([]) == 0

    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet")
    row = df.row(0, named=True)
    assert row["text"] == full
    assert row["is_truncated"] is False


def test_unavailable_event_marks_existing_row(tmp_repo: Path) -> None:
    write_capture(
        tmp_repo,
        "test-handle",
        "01.json",
        make_capture([make_tweet("gone01", text="original body")]),
    )
    unavailable = make_capture([])
    unavailable["unavailable_tweets"] = [
        {
            "tweet_id": "gone01",
            "account_handle": "test-handle",
            "unavailable_detected_at": "2025-04-14T10:00:00Z",
            "unavailable_reason": "copyright",
            "unavailable_text": "This media has been removed due to a copyright report.",
            "unavailable_source_url": "https://x.com/test-handle/status/gone01",
        }
    ]
    write_capture(tmp_repo, "test-handle", "02-unavailable.json", unavailable)

    assert ingest.main([]) == 0

    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet")
    row = df.row(0, named=True)
    assert row["text"] == "original body"
    assert row["unavailable_detected_at"] == "2025-04-14T10:00:00Z"
    assert row["unavailable_reason"] == "copyright"
    assert "copyright report" in row["unavailable_text"]


def test_full_text_not_clobbered_by_later_truncated_scroll(tmp_repo: Path) -> None:
    """Once we've archived the full body of a long tweet, a later timeline
    scroll that returns only the truncated head must not overwrite it."""
    full = "Lorem ipsum dolor sit amet, consectetur adipiscing elit, " * 10
    cap_full = make_capture(
        [
            make_tweet(
                "trunc02",
                captured_at="2025-04-12T10:00:00Z",
                text=full,
                is_truncated=False,
            )
        ]
    )
    cap_head = make_capture(
        [
            make_tweet(
                "trunc02",
                captured_at="2025-04-13T10:00:00Z",
                text="Lorem ipsum dolor sit amet… https://t.co/abc",
                is_truncated=True,
            )
        ]
    )
    write_capture(tmp_repo, "test-handle", "01-full.json", cap_full)
    write_capture(tmp_repo, "test-handle", "02-head.json", cap_head)
    assert ingest.main([]) == 0

    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet")
    row = df.row(0, named=True)
    assert row["text"] == full
    assert row["is_truncated"] is False


def test_community_note_preserved_when_later_payload_lacks_pivot(tmp_repo: Path) -> None:
    note = {
        "note_id": "note-abc",
        "title": "Readers added context",
        "short_title": "Readers added context",
        "summary": "This claim is missing context: …",
        "destination_url": "https://x.com/i/birdwatch/n/note-abc",
        "observed_at": "2025-04-12T10:00:00Z",
    }
    cap_with = make_capture(
        [
            make_tweet(
                "cn01",
                captured_at="2025-04-12T10:00:00Z",
                community_note=note,
            )
        ]
    )
    cap_without = make_capture(
        [
            make_tweet(
                "cn01",
                captured_at="2025-04-13T10:00:00Z",
                community_note=None,
            )
        ]
    )
    write_capture(tmp_repo, "test-handle", "01.json", cap_with)
    write_capture(tmp_repo, "test-handle", "02.json", cap_without)
    assert ingest.main([]) == 0

    df = pl.read_parquet(tmp_repo / "data" / "test-handle.parquet")
    cn = df["community_note"].to_list()[0]
    assert cn is not None
    assert cn["note_id"] == "note-abc"
    assert cn["summary"].startswith("This claim is missing context")


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
