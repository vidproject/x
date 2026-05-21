from __future__ import annotations

from typing import Any

from scripts.describe_media import describe_media_item, derive_description_tags, input_hash_for


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

    assert row["status"] == "metadata-context"
    assert "media:video" in tags
    assert "media:short-video" in tags
    assert "media:needs-vision" in tags
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
    assert "media:needs-vision" in tags


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


def test_manual_review_observation_promotes_visual_tags() -> None:
    media = {
        "media_id": "p1",
        "media_type": "photo",
        "release_asset_url": "https://github.com/asset.jpg",
    }
    manual_review = {
        "visual_observation": "Press-photo of President Trump at a podium with visible title-card text.",
        "tweet_text_excerpt": "MAKE AMERICA GREAT AGAIN!!!",
        "candidate_visual_tags": ["subject:official", "media:text-overlay", "video:news-clip"],
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
    assert "media:text-overlay" in tags
    assert "video:news-clip" not in tags
    assert "media:needs-vision" not in tags
    assert [entry["tag"] for entry in row["tags"]].count("media:text-overlay") == 1


def test_manual_review_aliases_legacy_video_genre_tags() -> None:
    media = {
        "media_id": "v1",
        "media_type": "video",
        "release_asset_url": "https://github.com/asset.mp4",
    }
    manual_review = {
        "visual_observation": "Produced public-service spot with title cards.",
        "candidate_visual_tags": ["video:ad", "video:psa"],
    }
    row = describe_media_item(
        _tweet(),
        media,
        generated_at="2026-05-20T00:00:00Z",
        manual_review=manual_review,
    )
    tags = {entry["tag"] for entry in row["tags"]}

    assert "genre:advertisement" in tags
    assert "genre:psa" in tags
    assert "video:ad" not in tags
    assert "video:psa" not in tags


def test_credit_score_context_does_not_become_music_video() -> None:
    tags = derive_description_tags(
        "video; tweet context: their credit score is ruined after identity theft",
        media_type="video",
    )

    assert "media:produced-video" not in tags
    assert "media:music-video" not in tags
    assert "genre:music-video" not in tags


def test_negated_music_video_note_does_not_become_music_video() -> None:
    tags = derive_description_tags(
        "Human review says this is speech footage, not a music video or music-led montage.",
        media_type="video",
    )

    assert "media:produced-video" not in tags
    assert "media:music-video" not in tags
    assert "media:montage" not in tags
    assert "genre:music-video" not in tags


def test_musical_score_context_still_marks_music_video() -> None:
    tags = derive_description_tags(
        "polished montage with a dramatic musical score and background music",
        media_type="video",
    )

    assert "media:produced-video" in tags
    assert "media:music-video" in tags
    assert "genre:music-video" in tags


def test_input_hash_changes_when_media_evidence_changes() -> None:
    media = {"media_id": "v1", "media_type": "video", "duration_sec": 12.4}
    changed = {**media, "duration_sec": 13.0}

    assert input_hash_for(_tweet(), media) != input_hash_for(_tweet(), changed)
