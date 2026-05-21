"""Optional paid vision tier for archived photos and video keyframes.

This is the paid, budget-gated layer above the free local analyzers:

* ``describe_media`` records metadata and alt text.
* ``extract_video_frames`` creates cheap keyframes/thumbnails.
* ``tag_image_ocr`` reads text in images.
* ``detect_audio_music`` records audio/no-audio/music-likely signals.
* this script asks a small multimodal model to describe selected archived
  photos or video keyframes and writes ``data/tags/media_llm.parquet``.

The script never runs from the public viewer and never stores an API key.
OpenAI is the first-line recognizer via ``OPENAI_API_KEY``. Gemini is used only
as a narrow watermark/provenance verifier for suspected AI-generated
cases, capped at 5 calls per minute by default. Without an OpenAI key, normal
runs skip cleanly; ``--dry-run`` can still report the candidate count.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from scripts._logging import configure
from scripts._schema import MEDIA_VISION_SCHEMA, empty_media_vision_dataframe

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"
OUT_PATH = TAGS_DIR / "media_llm.parquet"
MANIFEST_PATH = TAGS_DIR / "manifest.json"
KEYFRAMES_PATH = TAGS_DIR / "keyframes.parquet"

GEMINI_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENAI_URL = "https://api.openai.com/v1/responses"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_OPENAI_MODEL = "gpt-5.4-nano"
DEFAULT_MODEL = DEFAULT_OPENAI_MODEL
PRIMARY_PROVIDER = "openai"
WATERMARK_PROVIDER = "gemini"
MODEL_VERSION = "openai-vision-v2"
DEFAULT_MAX_ITEMS = 20
DEFAULT_BUDGET_USD = 2.0
DEFAULT_GEMINI_RATE_LIMIT_PER_MINUTE = 5
MAX_IMAGES_PER_VIDEO = 3
MAX_REMOTE_IMAGE_BYTES = 8_000_000
HTTP_TIMEOUT_SECS = 90.0

# Defaults match the primary OpenAI model. Operators can override for
# another model with env vars.
DEFAULT_INPUT_USD_PER_MTOK = 0.10
DEFAULT_OUTPUT_USD_PER_MTOK = 0.625

ALLOWED_TAG_PREFIXES = (
    "action:",
    "agency:",
    "audio:",
    "branch:",
    "crime:",
    "format:",
    "frame:",
    "homicide:",
    "legal:",
    "media:",
    "phrase:",
    "shape:",
    "slogan:",
    "speaker:",
    "subject:",
    "theme:",
    "topic:",
    "video:",
)

PRODUCED_VIDEO_GENRE_TAGS = {
    "video:bodycam",
    "video:interview",
    "video:music-video",
    "video:news-clip",
    "video:psa",
    "video:speech",
    "video:ad",
}

PROMPT = """You are describing archived public X/Twitter media for an academic research archive.

Return ONLY a compact JSON object with this shape:
{"description": "...", "summary_text": "...", "tags": ["media:text-overlay"], "confidence": 0.0, "provenance_signal": false}

Rules:
- Describe only what is visible in the image(s) and the tweet context supplied below.
- For videos, the images are keyframes, not the full video. Say "keyframes show" when appropriate.
- Use concise, neutral language. Do not use the word propaganda.
- Useful media tags include media:produced-video, media:music-video, media:montage, media:text-overlay, media:voiceover.
- For produced videos, identify the genre where visible/context-supported with one or more of: video:bodycam, video:interview, video:music-video, video:news-clip, video:psa, video:speech, video:ad.
- If the media appears AI-generated or heavily synthetic from visual cues alone, add media:ai-generated, set provenance_signal false, and mention the visible cues in the description; do not assert certainty.
- If visible metadata, watermark labels, C2PA/Content Credentials text, or another explicit provenance signal indicates AI generation, add media:ai-generated and set provenance_signal true.
- Use speaker:<title/name> only when the tweet text or visible captions identify the speaker. Do not guess from a face.
- Use slogan:* only for branded slogans; use phrase:* for visible or context-supported recurring domain terms.
- Include no more than 8 tags. Prefer existing namespaces over inventing new ones.
"""
PROMPT_HASH = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()[:16]
CACHEABLE_STATUSES = {"ok", "no-visual-signal"}

GEMINI_WATERMARK_PROMPT = """You are verifying AI provenance for an academic media archive.

