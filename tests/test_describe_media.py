from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest

from scripts import describe_media
from scripts._schema import MEDIA_VISION_SCHEMA, TWEET_SCHEMA
from scripts.describe_media import derive_description_tags, describe_media_item, input_hash_for
from tests.conftest import make_media, make_tweet


def _tweet() -> dict[str, Any]:
    return {
        "tweet_id": "1234567890123456789",
        "account_handle": "DHSgov",
        "text_resolved": "A public update with attached video.",
    }


def test_describe_media_item_uses_alt_text_and_metadata() -> None:
    media = {
        "media_id": "m1",
        "media_type": "photo",
        "release_asset_url": "https://github.com/asset.jpg",
        "sha256": "abc",
        "width": 1200,
        "height": 800,
        "bytes": 1024 * 1024,
        "alt_text": "Agents standing outside a building.",
    }
    row = describe_media_item(_tweet(), media, generated_at="2026-05-20T00:00:00Z")
    tags = {entry["tag"] for entry in row["tags"]}

    assert row["status"] == "metadata-alt"
    assert row["cost_estimate_usd"] == 0.0
    assert "alt text: Agents standing outside a building." in row["description"]
    assert "media:photo" in tags
    assert "media-status:archived" in tags
    assert "media-status:has-alt-text" in tags
    assert "media-status:needs-vision" not in tags


def test_describe_media_item_marks_video_without_alt_text_for_followup() -> None:
    media = {
        "media_id": "v1",
        "media_type": "video",
        "release_asset_url": "https://github.com/asset.mp4",
        "duration_sec": 12.4,
        "width": 640,
        "height": 360,
    }
    row = describe_media_item(_tweet(), media, generated_at="2026-05-20T00:00:00Z")
    tags = {entry["tag"] for entry in row["tags"]}

    assert row["status"] == "metadata-context"
    assert "media:video" in tags
    assert "video:short" in tags
    assert "media-status:needs-vision" in tags
    assert "needs OCR, transcript, or frame-level vision" in row["description"]


def test_describe_media_item_uses_card_and_url_context_without_visual_claims() -> None:
    tweet = {
        **_tweet(),
        "text_resolved": "https://t.co/example",
        "card": {
            "title": "DHS Announces New Immigration Fees & Enforcement Measures",
            "description": "A public notice about immigration forms.",
            "vendor_url": "https://www.dhs.gov/news/example",
        },
    }
    media = {
        "media_id": "p2",
        "media_type": "photo",
        "release_asset_url": "https://github.com/asset.jpg",
        "original_url": "https://pbs.twimg.com/media/example.jpg",
        "width": 1200,
        "height": 800,
    }

    row = describe_media_item(tweet, media, generated_at="2026-05-20T00:00:00Z")
    tags = {entry["tag"] for entry in row["tags"]}

    assert row["status"] == "metadata-context"
    assert "card title: DHS Announces New Immigration Fees" in row["description"]
    assert "source URL: https://pbs.twimg.com/media/example.jpg" in row["description"]
    assert "archive URL: https://github.com/asset.jpg" in row["description"]
    assert "needs OCR, transcript, or frame-level vision" in row["description"]
    assert "media-status:needs-vision" in tags


def test_describe_media_item_does_not_mark_unknown_duration_as_short() -> None:
    media = {
        "media_id": "v2",
        "media_type": "video",
        "release_asset_url": "https://github.com/asset.mp4",
    }
    row = describe_media_item(_tweet(), media, generated_at="2026-05-20T00:00:00Z")
    tags = {entry["tag"] for entry in row["tags"]}

    assert "media:video" in tags
    assert "video:short" not in tags
    assert "media:short-video" not in tags


def test_manual_review_observation_promotes_visual_tags() -> None:
    media = {
        "media_id": "p1",
        "media_type": "photo",
        "release_asset_url": "https://github.com/asset.jpg",
    }
    manual_review = {
        "visual_observation": "Press-photo of President Trump at a podium with visible title-card text.",
        "tweet_text_excerpt": "MAKE AMERICA GREAT AGAIN!!!",
        "candidate_visual_tags": ["subject:official", "video:text-overlay", "video:news-clip"],
    }
    row = describe_media_item(
        _tweet(),
        media,
        generated_at="2026-05-20T00:00:00Z",
        manual_review=manual_review,
    )
    tags = {entry["tag"] for entry in row["tags"]}

    assert row["status"] == "manual-review"
    assert row["confidence"] == 0.92
    assert "visual observation: Press-photo of President Trump" in row["description"]
    assert "subject:official" in tags
    assert "video:text-overlay" in tags
    assert "media:text-overlay" not in tags
    assert "video:news-clip" not in tags
    assert "media-status:needs-vision" not in tags
    assert [entry["tag"] for entry in row["tags"]].count("video:text-overlay") == 1


