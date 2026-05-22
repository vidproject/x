"""Unit tests for the cheap audio-recognition sidecar."""

from __future__ import annotations

import array
import json
import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from scripts import detect_audio_music
from scripts._schema import TWEET_SCHEMA
from scripts.detect_audio_music import (
    DETECTOR_VERSION,
    AudioCandidate,
    AudioResult,
    analyze_pcm,
    is_cache_hit,
    tag_entry,
)
from tests.conftest import make_media, make_tweet


@pytest.fixture
def tmp_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    (tmp_path / "data" / "tags").mkdir(parents=True)
    monkeypatch.setattr(detect_audio_music, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(detect_audio_music, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(detect_audio_music, "TAGS_DIR", tmp_path / "data" / "tags")
    monkeypatch.setattr(
        detect_audio_music, "OUT_PATH", tmp_path / "data" / "tags" / "audio_music.parquet"
    )
    monkeypatch.setattr(
        detect_audio_music, "MANIFEST_PATH", tmp_path / "data" / "tags" / "manifest.json"
    )
    yield tmp_path


def _write_handle_parquet(repo: Path, handle: str, tweets: list[dict[str, Any]]) -> Path:
    path = repo / "data" / f"{handle}.parquet"
    df = pl.DataFrame(tweets, schema=TWEET_SCHEMA, strict=False)
    df.write_parquet(path, compression="zstd")
    return path


def _archived_video(media_id: str, sha: str, duration: float = 60.0) -> dict[str, Any]:
    media = make_media(media_type="video", media_id=media_id, duration_sec=duration)
    media["release_asset_url"] = f"https://example.invalid/{media_id}.mp4"
    media["sha256"] = sha
    media["archive_status"] = "archived"
    media["bytes"] = 1234567
    return media


def _ok_result(*, music: bool = False) -> AudioResult:
    result = AudioResult(
        status="ok",
        audio_duration_sec=60.0,
        sample_duration_sec=45.0,
        audio_stream_count=1,
        codec="aac",
        channels=2,
        sample_rate=16_000,
        music_score=0.91 if music else 0.42,
        speech_score=0.30,
        non_silent_ratio=0.96,
        zero_crossing_rate=0.07,
        rms_mean=0.04,
        rms_variance=0.0001,
        features={"fake": True},
    )
    result.tags = [tag_entry("audio:has-audio")]
    if music:
        result.tags.append(tag_entry("audio:music-likely", tentative=True))
    return result


def test_discovery_skips_unarchived_and_non_video_items(tmp_corpus: Path) -> None:
    archived = _archived_video("vid-1", "a" * 64)
    pending = make_media(media_type="video", media_id="vid-pending")
    photo = make_media(media_type="photo", media_id="pic-1")
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [
            make_tweet("t-1", handle="DHSgov", media=[archived]),
            make_tweet("t-2", handle="DHSgov", media=[pending]),
            make_tweet("t-3", handle="DHSgov", media=[photo]),
        ],
    )

    cands = list(detect_audio_music.discover_candidates([tmp_corpus / "data" / "DHSgov.parquet"]))
    assert len(cands) == 1
    assert cands[0].tweet_id == "t-1"
    assert cands[0].media_sha256 == "a" * 64


def test_analyze_pcm_marks_continuous_audio_as_music_likely() -> None:
    sample_rate = 16_000
    duration = 20
    samples = array.array(
        "h",
        [
            int(9000 * math.sin(2 * math.pi * 440 * i / sample_rate))
            for i in range(sample_rate * duration)
        ],
    )
    result = analyze_pcm(samples.tobytes(), sample_rate=sample_rate)
    tags = {entry["tag"] for entry in result.tags}
    assert result.status == "ok"
    assert result.music_score >= detect_audio_music.DEFAULT_MUSIC_THRESHOLD
    assert "audio:has-audio" in tags
    assert "audio:music-likely" in tags


def test_no_audio_stream_gets_no_audio_tag() -> None:
    result = AudioResult(status="no-audio-stream")
    result.tags = detect_audio_music.tags_for_result(result)
    assert [entry["tag"] for entry in result.tags] == ["audio:no-audio"]


def _speechlike_result() -> AudioResult:
    # Sustained oratory: continuous, non-silent, high RMS, mid-band ZCR. It
    # scores high on the music heuristic but speech_score is nearly as high.
    # This mirrors the JD Vance reference clip (music ~0.87, speech ~0.75).
    return AudioResult(
        status="ok",
        sample_duration_sec=45.0,
        music_score=0.91,
        speech_score=0.75,
        non_silent_ratio=0.98,
        zero_crossing_rate=0.09,
        rms_mean=0.12,
    )


def _music_dominant_result() -> AudioResult:
    # Genuinely music-dominated: high music_score, low speech_score.
    return AudioResult(
        status="ok",
        sample_duration_sec=45.0,
        music_score=0.91,
        speech_score=0.50,
        non_silent_ratio=0.99,
        zero_crossing_rate=0.07,
        rms_mean=0.04,
    )


def test_speech_like_vector_is_not_music_likely() -> None:
    result = _speechlike_result()
    tags = {entry["tag"] for entry in detect_audio_music.tags_for_result(result)}
    assert "audio:has-audio" in tags
    assert "audio:music-likely" not in tags


def test_music_dominant_vector_is_music_likely() -> None:
    result = _music_dominant_result()
    tags = {entry["tag"] for entry in detect_audio_music.tags_for_result(result)}
    assert "audio:music-likely" in tags


def test_jd_vance_reference_scores_are_not_music_likely() -> None:
    # The actual reference false positive: music_score below threshold AND a
    # speech_score that the dominance gate caps out either way.
    result = AudioResult(
        status="ok",
        sample_duration_sec=45.0,
        music_score=0.874,
        speech_score=0.747,
        non_silent_ratio=0.989,
        zero_crossing_rate=0.097,
        rms_mean=0.120,
    )
    tags = {entry["tag"] for entry in detect_audio_music.tags_for_result(result)}
    assert "audio:music-likely" not in tags
    # Even if a future score formula nudged music_score above threshold, the
    # speech-dominance gate must still block this speech-like clip.
    bumped = AudioResult(
        status="ok",
        sample_duration_sec=45.0,
        music_score=0.92,
        speech_score=0.747,
        non_silent_ratio=0.989,
        zero_crossing_rate=0.097,
        rms_mean=0.120,
    )
    bumped_tags = {entry["tag"] for entry in detect_audio_music.tags_for_result(bumped)}
    assert "audio:music-likely" not in bumped_tags


def test_music_threshold_and_gate_are_configurable() -> None:
    # The margin gate can be relaxed to admit a borderline clip when explicitly
    # configured, keeping behaviour tunable.
    result = AudioResult(
        status="ok",
        sample_duration_sec=45.0,
        music_score=0.90,
        speech_score=0.61,
        non_silent_ratio=0.98,
        zero_crossing_rate=0.07,
        rms_mean=0.04,
    )
    strict = {e["tag"] for e in detect_audio_music.tags_for_result(result)}
    assert "audio:music-likely" in strict  # margin 0.29 >= default 0.12

    capped_out = {
        e["tag"]
        for e in detect_audio_music.tags_for_result(result, speech_score_cap=0.50)
    }
    assert "audio:music-likely" not in capped_out  # speech 0.61 > cap 0.50


def test_is_cache_hit_uses_cacheable_status_and_version() -> None:
    assert is_cache_hit({"detector_version": DETECTOR_VERSION, "status": "ok"}, DETECTOR_VERSION)
    assert is_cache_hit(
        {"detector_version": DETECTOR_VERSION, "status": "no-audio-stream"}, DETECTOR_VERSION
    )
    assert not is_cache_hit(
        {"detector_version": DETECTOR_VERSION, "status": "fetch-failed"}, DETECTOR_VERSION
    )
    assert not is_cache_hit({"detector_version": "old", "status": "ok"}, DETECTOR_VERSION)


def test_run_writes_sidecar_and_manifest(tmp_corpus: Path) -> None:
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [make_tweet("t-1", handle="DHSgov", media=[_archived_video("vid-1", "a" * 64)])],
    )

    stats = detect_audio_music.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        analyzer=lambda _c: _ok_result(music=True),
    )
    assert stats["attempted"] == 1
    assert stats["analyzed"] == 1
    assert stats["status_ok"] == 1

    out = pl.read_parquet(tmp_corpus / "data" / "tags" / "audio_music.parquet")
    assert out.height == 1
    assert out["music_score"].to_list() == [0.91]
    tags = {entry["tag"] for entry in out["tags"][0]}
    assert tags == {"audio:has-audio", "audio:music-likely"}

    manifest = json.loads((tmp_corpus / "data" / "tags" / "manifest.json").read_text())
    layer = manifest["layers"]["audio_music"]
    assert layer["row_count"] == 1
    assert layer["detector_version"] == DETECTOR_VERSION
    assert layer["tag_frequency"]["audio:music-likely"] == 1


