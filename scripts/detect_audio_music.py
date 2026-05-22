"""Cheap archived-video audio recognition.

For every archived video / animated GIF in the corpus, fetch the GitHub
Release asset, use ffprobe to check for audio streams, and decode a bounded
mono sample through ffmpeg. The detector intentionally uses no downloaded ML
model: it records objective audio-stream metadata and a conservative
``audio:music-likely`` heuristic only when the sampled audio looks continuous
enough to be worth reviewer attention.

The output sidecar is ``data/tags/audio_music.parquet``. Canonical tweet
parquets stay untouched, and the lexical tagger imports these tags on the next
run.

Run with::

    uv run python -m scripts.detect_audio_music
"""

from __future__ import annotations

import argparse
import array
import contextlib
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from scripts._logging import configure
from scripts._schema import AUDIO_MUSIC_SCHEMA, empty_audio_music_dataframe

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"
OUT_PATH = TAGS_DIR / "audio_music.parquet"
MANIFEST_PATH = TAGS_DIR / "manifest.json"

DETECTOR = "ffmpeg-audio-heuristic"
DETECTOR_VERSION = "ffmpeg-audio-heuristic-v1"
DEFAULT_SAMPLE_SECONDS = 45.0
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_MUSIC_THRESHOLD = 0.88
HTTP_TIMEOUT_SECS = 60.0
MAX_VIDEO_BYTES = 600 * 1024 * 1024
CACHEABLE_STATUSES = {"ok", "no-audio-stream", "silent-audio", "too-short"}


@dataclass(frozen=True)
class AudioCandidate:
    tweet_id: str
    account_handle: str
    media_id: str
    media_type: str
    media_sha256: str
    release_asset_url: str
    declared_duration_sec: float
    declared_bytes: int


@dataclass
class AudioResult:
    status: str
    audio_duration_sec: float = 0.0
    sample_duration_sec: float = 0.0
    audio_stream_count: int = 0
    codec: str | None = None
    channels: int = 0
    sample_rate: int = 0
    music_score: float = 0.0
    speech_score: float = 0.0
    non_silent_ratio: float = 0.0
    zero_crossing_rate: float = 0.0
    rms_mean: float = 0.0
    rms_variance: float = 0.0
    tags: list[dict[str, Any]] = field(default_factory=list)
    features: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def discover_candidates(parquets: list[Path]) -> Iterator[AudioCandidate]:
    """Yield one candidate per archived video / animated-gif media item."""
    for path in parquets:
        try:
            df = pl.read_parquet(path)
        except Exception:
            LOG.exception("audio: could not read parquet", path=str(path))
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
                yield AudioCandidate(
                    tweet_id=str(tweet.get("tweet_id") or ""),
                    account_handle=str(tweet.get("account_handle") or ""),
                    media_id=media_id,
                    media_type=media_type,
                    media_sha256=sha,
                    release_asset_url=asset_url,
                    declared_duration_sec=_to_float(item.get("duration_sec")),
                    declared_bytes=_to_int(item.get("bytes")),
                )


def input_hash_for(cand: AudioCandidate) -> str:
    payload = {
        "detector_version": DETECTOR_VERSION,
        "media_sha256": cand.media_sha256,
        "media_type": cand.media_type,
        "release_asset_url": cand.release_asset_url,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _to_float(value: Any) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def tag_entry(tag: str, *, tentative: bool = False, source: str = "audio-heuristic") -> dict[str, Any]:
    return {
        "tag": tag,
        "tentative": True if tentative else None,
        "source": source,
        "span_start": None,
        "span_end": None,
    }


def load_existing_index(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    df = pl.read_parquet(path)
    out: dict[str, dict[str, Any]] = {}
    for row in df.iter_rows(named=True):
        sha = str(row.get("media_sha256") or "")
        if sha:
            out[sha] = row
    return out


def is_cache_hit(cached: dict[str, Any], detector_version: str) -> bool:
    if not cached:
        return False
    if str(cached.get("detector_version") or "") != detector_version:
        return False
    return str(cached.get("status") or "") in CACHEABLE_STATUSES


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def fetch_to_tempfile(url: str, http: httpx.Client) -> Path:
    with http.stream("GET", url, timeout=HTTP_TIMEOUT_SECS, follow_redirects=True) as resp:
        resp.raise_for_status()
        total = 0
        suffix = Path(url.split("?", 1)[0]).suffix or ".bin"
        with tempfile.NamedTemporaryFile(
            prefix="imm-archive-audio-", suffix=suffix, delete=False
        ) as fh:
            tmp_path = Path(fh.name)
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                total += len(chunk)
                if total > MAX_VIDEO_BYTES:
                    tmp_path.unlink(missing_ok=True)
                    raise ValueError(f"video exceeds {MAX_VIDEO_BYTES} bytes")
                fh.write(chunk)
        return tmp_path


def ffprobe_audio(path: Path) -> dict[str, Any]:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_name,channels,sample_rate,duration:format=duration",
            "-of",
            "json",
            str(path),
        ],
        timeout=60,
    )
    return json.loads(out)