def test_manual_review_aliases_legacy_video_genre_tags() -> None:
    media = {
        "media_id": "v1",
        "media_type": "video",
        "release_asset_url": "https://github.com/asset.mp4",
    }
    manual_review = {
        "visual_observation": "Produced public-service spot with title cards.",
        "candidate_visual_tags": [
            "media:produced-video",
            "video:ad",
            "video:psa",
            "branch:coast-guard",
        ],
    }
    row = describe_media_item(
        _tweet(),
        media,
        generated_at="2026-05-20T00:00:00Z",
        manual_review=manual_review,
    )
    tags = {entry["tag"] for entry in row["tags"]}

    assert "video:produced" in tags
    assert "genre:advertisement" in tags
    assert "genre:psa" in tags
    assert "military:coast-guard" in tags
    assert "topic:military" in tags
    assert "media:produced-video" not in tags
    assert "branch:coast-guard" not in tags
    assert "video:ad" not in tags
    assert "video:psa" not in tags


def test_credit_score_context_does_not_become_music_video() -> None:
    tags = derive_description_tags(
        "video; tweet context: their credit score is ruined after identity theft",
        media_type="video",
    )

    assert "video:produced" not in tags
    assert "media:music-video" not in tags
    assert "genre:music-video" not in tags


def test_negated_music_video_note_does_not_become_music_video() -> None:
    tags = derive_description_tags(
        "Human review says this is speech footage, not a music video or music-led montage.",
        media_type="video",
    )

    assert "video:produced" not in tags
    assert "video:montage" not in tags
    assert "media:montage" not in tags
    assert "genre:music-video" not in tags


def test_musical_score_context_is_not_music_video() -> None:
    # "musical score" / "background music" are incidental/metaphorical music
    # wording. They mark the clip as produced but must NOT make it a music video.
    tags = derive_description_tags(
        "polished montage with a dramatic musical score and background music",
        media_type="video",
    )

    assert "video:produced" in tags
    assert "media:music-video" not in tags
    assert "genre:music-video" not in tags


def test_explicit_music_video_phrasing_still_marks_music_video() -> None:
    for phrase in (
        "official music video for the new single",
        "a short clip set to music",
        "official audio for the campaign anthem",
    ):
        tags = derive_description_tags(phrase, media_type="video")
        assert "genre:music-video" in tags, phrase
        assert "media:music-video" not in tags, phrase


def test_speech_clip_with_background_music_is_not_music_video() -> None:
    tags = derive_description_tags(
        "the Vice President delivers remarks at a press conference; "
        "background music plays softly during his remarks",
        media_type="video",
    )

    assert "video:speech" in tags
    assert "media:music-video" not in tags
    assert "genre:music-video" not in tags


def test_input_hash_changes_when_media_evidence_changes() -> None:
    media = {"media_id": "v1", "media_type": "video", "duration_sec": 12.4}
    changed = {**media, "duration_sec": 13.0}

    assert input_hash_for(_tweet(), media) != input_hash_for(_tweet(), changed)


def _write_handle_parquet(repo: Path, handle: str, tweets: list[dict[str, Any]]) -> Path:
    path = repo / "data" / f"{handle}.parquet"
    pl.DataFrame(tweets, schema=TWEET_SCHEMA, strict=False).write_parquet(path)
    return path


def _archived_photo(media_id: str, sha: str) -> dict[str, Any]:
    media = make_media(media_type="photo", media_id=media_id)
    media["release_asset_url"] = f"https://example.invalid/{media_id}.jpg"
    media["sha256"] = sha
    media["archive_status"] = "archived"
    return media


def test_scoped_build_preserves_existing_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "data" / "tags").mkdir(parents=True)
    out_path = tmp_path / "data" / "tags" / "media_vision.parquet"
    monkeypatch.setattr(describe_media, "OUT_PATH", out_path)
    monkeypatch.setattr(describe_media, "TAGS_DIR", tmp_path / "data" / "tags")
    monkeypatch.setattr(
        describe_media,
        "MANUAL_REVIEW_QUEUE_PATH",
        tmp_path / "data" / "tags" / "manual_media_review_queue.json",
    )

    old_row = describe_media_item(
        make_tweet("old-tweet", handle="WhiteHouse"),
        _archived_photo("old-photo", "b" * 64),
        generated_at="2026-05-20T00:00:00Z",
    )
    pl.DataFrame([old_row], schema=MEDIA_VISION_SCHEMA, strict=False).write_parquet(out_path)

    scoped = _write_handle_parquet(
        tmp_path,
        "DHSgov",
        [
            make_tweet(
                "new-tweet",
                handle="DHSgov",
                media=[_archived_photo("new-photo", "a" * 64)],
            )
        ],
    )
    rows, stats = describe_media.build_rows(
        [scoped],
        generated_at="2026-05-21T00:00:00Z",
        include_pending=False,
        force=False,
        max_items=None,
        preserve_existing=True,
    )

    assert stats["rows"] == 2
    assert {(row["tweet_id"], row["media_id"]) for row in rows} == {
        ("old-tweet", "old-photo"),
        ("new-tweet", "new-photo"),
    }
