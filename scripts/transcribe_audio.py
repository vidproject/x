"""Local speech-to-text for archived videos (Layer 3c).

For every archived video / animated GIF with an audio stream, fetch the GitHub
Release asset, decode a bounded mono sample through ffmpeg, and transcribe it
with a local, free speech recognizer (``faster-whisper``). No paid API and no
credentials: the recognizer is optional, imported lazily, and the run skips
cleanly (status ``skipped-no-asr``) when it is not installed — exactly like the
ffmpeg-only audio layer it sits beside.

The output sidecar is ``data/tags/transcripts.parquet``. Canonical tweet
parquets stay untouched; ``scripts.tag_lexical`` folds the recovered transcript
text into its regex pass on the next run, so spoken slogans, agency names, and
other speech earn the same tags as tweet-body text.

Run with::

    uv run python -m scripts.transcribe_audio                # all archived videos
    uv run python -m scripts.transcribe_audio --max-items 20 # bounded batch

Install the optional recognizer with ``uv sync --group asr`` (CI installs it in
the archive-media workflow).
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
from scripts._schema import TRANSCRIPT_SCHEMA, empty_transcript_dataframe

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"
OUT_PATH = TAGS_DIR / "transcripts.parquet"
MANIFEST_PATH = TAGS_DIR / "manifest.json"

TRANSCRIBER = "faster-whisper"
TRANSCRIBER_VERSION = "faster-whisper-v1"
DEFAULT_MODEL = os.environ.get("ASR_MODEL", "base")
DEFAULT_SAMPLE_SECONDS = 180.0
DEFAULT_SAMPLE_RATE = 16_000
HTTP_TIMEOUT_SECS = 60.0
MAX_VIDEO_BYTES = 600 * 1024 * 1024
# A cached row is reused as-is (keeping its generated_at) when the recognizer
# version is unchanged and the status was a real, repeatable outcome.
CACHEABLE_STATUSES = {"ok", "no-audio-stream", "silent-audio", "too-short", "empty-transcript"}
# Persist progress every N new transcriptions. CPU whisper is slow, so a CI
# step can hit its time limit mid-run; flushing means a killed run keeps what it
# finished (and seeds the cache) instead of losing everything and repeating from
# zero on the next run.
FLUSH_EVERY = 5


@dataclass(frozen=True)
class TranscribeCandidate:
    tweet_id: str
    account_handle: str
    media_id: str
    media_type: str
    media_sha256: str
    release_asset_url: str
    declared_duration_sec: float
    declared_bytes: int


@dataclass
class TranscriptResult:
    status: str
    language: str | None = None
    language_prob: float = 0.0
    audio_duration_sec: float = 0.0
    sample_duration_sec: float = 0.0
    segment_count: int = 0
    text: str = ""
    avg_logprob: float = 0.0
    error: str | None = None


def discover_candidates(parquets: list[Path]) -> Iterator[TranscribeCandidate]:
    for path in parquets:
        try:
            df = pl.read_parquet(path)
        except Exception:
            LOG.exception("transcribe: could not read parquet", path=str(path))
            continue
        for tweet in df.iter_rows(named=True):
            media = tweet.get("media") or []
            if not isinstance(media, list):
                continue
            for item in media:
                if not isinstance(item, dict):
                    continue
                if str(item.get("media_type") or "") not in {"video", "animated_gif"}:
                    continue
                asset_url = str(item.get("release_asset_url") or "")
                sha = str(item.get("sha256") or "")
                media_id = str(item.get("media_id") or "")
                if not asset_url or not sha or not media_id:
                    continue
                yield TranscribeCandidate(
                    tweet_id=str(tweet.get("tweet_id") or ""),
                    account_handle=str(tweet.get("account_handle") or ""),
                    media_id=media_id,
                    media_type=str(item.get("media_type") or ""),
                    media_sha256=sha,
                    release_asset_url=asset_url,
                    declared_duration_sec=_to_float(item.get("duration_sec")),
                    declared_bytes=_to_int(item.get("bytes")),
                )


def input_hash_for(cand: TranscribeCandidate, *, model: str) -> str:
    payload = {
        "transcriber_version": TRANSCRIBER_VERSION,
        "model": model,
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


def is_cache_hit(cached: dict[str, Any], *, model: str) -> bool:
    if not cached:
        return False
    if str(cached.get("transcriber_version") or "") != TRANSCRIBER_VERSION:
        return False
    if str(cached.get("model") or "") != model:
        return False
    return str(cached.get("status") or "") in CACHEABLE_STATUSES


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def load_whisper_model(model: str) -> Any | None:
    """Return a faster-whisper model, or None when the recognizer is unusable.

    None is returned both when the package is absent and when the model weights
    can't be loaded (e.g. the weights host is unreachable). Returning None lets
    the run skip transcription cleanly instead of crashing the pipeline step.
    """
    try:
        from faster_whisper import WhisperModel
    except Exception:
        return None
    try:
        compute_type = os.environ.get("ASR_COMPUTE_TYPE", "int8")
        return WhisperModel(model, device="cpu", compute_type=compute_type)
    except Exception as e:
        LOG.warning(
            "asr: could not load whisper model; skipping transcription", model=model, error=str(e)
        )
        return None


def fetch_to_tempfile(url: str, http: httpx.Client) -> Path:
    with http.stream("GET", url, timeout=HTTP_TIMEOUT_SECS, follow_redirects=True) as resp:
        resp.raise_for_status()
        total = 0
        suffix = Path(url.split("?", 1)[0]).suffix or ".bin"
        with tempfile.NamedTemporaryFile(
            prefix="imm-archive-asr-", suffix=suffix, delete=False
        ) as fh:
            tmp_path = Path(fh.name)
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                total += len(chunk)
                if total > MAX_VIDEO_BYTES:
                    tmp_path.unlink(missing_ok=True)
                    raise ValueError(f"video exceeds {MAX_VIDEO_BYTES} bytes")
                fh.write(chunk)
        return tmp_path


def has_audio_stream(path: Path) -> bool:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        timeout=60,
    )
    streams = json.loads(out).get("streams") or []
    return bool(streams)


def extract_audio_wav(path: Path, *, sample_seconds: float, sample_rate: int) -> Path:
    out = path.with_suffix(".asr.wav")
    subprocess.check_output(
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
            "-y",
            str(out),
        ],
        timeout=300,
    )
    return out


def transcribe_with_model(model: Any, wav_path: Path) -> TranscriptResult:
    segments, info = model.transcribe(str(wav_path), vad_filter=True)
    texts: list[str] = []
    logprobs: list[float] = []
    for seg in segments:
        piece = str(getattr(seg, "text", "") or "").strip()
        if piece:
            texts.append(piece)
        lp = getattr(seg, "avg_logprob", None)
        if lp is not None:
            logprobs.append(float(lp))
    text = " ".join(texts).strip()
    status = "ok" if text else "empty-transcript"
    return TranscriptResult(
        status=status,
        language=str(getattr(info, "language", "") or "") or None,
        language_prob=float(getattr(info, "language_probability", 0.0) or 0.0),
        audio_duration_sec=float(getattr(info, "duration", 0.0) or 0.0),
        segment_count=len(texts),
        text=text,
        avg_logprob=sum(logprobs) / len(logprobs) if logprobs else 0.0,
    )


def analyze_candidate(
    cand: TranscribeCandidate,
    *,
    http: httpx.Client,
    model: Any,
    sample_seconds: float,
    sample_rate: int,
) -> TranscriptResult:
    if not ffmpeg_available():
        return TranscriptResult(status="skipped-no-ffmpeg", error="ffmpeg / ffprobe not on PATH")
    if model is None:
        return TranscriptResult(status="skipped-no-asr", error="faster-whisper not installed")
    try:
        local_video = fetch_to_tempfile(cand.release_asset_url, http)
    except Exception as e:
        return TranscriptResult(
            status="video-too-large" if isinstance(e, ValueError) else "fetch-failed",
            error=str(e),
        )
    wav: Path | None = None
    try:
        try:
            if not has_audio_stream(local_video):
                return TranscriptResult(status="no-audio-stream")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return TranscriptResult(status="ffprobe-failed", error=str(e))
        try:
            wav = extract_audio_wav(
                local_video, sample_seconds=sample_seconds, sample_rate=sample_rate
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            return TranscriptResult(status="ffmpeg-failed", error=str(e))
        try:
            result = transcribe_with_model(model, wav)
        except Exception as e:
            return TranscriptResult(status="asr-failed", error=str(e))
        result.sample_duration_sec = min(
            sample_seconds, cand.declared_duration_sec or sample_seconds
        )
        if not result.audio_duration_sec:
            result.audio_duration_sec = cand.declared_duration_sec
        return result
    finally:
        with contextlib.suppress(OSError):
            local_video.unlink(missing_ok=True)
        if wav is not None:
            with contextlib.suppress(OSError):
                wav.unlink(missing_ok=True)


def build_row(
    cand: TranscribeCandidate, result: TranscriptResult, *, model: str, generated_at: str
) -> dict[str, Any]:
    return {
        "tweet_id": cand.tweet_id,
        "account_handle": cand.account_handle,
        "media_id": cand.media_id,
        "media_type": cand.media_type,
        "media_sha256": cand.media_sha256,
        "release_asset_url": cand.release_asset_url,
        "input_hash": input_hash_for(cand, model=model),
        "generated_at": generated_at,
        "transcriber": TRANSCRIBER,
        "transcriber_version": TRANSCRIBER_VERSION,
        "model": model,
        "language": result.language,
        "language_prob": result.language_prob,
        "audio_duration_sec": result.audio_duration_sec,
        "sample_duration_sec": result.sample_duration_sec,
        "segment_count": result.segment_count,
        "text": result.text,
        "avg_logprob": result.avg_logprob,
        "status": result.status,
        "cost_estimate_usd": 0.0,
        "error": result.error,
    }


def write_parquet(rows: list[dict[str, Any]], path: Path) -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    df = (
        pl.DataFrame(rows, schema=TRANSCRIPT_SCHEMA, strict=False)
        if rows
        else empty_transcript_dataframe()
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.write_parquet(tmp, compression="zstd")
    os.replace(tmp, path)


def _layer_without_timestamp(layer: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in layer.items() if k != "generated_at"}


def update_manifest(rows: list[dict[str, Any]], stats: dict[str, int], generated_at: str) -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    layers = manifest.get("layers")
    if not isinstance(layers, dict):
        layers = {}
    status_counts: dict[str, int] = dict(Counter(str(r.get("status") or "") for r in rows))
    transcribed = sum(1 for r in rows if str(r.get("status")) == "ok")
    new_layer = {
        "generated_at": generated_at,
        "transcriber": TRANSCRIBER,
        "transcriber_version": TRANSCRIBER_VERSION,
        "row_count": len(rows),
        "transcribed": transcribed,
        "cost_estimate_usd": 0.0,
        "status_counts": status_counts,
        **stats,
    }
    prior = layers.get("transcripts")
    if (
        isinstance(prior, dict)
        and prior.get("generated_at")
        and _layer_without_timestamp(prior) == _layer_without_timestamp(new_layer)
    ):
        new_layer["generated_at"] = prior["generated_at"]
    layers["transcripts"] = new_layer
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
    model: str = DEFAULT_MODEL,
    sample_seconds: float = DEFAULT_SAMPLE_SECONDS,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    out_path: Path | None = None,
    only_tweet_ids: set[str] | None = None,
    transcriber: Callable[[TranscribeCandidate], TranscriptResult] | None = None,
) -> dict[str, int]:
    if out_path is None:
        out_path = OUT_PATH
    parquets = parquets if parquets is not None else discover_canonical_parquets()
    existing = load_existing_index(out_path)
    rows: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _leftover_rows() -> list[dict[str, Any]]:
        # Prior transcripts for media not (re)visited this run — filtered out by
        # --handle / --tweet-ids-file, or not yet reached. Carried forward so a
        # partial/early write never shrinks the sidecar.
        done = {str(r.get("media_sha256") or "") for r in rows}
        return [r for sha, r in existing.items() if sha not in done]

    def _flush() -> None:
        if dry_run:
            return
        write_parquet(rows + _leftover_rows(), out_path)

    http: httpx.Client | None = None
    runner: Callable[[TranscribeCandidate], TranscriptResult]
    if transcriber is None:
        http = httpx.Client(
            timeout=HTTP_TIMEOUT_SECS,
            follow_redirects=True,
            headers={"user-agent": "imm-archive-asr/1.0"},
        )
        whisper_model = load_whisper_model(model)
        if whisper_model is None:
            http.close()
            # Recognizer or model unavailable: leave the existing sidecar
            # untouched (no all-skipped rewrite) so we don't churn the data dir
            # on environments without the model.
            LOG.warning("asr unavailable; transcripts sidecar left unchanged", model=model)
            return {"skipped_no_asr": 1, "rows": 0}
        _http = http

        def _real(c: TranscribeCandidate) -> TranscriptResult:
            return analyze_candidate(
                c,
                http=_http,
                model=whisper_model,
                sample_seconds=sample_seconds,
                sample_rate=sample_rate,
            )

        runner = _real
    else:
        runner = transcriber

    try:
        seen_sha: set[str] = set()
        for cand in discover_candidates(parquets):
            if only_tweet_ids is not None and cand.tweet_id not in only_tweet_ids:
                stats["skipped_not_in_filter"] += 1
                continue
            cached = existing.get(cand.media_sha256)
            if not force and is_cache_hit(cached or {}, model=model):
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
                stats["transcribed"] += 1
            rows.append(build_row(cand, result, model=model, generated_at=generated_at))
            seen_sha.add(cand.media_sha256)
            if stats["attempted"] % FLUSH_EVERY == 0:
                _flush()
                LOG.info("asr: progress flush", attempted=int(stats["attempted"]), rows=len(rows))
    finally:
        if http is not None:
            http.close()

    rows_to_write = rows + _leftover_rows()
    stats["rows"] = len(rows_to_write)
    if not dry_run:
        write_parquet(rows_to_write, out_path)
        update_manifest(rows_to_write, dict(stats), generated_at)
    return dict(stats)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handle", help="Restrict to one data/<handle>.parquet file.")
    parser.add_argument("--max-items", type=int, help="Maximum uncached videos to transcribe.")
    parser.add_argument("--force", action="store_true", help="Ignore the cache and re-transcribe.")
    parser.add_argument("--dry-run", action="store_true", help="Report planned rows; do not write.")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"Whisper model (default {DEFAULT_MODEL})."
    )
    parser.add_argument(
        "--sample-seconds",
        type=float,
        default=DEFAULT_SAMPLE_SECONDS,
        help=f"Seconds of audio to transcribe per video (default {DEFAULT_SAMPLE_SECONDS}).",
    )
    parser.add_argument(
        "--tweet-ids-file",
        type=Path,
        help="Only transcribe videos whose tweet_id is listed (one per line) in this file.",
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
        max_items=args.max_items,
        force=args.force,
        dry_run=args.dry_run,
        model=args.model,
        sample_seconds=args.sample_seconds,
        only_tweet_ids=only_tweet_ids,
    )
    LOG.info("audio transcription complete", **stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
