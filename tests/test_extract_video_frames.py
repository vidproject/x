"""Unit tests for the Layer-2 keyframe extractor.

The ffmpeg/ffprobe/network paths are not exercised here — they're
mocked via an injected ``extractor`` callable so the test suite stays
fast and runs anywhere the rest of the suite runs (no ffmpeg required,
no network).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from scripts import extract_video_frames
from scripts._schema import TWEET_SCHEMA
from scripts.extract_video_frames import (
    EXTRACTOR_VERSION,
    ExtractResult,
    FrameRecord,
    VideoCandidate,
    evenly_spaced_timestamps,
    is_cache_hit,
)
from tests.conftest import make_media, make_tweet


@pytest.fixture
def tmp_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Build a self-contained data/ + data/tags/ workspace with one
    handle parquet that contains one tweet carrying one archived video.
    Re-points the module's path constants so we don't touch the repo."""
    (tmp_path / "data" / "tags").mkdir(parents=True)
    derived = tmp_path / "data" / "derived" / "keyframes"
    derived.mkdir(parents=True)

    monkeypatch.setattr(extract_video_frames, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(extract_video_frames, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(extract_video_frames, "TAGS_DIR", tmp_path / "data" / "tags")
    monkeypatch.setattr(extract_video_frames, "DERIVED_DIR", derived)
    monkeypatch.setattr(
        extract_video_frames, "OUT_PATH", tmp_path / "data" / "tags" / "keyframes.parquet"
    )
    monkeypatch.setattr(
        extract_video_frames, "MANIFEST_PATH", tmp_path / "data" / "tags" / "manifest.json"
    )

    yield tmp_path


def _write_handle_parquet(repo: Path, handle: str, tweets: list[dict[str, Any]]) -> Path:
    path = repo / "data" / f"{handle}.parquet"
    df = pl.DataFrame(tweets, schema=TWEET_SCHEMA, strict=False)
    df.write_parquet(path, compression="zstd")
    return path


def _archived_video(
    media_id: str, sha: str, url: str | None = None, duration: float = 60.0
) -> dict[str, Any]:
    media = make_media(media_type="video", media_id=media_id, duration_sec=duration)
    media["release_asset_url"] = url or f"https://example.invalid/{media_id}.mp4"
    media["sha256"] = sha
    media["archive_status"] = "archived"
    media["bytes"] = 1234567
    return media


# --------------------------------------------------------------------------
# Pure helpers


def test_evenly_spaced_timestamps_distributes_across_duration() -> None:
    ts = evenly_spaced_timestamps(60.0, 5)
    assert ts == [6.0, 18.0, 30.0, 42.0, 54.0]
    # No endpoints — the smallest is well above 0, the largest well below the duration.
    assert ts[0] > 0
    assert ts[-1] < 60.0


def test_evenly_spaced_timestamps_degenerate_inputs() -> None:
    assert evenly_spaced_timestamps(0, 5) == []
    assert evenly_spaced_timestamps(60.0, 0) == []
    assert evenly_spaced_timestamps(-1.0, 5) == []


def test_is_cache_hit_requires_status_ok_and_matching_version() -> None:
    base = {"extractor_version": EXTRACTOR_VERSION, "status": "ok"}
    assert is_cache_hit(base, EXTRACTOR_VERSION)
    assert not is_cache_hit({**base, "status": "ffmpeg-failed"}, EXTRACTOR_VERSION)
    assert not is_cache_hit({**base, "extractor_version": "old"}, EXTRACTOR_VERSION)
    assert not is_cache_hit({}, EXTRACTOR_VERSION)


# --------------------------------------------------------------------------
# Discovery


def test_discovery_skips_unarchived_and_non_video_items(tmp_corpus: Path) -> None:
    # One tweet with an archived video, one with a pending (no sha) video, one with a photo only.
    media_archived = _archived_video("vid-1", sha="a" * 64)
    media_pending = make_media(media_type="video", media_id="vid-pending", duration_sec=30.0)
    media_pending["release_asset_url"] = None
    media_pending["sha256"] = None
    media_photo = make_media(media_type="photo", media_id="pic-1")
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [
            make_tweet("t-1", handle="DHSgov", media=[media_archived]),
            make_tweet("t-2", handle="DHSgov", media=[media_pending]),
            make_tweet("t-3", handle="DHSgov", media=[media_photo]),
        ],
    )

    cands = list(extract_video_frames.discover_candidates([tmp_corpus / "data" / "DHSgov.parquet"]))
    assert len(cands) == 1
    assert cands[0].tweet_id == "t-1"
    assert cands[0].media_id == "vid-1"
    assert cands[0].media_sha256 == "a" * 64


def test_discovery_includes_animated_gifs(tmp_corpus: Path) -> None:
    gif = make_media(media_type="animated_gif", media_id="gif-1", duration_sec=4.0)
    gif["release_asset_url"] = "https://example.invalid/gif-1.mp4"
    gif["sha256"] = "b" * 64
    _write_handle_parquet(tmp_corpus, "DHSgov", [make_tweet("t-gif", handle="DHSgov", media=[gif])])
    cands = list(extract_video_frames.discover_candidates([tmp_corpus / "data" / "DHSgov.parquet"]))
    assert [c.media_type for c in cands] == ["animated_gif"]


# --------------------------------------------------------------------------
# Top-level run with injected extractor


def _fake_extractor_factory(
    sha_to_frames: dict[str, int],
) -> Callable[[VideoCandidate], ExtractResult]:
    """Return a callable that fakes the ffmpeg extraction. The frames it
    'produces' don't exist on disk; the test only asserts on what we
    record in the sidecar."""

    def fake(cand: VideoCandidate) -> ExtractResult:
        n = sha_to_frames.get(cand.media_sha256, 5)
        frames = [
            FrameRecord(
                index=i,
                timestamp_sec=float(i * 10 + 5),
                path=f"data/derived/keyframes/{cand.media_sha256}/{i:03d}.jpg",
                sha256=f"frame-{cand.media_sha256[:8]}-{i:03d}",
                width=640,
                height=360,
                bytes=50_000,
            )
            for i in range(n)
        ]
        return ExtractResult(
            status="ok",
            frames=frames,
            video_duration_sec=cand.declared_duration_sec,
            video_width=1920,
            video_height=1080,
            error=None,
        )

    return fake


def test_run_extracts_and_writes_sidecar(tmp_corpus: Path) -> None:
    m1 = _archived_video("vid-1", sha="a" * 64, duration=50.0)
    m2 = _archived_video("vid-2", sha="b" * 64, duration=120.0)
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [
            make_tweet("t-1", handle="DHSgov", media=[m1]),
            make_tweet("t-2", handle="DHSgov", media=[m2]),
        ],
    )

    stats = extract_video_frames.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        extractor=_fake_extractor_factory({}),
    )
    assert stats["extracted"] == 2
    assert stats["rows"] == 2
    assert stats["status_ok"] == 2

    out = pl.read_parquet(tmp_corpus / "data" / "tags" / "keyframes.parquet")
    assert out.height == 2
    assert set(out["media_sha256"].to_list()) == {"a" * 64, "b" * 64}
    # Each row has 5 frames by default.
    assert out["frame_count"].to_list() == [5, 5]

    manifest = json.loads((tmp_corpus / "data" / "tags" / "manifest.json").read_text())
    layer = manifest["layers"]["keyframes"]
    assert layer["row_count"] == 2
    assert layer["frame_count"] == 10
    assert layer["extractor_version"] == EXTRACTOR_VERSION
    assert layer["cost_estimate_usd"] == 0.0
    assert layer["status_counts"] == {"ok": 2}


