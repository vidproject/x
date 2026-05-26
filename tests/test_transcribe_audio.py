"""Unit tests for the local speech-transcript sidecar (no model/network)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from scripts import transcribe_audio
from scripts._schema import TWEET_SCHEMA
from scripts.transcribe_audio import (
    TranscribeCandidate,
    TranscriptResult,
    input_hash_for,
    is_cache_hit,
)
from tests.conftest import make_media, make_tweet


@pytest.fixture
def tmp_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    (tmp_path / "data" / "tags").mkdir(parents=True)
    monkeypatch.setattr(transcribe_audio, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(transcribe_audio, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(transcribe_audio, "TAGS_DIR", tmp_path / "data" / "tags")
    monkeypatch.setattr(
        transcribe_audio, "OUT_PATH", tmp_path / "data" / "tags" / "transcripts.parquet"
    )
    monkeypatch.setattr(
        transcribe_audio, "MANIFEST_PATH", tmp_path / "data" / "tags" / "manifest.json"
    )
    yield tmp_path


def _archived_video(media_id: str, sha: str) -> dict[str, Any]:
    media = make_media(media_type="video", media_id=media_id, duration_sec=42.0)
    media["release_asset_url"] = f"https://example.invalid/{media_id}.mp4"
    media["sha256"] = sha
    media["archive_status"] = "archived"
    media["bytes"] = 1234567
    return media


def _write_handle(repo: Path, handle: str, tweets: list[dict[str, Any]]) -> Path:
    path = repo / "data" / f"{handle}.parquet"
    pl.DataFrame(tweets, schema=TWEET_SCHEMA, strict=False).write_parquet(path, compression="zstd")
    return path


def _stub(text: str) -> Any:
    def _t(_c: TranscribeCandidate) -> TranscriptResult:
        return TranscriptResult(
            status="ok" if text else "empty-transcript",
            language="en",
            language_prob=0.99,
            audio_duration_sec=42.0,
            sample_duration_sec=42.0,
            segment_count=1,
            text=text,
            avg_logprob=-0.2,
        )

    return _t


def test_transcribes_and_writes_text(tmp_corpus: Path) -> None:
    p = _write_handle(
        tmp_corpus,
        "DHSgov",
        [make_tweet("t1", handle="DHSgov", media=[_archived_video("13_1", "shaA")])],
    )
    out = tmp_corpus / "data" / "tags" / "transcripts.parquet"
    stats = transcribe_audio.run(
        parquets=[p], out_path=out, transcriber=_stub("We will make America safe again.")
    )
    assert stats["status_ok"] == 1
    df = pl.read_parquet(out)
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["text"] == "We will make America safe again."
    assert row["status"] == "ok"
    assert row["language"] == "en"


def test_cache_hit_reuses_row_and_keeps_timestamp(tmp_corpus: Path) -> None:
    p = _write_handle(
        tmp_corpus,
        "DHSgov",
        [make_tweet("t1", handle="DHSgov", media=[_archived_video("13_1", "shaA")])],
    )
    out = tmp_corpus / "data" / "tags" / "transcripts.parquet"
    transcribe_audio.run(parquets=[p], out_path=out, transcriber=_stub("first pass"))
    first = pl.read_parquet(out).row(0, named=True)

    def _boom(_c: TranscribeCandidate) -> TranscriptResult:
        raise AssertionError("transcriber should not run on a cache hit")

    stats = transcribe_audio.run(parquets=[p], out_path=out, transcriber=_boom)
    assert stats["cache_hits"] == 1
    second = pl.read_parquet(out).row(0, named=True)
    assert second["text"] == "first pass"
    assert second["generated_at"] == first["generated_at"]


def test_manifest_generated_at_stable_on_noop(tmp_corpus: Path) -> None:
    p = _write_handle(
        tmp_corpus,
        "DHSgov",
        [make_tweet("t1", handle="DHSgov", media=[_archived_video("13_1", "shaA")])],
    )
    out = tmp_corpus / "data" / "tags" / "transcripts.parquet"
    man = tmp_corpus / "data" / "tags" / "manifest.json"
    transcribe_audio.run(parquets=[p], out_path=out, transcriber=_stub("hello"))
    ts1 = json.loads(man.read_text())["layers"]["transcripts"]["generated_at"]
    transcribe_audio.run(parquets=[p], out_path=out, transcriber=_stub("hello"))
    ts2 = json.loads(man.read_text())["layers"]["transcripts"]["generated_at"]
    assert ts1 == ts2


def test_run_skips_cleanly_when_model_unavailable(
    tmp_corpus: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = _write_handle(
        tmp_corpus,
        "DHSgov",
        [make_tweet("t1", handle="DHSgov", media=[_archived_video("13_1", "shaA")])],
    )
    out = tmp_corpus / "data" / "tags" / "transcripts.parquet"
    monkeypatch.setattr(transcribe_audio, "load_whisper_model", lambda _model: None)
    stats = transcribe_audio.run(parquets=[p], out_path=out)
    assert stats.get("skipped_no_asr") == 1
    # No model -> the sidecar is left untouched (no all-skipped rewrite/churn).
    assert not out.exists()


def test_scoped_run_preserves_prior_transcripts(tmp_corpus: Path) -> None:
    # A run scoped to one handle (or tweet-ids file) must carry forward
    # transcripts for media it didn't revisit, instead of shrinking the sidecar.
    out = tmp_corpus / "data" / "tags" / "transcripts.parquet"
    pa = _write_handle(
        tmp_corpus,
        "DHSgov",
        [make_tweet("t1", handle="DHSgov", media=[_archived_video("13_1", "shaA")])],
    )
    transcribe_audio.run(parquets=[pa], out_path=out, transcriber=_stub("alpha"))
    pb = _write_handle(
        tmp_corpus,
        "ICEgov",
        [make_tweet("t2", handle="ICEgov", media=[_archived_video("13_2", "shaB")])],
    )
    transcribe_audio.run(parquets=[pb], out_path=out, transcriber=_stub("bravo"))
    df = pl.read_parquet(out)
    texts = {r["media_sha256"]: r["text"] for r in df.iter_rows(named=True)}
    assert texts == {"shaA": "alpha", "shaB": "bravo"}
    manifest = json.loads((tmp_corpus / "data" / "tags" / "manifest.json").read_text())
    assert manifest["layers"]["transcripts"]["row_count"] == 2
    assert manifest["layers"]["transcripts"]["status_counts"] == {"ok": 2}


def test_is_cache_hit_respects_version_and_model() -> None:
    base = {
        "transcriber_version": transcribe_audio.TRANSCRIBER_VERSION,
        "model": "base",
        "status": "ok",
    }
    assert is_cache_hit(base, model="base")
    assert not is_cache_hit({**base, "model": "small"}, model="base")
    assert not is_cache_hit({**base, "transcriber_version": "old"}, model="base")
    assert not is_cache_hit({**base, "status": "asr-failed"}, model="base")


def test_input_hash_changes_with_model() -> None:
    cand = TranscribeCandidate("t1", "DHSgov", "13_1", "video", "shaA", "https://x/y.mp4", 42.0, 1)
    assert input_hash_for(cand, model="base") != input_hash_for(cand, model="small")
