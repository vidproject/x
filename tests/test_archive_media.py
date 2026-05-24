from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import httpx
import polars as pl

from scripts import archive_media
from scripts._schema import TWEET_SCHEMA
from tests.conftest import make_media, make_tweet


class FakeGitHub:
    owner = "vidproject"
    repo = "x"

    def __init__(
        self,
        assets_by_tag: dict[str, dict[str, dict[str, Any]]] | None = None,
        *,
        always_422: bool = False,
    ) -> None:
        self.assets_by_tag = assets_by_tag or {}
        self.always_422 = always_422
        self.uploads: list[tuple[str, str]] = []
        self.releases: dict[str, dict[str, Any]] = {}
        self.tag_by_id: dict[int, str] = {}
        for tag in self.assets_by_tag:
            self._ensure_release(tag)

    def get_release(self, tag: str) -> dict[str, Any] | None:
        return self.releases.get(tag)

    def get_or_create_release(self, tag: str, name: str) -> dict[str, Any]:
        return self._ensure_release(tag, name)

    def list_existing_assets(self, release_id: int) -> dict[str, dict[str, Any]]:
        tag = self.tag_by_id[release_id]
        return dict(self.assets_by_tag.setdefault(tag, {}))

    def upload_asset(
        self,
        upload_url: str,
        name: str,
        content_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        del content_type
        tag = upload_url.removeprefix("upload://")
        assets = self.assets_by_tag.setdefault(tag, {})
        if self.always_422 or len(assets) >= archive_media.RELEASE_ASSET_LIMIT or name in assets:
            request = httpx.Request("POST", "https://uploads.github.com/assets")
            response = httpx.Response(422, request=request, json={"message": "Validation Failed"})
            raise httpx.HTTPStatusError("Validation Failed", request=request, response=response)
        asset = {
            "name": name,
            "browser_download_url": (
                f"https://github.com/{self.owner}/{self.repo}/releases/download/{tag}/{name}"
            ),
            "size": len(data),
        }
        assets[name] = asset
        self.uploads.append((tag, name))
        return asset

    def _ensure_release(self, tag: str, name: str | None = None) -> dict[str, Any]:
        release = self.releases.get(tag)
        if release is not None:
            return release
        release_id = len(self.releases) + 1
        release = {
            "id": release_id,
            "tag_name": tag,
            "name": name or tag,
            "upload_url": f"upload://{tag}",
        }
        self.releases[tag] = release
        self.tag_by_id[release_id] = tag
        self.assets_by_tag.setdefault(tag, {})
        return release


def _asset(tag: str, name: str, size: int = 10) -> dict[str, Any]:
    return {
        "name": name,
        "browser_download_url": f"https://github.com/vidproject/x/releases/download/{tag}/{name}",
        "size": size,
    }


def _full_release(tag: str) -> dict[str, dict[str, Any]]:
    return {f"existing-{i}.mp4": _asset(tag, f"existing-{i}.mp4") for i in range(1000)}


def _write_handle(path: Path, media: dict[str, Any]) -> None:
    row = make_tweet("tweet-1", handle="DHSgov", media=[media])
    pl.DataFrame([row], schema=TWEET_SCHEMA, strict=False).write_parquet(path)


def _read_media(path: Path) -> dict[str, Any]:
    row = pl.read_parquet(path).to_dicts()[0]
    media: dict[str, Any] = row["media"][0]
    return media


def test_archive_uploads_to_numbered_release_when_primary_is_full(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    parquet_path = tmp_path / "DHSgov.parquet"
    _write_handle(parquet_path, make_media(media_id="m-new"))
    gh = FakeGitHub({"media-DHSgov": _full_release("media-DHSgov")})
    monkeypatch.setattr(archive_media, "fetch_bytes", lambda url, http: b"video-bytes")

    archived, failed, skipped = archive_media.archive_one_handle(
        "DHSgov",
        parquet_path,
        cast(archive_media.GitHubReleaseClient, gh),
        cast(httpx.Client, object()),
        1,
    )

    assert (archived, failed, skipped) == (1, 0, 0)
    assert gh.uploads == [("media-DHSgov-0002", "m-new.mp4")]
    media = _read_media(parquet_path)
    assert media["archive_status"] == "archived"
    assert "/releases/download/media-DHSgov-0002/m-new.mp4" in media["release_asset_url"]


def test_archive_stitches_existing_asset_from_numbered_release_without_refetch(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    parquet_path = tmp_path / "DHSgov.parquet"
    media = make_media(media_id="m-existing")
    media["release_asset_url"] = (
        "https://github.com/vidproject/x/releases/download/media-DHSgov/m-existing.mp4"
    )
    media["archive_status"] = "archived"
    _write_handle(parquet_path, media)
    gh = FakeGitHub(
        {
            "media-DHSgov": _full_release("media-DHSgov"),
            "media-DHSgov-0002": {
                "m-existing.mp4": _asset("media-DHSgov-0002", "m-existing.mp4", size=123)
            },
        }
    )
    monkeypatch.setattr(
        archive_media,
        "fetch_bytes",
        lambda url, http: (_ for _ in ()).throw(AssertionError("should not fetch")),
    )

    archived, failed, skipped = archive_media.archive_one_handle(
        "DHSgov",
        parquet_path,
        cast(archive_media.GitHubReleaseClient, gh),
        cast(httpx.Client, object()),
        1,
    )

    assert (archived, failed, skipped) == (1, 0, 0)
    assert gh.uploads == []
    media = _read_media(parquet_path)
    assert media["archive_status"] == "archived"
    assert media["bytes"] == 123
    assert media["release_asset_url"].endswith("/media-DHSgov-0002/m-existing.mp4")


def test_archive_does_not_construct_download_url_for_unverified_422(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    parquet_path = tmp_path / "DHSgov.parquet"
    media = make_media(media_id="m-422")
    media["release_asset_url"] = (
        "https://github.com/vidproject/x/releases/download/media-DHSgov/m-422.mp4"
    )
    media["archive_status"] = "archived"
    _write_handle(parquet_path, media)
    gh = FakeGitHub(always_422=True)
    monkeypatch.setattr(archive_media, "fetch_bytes", lambda url, http: b"video-bytes")

    archived, failed, skipped = archive_media.archive_one_handle(
        "DHSgov",
        parquet_path,
        cast(archive_media.GitHubReleaseClient, gh),
        cast(httpx.Client, object()),
        1,
    )

    assert (archived, failed, skipped) == (0, 1, 0)
    media = _read_media(parquet_path)
    assert media["archive_status"] == "failed"
    assert media["release_asset_url"] is None


def test_rate_limit_wait_seconds_honors_retry_after() -> None:
    r = httpx.Response(403, headers={"Retry-After": "30"}, json={"message": "secondary rate limit"})
    assert archive_media.rate_limit_wait_seconds(r) == 30.0


def test_rate_limit_wait_seconds_caps_and_defaults() -> None:
    # Retry-After is capped so a bogus huge value can't stall forever.
    big = httpx.Response(429, headers={"Retry-After": "99999"}, json={"message": "rate limit"})
    assert archive_media.rate_limit_wait_seconds(big) == 300.0
    # Rate-limited 403 with no Retry-After falls back to a sane default.
    nohdr = httpx.Response(403, json={"message": "You have exceeded a secondary rate limit"})
    assert archive_media.rate_limit_wait_seconds(nohdr) == 60.0


def test_rate_limit_wait_seconds_ignores_non_rate_limit() -> None:
    # A 200 and a genuine permission/404 403 are not rate limits → no wait.
    assert archive_media.rate_limit_wait_seconds(httpx.Response(200)) is None
    perm = httpx.Response(403, json={"message": "Resource not accessible by personal access token"})
    assert archive_media.rate_limit_wait_seconds(perm) is None