def test_run_caches_on_media_sha256(tmp_corpus: Path) -> None:
    """A second run finds the prior row, sees status=ok + matching
    extractor_version, and skips re-extraction."""
    m = _archived_video("vid-1", sha="a" * 64, duration=42.0)
    _write_handle_parquet(tmp_corpus, "DHSgov", [make_tweet("t-1", handle="DHSgov", media=[m])])

    extract_video_frames.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        extractor=_fake_extractor_factory({}),
    )

    # Second run with an extractor that would explode if called.
    def explode(_c: VideoCandidate) -> ExtractResult:
        raise AssertionError("cache miss — extractor should not have been invoked")

    stats = extract_video_frames.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        extractor=explode,
    )
    assert stats["cache_hits"] == 1
    assert stats.get("extracted", 0) == 0


def test_run_survives_extractor_exception_and_still_writes(tmp_corpus: Path) -> None:
    """A single video that makes the extractor raise must not abort the whole
    pass and lose every other row. Regression: a short video whose last frame
    file was never written by ffmpeg raised FileNotFoundError and took down the
    entire keyframe run (the sidecar is only written at the end)."""
    good = _archived_video("vid-good", sha="a" * 64, duration=50.0)
    bad = _archived_video("vid-bad", sha="b" * 64, duration=2.0)
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [
            make_tweet("t-good", handle="DHSgov", media=[good]),
            make_tweet("t-bad", handle="DHSgov", media=[bad]),
        ],
    )

    ok = _fake_extractor_factory({})

    def flaky(cand: VideoCandidate) -> ExtractResult:
        if cand.media_sha256 == "b" * 64:
            raise FileNotFoundError("simulated missing frame file")
        return ok(cand)

    stats = extract_video_frames.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        extractor=flaky,
    )
    assert stats["status_ok"] == 1
    assert stats["status_extractor-error"] == 1

    out = pl.read_parquet(tmp_corpus / "data" / "tags" / "keyframes.parquet")
    assert out.height == 2
    by_sha = {r["media_sha256"]: r for r in out.to_dicts()}
    assert by_sha["a" * 64]["status"] == "ok"
    assert by_sha["b" * 64]["status"] == "extractor-error"
    # The error row is not cacheable, so a later run retries it.
    assert not is_cache_hit(by_sha["b" * 64], EXTRACTOR_VERSION)


