from __future__ import annotations

from scripts.build_core_video_audit import (
    archive_recovery_items,
    build_item,
    classify_from_text,
    missing_steps,
    tag_values,
)


def _empty_maps() -> dict[str, dict]:
    return {"vision": {}, "audio": {}, "keyframes": {}, "ocr": {}, "manual": {}}


def test_release_asset_url_counts_as_archived_for_missing_steps() -> None:
    missing = missing_steps(
        {"archive_status": "pending", "release_asset_url": "https://example.invalid/video.mp4"},
        keyframe_rows=[],
        audio_rows=[],
        vision_rows=[],
        tags=set(),
    )
    assert "archive-media" not in missing
    assert "extract-keyframes" in missing
    assert "detect-audio" in missing


def test_archive_recovery_queue_is_limited_to_produced_or_genre_items() -> None:
    recovery = archive_recovery_items(
        [
            {
                "tweet_id": "1",
                "media_id": "m1",
                "missing_steps": ["archive-media"],
                "produced_video_tags": ["video:produced"],
                "genre_tags": [],
            },
            {
                "tweet_id": "2",
                "media_id": "m2",
                "missing_steps": ["archive-media"],
                "produced_video_tags": [],
                "genre_tags": ["genre:psa"],
            },
            {
                "tweet_id": "3",
                "media_id": "m3",
                "missing_steps": ["archive-media"],
                "produced_video_tags": [],
                "genre_tags": [],
            },
        ]
    )
    assert [item["tweet_id"] for item in recovery] == ["1", "2"]


def test_tag_values_normalizes_legacy_produced_video_tag() -> None:
    assert tag_values([{"tag": "media:produced-video"}]) == ["video:produced"]


def test_tag_values_normalizes_legacy_branch_tags() -> None:
    assert tag_values([{"tag": "branch:coast-guard"}]) == ["military:coast-guard"]


def test_classify_from_text_ignores_incidental_music_wording() -> None:
    tags = classify_from_text("background music plays during the soundtrack of America")
    assert "media:music-video" not in tags
    assert "genre:music-video" not in tags
    # Never synthesize the acoustic heuristic tag from text.
    assert "audio:music-likely" not in tags


def test_classify_from_text_keeps_explicit_music_video() -> None:
    tags = classify_from_text("the official music video for the campaign anthem")
    assert "genre:music-video" in tags
    assert "media:music-video" not in tags
    assert "audio:music-likely" not in tags


def test_classify_from_text_suppresses_music_video_on_speech() -> None:
    tags = classify_from_text(
        "delivers remarks at a press conference, set to music for the reel"
    )
    assert "genre:music-video" not in tags
    assert "media:music-video" not in tags


def test_audio_music_likely_alone_is_not_upgraded_to_music_video() -> None:
    maps = _empty_maps()
    row = {
        "tweet_id": "t1",
        "account_handle": "DHSgov",
        "text_resolved": "Watch this clip.",
        "media": [{"media_id": "m1", "media_type": "video"}],
    }
    media = {"media_id": "m1", "media_type": "video", "release_asset_url": "https://x/v.mp4"}
    maps["audio"][("t1", "m1")] = [
        {
            "tweet_id": "t1",
            "media_id": "m1",
            "tags": [{"tag": "audio:music-likely", "tentative": True}],
        }
    ]
    item = build_item(
        row,
        media,
        lexical={},
        vision=maps["vision"],
        audio=maps["audio"],
        keyframes=maps["keyframes"],
        ocr=maps["ocr"],
        manual=maps["manual"],
    )
    assert "audio:music-likely" in item["tags"]
    assert "genre:music-video" not in item["tags"]
