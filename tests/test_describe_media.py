from __future__ import annotations

from scripts.describe_media import describe_media_item, input_hash_for


def _tweet() -> dict:
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
    assert "media:archived" in tags
    assert "media:has-alt-text" in tags
    assert "media:needs-vision" not in tags


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

    assert row["status"] == "metadata-only"
    assert "media:video" in tags
    assert "media:short-video" in tags
    assert "media:needs-vision" in tags
    assert "needs OCR, transcript, or frame-level vision" in row["description"]


def test_describe_media_item_does_not_mark_unknown_duration_as_short() -> None:
    media = {
        "media_id": "v2",
        "media_type": "video",
        "release_asset_url": "https://github.com/asset.mp4",
    }
    row = describe_media_item(_tweet(), media, generated_at="2026-05-20T00:00:00Z")
    tags = {entry["tag"] for entry in row["tags"]}

    assert "media:video" in tags
    assert "media:short-video" not in tags


def test_input_hash_changes_when_media_evidence_changes() -> None:
    media = {"media_id": "v1", "media_type": "video", "duration_sec": 12.4}
    changed = {**media, "duration_sec": 13.0}

    assert input_hash_for(_tweet(), media) != input_hash_for(_tweet(), changed)
