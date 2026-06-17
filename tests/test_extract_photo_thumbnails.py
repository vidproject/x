"""Unit tests for the photo-thumbnail sidecar (no ffmpeg/network)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from scripts import extract_photo_thumbnails as ept
from scripts._schema import TWEET_SCHEMA
from scripts.extract_photo_thumbnails import PhotoCandidate, ThumbResult, is_cache_hit
from tests.conftest import make_media, make_tweet


@pytest.fixture
def tmp_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    (tmp_path / "data" / "tags").mkdir(parents=True)
    (tmp_path / "data" / "thumbnails" / "photo").mkdir(parents=True)
    monkeypatch.setattr(ept, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ept, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(ept, "TAGS_DIR", tmp_path / "data" / "tags")
    monkeypatch.setattr(ept, "THUMBNAILS_DIR", tmp_path / "data" / "thumbnails" / "photo")
    monkeypatch.setattr(ept, "OUT_PATH", tmp_path / "data" / "tags" / "photo_thumbnails.parquet")
    monkeypatch.setattr(ept, "MANIFEST_PATH", tmp_path / "data" / "tags" / "manifest.json")
    yield tmp_path


def _archived_photo(media_id: str, sha: str) -> dict[str, Any]:
    media = make_media(media_type="photo", media_id=media_id)
    media["release_asset_url"] = f"https://example.invalid/{media_id}.jpg"
    media["sha256"] = sha
    media["archive_status"] = "archived"
    return media


def _write_handle(repo: Path, handle: str, tweets: list[dict[str, Any]]) -> Path:
    path = repo / "data" / f"{handle}.parquet"
    pl.DataFrame(tweets, schema=TWEET_SCHEMA, strict=False).write_parquet(path, compression="zstd")
    return path


def _ok_stub(_c: PhotoCandidate) -> ThumbResult:
    return ThumbResult(
        status="ok",
        thumbnail_path="data/thumbnails/photo/shaA.jpg",
        thumbnail_sha256="t-sha",
        thumbnail_width=160,
        thumbnail_height=90,
        thumbnail_bytes=4096,
    )


def test_writes_thumbnail_row(tmp_corpus: Path) -> None:
    p = _write_handle(
        tmp_corpus,
        "DHSgov",
        [make_tweet("t1", handle="DHSgov", media=[_archived_photo("3_1", "shaA")])],
    )
    out = tmp_corpus / "data" / "tags" / "photo_thumbnails.parquet"
    stats = ept.run(parquets=[p], out_path=out, extractor=_ok_stub)
    assert stats["status_ok"] == 1
    row = pl.read_parquet(out).row(0, named=True)
    assert row["status"] == "ok"
    assert row["thumbnail_path"] == "data/thumbnails/photo/shaA.jpg"
    assert row["thumbnail_width"] == 160


def test_only_tweet_ids_filter(tmp_corpus: Path) -> None:
    p = _write_handle(
        tmp_corpus,
        "DHSgov",
        [
            make_tweet("t1", handle="DHSgov", media=[_archived_photo("3_1", "shaA")]),
            make_tweet("t2", handle="DHSgov", media=[_archived_photo("3_2", "shaB")]),
        ],
    )
    out = tmp_corpus / "data" / "tags" / "photo_thumbnails.parquet"
    stats = ept.run(parquets=[p], out_path=out, extractor=_ok_stub, only_tweet_ids={"t2"})
    assert stats["attempted"] == 1
    df = pl.read_parquet(out)
    assert df.height == 1
    assert df.row(0, named=True)["tweet_id"] == "t2"


def test_only_tweet_ids_filter_preserves_existing_rows(tmp_corpus: Path) -> None:
    p = _write_handle(
        tmp_corpus,
        "DHSgov",
        [
            make_tweet("t1", handle="DHSgov", media=[_archived_photo("3_1", "shaA")]),
            make_tweet("t2", handle="DHSgov", media=[_archived_photo("3_2", "shaB")]),
        ],
    )
    out = tmp_corpus / "data" / "tags" / "photo_thumbnails.parquet"
    ept.run(parquets=[p], out_path=out, extractor=_ok_stub)

    def updated(_c: PhotoCandidate) -> ThumbResult:
        return ThumbResult(
            status="ok",
            thumbnail_path="data/thumbnails/photo/updated.jpg",
            thumbnail_sha256="updated",
            thumbnail_width=160,
            thumbnail_height=90,
            thumbnail_bytes=4096,
        )

    stats = ept.run(
        parquets=[p],
        out_path=out,
        extractor=updated,
        force=True,
        only_tweet_ids={"t2"},
    )
    assert stats["attempted"] == 1
    df = pl.read_parquet(out)
    assert set(df["tweet_id"].to_list()) == {"t1", "t2"}
    by_tweet = {row["tweet_id"]: row for row in df.to_dicts()}
    assert by_tweet["t1"]["thumbnail_path"] == "data/thumbnails/photo/shaA.jpg"
    assert by_tweet["t2"]["thumbnail_path"] == "data/thumbnails/photo/updated.jpg"


def test_is_cache_hit_requires_existing_thumbnail(tmp_corpus: Path) -> None:
    thumb = tmp_corpus / "data" / "thumbnails" / "photo" / "shaA.jpg"
    thumb.write_bytes(b"x")
    cached = {
        "extractor_version": ept.EXTRACTOR_VERSION,
        "status": "ok",
        "thumbnail_path": "data/thumbnails/photo/shaA.jpg",
    }
    assert is_cache_hit(cached)
    assert not is_cache_hit({**cached, "thumbnail_path": "data/thumbnails/photo/missing.jpg"})
    assert not is_cache_hit({**cached, "extractor_version": "old"})
    assert not is_cache_hit({**cached, "status": "fetch-failed"})
