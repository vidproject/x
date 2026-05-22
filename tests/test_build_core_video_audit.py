from __future__ import annotations

from scripts.build_core_video_audit import archive_recovery_items, missing_steps


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
                "produced_video_tags": ["media:produced-video"],
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
