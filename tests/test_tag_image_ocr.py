"""Unit tests for the image OCR sidecar."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from scripts import tag_image_ocr
from scripts._schema import KEYFRAMES_SCHEMA, TWEET_SCHEMA
from scripts.tag_image_ocr import OCR_VERSION, OcrCandidate, OcrResult, parse_tesseract_tsv
from tests.conftest import make_media, make_tweet


@pytest.fixture
def tmp_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    (tmp_path / "data" / "tags").mkdir(parents=True)
    (tmp_path / "data" / "derived" / "keyframes" / ("k" * 64)).mkdir(parents=True)
    monkeypatch.setattr(tag_image_ocr, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(tag_image_ocr, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(tag_image_ocr, "TAGS_DIR", tmp_path / "data" / "tags")
    monkeypatch.setattr(tag_image_ocr, "OUT_PATH", tmp_path / "data" / "tags" / "image_ocr.parquet")
    monkeypatch.setattr(
        tag_image_ocr, "KEYFRAMES_PATH", tmp_path / "data" / "tags" / "keyframes.parquet"
    )
    monkeypatch.setattr(
        tag_image_ocr, "MANIFEST_PATH", tmp_path / "data" / "tags" / "manifest.json"
    )
    monkeypatch.setattr(tag_image_ocr, "tesseract_available", lambda: True)
    monkeypatch.setattr(tag_image_ocr, "tesseract_version", lambda: OCR_VERSION)
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


def test_parse_tesseract_tsv_returns_text_and_mean_confidence() -> None:
    text, confidence = parse_tesseract_tsv(
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t91\tGod\n"
        "5\t1\t1\t1\t1\t2\t0\t0\t10\t10\t85\tbless\n"
        "5\t1\t1\t1\t1\t3\t0\t0\t10\t10\t-1\t\n"
    )
    assert text == "God bless"
    assert confidence == pytest.approx(0.88)


def test_photo_discovery_skips_unarchived_and_non_photo(tmp_corpus: Path) -> None:
    archived = _archived_photo("pic-1", "a" * 64)
    pending = make_media(media_type="photo", media_id="pic-pending")
    video = make_media(media_type="video", media_id="vid-1")
    video["release_asset_url"] = "https://example.invalid/vid.mp4"
    video["sha256"] = "b" * 64
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [
            make_tweet("t-1", handle="DHSgov", media=[archived]),
            make_tweet("t-2", handle="DHSgov", media=[pending]),
            make_tweet("t-3", handle="DHSgov", media=[video]),
        ],
    )

    cands = list(tag_image_ocr.discover_photo_candidates([tmp_corpus / "data" / "DHSgov.parquet"]))
    assert len(cands) == 1
    assert cands[0].tweet_id == "t-1"
    assert cands[0].source_kind == "photo"


def test_keyframe_discovery_uses_existing_frame_files(tmp_corpus: Path) -> None:
    frame_path = tmp_corpus / "data" / "derived" / "keyframes" / ("k" * 64) / "000.jpg"
    frame_path.write_bytes(b"fakejpg")
    rows = [
        {
            "tweet_id": "t-video",
            "account_handle": "DHSgov",
            "media_id": "vid-1",
            "media_sha256": "k" * 64,
            "release_asset_url": "https://example.invalid/vid.mp4",
            "thumbnail_path": None,
            "thumbnail_sha256": None,
            "thumbnail_width": None,
            "thumbnail_height": None,
            "thumbnail_bytes": None,
            "video_duration_sec": 20.0,
            "video_width": 640,
            "video_height": 360,
            "frame_count": 1,
            "frames": [
                {
                    "index": 0,
                    "timestamp_sec": 10.0,
                    "path": "data/derived/keyframes/" + ("k" * 64) + "/000.jpg",
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
        tmp_corpus / "data" / "tags" / "keyframes.parquet"
    )
    cands = list(tag_image_ocr.discover_keyframe_candidates())
    assert len(cands) == 1
    assert cands[0].tweet_id == "t-video"
    assert cands[0].source_kind == "keyframe"
    assert cands[0].source_sha256 == "f" * 64


def test_run_writes_sidecar_and_manifest(tmp_corpus: Path) -> None:
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [make_tweet("t-1", handle="DHSgov", media=[_archived_photo("pic-1", "a" * 64)])],
    )

    stats = tag_image_ocr.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        ocr_runner=lambda _c: OcrResult(
            status="ok", text="God bless Border Patrol and ICE", confidence=0.93
        ),
    )
    assert stats["attempted"] == 1
    assert stats["analyzed"] == 1
    assert stats["status_ok"] == 1

    out = pl.read_parquet(tmp_corpus / "data" / "tags" / "image_ocr.parquet")
    assert out.height == 1
    assert out["text"].to_list() == ["God bless Border Patrol and ICE"]

    manifest = json.loads((tmp_corpus / "data" / "tags" / "manifest.json").read_text())
    assert manifest["layers"]["image_ocr"]["row_count"] == 1
    assert manifest["layers"]["image_ocr"]["status_counts"] == {"ok": 1}


def test_failures_are_not_cached(tmp_corpus: Path) -> None:
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [make_tweet("t-1", handle="DHSgov", media=[_archived_photo("pic-1", "a" * 64)])],
    )
    tag_image_ocr.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        ocr_runner=lambda _c: OcrResult(status="fetch-failed", error="503"),
    )
    calls: list[str] = []

    def ocr(cand: OcrCandidate) -> OcrResult:
        calls.append(cand.media_id)
        return OcrResult(status="ok", text="ICE", confidence=0.8)

    tag_image_ocr.run(parquets=[tmp_corpus / "data" / "DHSgov.parquet"], ocr_runner=ocr)
    assert calls == ["pic-1"]


def test_max_items_counts_attempts(tmp_corpus: Path) -> None:
    media = [_archived_photo(f"pic-{i}", chr(ord("a") + i) * 64) for i in range(4)]
    _write_handle_parquet(
        tmp_corpus,
        "DHSgov",
        [make_tweet(f"t-{i}", handle="DHSgov", media=[m]) for i, m in enumerate(media)],
    )
    stats = tag_image_ocr.run(
        parquets=[tmp_corpus / "data" / "DHSgov.parquet"],
        max_items=2,
        dry_run=True,
        ocr_runner=lambda _c: OcrResult(status="skipped-no-tesseract"),
    )
    assert stats["attempted"] == 2
    assert stats["skipped_max_items"] == 2