def test_run_dedupes_same_sha_across_tweets(tmp_corpus: Path) -> None:
    shared = _archived_video("vid-shared", "c" * 64)
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [
            make_tweet("t-1", handle="DHSgov", media=[shared]),
            make_tweet("t-2", handle="DHSgov", media=[shared]),
        ],
    )
    calls: list[str] = []

    def analyzer(cand: AudioCandidate) -> AudioResult:
        calls.append(cand.tweet_id)
        return _ok_result()

    stats = detect_audio_music.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"], analyzer=analyzer
    )
    assert calls == ["t-1"]
    assert stats["intra_run_dedup"] == 1
    out = pl.read_parquet(tmp_corpus / "data" / "tags" / "audio_music.parquet")
    assert set(out["tweet_id"].to_list()) == {"t-1", "t-2"}


def test_failures_are_not_cached(tmp_corpus: Path) -> None:
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [make_tweet("t-1", handle="DHSgov", media=[_archived_video("vid-1", "a" * 64)])],
    )

    detect_audio_music.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        analyzer=lambda _c: AudioResult(status="fetch-failed", error="503"),
    )
    calls: list[str] = []

    def analyzer(cand: AudioCandidate) -> AudioResult:
        calls.append(cand.media_sha256)
        return _ok_result()

    detect_audio_music.run(parquets=[tmp_corpus / "data" / "DHSgov.parquet"], analyzer=analyzer)
    assert calls == ["a" * 64]


def test_max_items_counts_attempts_not_successes(tmp_corpus: Path) -> None:
    media = [_archived_video(f"vid-{i}", chr(ord("a") + i) * 64) for i in range(4)]
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [make_tweet(f"t-{i}", handle="DHSgov", media=[m]) for i, m in enumerate(media)],
    )
    stats = detect_audio_music.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        max_items=2,
        dry_run=True,
        analyzer=lambda _c: AudioResult(status="skipped-no-ffmpeg"),
    )
    assert stats["attempted"] == 2
    assert stats["skipped_max_items"] == 2


def test_run_caches_successful_rows(tmp_corpus: Path) -> None:
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [make_tweet("t-1", handle="DHSgov", media=[_archived_video("vid-1", "a" * 64)])],
    )
    detect_audio_music.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"], analyzer=lambda _c: _ok_result()
    )

    def explode(_c: AudioCandidate) -> AudioResult:
        raise AssertionError("cache miss")

    stats = detect_audio_music.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"], analyzer=explode
    )
    assert stats["cache_hits"] == 1