def decode_audio_sample(
    path: Path,
    *,
    sample_seconds: float,
    sample_rate: int,
) -> bytes:
    return subprocess.check_output(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-t",
            f"{sample_seconds:.3f}",
            "-f",
            "s16le",
            "pipe:1",
        ],
        timeout=180,
    )


def analyze_pcm(
    pcm: bytes,
    *,
    sample_rate: int,
    music_threshold: float = DEFAULT_MUSIC_THRESHOLD,
) -> AudioResult:
    sample_count = len(pcm) // 2
    if sample_count <= sample_rate:
        result = AudioResult(status="too-short", sample_rate=sample_rate)
        result.sample_duration_sec = sample_count / sample_rate if sample_rate else 0.0
        result.tags = tags_for_result(result, music_threshold=music_threshold)
        result.features = {"sample_count": sample_count}
        return result

    samples = array.array("h")
    samples.frombytes(pcm[: sample_count * 2])
    if sys.byteorder != "little":
        samples.byteswap()

    window_size = max(sample_rate // 2, 1)
    rms_values: list[float] = []
    zcr_values: list[float] = []
    for start in range(0, len(samples), window_size):
        window = samples[start : start + window_size]
        if len(window) < sample_rate // 10:
            continue
        rms_values.append(_rms(window))
        zcr_values.append(_zero_crossing_rate(window))

    if not rms_values:
        result = AudioResult(status="too-short", sample_rate=sample_rate)
        result.sample_duration_sec = sample_count / sample_rate if sample_rate else 0.0
        result.tags = tags_for_result(result, music_threshold=music_threshold)
        result.features = {"sample_count": sample_count}
        return result

    rms_mean = sum(rms_values) / len(rms_values)
    rms_variance = sum((x - rms_mean) ** 2 for x in rms_values) / len(rms_values)
    non_silent_ratio = sum(1 for x in rms_values if x >= 0.01) / len(rms_values)
    zcr = sum(zcr_values) / len(zcr_values) if zcr_values else 0.0
    rel_std = math.sqrt(rms_variance) / (rms_mean + 1e-9)

    stability = 1.0 - min(1.0, rel_std / 1.2)
    zcr_music_band = max(0.0, 1.0 - abs(zcr - 0.075) / 0.075)
    level = min(1.0, rms_mean / 0.05)
    duration_bonus = min(1.0, (sample_count / sample_rate) / 20.0)
    music_score = clamp(
        0.38 * non_silent_ratio
        + 0.27 * stability
        + 0.18 * zcr_music_band
        + 0.10 * level
        + 0.07 * duration_bonus
    )
    speech_score = clamp(
        0.45 * non_silent_ratio
        + 0.40 * min(1.0, rel_std / 0.8)
        + 0.15 * duration_bonus
    )
    status = "silent-audio" if non_silent_ratio == 0 or rms_mean < 0.002 else "ok"
    result = AudioResult(
        status=status,
        sample_duration_sec=sample_count / sample_rate,
        sample_rate=sample_rate,
        music_score=music_score,
        speech_score=speech_score,
        non_silent_ratio=non_silent_ratio,
        zero_crossing_rate=zcr,
        rms_mean=rms_mean,
        rms_variance=rms_variance,
        features={
            "sample_count": sample_count,
            "window_count": len(rms_values),
            "relative_rms_std": rel_std,
            "stability": stability,
            "zcr_music_band": zcr_music_band,
            "level": level,
            "duration_bonus": duration_bonus,
            "music_threshold": music_threshold,
        },
    )
    result.tags = tags_for_result(result, music_threshold=music_threshold)
    return result


def _rms(samples: array.array[int]) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum((s / 32768.0) ** 2 for s in samples) / len(samples))