def test_run_intra_run_dedup_across_tweets_with_same_sha(tmp_corpus: Path) -> None:
    """A retweet (and other cross-references) share the same media
    sha256. We extract once and replicate the row for each tweet
    pointer so the tweet_id join still works downstream."""
    shared = _archived_video("vid-shared", sha="c" * 64, duration=30.0)
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [
            make_tweet("t-original", handle="DHSgov", media=[shared]),
            make_tweet("t-retweet-1", handle="DHSgov", media=[shared]),
            make_tweet("t-retweet-2", handle="DHSgov", media=[shared]),
        ],
    )
    calls: list[str] = []

    def counting(cand: VideoCandidate) -> ExtractResult:
        calls.append(cand.tweet_id)
        return _fake_extractor_factory({})(cand)

    stats = extract_video_frames.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        extractor=counting,
    )
    assert len(calls) == 1, "extractor should run exactly once per unique sha"
    assert stats["extracted"] == 1
    assert stats["intra_run_dedup"] == 2
    out = pl.read_parquet(tmp_corpus / "data" / "tags" / "keyframes.parquet")
    assert out.height == 3
    assert set(out["tweet_id"].to_list()) == {"t-original", "t-retweet-1", "t-retweet-2"}


def test_run_respects_max_items(tmp_corpus: Path) -> None:
    media = [_archived_video(f"vid-{i}", sha=chr(ord("a") + i) * 64) for i in range(4)]
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [make_tweet(f"t-{i}", handle="DHSgov", media=[m]) for i, m in enumerate(media)],
    )
    stats = extract_video_frames.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        max_items=2,
        extractor=_fake_extractor_factory({}),
    )
    assert stats["extracted"] == 2
    assert stats["skipped_max_items"] == 2


def test_run_respects_max_items_for_failed_attempts(tmp_corpus: Path) -> None:
    media = [_archived_video(f"vid-{i}", sha=chr(ord("a") + i) * 64) for i in range(4)]
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [make_tweet(f"t-{i}", handle="DHSgov", media=[m]) for i, m in enumerate(media)],
    )

    def skipped(cand: VideoCandidate) -> ExtractResult:
        return ExtractResult(
            status="skipped-no-ffmpeg",
            frames=[],
            video_duration_sec=cand.declared_duration_sec,
            video_width=0,
            video_height=0,
            error="ffmpeg / ffprobe not on PATH",
        )

    stats = extract_video_frames.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        max_items=2,
        dry_run=True,
        extractor=skipped,
    )
    assert stats["attempted"] == 2
    assert stats.get("extracted", 0) == 0
    assert stats["skipped_max_items"] == 2


def test_all_no_ffmpeg_run_does_not_overwrite_existing_ok_sidecar(tmp_corpus: Path) -> None:
    m = _archived_video("vid-1", sha="a" * 64, duration=42.0)
    _write_handle_parquet(tmp_corpus, "DHSgov", [make_tweet("t-1", handle="DHSgov", media=[m])])
    extract_video_frames.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        extractor=_fake_extractor_factory({}),
    )
    before = pl.read_parquet(tmp_corpus / "data" / "tags" / "keyframes.parquet")
    assert before["status"].to_list() == ["ok"]

    def skipped(cand: VideoCandidate) -> ExtractResult:
        return ExtractResult(
            status="skipped-no-ffmpeg",
            frames=[],
            video_duration_sec=cand.declared_duration_sec,
            video_width=0,
            video_height=0,
            error="ffmpeg / ffprobe not on PATH",
        )

    stats = extract_video_frames.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        force=True,
        extractor=skipped,
    )
    after = pl.read_parquet(tmp_corpus / "data" / "tags" / "keyframes.parquet")
    assert stats["skipped_write_all_no_ffmpeg"] == 1
    assert after["status"].to_list() == ["ok"]


def test_run_records_failure_status_without_caching(tmp_corpus: Path) -> None:
    m = _archived_video("vid-1", sha="a" * 64)
    _write_handle_parquet(tmp_corpus, "DHSgov", [make_tweet("t-1", handle="DHSgov", media=[m])])

    def failing(cand: VideoCandidate) -> ExtractResult:
        return ExtractResult(
            status="fetch-failed",
            frames=[],
            video_duration_sec=cand.declared_duration_sec,
            video_width=0,
            video_height=0,
            error="HTTPStatusError: 503",
        )

    extract_video_frames.run(parquets=[tmp_corpus / "data" / "DHSgov.parquet"], extractor=failing)
    out = pl.read_parquet(tmp_corpus / "data" / "tags" / "keyframes.parquet")
    assert out["status"].to_list() == ["fetch-failed"]
    assert out["frame_count"].to_list() == [0]
    # The next run must re-attempt the failed item — failures are not cached.
    calls: list[str] = []

    def counting(cand: VideoCandidate) -> ExtractResult:
        calls.append(cand.media_sha256)
        return _fake_extractor_factory({})(cand)

    extract_video_frames.run(parquets=[tmp_corpus / "data" / "DHSgov.parquet"], extractor=counting)
    assert calls == ["a" * 64]
