"""Layer-2 keyframe extraction.

For every archived video in the corpus, pull N evenly-spaced keyframes
via ffmpeg and record the metadata into ``data/tags/keyframes.parquet``.
The frame JPEGs themselves go under
``data/derived/keyframes/<media_sha256>/<NNN>.jpg`` and are gitignored —
they're deterministic from the archived video + extractor version + frame
indices, so downstream layers re-extract on demand.

Used by downstream image-analysis layers such as OCR and CLIP labels as
the shared frame catalog. The ``media_sha256`` is the natural cache key: a
re-archived identical video doesn't trigger re-extraction.

The script enforces a per-run cap (``--max-items``) and uses two cheap
gates before invoking ffmpeg:

1. ``release_asset_url`` must be set — we never pull from X CDNs.
2. ``media_sha256`` must be set — we won't blindly key off original_url
   because X sometimes returns the same canonical URL for different
   bitrates and we'd cross-contaminate the cache.

Idempotent: rows whose ``extractor_version`` + ``media_sha256`` match
the existing sidecar entry are skipped (``--force`` bypasses).

Run with::

    uv run python -m scripts.extract_video_frames

CI environments (GitHub Actions) ship with ffmpeg; locally the script
records ``status == "skipped-no-ffmpeg"`` and moves on instead of
failing the whole run.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from scripts._logging import configure
from scripts._schema import KEYFRAMES_SCHEMA, empty_keyframes_dataframe

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"
DERIVED_DIR = DATA_DIR / "derived" / "keyframes"
THUMBNAILS_DIR = DATA_DIR / "thumbnails" / "video"
OUT_PATH = TAGS_DIR / "keyframes.parquet"
MANIFEST_PATH = TAGS_DIR / "manifest.json"

EXTRACTOR_VERSION = "ffmpeg-keyframes-v2"
DEFAULT_FRAMES = 5
DEFAULT_FRAME_WIDTH = 640
DEFAULT_THUMBNAIL_WIDTH = 96
DEFAULT_THUMBNAIL_QUALITY = 9
DEFAULT_JPEG_QUALITY = 4  # ffmpeg -q:v scale (lower = better; 4 ≈ ~85 quality)
HTTP_TIMEOUT_SECS = 60.0
MAX_VIDEO_BYTES = 600 * 1024 * 1024  # 600 MiB — anything bigger we skip & flag.


# --------------------------------------------------------------------------
# Candidate discovery


@dataclass(frozen=True)
class VideoCandidate:
    tweet_id: str
    account_handle: str
    media_id: str
    media_sha256: str
    release_asset_url: str
    media_type: str
    declared_duration_sec: float
    declared_width: int
    declared_height: int


def discover_candidates(parquets: list[Path]) -> Iterator[VideoCandidate]:
    """Walk every canonical parquet and yield one ``VideoCandidate`` per
    archived video / animated-gif media item.

    Skips items missing ``release_asset_url`` or ``sha256`` — both are
    required as cache keys and as the fetch source. Without them the
    candidate isn't actionable.
    """
    for path in parquets:
        try:
            df = pl.read_parquet(path)
        except Exception:
            LOG.exception("keyframes: could not read parquet", path=str(path))
            continue
        for tweet in df.iter_rows(named=True):
            media = tweet.get("media") or []
            if not isinstance(media, list):
                continue
            for item in media:
                if not isinstance(item, dict):
                    continue
                media_type = str(item.get("media_type") or "")
                if media_type not in {"video", "animated_gif"}:
                    continue
                asset_url = str(item.get("release_asset_url") or "")
                sha = str(item.get("sha256") or "")
                media_id = str(item.get("media_id") or "")
                if not asset_url or not sha or not media_id:
                    continue
                yield VideoCandidate(
                    tweet_id=str(tweet.get("tweet_id") or ""),
                    account_handle=str(tweet.get("account_handle") or ""),
                    media_id=media_id,
                    media_sha256=sha,
                    release_asset_url=asset_url,
                    media_type=media_type,
                    declared_duration_sec=_to_float(item.get("duration_sec")),
                    declared_width=_to_int(item.get("width")),
                    declared_height=_to_int(item.get("height")),
                )


def _to_float(v: Any) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _to_int(v: Any) -> int:
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------
# Cache (read existing sidecar, decide skip vs. re-extract)


def load_existing_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return pl.read_parquet(path).to_dicts()


def load_existing_index(path: Path) -> dict[str, dict[str, Any]]:
    """Return ``{media_sha256: row_dict}`` from any existing sidecar.

    Empty when the sidecar doesn't exist yet. ``media_sha256`` is the
    cache key — duplicate uploads of the same physical video share an
    entry regardless of which tweet they're attached to.
    """
    out: dict[str, dict[str, Any]] = {}
    for row in load_existing_rows(path):
        sha = str(row.get("media_sha256") or "")
        if sha:
            out[sha] = row
    return out


def row_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("tweet_id") or ""),
        str(row.get("media_id") or ""),
        str(row.get("media_sha256") or ""),
    )


def merge_existing_rows(
    rows: list[dict[str, Any]], existing_rows: list[dict[str, Any]], *, preserve_existing: bool
) -> list[dict[str, Any]]:
    if not preserve_existing:
        return rows
    merged: dict[tuple[str, str, str], dict[str, Any]] = {
        row_key(row): row for row in existing_rows
    }
    for row in rows:
        merged[row_key(row)] = row
    return list(merged.values())


def is_cache_hit(cached: dict[str, Any], extractor_version: str) -> bool:
    """A cached row is reusable when it was produced by the current
    extractor version AND the extraction reportedly succeeded. Failures
    are not cached — re-run gets a fresh attempt."""
    if not cached:
        return False
    if str(cached.get("extractor_version") or "") != extractor_version:
        return False
    return str(cached.get("status") or "") == "ok"


# --------------------------------------------------------------------------
# Extraction primitives (ffmpeg / ffprobe wrappers)


@dataclass
class FrameRecord:
    index: int
    timestamp_sec: float
    path: str
    sha256: str
    width: int
    height: int
    bytes: int


@dataclass
class ThumbnailRecord:
    path: str
    sha256: str
    width: int
    height: int
    bytes: int


@dataclass
class ExtractResult:
    status: str  # "ok" | "fetch-failed" | "ffprobe-failed" | "ffmpeg-failed" | "skipped-no-ffmpeg" | "video-too-large" | "no-frames"
    frames: list[FrameRecord]
    video_duration_sec: float
    video_width: int
    video_height: int
    error: str | None
    thumbnail: ThumbnailRecord | None = None


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def ffprobe_video(path: Path) -> tuple[float, int, int]:
    """Return (duration_sec, width, height). Raises CalledProcessError on
    ffprobe failure so the caller can record a structured error."""
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(path),
        ],
        timeout=60,
    )
    data = json.loads(out)
    fmt = data.get("format") or {}
    duration = _to_float(fmt.get("duration"))
    streams = data.get("streams") or []
    width = _to_int(streams[0].get("width")) if streams else 0
    height = _to_int(streams[0].get("height")) if streams else 0
    return duration, width, height


def evenly_spaced_timestamps(duration: float, n: int) -> list[float]:
    """``n`` timestamps placed at (i + 0.5) / n of the duration. Skips
    the absolute 0.0 and ``duration`` endpoints because ffmpeg often
    returns black or fade-in frames at those instants."""
    if duration <= 0 or n <= 0:
        return []
    return [(i + 0.5) / n * duration for i in range(n)]


def run_ffmpeg_frame(
    video: Path,
    timestamp: float,
    out_path: Path,
    frame_width: int,
    *,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
) -> None:
    """Pull a single frame at ``timestamp`` (seconds). Uses ``-ss`` BEFORE
    ``-i`` for fast input seeking; the resulting frame may be slightly off
    the requested timestamp at I-frame boundaries but is more than good
    enough for OCR / CLIP."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            f"scale={frame_width}:-2",
            "-q:v",
            str(jpeg_quality),
            str(out_path),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )


def fetch_to_tempfile(url: str, http: httpx.Client) -> Path:
    """Download a release asset to a temp file. Caps at ``MAX_VIDEO_BYTES``
    to keep CI runners from OOMing on outlier uploads."""
    with http.stream("GET", url, timeout=HTTP_TIMEOUT_SECS, follow_redirects=True) as resp:
        resp.raise_for_status()
        total = 0
        suffix = Path(url.split("?", 1)[0]).suffix or ".bin"
        # delete=False so subprocess can read it after we close the handle;
        # the caller is responsible for unlinking.
        with tempfile.NamedTemporaryFile(
            prefix="imm-archive-vid-", suffix=suffix, delete=False
        ) as fh:
            tmp_path = Path(fh.name)
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                total += len(chunk)
                if total > MAX_VIDEO_BYTES:
                    tmp_path.unlink(missing_ok=True)
                    raise ValueError(f"video exceeds {MAX_VIDEO_BYTES} bytes")
                fh.write(chunk)
        return tmp_path


def hash_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def repo_relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def jpeg_dimensions(path: Path) -> tuple[int, int]:
    """Cheapest possible JPEG dimension probe: ffprobe one frame.
    Returns ``(0, 0)`` when probing fails — the dimensions are nice-to-have,
    not required for downstream layers (they can re-probe)."""
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "json",
                str(path),
            ],
            timeout=15,
        )
        data = json.loads(out)
        streams = data.get("streams") or []
        if not streams:
            return 0, 0
        return _to_int(streams[0].get("width")), _to_int(streams[0].get("height"))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return 0, 0