def _zero_crossing_rate(samples: array.array[int]) -> float:
    if len(samples) < 2:
        return 0.0
    crossings = 0
    prev = samples[0]
    for current in samples[1:]:
        if (prev < 0 <= current) or (prev >= 0 > current):
            crossings += 1
        prev = current
    return crossings / (len(samples) - 1)


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def tags_for_result(
    result: AudioResult,
    *,
    music_threshold: float = DEFAULT_MUSIC_THRESHOLD,
) -> list[dict[str, Any]]:
    tags: list[dict[str, Any]] = []
    if result.status == "no-audio-stream":
        tags.append(tag_entry("audio:no-audio"))
        return tags
    if result.status in {"ok", "silent-audio", "too-short"}:
        tags.append(tag_entry("audio:has-audio"))
    if result.status == "silent-audio":
        tags.append(tag_entry("audio:silent"))
    likely_music = (
        result.status == "ok"
        and result.music_score >= music_threshold
        and result.sample_duration_sec >= 12.0
        and result.non_silent_ratio >= 0.80
        and result.rms_mean >= 0.012
        and 0.015 <= result.zero_crossing_rate <= 0.22
    )
    if likely_music:
        tags.append(tag_entry("audio:music-likely", tentative=True))
    return tags


def result_with_probe_metadata(result: AudioResult, probe: dict[str, Any]) -> AudioResult:
    streams = probe.get("streams") or []
    fmt = probe.get("format") or {}
    result.audio_stream_count = len(streams)
    if streams:
        stream = streams[0]
        result.codec = str(stream.get("codec_name") or "")
        result.channels = _to_int(stream.get("channels"))
        result.sample_rate = _to_int(stream.get("sample_rate")) or result.sample_rate
        result.audio_duration_sec = _to_float(stream.get("duration")) or _to_float(
            fmt.get("duration")
        )
    else:
        result.audio_duration_sec = _to_float(fmt.get("duration"))
    return result


