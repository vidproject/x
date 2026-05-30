"""OCR archived images and extracted video keyframes.

This is the first pixel-reading image-recognition layer. It uses the
system Tesseract binary when available, writes ``data/tags/image_ocr.parquet``,
and lets ``scripts.tag_lexical`` turn recovered text into normal topic,
slogan, agency, crime, and theme tags.

The script is deliberately bounded and cacheable:

* archived photos are fetched from GitHub Release URLs, never from X CDN;
* video OCR uses the keyframe JPEGs already extracted in this CI run;
* rows are cached by source hash + OCR version;
* missing Tesseract is recorded as ``skipped-no-tesseract`` instead of
  crashing a local run.

Run with::

    uv run python -m scripts.tag_image_ocr
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from scripts._logging import configure
from scripts._schema import IMAGE_OCR_SCHEMA, empty_image_ocr_dataframe

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"
OUT_PATH = TAGS_DIR / "image_ocr.parquet"
KEYFRAMES_PATH = TAGS_DIR / "keyframes.parquet"
MANIFEST_PATH = TAGS_DIR / "manifest.json"

OCR_ENGINE = "tesseract"
OCR_VERSION = "tesseract-ocr-v1"
HTTP_TIMEOUT_SECS = 45.0
MAX_IMAGE_BYTES = 30 * 1024 * 1024
CACHEABLE_STATUSES = {"ok", "no-text"}


@dataclass(frozen=True)
class OcrCandidate:
    tweet_id: str
    account_handle: str
    media_id: str
    media_type: str
    media_sha256: str
    source_kind: str
    source_path: str
    release_asset_url: str
    source_sha256: str


@dataclass
class OcrResult:
    status: str
    text: str = ""
    confidence: float = 0.0
    error: str | None = None


def discover_photo_candidates(parquets: list[Path]) -> Iterator[OcrCandidate]:
    for path in parquets:
        try:
            df = pl.read_parquet(path)
        except Exception:
            LOG.exception("image OCR: could not read parquet", path=str(path))
            continue
        for tweet in df.iter_rows(named=True):
            media = tweet.get("media") or []
            if not isinstance(media, list):
                continue
            for item in media:
                if not isinstance(item, dict):
                    continue
                if str(item.get("media_type") or "") != "photo":
                    continue
                asset_url = str(item.get("release_asset_url") or "")
                sha = str(item.get("sha256") or "")
                media_id = str(item.get("media_id") or "")
                if not asset_url or not sha or not media_id:
                    continue
                yield OcrCandidate(
                    tweet_id=str(tweet.get("tweet_id") or ""),
                    account_handle=str(tweet.get("account_handle") or ""),
                    media_id=media_id,
                    media_type="photo",
                    media_sha256=sha,
                    source_kind="photo",
                    source_path=asset_url,
                    release_asset_url=asset_url,
                    source_sha256=sha,
                )


def discover_keyframe_candidates(keyframes_path: Path | None = None) -> Iterator[OcrCandidate]:
    if keyframes_path is None:
        keyframes_path = KEYFRAMES_PATH
    if not keyframes_path.exists():
        return
    try:
        df = pl.read_parquet(keyframes_path)
    except Exception:
        LOG.exception("image OCR: could not read keyframes sidecar", path=str(keyframes_path))
        return
    for row in df.iter_rows(named=True):
        if str(row.get("status") or "") != "ok":
            continue
        frames = row.get("frames") or []
        if not isinstance(frames, list):
            continue
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            path = str(frame.get("path") or "")
            sha = str(frame.get("sha256") or "")
            if not path or not sha:
                continue
            local_path = REPO_ROOT / path
            if not local_path.exists():
                continue
            yield OcrCandidate(
                tweet_id=str(row.get("tweet_id") or ""),
                account_handle=str(row.get("account_handle") or ""),
                media_id=str(row.get("media_id") or ""),
                media_type="video_keyframe",
                media_sha256=str(row.get("media_sha256") or ""),
                source_kind="keyframe",
                source_path=path,
                release_asset_url="",
                source_sha256=sha,
            )


def discover_candidates(parquets: list[Path]) -> Iterator[OcrCandidate]:
    yield from discover_photo_candidates(parquets)
    yield from discover_keyframe_candidates()


def input_hash_for(cand: OcrCandidate) -> str:
    payload = {
        "ocr_version": OCR_VERSION,
        "source_kind": cand.source_kind,
        "source_sha256": cand.source_sha256,
        "source_path": cand.source_path,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_existing_index(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    df = pl.read_parquet(path)
    out: dict[str, dict[str, Any]] = {}
    for row in df.iter_rows(named=True):
        key = str(row.get("input_hash") or "")
        if key:
            out[key] = row
    return out


def load_existing_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return pl.read_parquet(path).to_dicts()


def row_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("tweet_id") or ""),
        str(row.get("media_id") or ""),
        str(row.get("source_kind") or ""),
        str(row.get("source_path") or ""),
        str(row.get("input_hash") or ""),
    )


def merge_existing_rows(
    rows: list[dict[str, Any]], existing_rows: list[dict[str, Any]], *, preserve_existing: bool
) -> list[dict[str, Any]]:
    if not preserve_existing:
        return rows
    merged: dict[tuple[str, str, str, str, str], dict[str, Any]] = {
        row_key(row): row for row in existing_rows
    }
    for row in rows:
        merged[row_key(row)] = row
    return list(merged.values())


def is_cache_hit(cached: dict[str, Any], ocr_version: str) -> bool:
    if not cached:
        return False
    if str(cached.get("ocr_version") or "") != ocr_version:
        return False
    return str(cached.get("status") or "") in CACHEABLE_STATUSES


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def tesseract_version() -> str:
    try:
        out = subprocess.check_output(
            ["tesseract", "--version"], timeout=15, stderr=subprocess.STDOUT
        )
    except Exception:
        return OCR_VERSION
    first = out.decode("utf-8", errors="replace").splitlines()[0].strip()
    return first or OCR_VERSION


def fetch_to_tempfile(url: str, http: httpx.Client) -> Path:
    with http.stream("GET", url, timeout=HTTP_TIMEOUT_SECS, follow_redirects=True) as resp:
        resp.raise_for_status()
        total = 0
        suffix = Path(url.split("?", 1)[0]).suffix or ".jpg"
        with tempfile.NamedTemporaryFile(
            prefix="imm-archive-ocr-", suffix=suffix, delete=False
        ) as fh:
            tmp_path = Path(fh.name)
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    tmp_path.unlink(missing_ok=True)
                    raise ValueError(f"image exceeds {MAX_IMAGE_BYTES} bytes")
                fh.write(chunk)
        return tmp_path


def run_tesseract(image_path: Path) -> OcrResult:
    if not tesseract_available():
        return OcrResult(status="skipped-no-tesseract", error="tesseract not on PATH")
    try:
        proc = subprocess.run(
            ["tesseract", str(image_path), "stdout", "--psm", "6", "-l", "eng", "tsv"],
            check=True,
            capture_output=True,
            timeout=90,
        )
    except subprocess.CalledProcessError as e:
        return OcrResult(
            status="tesseract-failed",
            error=(e.stderr or e.stdout or b"").decode("utf-8", errors="replace")[:1000],
        )
    except subprocess.TimeoutExpired as e:
        return OcrResult(status="tesseract-timeout", error=str(e))
    text, confidence = parse_tesseract_tsv(proc.stdout.decode("utf-8", errors="replace"))
    if not text:
        return OcrResult(status="no-text", text="", confidence=confidence)
    return OcrResult(status="ok", text=text, confidence=confidence)


def parse_tesseract_tsv(tsv_text: str) -> tuple[str, float]:
    reader = csv.DictReader(StringIO(tsv_text), delimiter="\t")
    words: list[str] = []
    confidences: list[float] = []
    for row in reader:
        word = clean_ocr_token(row.get("text") or "")
        if not word:
            continue
        words.append(word)
        try:
            conf = float(row.get("conf") or -1)
        except ValueError:
            conf = -1.0
        if conf >= 0:
            confidences.append(conf)
    text = clean_ocr_text(" ".join(words))
    confidence = sum(confidences) / len(confidences) / 100.0 if confidences else 0.0
    return text, confidence


def clean_ocr_token(token: str) -> str:
    token = token.strip()
    if not token:
        return ""
    token = re.sub(r"\s+", " ", token)
    return token


def clean_ocr_text(text: str) -> str:
    text = " ".join(text.split())
    # Tesseract often emits isolated punctuation runs from logos/borders.
    text = re.sub(r"(?:^|\s)[|_~`]{1,}(?=\s|$)", " ", text)
    return " ".join(text.split())[:6000]


def analyze_candidate(cand: OcrCandidate, *, http: httpx.Client) -> OcrResult:
    if cand.source_kind == "keyframe":
        return run_tesseract(REPO_ROOT / cand.source_path)
    try:
        local_image = fetch_to_tempfile(cand.release_asset_url, http)
    except Exception as e:
        return OcrResult(
            status="image-too-large" if isinstance(e, ValueError) else "fetch-failed",
            error=str(e),
        )
    try:
        return run_tesseract(local_image)
    finally:
        with contextlib.suppress(OSError):
            local_image.unlink(missing_ok=True)


def build_row(
    cand: OcrCandidate,
    result: OcrResult,
    *,
    generated_at: str,
    ocr_version: str,
) -> dict[str, Any]:
    return {
        "tweet_id": cand.tweet_id,
        "account_handle": cand.account_handle,
        "media_id": cand.media_id,
        "media_type": cand.media_type,
        "media_sha256": cand.media_sha256,
        "source_kind": cand.source_kind,
        "source_path": cand.source_path,
        "input_hash": input_hash_for(cand),
        "ocr_engine": OCR_ENGINE,
        "ocr_version": ocr_version,
        "ocr_at": generated_at,
        "text": result.text,
        "confidence": result.confidence,
        "status": result.status,
        "cost_estimate_usd": 0.0,
        "error": result.error,
    }


def write_parquet(rows: list[dict[str, Any]], path: Path) -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    df = (
        pl.DataFrame(rows, schema=IMAGE_OCR_SCHEMA, strict=False)
        if rows
        else empty_image_ocr_dataframe()
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.write_parquet(tmp, compression="zstd")
    os.replace(tmp, path)


def update_manifest(
    rows: list[dict[str, Any]],
    stats: dict[str, int],
    generated_at: str,
    *,
    ocr_version: str,
) -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    layers = manifest.get("layers")
    if not isinstance(layers, dict):
        layers = {}
    status_counts: dict[str, int] = dict(Counter(str(r.get("status") or "") for r in rows))
    layers["image_ocr"] = {
        "generated_at": generated_at,
        "ocr_engine": OCR_ENGINE,
        "ocr_version": ocr_version,
        "row_count": len(rows),
        "cost_estimate_usd": 0.0,
        "status_counts": status_counts,
        **{k: v for k, v in stats.items() if k != "ocr_version"},
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
    out_path: Path | None = None,
    ocr_runner: Callable[[OcrCandidate], OcrResult] | None = None,
) -> dict[str, int]:
    if out_path is None:
        out_path = OUT_PATH
    all_parquets = discover_canonical_parquets()
    parquets = parquets if parquets is not None else all_parquets
    ocr_version = tesseract_version() if tesseract_available() else OCR_VERSION
    existing_rows = load_existing_rows(out_path)
    existing = {
        str(row.get("input_hash") or ""): row
        for row in existing_rows
        if str(row.get("input_hash") or "")
    }
    preserve_existing = max_items is not None or {p.resolve() for p in parquets} != {
        p.resolve() for p in all_parquets
    }
    rows: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    http: httpx.Client | None = None
    runner: Callable[[OcrCandidate], OcrResult]
    if ocr_runner is None:
        http = httpx.Client(
            timeout=HTTP_TIMEOUT_SECS,
            follow_redirects=True,
            headers={"user-agent": "imm-archive-image-ocr/1.0"},
        )
        _http = http

        def _runner(c: OcrCandidate) -> OcrResult:
            return analyze_candidate(c, http=_http)

        runner = _runner
    else:
        runner = ocr_runner

    try:
        for cand in discover_candidates(parquets):
            input_hash = input_hash_for(cand)
            cached = existing.get(input_hash)
            if not force and is_cache_hit(cached or {}, ocr_version):
                row = {**(cached or {})}
                row["tweet_id"] = cand.tweet_id
                row["account_handle"] = cand.account_handle
                row["media_id"] = cand.media_id
                rows.append(row)
                stats["cache_hits"] += 1
                continue

            if max_items is not None and stats["attempted"] >= max_items:
                if cached:
                    rows.append(cached)
                    stats["preserved_after_max_items"] += 1
                stats["skipped_max_items"] += 1
                continue

            stats["attempted"] += 1
            result = runner(cand)
            stats[f"status_{result.status}"] += 1
            if result.status in CACHEABLE_STATUSES:
                stats["analyzed"] += 1
            rows.append(build_row(cand, result, generated_at=generated_at, ocr_version=ocr_version))
    finally:
        if http is not None:
            http.close()

    rows_to_write = merge_existing_rows(
        rows, existing_rows, preserve_existing=preserve_existing
    )
    stats["rows"] = len(rows_to_write)
    if not dry_run:
        write_parquet(rows_to_write, out_path)
        update_manifest(rows_to_write, dict(stats), generated_at, ocr_version=ocr_version)
    return dict(stats)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handle", help="Restrict to one data/<handle>.parquet file.")
    parser.add_argument(
        "--max-items",
        type=int,
        help="Maximum number of uncached images/keyframes to OCR this run.",
    )
    parser.add_argument("--force", action="store_true", help="Ignore the existing OCR cache.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Report planned rows without writing."
    )
    args = parser.parse_args(argv)

    parquets = discover_canonical_parquets()
    if args.handle:
        parquets = [p for p in parquets if p.stem == args.handle]

    stats = run(parquets=parquets, max_items=args.max_items, force=args.force, dry_run=args.dry_run)
    LOG.info("image OCR complete", **stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