# --------------------------------------------------------------------------
# Per-candidate extraction


def extract_candidate(
    cand: VideoCandidate,
    *,
    derived_root: Path,
    thumbnail_root: Path,
    http: httpx.Client,
    n_frames: int,
    frame_width: int,
) -> ExtractResult:
    """Download, probe, extract, write frames. Each failure mode maps to a
    structured ``status`` so the sidecar tells you why a row is empty
    without forcing you to read logs."""
    if not ffmpeg_available():
        return ExtractResult(
            status="skipped-no-ffmpeg",
            frames=[],
            video_duration_sec=cand.declared_duration_sec,
            video_width=cand.declared_width,
            video_height=cand.declared_height,
            error="ffmpeg / ffprobe not on PATH",
        )

    try:
        local_video = fetch_to_tempfile(cand.release_asset_url, http)
    except Exception as e:
        return ExtractResult(
            status="video-too-large" if isinstance(e, ValueError) else "fetch-failed",
            frames=[],
            video_duration_sec=cand.declared_duration_sec,
            video_width=cand.declared_width,
            video_height=cand.declared_height,
            error=str(e),
        )

    try:
        try:
            duration, width, height = ffprobe_video(local_video)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return ExtractResult(
                status="ffprobe-failed",
                frames=[],
                video_duration_sec=cand.declared_duration_sec,
                video_width=cand.declared_width,
                video_height=cand.declared_height,
                error=str(e),
            )
        # Fall back to the declared duration when ffprobe couldn't read it
        # (animated GIFs sometimes report 0.0 duration).
        effective_duration = duration if duration > 0 else cand.declared_duration_sec
        timestamps = evenly_spaced_timestamps(effective_duration, n_frames)
        if not timestamps:
            return ExtractResult(
                status="no-frames",
                frames=[],
                video_duration_sec=effective_duration,
                video_width=width or cand.declared_width,
                video_height=height or cand.declared_height,
                error="duration ≤ 0 or n_frames ≤ 0",
            )
        out_dir = derived_root / cand.media_sha256
        # Wipe the dir so partial prior runs don't bleed into this row.
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        thumb_ts = timestamps[len(timestamps) // 2]
        thumb_path = thumbnail_root / f"{cand.media_sha256}.jpg"
        try:
            run_ffmpeg_frame(
                local_video,
                thumb_ts,
                thumb_path,
                DEFAULT_THUMBNAIL_WIDTH,
                jpeg_quality=DEFAULT_THUMBNAIL_QUALITY,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return ExtractResult(
                status="ffmpeg-failed",
                frames=[],
                video_duration_sec=effective_duration,
                video_width=width or cand.declared_width,
                video_height=height or cand.declared_height,
                error=f"thumbnail ts={thumb_ts:.3f}: {e}",
            )
        thumb_sha, thumb_size = hash_file(thumb_path)
        thumb_w, thumb_h = jpeg_dimensions(thumb_path)
        thumbnail = ThumbnailRecord(
            path=repo_relative(thumb_path),
            sha256=thumb_sha,
            width=thumb_w,
            height=thumb_h,
            bytes=thumb_size,
        )
        frames: list[FrameRecord] = []
        for i, ts in enumerate(timestamps):
            frame_path = out_dir / f"{i:03d}.jpg"
            try:
                run_ffmpeg_frame(local_video, ts, frame_path, frame_width)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                return ExtractResult(
                    status="ffmpeg-failed",
                    frames=[],
                    video_duration_sec=effective_duration,
                    video_width=width or cand.declared_width,
                    video_height=height or cand.declared_height,
                    error=f"frame {i} ts={ts:.3f}: {e}",
                )
            if not frame_path.exists():
                # ffmpeg can exit 0 yet write no file when the seek lands at or
                # past the true end of a short video. Skip the frame instead of
                # crashing the whole run on the missing file.
                continue
            sha, size = hash_file(frame_path)
            fw, fh = jpeg_dimensions(frame_path)
            frames.append(
                FrameRecord(
                    index=i,
                    timestamp_sec=ts,
                    path=repo_relative(frame_path),
                    sha256=sha,
                    width=fw,
                    height=fh,
                    bytes=size,
                )
            )
        if not frames:
            return ExtractResult(
                status="no-frames",
                frames=[],
                video_duration_sec=effective_duration,
                video_width=width or cand.declared_width,
                video_height=height or cand.declared_height,
                error="ffmpeg produced no frame files",
            )
        return ExtractResult(
            status="ok",
            frames=frames,
            video_duration_sec=effective_duration,
            video_width=width or cand.declared_width,
            video_height=height or cand.declared_height,
            error=None,
            thumbnail=thumbnail,
        )
    finally:
        with contextlib.suppress(OSError):
            local_video.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Top-level run


def build_row(cand: VideoCandidate, result: ExtractResult, *, generated_at: str) -> dict[str, Any]:
    return {
        "tweet_id": cand.tweet_id,
        "account_handle": cand.account_handle,
        "media_id": cand.media_id,
        "media_sha256": cand.media_sha256,
        "release_asset_url": cand.release_asset_url,
        "thumbnail_path": result.thumbnail.path if result.thumbnail else None,
        "thumbnail_sha256": result.thumbnail.sha256 if result.thumbnail else None,
        "thumbnail_width": result.thumbnail.width if result.thumbnail else None,
        "thumbnail_height": result.thumbnail.height if result.thumbnail else None,
        "thumbnail_bytes": result.thumbnail.bytes if result.thumbnail else None,
        "video_duration_sec": result.video_duration_sec,
        "video_width": result.video_width,
        "video_height": result.video_height,
        "frame_count": len(result.frames),
        "frames": [
            {
                "index": f.index,
                "timestamp_sec": f.timestamp_sec,
                "path": f.path,
                "sha256": f.sha256,
                "width": f.width,
                "height": f.height,
                "bytes": f.bytes,
            }
            for f in result.frames
        ],
        "generated_at": generated_at,
        "extractor_version": EXTRACTOR_VERSION,
        "status": result.status,
        "cost_estimate_usd": 0.0,
        "error": result.error,
    }


def write_parquet(rows: list[dict[str, Any]], path: Path) -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    df = (
        pl.DataFrame(rows, schema=KEYFRAMES_SCHEMA, strict=False)
        if rows
        else empty_keyframes_dataframe()
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.write_parquet(tmp, compression="zstd")
    os.replace(tmp, path)


def update_manifest(rows: list[dict[str, Any]], stats: dict[str, int], generated_at: str) -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    layers = manifest.get("layers")
    if not isinstance(layers, dict):
        layers = {}
    status_counts: dict[str, int] = dict(Counter(str(r.get("status") or "") for r in rows))
    total_frames = sum(int(r.get("frame_count") or 0) for r in rows)
    layers["keyframes"] = {
        "generated_at": generated_at,
        "extractor_version": EXTRACTOR_VERSION,
        "row_count": len(rows),
        "frame_count": total_frames,
        "cost_estimate_usd": 0.0,
        "status_counts": status_counts,
        **stats,
    }
    manifest["layers"] = layers
    tmp = MANIFEST_PATH.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, MANIFEST_PATH)


def discover_canonical_parquets() -> list[Path]:
    return sorted(
        p for p in DATA_DIR.glob("*.parquet") if p.is_file() and p.name != "catalog.parquet"
    )


def run(
    *,
    parquets: list[Path] | None = None,
    n_frames: int = DEFAULT_FRAMES,
    frame_width: int = DEFAULT_FRAME_WIDTH,
    max_items: int | None = None,
    force: bool = False,
    dry_run: bool = False,
    out_path: Path | None = None,
    derived_root: Path | None = None,
    thumbnail_root: Path | None = None,
    only_tweet_ids: set[str] | None = None,
    extractor: Callable[[VideoCandidate], ExtractResult] | None = None,
) -> dict[str, int]:
    """Core run loop, factored out of ``main`` so tests can substitute a
    fake ``extractor`` and exercise discovery / caching / row-building
    without touching ffmpeg or the network.

    ``out_path`` and ``derived_root`` default to the module constants but
    are resolved at call time so monkeypatched test fixtures take
    effect."""
    if out_path is None:
        out_path = OUT_PATH
    if derived_root is None:
        derived_root = DERIVED_DIR
    if thumbnail_root is None:
        thumbnail_root = THUMBNAILS_DIR
    all_parquets = discover_canonical_parquets()
    parquets = parquets if parquets is not None else all_parquets
    existing_rows = load_existing_rows(out_path)
    existing = {
        str(row.get("media_sha256") or ""): row
        for row in existing_rows
        if str(row.get("media_sha256") or "")
    }
    preserve_existing = only_tweet_ids is not None or {p.resolve() for p in parquets} != {
        p.resolve() for p in all_parquets
    }
    rows: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    http: httpx.Client | None = None
    runner: Callable[[VideoCandidate], ExtractResult]
    if extractor is None:
        http = httpx.Client(
            timeout=HTTP_TIMEOUT_SECS,
            follow_redirects=True,
            headers={"user-agent": "imm-archive-keyframes/1.0"},
        )
        assert derived_root is not None  # type narrowing
        _derived_root = derived_root
        assert thumbnail_root is not None  # type narrowing
        _thumbnail_root = thumbnail_root
        _http = http

        def _real_extractor(c: VideoCandidate) -> ExtractResult:
            return extract_candidate(
                c,
                derived_root=_derived_root,
                thumbnail_root=_thumbnail_root,
                http=_http,
                n_frames=n_frames,
                frame_width=frame_width,
            )

        runner = _real_extractor
    else:
        runner = extractor

    try:
        seen_sha: set[str] = set()
        for cand in discover_candidates(parquets):
            if only_tweet_ids is not None and cand.tweet_id not in only_tweet_ids:
                stats["skipped_not_in_filter"] += 1
                continue
            # A single physical video can be attached to many tweets (RTs,
            # cross-posts). We extract once per sha256, but every tweet that
            # references it still gets a row so the viewer's tweet_id join
            # works without an extra step.
            cached = existing.get(cand.media_sha256)
            if not force and is_cache_hit(cached or {}, EXTRACTOR_VERSION):
                row = {**(cached or {})}
                row["tweet_id"] = cand.tweet_id
                row["account_handle"] = cand.account_handle
                row["media_id"] = cand.media_id
                rows.append(row)
                stats["cache_hits"] += 1
                continue

            if cand.media_sha256 in seen_sha:
                # We've extracted this sha this run; reuse the freshly-built row.
                # Find the most recent row for the sha (last append wins).
                fresh = next(
                    (r for r in reversed(rows) if r["media_sha256"] == cand.media_sha256), None
                )
                if fresh:
                    row = {**fresh}
                    row["tweet_id"] = cand.tweet_id
                    row["account_handle"] = cand.account_handle
                    row["media_id"] = cand.media_id
                    rows.append(row)
                    stats["intra_run_dedup"] += 1
                    continue

            if max_items is not None and stats["attempted"] >= max_items:
                stats["skipped_max_items"] += 1
                continue

            stats["attempted"] += 1
            try:
                result = runner(cand)
            except Exception as e:
                # One bad video must never abort the whole pass and lose every
                # other extracted row (the sidecar is only written at the end).
                LOG.exception(
                    "keyframes: extractor raised; recording error row",
                    media_sha256=cand.media_sha256,
                    tweet_id=cand.tweet_id,
                )
                result = ExtractResult(
                    status="extractor-error",
                    frames=[],
                    video_duration_sec=0.0,
                    video_width=cand.declared_width,
                    video_height=cand.declared_height,
                    error=str(e)[:1000],
                )
            stats[f"status_{result.status}"] += 1
            if result.status == "ok":
                stats["extracted"] += 1
            row = build_row(cand, result, generated_at=generated_at)
            rows.append(row)
            seen_sha.add(cand.media_sha256)
    finally:
        if http is not None:
            http.close()

    rows_to_write = merge_existing_rows(rows, existing_rows, preserve_existing=preserve_existing)
    stats["rows"] = len(rows_to_write)
    if not dry_run:
        if rows and all(str(r.get("status") or "") == "skipped-no-ffmpeg" for r in rows):
            stats["skipped_write_all_no_ffmpeg"] = 1
            LOG.warning(
                "not writing keyframe sidecar because every attempted row lacked ffmpeg",
                rows=len(rows),
            )
        else:
            write_parquet(rows_to_write, out_path)
            update_manifest(rows_to_write, dict(stats), generated_at)
    return dict(stats)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handle", help="Restrict to one data/<handle>.parquet file.")
    parser.add_argument(
        "--frames",
        type=int,
        default=DEFAULT_FRAMES,
        help=f"Number of keyframes to extract per video (default {DEFAULT_FRAMES}).",
    )
    parser.add_argument(
        "--frame-width",
        type=int,
        default=DEFAULT_FRAME_WIDTH,
        help=f"Output frame width in pixels (default {DEFAULT_FRAME_WIDTH}).",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        help="Maximum number of uncached videos to extract this run.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore the existing sidecar cache and re-extract every video.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report planned rows without writing."
    )
    parser.add_argument(
        "--tweet-ids-file",
        type=Path,
        help="Only extract videos whose tweet_id is listed (one per line) in this file. "
        "Use with data/tags/produced_likely_unprocessed_tweet_ids.txt to widen keyframe "
        "coverage for likely-produced videos without processing the whole archive.",
    )
    args = parser.parse_args(argv)

    parquets = discover_canonical_parquets()
    if args.handle:
        parquets = [p for p in parquets if p.stem == args.handle]

    only_tweet_ids: set[str] | None = None
    if args.tweet_ids_file:
        only_tweet_ids = {
            line.strip()
            for line in args.tweet_ids_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    stats = run(
        parquets=parquets,
        n_frames=args.frames,
        frame_width=args.frame_width,
        max_items=args.max_items,
        force=args.force,
        dry_run=args.dry_run,
        only_tweet_ids=only_tweet_ids,
    )
    LOG.info("keyframe extraction complete", **stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