The primary recognizer suspects this media may be AI-generated. Your only task is to check whether the supplied image/keyframes show a Gemini/SynthID-style watermark, C2PA/Content Credentials, or another explicit AI-generation provenance marker.

Return ONLY compact JSON:
{"provenance_signal": false, "description": "", "confidence": 0.0}

Rules:
- Do not infer AI generation from style, artifacts, faces, hands, lighting, or general visual weirdness.
- Set provenance_signal true only for an explicit watermark, visible provenance label, C2PA/Content Credentials text, or equivalent machine/provider marker.
- Keep description short and name the visible provenance cue if one exists.
"""
GEMINI_WATERMARK_PROMPT_HASH = hashlib.sha256(
    GEMINI_WATERMARK_PROMPT.encode("utf-8")
).hexdigest()[:16]


@dataclass(frozen=True)
class ImageRef:
    kind: str
    url: str = ""
    path: str = ""
    sha256: str = ""


@dataclass(frozen=True)
class MediaLlmCandidate:
    tweet_id: str
    account_handle: str
    media_id: str
    media_type: str
    media_sha256: str
    release_asset_url: str
    tweet_text: str
    image_refs: tuple[ImageRef, ...]


@dataclass
class MediaLlmResult:
    status: str
    description: str = ""
    summary_text: str = ""
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    error: str | None = None
    provenance_signal: bool = False
    provenance_source: str = ""


@dataclass
class WatermarkResult:
    provenance_signal: bool
    description: str = ""
    confidence: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""


class RateLimiter:
    """Tiny wall-clock limiter for narrow verifier calls."""

    def __init__(self, max_per_minute: int = DEFAULT_GEMINI_RATE_LIMIT_PER_MINUTE) -> None:
        self.interval = 60.0 / max(1, max_per_minute)
        self.last_call_at = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        delay = self.interval - (now - self.last_call_at)
        if delay > 0:
            time.sleep(delay)
        self.last_call_at = time.monotonic()


def discover_canonical_parquets() -> list[Path]:
    return sorted(p for p in DATA_DIR.glob("*.parquet") if p.is_file())


def load_keyframes(path: Path | None = None) -> dict[str, list[ImageRef]]:
    path = path or KEYFRAMES_PATH
    if not path.exists():
        return {}
    try:
        df = pl.read_parquet(path)
    except Exception:
        LOG.exception("media llm: could not read keyframes", path=str(path))
        return {}
    by_media: dict[str, list[ImageRef]] = {}
    for row in df.iter_rows(named=True):
        if str(row.get("status") or "") != "ok":
            continue
        media_id = str(row.get("media_id") or "")
        frames = row.get("frames") or []
        if not media_id or not isinstance(frames, list):
            continue
        refs: list[ImageRef] = []
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            rel_path = str(frame.get("path") or "")
            sha = str(frame.get("sha256") or "")
            if not rel_path or not sha:
                continue
            if not (REPO_ROOT / rel_path).exists():
                continue
            refs.append(ImageRef(kind="keyframe", path=rel_path, sha256=sha))
        if refs:
            by_media[media_id] = refs[:MAX_IMAGES_PER_VIDEO]
    return by_media


def discover_candidates(
    parquets: list[Path],
    *,
    keyframes_by_media: dict[str, list[ImageRef]] | None = None,
) -> Iterator[MediaLlmCandidate]:
    keyframes_by_media = keyframes_by_media or load_keyframes()
    for path in parquets:
        try:
            df = pl.read_parquet(path)
        except Exception:
            LOG.exception("media llm: could not read parquet", path=str(path))
            continue
        for tweet in df.iter_rows(named=True):
            tweet_text = str(tweet.get("text_resolved") or tweet.get("text") or "")
            media = tweet.get("media") or []
            if not isinstance(media, list):
                continue
            for item in media:
                if not isinstance(item, dict):
                    continue
                media_type = str(item.get("media_type") or "")
                if media_type not in {"photo", "video", "animated_gif"}:
                    continue
                media_id = str(item.get("media_id") or "")
                media_sha = str(item.get("sha256") or "")
                asset_url = str(item.get("release_asset_url") or "")
                if not media_id or not media_sha:
                    continue
                refs: list[ImageRef] = []
                if media_type == "photo" and asset_url:
                    refs.append(ImageRef(kind="photo", url=asset_url, sha256=media_sha))
                elif media_type in {"video", "animated_gif"}:
                    refs.extend(keyframes_by_media.get(media_id, []))
                if not refs:
                    continue
                yield MediaLlmCandidate(
                    tweet_id=str(tweet.get("tweet_id") or ""),
                    account_handle=str(tweet.get("account_handle") or ""),
                    media_id=media_id,
                    media_type=media_type,
                    media_sha256=media_sha,
                    release_asset_url=asset_url,
                    tweet_text=tweet_text,
                    image_refs=tuple(refs),
                )


def input_hash_for(cand: MediaLlmCandidate, model: str, *, provider: str = PRIMARY_PROVIDER) -> str:
    payload = {
        "provider": provider,
        "model": model,
        "model_version": MODEL_VERSION,
        "prompt_hash": PROMPT_HASH,
        "tweet_id": cand.tweet_id,
        "media_id": cand.media_id,
        "media_sha256": cand.media_sha256,
        "refs": [ref.__dict__ for ref in cand.image_refs],
        "tweet_text": cand.tweet_text[:1200],
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


def is_cache_hit(cached: dict[str, Any], model: str, *, provider: str = PRIMARY_PROVIDER) -> bool:
    if not cached:
        return False
    if str(cached.get("model") or "") != provider:
        return False
    if str(cached.get("model_version") or "") != model:
        return False
    return str(cached.get("status") or "") in CACHEABLE_STATUSES


def image_ref_to_part(ref: ImageRef, http: httpx.Client | None = None) -> dict[str, Any]:
    if ref.url:
        data, mime = fetch_remote_image(ref.url, http=http)
        return {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode("ascii")}}
    path = REPO_ROOT / ref.path
    data = path.read_bytes()
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode("ascii")}}


def image_ref_to_openai_content(ref: ImageRef) -> dict[str, Any]:
    if ref.url:
        return {"type": "input_image", "image_url": ref.url, "detail": "low"}
    path = REPO_ROOT / ref.path
    data = path.read_bytes()
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    b64 = base64.b64encode(data).decode("ascii")
    return {"type": "input_image", "image_url": f"data:{mime};base64,{b64}", "detail": "low"}


def fetch_remote_image(url: str, http: httpx.Client | None = None) -> tuple[bytes, str]:
    """Download an archived still image with a hard byte cap."""
    close_http = http is None
    client = http or httpx.Client(timeout=HTTP_TIMEOUT_SECS, follow_redirects=True)
    try:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            mime = str(resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            if not mime.startswith("image/"):
                mime = "image/jpeg" if ".jpg" in url.lower() or ".jpeg" in url.lower() else "image/png"
            chunks: list[bytes] = []
            size = 0
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > MAX_REMOTE_IMAGE_BYTES:
                    raise ValueError(f"remote image exceeds {MAX_REMOTE_IMAGE_BYTES} byte cap")
                chunks.append(chunk)
            return b"".join(chunks), mime
    finally:
        if close_http:
            client.close()


def prompt_for(cand: MediaLlmCandidate) -> str:
    frame_note = (
        f"{len(cand.image_refs)} keyframe(s)" if cand.media_type != "photo" else "1 archived photo"
    )
    return (
        f"{PROMPT}\n\n"
        f"Tweet id: {cand.tweet_id}\n"
        f"Account: @{cand.account_handle}\n"
        f"Media type: {cand.media_type}; supplied visual inputs: {frame_note}\n"
        f"Tweet text/context:\n{cand.tweet_text[:1600]}\n"
    )


def watermark_prompt_for(cand: MediaLlmCandidate, primary: MediaLlmResult) -> str:
    frame_note = (
        f"{len(cand.image_refs)} keyframe(s)" if cand.media_type != "photo" else "1 archived photo"
    )
    return (
        f"{GEMINI_WATERMARK_PROMPT}\n\n"
        f"Tweet id: {cand.tweet_id}\n"
        f"Account: @{cand.account_handle}\n"
        f"Media type: {cand.media_type}; supplied visual inputs: {frame_note}\n"
        f"Primary recognizer description:\n{primary.description[:1000]}\n"
        f"Tweet text/context:\n{cand.tweet_text[:1000]}\n"
    )
def call_gemini_watermark(
    cand: MediaLlmCandidate,
    *,
    api_key: str,
    model: str,
    primary: MediaLlmResult,
    rate_limiter: RateLimiter | None = None,
    timeout: float = HTTP_TIMEOUT_SECS,
) -> WatermarkResult:
    if rate_limiter is not None:
        rate_limiter.wait()
    parts: list[dict[str, Any]] = [{"text": watermark_prompt_for(cand, primary)}]
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 220,
            "responseMimeType": "application/json",
        },
    }
    with httpx.Client(timeout=timeout, follow_redirects=True) as http:
        for ref in cand.image_refs:
            parts.append(image_ref_to_part(ref, http=http))
        resp = http.post(
            GEMINI_URL_TEMPLATE.format(model=model),
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
                "User-Agent": "imm-archive-gemini-watermark/1.0",
            },
            json=body,
        )
        resp.raise_for_status()
        payload = resp.json()
    text = extract_response_text(payload)
    parsed = parse_model_json(text)
    return WatermarkResult(
        provenance_signal=bool(parsed.get("provenance_signal")),
        description=clean_text(str(parsed.get("description") or "")),
        confidence=clamp_float(parsed.get("confidence"), 0.0, 1.0, default=0.0),
        usage=payload.get("usageMetadata") if isinstance(payload.get("usageMetadata"), dict) else {},
        raw_text=text,
    )


def call_openai(
    cand: MediaLlmCandidate,
    *,
    api_key: str,
    model: str,
    timeout: float = HTTP_TIMEOUT_SECS,
) -> MediaLlmResult:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt_for(cand)}]
    for ref in cand.image_refs:
        content.append(image_ref_to_openai_content(ref))
    body = {
        "model": model,
        "input": [{"role": "user", "content": content}],
        "max_output_tokens": 700,
    }
    with httpx.Client(timeout=timeout) as http:
        resp = http.post(
            OPENAI_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "imm-archive-openai-vision/1.0",
            },
            json=body,
        )
        resp.raise_for_status()
        payload = resp.json()
    text = extract_response_text(payload)
    parsed = parse_model_json(text)
    tags = normalize_llm_tags(parsed.get("tags"))
    status = "ok" if parsed.get("description") or tags else "no-visual-signal"
    return MediaLlmResult(
        status=status,
        description=clean_text(str(parsed.get("description") or "")),
        summary_text=clean_text(str(parsed.get("summary_text") or "")),
        tags=tags,
        confidence=clamp_float(parsed.get("confidence"), 0.0, 1.0, default=0.0),
        usage=payload.get("usage") if isinstance(payload.get("usage"), dict) else {},
        raw_text=text,
        provenance_signal=bool(parsed.get("provenance_signal")),
        provenance_source="openai" if parsed.get("provenance_signal") else "",
    )


def extract_response_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    parts: list[str] = []
    for cand in payload.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        content = cand.get("content")
        if not isinstance(content, dict):
            continue
        for part in content.get("parts") or []:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str):
                parts.append(text)
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts).strip()


def parse_model_json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def normalize_llm_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        tag = str(item or "").strip()
        if not tag or tag in seen:
            continue
        if not any(tag.startswith(prefix) for prefix in ALLOWED_TAG_PREFIXES):
            continue
        seen.add(tag)
        out.append(tag)
        if len(out) >= 8:
            break
    return out


def require_produced_video_parent(tags: list[str]) -> list[str]:
    if any(tag in PRODUCED_VIDEO_GENRE_TAGS for tag in tags) and "media:produced-video" not in tags:
        return ["media:produced-video", *tags]
    return tags


def should_verify_with_gemini(result: MediaLlmResult) -> bool:
    return "media:ai-generated" in result.tags


def merge_watermark_result(result: MediaLlmResult, watermark: WatermarkResult) -> MediaLlmResult:
    if not watermark.provenance_signal:
        return result
    description = result.description
    if watermark.description and watermark.description not in description:
        description = clean_text(f"{description} Gemini provenance check: {watermark.description}")
    usage = dict(result.usage)
    usage["gemini_watermark"] = watermark.usage
    return MediaLlmResult(
        status=result.status,
        description=description,
        summary_text=result.summary_text,
        tags=result.tags,
        confidence=max(result.confidence, watermark.confidence),
        usage=usage,
        raw_text=result.raw_text,
        error=result.error,
        provenance_signal=True,
        provenance_source="gemini-watermark",
    )


def tag_entry(tag: str, *, tentative: bool = False, source: str = "openai-vision") -> dict[str, Any]:
    return {
        "tag": tag,
        "tentative": True if tentative else None,
        "source": source,
        "span_start": None,
        "span_end": None,
    }


def build_row(
    cand: MediaLlmCandidate,
    result: MediaLlmResult,
    *,
    generated_at: str,
    model: str,
    provider: str,
    input_hash: str,
) -> dict[str, Any]:
    source = f"{provider}-vision"
    result_tags = require_produced_video_parent(result.tags)
    tags = [
        tag_entry(
            tag,
            tentative=result.confidence < 0.74
            or (tag == "media:ai-generated" and not result.provenance_signal),
            source=source,
        )
        for tag in result_tags
    ]
    if result.status == "ok":
        tags.insert(0, tag_entry("media:described", source=source))
    if cand.media_type in {"video", "animated_gif"} and any(ref.kind == "keyframe" for ref in cand.image_refs):
        tags.append(tag_entry("media:keyframe-reviewed", source=source))
    return {
        "tweet_id": cand.tweet_id,
        "account_handle": cand.account_handle,
        "media_id": cand.media_id,
        "media_type": cand.media_type,
        "media_sha256": cand.media_sha256,
        "input_hash": input_hash,
        "generated_at": generated_at,
        "model": provider,
        "model_version": model,
        "prompt_hash": PROMPT_HASH,
        "description": result.description,
        "summary_text": result.summary_text or result.description[:240],
        "confidence": result.confidence,
        "cost_estimate_usd": cost_estimate_usd(result.usage, provider=provider),
        "status": result.status,
        "tags": tags,
        "source_fields": ["release_asset_url" if cand.media_type == "photo" else "keyframes"],
        "error": result.error,
    }


def cost_estimate_usd(usage: dict[str, Any], *, provider: str = PRIMARY_PROVIDER) -> float:
    if not usage:
        return 0.0
    nested_gemini = usage.get("gemini_watermark")
    nested_cost = (
        cost_estimate_usd(nested_gemini, provider=WATERMARK_PROVIDER)
        if isinstance(nested_gemini, dict)
        else 0.0
    )
    input_tokens = int(
        usage.get("promptTokenCount") or usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    )
    output_tokens = int(
        usage.get("candidatesTokenCount")
        or usage.get("output_tokens")
        or usage.get("completion_tokens")
        or 0
    )
    if provider == WATERMARK_PROVIDER:
        input_rate_env = "GEMINI_MEDIA_LLM_INPUT_USD_PER_MTOK"
        output_rate_env = "GEMINI_MEDIA_LLM_OUTPUT_USD_PER_MTOK"
        default_input = 0.30
        default_output = 2.50
    else:
        input_rate_env = "OPENAI_MEDIA_LLM_INPUT_USD_PER_MTOK"
        output_rate_env = "OPENAI_MEDIA_LLM_OUTPUT_USD_PER_MTOK"
        default_input = DEFAULT_INPUT_USD_PER_MTOK
        default_output = DEFAULT_OUTPUT_USD_PER_MTOK
    input_rate = float(os.environ.get(input_rate_env, default_input))
    output_rate = float(os.environ.get(output_rate_env, default_output))
    return (
        (input_tokens / 1_000_000 * input_rate)
        + (output_tokens / 1_000_000 * output_rate)
        + nested_cost
    )


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def clamp_float(value: Any, low: float, high: float, *, default: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def write_parquet(rows: list[dict[str, Any]], path: Path) -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    df = (
        pl.DataFrame(rows, schema=MEDIA_VISION_SCHEMA, strict=False)
        if rows
        else empty_media_vision_dataframe()
    )
    tmp = path.with_suffix(".tmp.parquet")
    df.write_parquet(tmp, compression="zstd")
    os.replace(tmp, path)


def update_manifest(
    rows: list[dict[str, Any]],
    stats: dict[str, int],
    generated_at: str,
    *,
    path: Path | None = None,
) -> None:
    path = path or MANIFEST_PATH
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    if path.exists():
        manifest = json.loads(path.read_text(encoding="utf-8"))
    layers = manifest.get("layers")
    if not isinstance(layers, dict):
        layers = {}
    layers["media_llm"] = {
        "generated_at": generated_at,
        "model": PRIMARY_PROVIDER,
        "model_version": rows[0]["model_version"]
        if rows
        else os.environ.get("OPENAI_MEDIA_LLM_MODEL", DEFAULT_OPENAI_MODEL),
        "prompt_hash": PROMPT_HASH,
        "rows": len(rows),
        "status_counts": dict(Counter(str(r.get("status") or "") for r in rows)),
        "cost_estimate_usd": sum(float(r.get("cost_estimate_usd") or 0.0) for r in rows),
        **stats,
    }
    manifest["layers"] = layers
    tmp = path.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run(
    *,
    parquets: list[Path] | None = None,
    max_items: int = DEFAULT_MAX_ITEMS,
    budget_usd: float = DEFAULT_BUDGET_USD,
    model: str = DEFAULT_OPENAI_MODEL,
    gemini_model: str = DEFAULT_GEMINI_MODEL,
    gemini_rate_limit_per_minute: int = DEFAULT_GEMINI_RATE_LIMIT_PER_MINUTE,
    force: bool = False,
    dry_run: bool = False,
    out_path: Path | None = None,
    openai_api_key: str | None = None,
    gemini_api_key: str | None = None,
    analyzer: Callable[[MediaLlmCandidate], MediaLlmResult] | None = None,
    watermark_analyzer: Callable[[MediaLlmCandidate, MediaLlmResult], WatermarkResult]
    | None = None,
) -> dict[str, int]:
    out_path = out_path or OUT_PATH
    parquets = parquets if parquets is not None else discover_canonical_parquets()
    existing = load_existing_index(out_path)
    rows: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    openai_api_key = openai_api_key or os.environ.get("OPENAI_API_KEY")
    gemini_api_key = gemini_api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get(
        "GOOGLE_API_KEY"
    )
    rate_limiter = RateLimiter(gemini_rate_limit_per_minute)
    spent = 0.0

    if analyzer is None and not openai_api_key:
        stats["skipped_no_api_key"] = 1
        if not dry_run:
            write_parquet(list(existing.values()), out_path)
        return dict(stats)

    for cand in discover_candidates(parquets):
        input_hash = input_hash_for(cand, model, provider=PRIMARY_PROVIDER)
        cached = existing.get(input_hash)
        if not force and is_cache_hit(cached or {}, model, provider=PRIMARY_PROVIDER):
            rows.append({**(cached or {})})
            stats["cache_hits"] += 1
            continue
        if stats["attempted"] >= max_items:
            stats["skipped_max_items"] += 1
            continue
        if spent >= budget_usd:
            stats["skipped_budget"] += 1
            continue
        stats["attempted"] += 1
        try:
            if analyzer is not None:
                result = analyzer(cand)
            else:
                result = call_openai(cand, api_key=str(openai_api_key), model=model)
        except Exception as exc:
            result = MediaLlmResult(status="model-error", error=str(exc))
        if should_verify_with_gemini(result):
            if watermark_analyzer is not None or gemini_api_key:
                try:
                    watermark = (
                        watermark_analyzer(cand, result)
                        if watermark_analyzer is not None
                        else call_gemini_watermark(
                            cand,
                            api_key=str(gemini_api_key),
                            model=gemini_model,
                            primary=result,
                            rate_limiter=rate_limiter,
                        )
                    )
                    stats["gemini_watermark_attempts"] += 1
                    if watermark.provenance_signal:
                        stats["gemini_watermark_confirmed"] += 1
                    result = merge_watermark_result(result, watermark)
                except Exception as exc:
                    stats["gemini_watermark_errors"] += 1
                    result.error = (
                        f"{result.error}; gemini watermark: {exc}"
                        if result.error
                        else f"gemini watermark: {exc}"
                    )
            else:
                stats["gemini_watermark_skipped_no_key"] += 1
        row = build_row(
            cand,
            result,
            generated_at=generated_at,
            model=model,
            provider=PRIMARY_PROVIDER,
            input_hash=input_hash,
        )
        spent += float(row.get("cost_estimate_usd") or 0.0)
        rows.append(row)
        stats[f"provider_{PRIMARY_PROVIDER}"] += 1
        stats[f"status_{result.status}"] += 1
    stats["rows"] = len(rows)
    if not dry_run:
        write_parquet(rows, out_path)
        update_manifest(rows, dict(stats), generated_at)
    return dict(stats)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handle", help="Restrict to one data/<handle>.parquet file.")
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD)
    parser.add_argument("--model", default=os.environ.get("OPENAI_MEDIA_LLM_MODEL", DEFAULT_OPENAI_MODEL))
    parser.add_argument(
        "--gemini-model",
        default=os.environ.get("GEMINI_MEDIA_LLM_MODEL", DEFAULT_GEMINI_MODEL),
        help="Gemini model used only for suspected-AI watermark/provenance verification.",
    )
    parser.add_argument(
        "--gemini-rate-limit-per-minute",
        type=int,
        default=DEFAULT_GEMINI_RATE_LIMIT_PER_MINUTE,
        help="Maximum Gemini watermark verifier calls per minute.",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    parquets = discover_canonical_parquets()
    if args.handle:
        parquets = [p for p in parquets if p.stem == args.handle]
    stats = run(
        parquets=parquets,
        max_items=args.max_items,
        budget_usd=args.budget_usd,
        model=args.model,
        gemini_model=args.gemini_model,
        gemini_rate_limit_per_minute=args.gemini_rate_limit_per_minute,
        force=args.force,
        dry_run=args.dry_run,
    )
    LOG.info("media llm tier complete", **stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
