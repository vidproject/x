"""Regression coverage for the paid image/video recognition tier."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from scripts import tag_lexical, tag_media_llm
from scripts._schema import KEYFRAMES_SCHEMA, MEDIA_VISION_SCHEMA, TWEET_SCHEMA
from scripts.tag_media_llm import (
    PRIMARY_PROVIDER,
    MediaLlmResult,
    WatermarkResult,
    extract_response_text,
    parse_model_json,
)
from tests.conftest import make_media, make_tweet


@pytest.fixture
def tmp_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    (tmp_path / "data" / "tags").mkdir(parents=True)
    (tmp_path / "data" / "derived" / "keyframes" / ("k" * 64)).mkdir(parents=True)
    monkeypatch.setattr(tag_media_llm, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tag_media_llm, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(tag_media_llm, "TAGS_DIR", tmp_path / "data" / "tags")
    monkeypatch.setattr(
        tag_media_llm, "OUT_PATH", tmp_path / "data" / "tags" / "media_llm.parquet"
    )
    monkeypatch.setattr(
        tag_media_llm, "KEYFRAMES_PATH", tmp_path / "data" / "tags" / "keyframes.parquet"
    )
    monkeypatch.setattr(
        tag_media_llm, "MANIFEST_PATH", tmp_path / "data" / "tags" / "manifest.json"
    )
    yield tmp_path


def _write_handle_parquet(repo: Path, handle: str, tweets: list[dict[str, Any]]) -> Path:
    path = repo / "data" / f"{handle}.parquet"
    df = pl.DataFrame(tweets, schema=TWEET_SCHEMA, strict=False)
    df.write_parquet(path, compression="zstd")
    return path


def _archived_photo(media_id: str, sha: str) -> dict[str, Any]:
    media = make_media(media_type="photo", media_id=media_id)
    media["release_asset_url"] = f"https://example.invalid/{media_id}.jpg"
    media["sha256"] = sha
    media["archive_status"] = "archived"
    media["bytes"] = 12345
    return media


def _archived_video(media_id: str, sha: str) -> dict[str, Any]:
    media = make_media(media_type="video", media_id=media_id, duration_sec=30.0)
    media["release_asset_url"] = f"https://example.invalid/{media_id}.mp4"
    media["sha256"] = sha
    media["archive_status"] = "archived"
    media["bytes"] = 123456
    return media


def _write_keyframes(repo: Path, *, media_id: str, media_sha: str) -> None:
    frame_path = repo / "data" / "derived" / "keyframes" / media_sha / "000.jpg"
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    frame_path.write_bytes(b"fakejpg")
    rows = [
        {
            "tweet_id": "t-video",
            "account_handle": "DHSgov",
            "media_id": media_id,
            "media_sha256": media_sha,
            "release_asset_url": f"https://example.invalid/{media_id}.mp4",
            "thumbnail_path": None,
            "thumbnail_sha256": None,
            "thumbnail_width": None,
            "thumbnail_height": None,
            "thumbnail_bytes": None,
            "video_duration_sec": 30.0,
            "video_width": 640,
            "video_height": 360,
            "frame_count": 1,
            "frames": [
                {
                    "index": 0,
                    "timestamp_sec": 10.0,
                    "path": f"data/derived/keyframes/{media_sha}/000.jpg",
                    "sha256": "f" * 64,
                    "width": 320,
                    "height": 180,
                    "bytes": 7,
                }
            ],
            "generated_at": "2026-05-20T00:00:00Z",
            "extractor_version": "test",
            "status": "ok",
            "cost_estimate_usd": 0.0,
            "error": None,
        }
    ]
    pl.DataFrame(rows, schema=KEYFRAMES_SCHEMA, strict=False).write_parquet(
        repo / "data" / "tags" / "keyframes.parquet"
    )


def test_extract_response_text_reads_gemini_candidates() -> None:
    payload = {"candidates": [{"content": {"parts": [{"text": '{"description":"ok"}'}]}}]}
    assert extract_response_text(payload) == '{"description":"ok"}'


def test_parse_model_json_accepts_wrapped_json() -> None:
    out = parse_model_json('Here is the result: {"description": "x", "confidence": 0.8}')
    assert out["description"] == "x"
    assert out["confidence"] == 0.8


def test_prompt_for_contains_openai_context() -> None:
    cand = tag_media_llm.MediaLlmCandidate(
        tweet_id="t-1",
        account_handle="DHSgov",
        media_id="m-1",
        media_type="photo",
        media_sha256="a" * 64,
        release_asset_url="https://example.invalid/p.jpg",
        tweet_text="Visible caption says test.",
        image_refs=(tag_media_llm.ImageRef(kind="photo", url="https://example.invalid/p.jpg"),),
    )

    prompt = tag_media_llm.prompt_for(cand)

    assert "Tweet id: t-1" in prompt
    assert "Visible caption says test." in prompt


def test_blank_model_env_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_MEDIA_LLM_MODEL", "")
    monkeypatch.setenv("GEMINI_MEDIA_LLM_MODEL", "   ")

    assert (
        tag_media_llm.env_or_default(
            "OPENAI_MEDIA_LLM_MODEL", tag_media_llm.DEFAULT_OPENAI_MODEL
        )
        == tag_media_llm.DEFAULT_OPENAI_MODEL
    )
    assert (
        tag_media_llm.env_or_default("GEMINI_MEDIA_LLM_MODEL", "gemini-fallback")
        == "gemini-fallback"
    )


def test_run_normalizes_blank_model_name(tmp_corpus: Path) -> None:
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [make_tweet("t-photo", handle="DHSgov", media=[_archived_photo("pic-1", "a" * 64)])],
    )

    tag_media_llm.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        max_items=1,
        model="",
        analyzer=lambda _c: MediaLlmResult(status="ok", description="A visible photo."),
    )

    out = pl.read_parquet(tmp_corpus / "data" / "tags" / "media_llm.parquet")
    assert out.row(0, named=True)["model_version"] == tag_media_llm.DEFAULT_OPENAI_MODEL


def test_dry_run_does_not_call_paid_analyzer(tmp_corpus: Path) -> None:
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [make_tweet("t-photo", handle="DHSgov", media=[_archived_photo("pic-1", "a" * 64)])],
    )

    def fail_if_called(_cand: Any) -> MediaLlmResult:
        raise AssertionError("dry-run must not call the paid analyzer")

    stats = tag_media_llm.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        max_items=1,
        dry_run=True,
        openai_api_key="present-but-unused",
        analyzer=fail_if_called,
    )

    assert stats["candidates"] == 1
    assert stats["would_attempt"] == 1
    assert not (tmp_corpus / "data" / "tags" / "media_llm.parquet").exists()


def test_video_genre_adds_produced_parent_and_ai_tag_is_tentative(tmp_corpus: Path) -> None:
    media_sha = "k" * 64
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [make_tweet("t-video", handle="DHSgov", media=[_archived_video("vid-1", media_sha)])],
    )
    _write_keyframes(tmp_corpus, media_id="vid-1", media_sha=media_sha)

    stats = tag_media_llm.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        max_items=1,
        analyzer=lambda _c: MediaLlmResult(
            status="ok",
            description="Keyframes show a polished synthetic recruitment-style video.",
            summary_text="Produced video with synthetic visual cues.",
            tags=["genre:advertisement", "genre:recruitment", "media:ai-generated"],
            confidence=0.93,
            usage={"promptTokenCount": 1000, "candidatesTokenCount": 100},
        ),
    )
    assert stats["status_ok"] == 1

    out = pl.read_parquet(tmp_corpus / "data" / "tags" / "media_llm.parquet")
    tags = out.row(0, named=True)["tags"]
    by_tag = {entry["tag"]: entry for entry in tags}
    assert "media:produced-video" in by_tag
    assert "genre:advertisement" in by_tag
    assert "genre:recruitment" in by_tag
    assert by_tag["media:ai-generated"]["tentative"] is True
    assert out.row(0, named=True)["model"] == PRIMARY_PROVIDER


def test_ai_generated_tag_is_firm_with_provenance_signal(tmp_corpus: Path) -> None:
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [make_tweet("t-photo", handle="DHSgov", media=[_archived_photo("pic-1", "a" * 64)])],
    )

    tag_media_llm.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        max_items=1,
        analyzer=lambda _c: MediaLlmResult(
            status="ok",
            description="Visible Content Credentials identify AI-generated media.",
            tags=["media:ai-generated"],
            confidence=0.95,
            provenance_signal=True,
        ),
    )

    out = pl.read_parquet(tmp_corpus / "data" / "tags" / "media_llm.parquet")
    tags = out.row(0, named=True)["tags"]
    ai = next(entry for entry in tags if entry["tag"] == "media:ai-generated")
    assert ai["tentative"] is None


def test_suspected_ai_triggers_limited_gemini_watermark_check(tmp_corpus: Path) -> None:
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [make_tweet("t-photo", handle="DHSgov", media=[_archived_photo("pic-1", "a" * 64)])],
    )

    stats = tag_media_llm.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        max_items=1,
        analyzer=lambda _c: MediaLlmResult(
            status="ok",
            description="OpenAI suspects synthetic media from visible cues.",
            tags=["media:ai-generated"],
            confidence=0.8,
            usage={"input_tokens": 100, "output_tokens": 20},
        ),
        watermark_analyzer=lambda _c, _r: WatermarkResult(
            provenance_signal=True,
            description="Visible SynthID/Content Credentials marker.",
            confidence=0.95,
            usage={"promptTokenCount": 20, "candidatesTokenCount": 8},
        ),
    )
    assert stats["gemini_watermark_attempts"] == 1
    assert stats["gemini_watermark_confirmed"] == 1
    assert stats["provider_openai"] == 1

    out = pl.read_parquet(tmp_corpus / "data" / "tags" / "media_llm.parquet")
    row = out.row(0, named=True)
    assert row["model"] == PRIMARY_PROVIDER
    assert "Gemini provenance check" in row["description"]
    ai = next(entry for entry in row["tags"] if entry["tag"] == "media:ai-generated")
    assert ai["tentative"] is None


def test_non_ai_result_does_not_call_gemini_watermark_check(tmp_corpus: Path) -> None:
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [make_tweet("t-photo", handle="DHSgov", media=[_archived_photo("pic-1", "a" * 64)])],
    )

    def fail_if_called(_cand: Any, _result: MediaLlmResult) -> WatermarkResult:
        raise AssertionError("Gemini should not be called for non-suspected-AI media")

    stats = tag_media_llm.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        max_items=1,
        analyzer=lambda _c: MediaLlmResult(
            status="ok",
            description="A normal press-photo with text overlay.",
            tags=["media:text-overlay"],
            confidence=0.8,
        ),
        watermark_analyzer=fail_if_called,
    )
    assert stats["provider_openai"] == 1
    assert "gemini_watermark_attempts" not in stats


def test_missing_keys_preserve_existing_rows(tmp_corpus: Path) -> None:
    existing = [
        {
            "tweet_id": "old",
            "account_handle": "DHSgov",
            "media_id": "m",
            "media_type": "photo",
            "media_sha256": "s",
            "input_hash": "h",
            "generated_at": "2026-05-20T00:00:00Z",
            "model": PRIMARY_PROVIDER,
            "model_version": "gemini-2.5-flash",
            "prompt_hash": "p",
            "description": "cached",
            "summary_text": "cached",
            "confidence": 0.9,
            "cost_estimate_usd": 0.01,
            "status": "ok",
            "tags": [],
            "source_fields": ["release_asset_url"],
            "error": None,
        }
    ]
    tag_media_llm.write_parquet(existing, tmp_corpus / "data" / "tags" / "media_llm.parquet")

    stats = tag_media_llm.run(parquets=[], max_items=1)
    assert stats["skipped_no_api_key"] == 1

    out = pl.read_parquet(tmp_corpus / "data" / "tags" / "media_llm.parquet")
    assert out.height == 1
    assert out.row(0, named=True)["tweet_id"] == "old"


def test_manifest_records_paid_layer(tmp_corpus: Path) -> None:
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [make_tweet("t-photo", handle="DHSgov", media=[_archived_photo("pic-1", "a" * 64)])],
    )
    tag_media_llm.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        max_items=1,
        analyzer=lambda _c: MediaLlmResult(status="ok", description="A visible photo."),
    )
    manifest = json.loads((tmp_corpus / "data" / "tags" / "manifest.json").read_text())
    layer = manifest["layers"]["media_llm"]
    assert layer["rows"] == 1
    assert layer["status_counts"] == {"ok": 1}


def test_lexical_media_context_reads_media_llm_sidecar(
    tmp_corpus: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = {
        "tweet_id": "llm-1",
        "account_handle": "DHSgov",
        "media_id": "m",
        "media_type": "photo",
        "media_sha256": "s",
        "input_hash": "h",
        "generated_at": "2026-05-20T00:00:00Z",
        "model": PRIMARY_PROVIDER,
        "model_version": "gemini-2.5-flash",
        "prompt_hash": "p",
        "description": "Visible text says Make America Great Again.",
        "summary_text": "MAGA text overlay.",
        "confidence": 0.9,
        "cost_estimate_usd": 0.01,
        "status": "ok",
        "tags": [
            {
                "tag": "media:text-overlay",
                "tentative": None,
                "source": "gemini-vision",
                "span_start": None,
                "span_end": None,
            }
        ],
        "source_fields": ["release_asset_url"],
        "error": None,
    }
    pl.DataFrame([row], schema=MEDIA_VISION_SCHEMA, strict=False).write_parquet(
        tmp_corpus / "data" / "tags" / "media_llm.parquet"
    )
    monkeypatch.setattr(tag_lexical, "TAGS_DIR", tmp_corpus / "data" / "tags")

    context = tag_lexical.load_media_context_map()

    assert "MAGA text overlay." in context["llm-1"]["text"]
    assert context["llm-1"]["tags"][0]["tag"] == "media:text-overlay"