def analyze_candidate(
    cand: AudioCandidate,
    *,
    http: httpx.Client,
    sample_seconds: float,
    sample_rate: int,
    music_threshold: float,
) -> AudioResult:
    if not ffmpeg_available():
        return AudioResult(
            status="skipped-no-ffmpeg",
            audio_duration_sec=cand.declared_duration_sec,
            error="ffmpeg / ffprobe not on PATH",
        )

    try:
        local_video = fetch_to_tempfile(cand.release_asset_url, http)
    except Exception as e:
        return AudioResult(
            status="video-too-large" if isinstance(e, ValueError) else "fetch-failed",
            audio_duration_sec=cand.declared_duration_sec,
            error=str(e),
        )

    try:
        try:
            probe = ffprobe_audio(local_video)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return AudioResult(
                status="ffprobe-failed",
                audio_duration_sec=cand.declared_duration_sec,
                error=str(e),
            )
        streams = probe.get("streams") or []
        if not streams:
            result = AudioResult(
                status="no-audio-stream",
                audio_duration_sec=cand.declared_duration_sec,
                audio_stream_count=0,
            )
            result.tags = tags_for_result(result, music_threshold=music_threshold)
            result.features = {"probe_streams": 0}
            return result_with_probe_metadata(result, probe)
        try:
            pcm = decode_audio_sample(
                local_video,
                sample_seconds=sample_seconds,
                sample_rate=sample_rate,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return result_with_probe_metadata(
                AudioResult(status="ffmpeg-failed", error=str(e)),
                probe,
            )
        result = analyze_pcm(pcm, sample_rate=sample_rate, music_threshold=music_threshold)
        return result_with_probe_metadata(result, probe)
    finally:
        with contextlib.suppress(OSError):
            local_video.unlink(missing_ok=True)


def build_row(cand: AudioCandidate, result: AudioResult, *, generated_at: str) -> dict[str, Any]:
    return {
        "tweet_id": cand.tweet_id,
        "account_handle": cand.account_handle,
        "media_id": cand.media_id,
        "media_type": cand.media_type,
        "media_sha256": cand.media_sha256,
        "release_asset_url": cand.release_asset_url,
        "input_hash": input_hash_for(cand),
        "generated_at": generated_at,
        "detector": DETECTOR,
        "detector_version": DETECTOR_VERSION,
        "audio_duration_sec": result.audio_duration_sec,
        "sample_duration_sec": result.sample_duration_sec,
        "audio_stream_count": result.audio_stream_count,
        "codec": result.codec,
        "channels": result.channels,
        "sample_rate": result.sample_rate,
        "music_score": result.music_score,
        "speech_score": result.speech_score,
        "non_silent_ratio": result.non_silent_ratio,
        "zero_crossing_rate": result.zero_crossing_rate,
        "rms_mean": result.rms_mean,
        "rms_variance": result.rms_variance,
        "status": result.status,
        "tags": result.tags,
        "features_json": json.dumps(result.features, sort_keys=True),
        "cost_estimate_usd": 0.0,
        "error": result.error,
    }


def write_parquet(rows: list[dict[str, Any]], path: Path) -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    df = (
        pl.DataFrame(rows, schema=AUDIO_MUSIC_SCHEMA, strict=False)
        if rows
        else empty_audio_music_dataframe()
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
    tag_counts: Counter[str] = Counter()
    for row in rows:
        for entry in row.get("tags") or []:
            if isinstance(entry, dict) and entry.get("tag"):
                tag_counts[str(entry["tag"])] += 1
    layers["audio_music"] = {
        "generated_at": generated_at,
        "detector": DETECTOR,
        "detector_version": DETECTOR_VERSION,
        "row_count": len(rows),
        "cost_estimate_usd": 0.0,
        "status_counts": status_counts,
        "tag_frequency": dict(sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
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
    max_items: int | None = None,
    force: bool = False,
    dry_run: bool = False,
    sample_seconds: float = DEFAULT_SAMPLE_SECONDS,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    music_threshold: float = DEFAULT_MUSIC_THRESHOLD,
    out_path: Path | None = None,
    analyzer: Callable[[AudioCandidate], AudioResult] | None = None,
) -> dict[str, int]:
    if out_path is None:
        out_path = OUT_PATH
    parquets = parquets if parquets is not None else discover_canonical_parquets()
    existing = load_existing_index(out_path)
    rows: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    http: httpx.Client | None = None
    runner: Callable[[AudioCandidate], AudioResult]
    if analyzer is None:
        http = httpx.Client(
            timeout=HTTP_TIMEOUT_SECS,
            follow_redirects=True,
            headers={"user-agent": "imm-archive-audio/1.0"},
        )
        _http = http

        def _real_analyzer(c: AudioCandidate) -> AudioResult:
            return analyze_candidate(
                c,
                http=_http,
                sample_seconds=sample_seconds,
                sample_rate=sample_rate,
                music_threshold=music_threshold,
            )

        runner = _real_analyzer
    else:
        runner = analyzer

    try:
        seen_sha: set[str] = set()
        for cand in discover_candidates(parquets):
            cached = existing.get(cand.media_sha256)
            if not force and is_cache_hit(cached or {}, DETECTOR_VERSION):
                row = {**(cached or {})}
                row["tweet_id"] = cand.tweet_id
                row["account_handle"] = cand.account_handle
                row["media_id"] = cand.media_id
                row["media_type"] = cand.media_type
                rows.append(row)
                stats["cache_hits"] += 1
                continue

            if cand.media_sha256 in seen_sha:
                fresh = next(
                    (r for r in reversed(rows) if r["media_sha256"] == cand.media_sha256), None
                )
                if fresh:
                    row = {**fresh}
                    row["tweet_id"] = cand.tweet_id
                    row["account_handle"] = cand.account_handle
                    row["media_id"] = cand.media_id
                    row["media_type"] = cand.media_type
                    rows.append(row)
                    stats["intra_run_dedup"] += 1
                    continue

            if max_items is not None and stats["attempted"] >= max_items:
                stats["skipped_max_items"] += 1
                continue

            stats["attempted"] += 1
            result = runner(cand)
            stats[f"status_{result.status}"] += 1
            if result.status in CACHEABLE_STATUSES:
                stats["analyzed"] += 1
            rows.append(build_row(cand, result, generated_at=generated_at))
            seen_sha.add(cand.media_sha256)
    finally:
        if http is not None:
            http.close()

    stats["rows"] = len(rows)
    if not dry_run:
        write_parquet(rows, out_path)
        update_manifest(rows, dict(stats), generated_at)
    return dict(stats)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handle", help="Restrict to one data/<handle>.parquet file.")
    parser.add_argument(
        "--max-items",
        type=int,
        help="Maximum number of uncached videos to analyze this run.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore the existing sidecar cache and re-analyze every video.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report planned rows without writing.")
    parser.add_argument(
        "--sample-seconds",
        type=float,
        default=DEFAULT_SAMPLE_SECONDS,
        help=f"Seconds of audio to decode per video (default {DEFAULT_SAMPLE_SECONDS}).",
    )
    parser.add_argument(
        "--music-threshold",
        type=float,
        default=DEFAULT_MUSIC_THRESHOLD,
        help=f"Score threshold for audio:music-likely (default {DEFAULT_MUSIC_THRESHOLD}).",
    )
    args = parser.parse_args(argv)

    parquets = discover_canonical_parquets()
    if args.handle:
        parquets = [p for p in parquets if p.stem == args.handle]

    stats = run(
        parquets=parquets,
        max_items=args.max_items,
        force=args.force,
        dry_run=args.dry_run,
        sample_seconds=args.sample_seconds,
        music_threshold=args.music_threshold,
    )
    LOG.info("audio music detection complete", **stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
