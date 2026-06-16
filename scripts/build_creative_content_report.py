"""Build a PDF dossier of creative and brutality-normalizing media candidates.

This report is an editorial research artifact. It reads canonical data and
annotation sidecars, but it does not write tags back into the archive.

Run with:

    uv run --with reportlab --with pillow python -m scripts.build_creative_content_report
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from scripts._logging import configure

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"
OUTPUT_DIR = REPO_ROOT / "output" / "pdf"

CREATIVE_TAG_WEIGHTS = {
    "genre:music-video": 9,
    "genre:parody": 8,
    "genre:dystopian": 8,
    "genre:war-movie": 8,
    "genre:utopian": 7,
    "video:montage": 7,
    "video:text-overlay": 5,
    "video:produced": 4,
    "genre:recruitment": 4,
    "genre:advertisement": 4,
    "genre:psa": 3,
    "audio:music-likely": 4,
    "media:ai-generated": 5,
}

CONCERN_TAG_WEIGHTS = {
    "media-status:graphic-content": 10,
    "slogan:find-and-kill": 10,
    "genre:war-movie": 8,
    "genre:dystopian": 8,
    "video:bodycam": 7,
    "action:deportation": 6,
    "action:detention": 5,
    "action:self-deportation": 5,
    "action:report-immigrants": 7,
    "slogan:mass-deportation": 6,
    "slogan:go-home": 6,
    "slogan:criminal-illegal-alien": 5,
    "slogan:illegal-alien": 3,
    "slogan:worst": 5,
    "theme:nativism": 6,
    "theme:criminal": 3,
    "theme:civil-disturbance": 4,
    "theme:martyrdom": 4,
    "crime:homicide": 5,
    "crime:murder": 5,
    "crime:assault": 4,
    "crime:rape": 5,
    "crime:child-sexual": 5,
    "crime:terrorism": 4,
    "crime:narcotics": 2,
}

CREATIVE_PATTERNS = [
    (
        re.compile(r"\b(montage|b-roll|rapid[- ]cut|multi[- ]shot|sequence of clips)\b", re.I),
        5,
        "edited montage language",
    ),
    (
        re.compile(r"\b(music video|soundtrack|music bed|background music|set to music)\b", re.I),
        5,
        "music or soundtrack language",
    ),
    (
        re.compile(r"\b(cinematic|trailer[- ]style|war movie|action movie)\b", re.I),
        6,
        "cinematic/trailer framing",
    ),
    (
        re.compile(r"\b(parody|star wars|top gun|ghostbusters|superman|pawn stars)\b", re.I),
        6,
        "parody/pop-culture framing",
    ),
    (
        re.compile(r"\b(text overlay|title card|end card|chyron|captioned)\b", re.I),
        4,
        "text-overlay language",
    ),
    (
        re.compile(r"\b(ai-generated|deepfake|synthetic media|midjourney)\b", re.I),
        5,
        "explicit AI/synthetic media signal",
    ),
    (re.compile(r"\b(hype|meme|viral|poster|graphic)\b", re.I), 3, "meme/graphic language"),
]

CONCERN_PATTERNS = [
    (
        re.compile(r"\b(deport|deported|deportation|mass deportation|self[- ]deport)\b", re.I),
        5,
        "deportation language",
    ),
    (
        re.compile(r"\b(detain|detained|detention|custody|jail|prison)\b", re.I),
        4,
        "detention/custody language",
    ),
    (
        re.compile(r"\b(arrest|raid|operation|take(?:n)? down|takedown)\b", re.I),
        4,
        "raid/arrest language",
    ),
    (
        re.compile(r"\b(kill|find and kill|hunt|hunted|target)\b", re.I),
        7,
        "kill/hunt/target language",
    ),
    (
        re.compile(r"\b(worst of the worst|criminal illegal alien|illegal alien|go home)\b", re.I),
        5,
        "dehumanizing or exclusionary slogan",
    ),
    (
        re.compile(r"\b(bodycam|surveillance|shackles|cuffed|handcuff|mugshot|lineup)\b", re.I),
        5,
        "carceral visual vocabulary",
    ),
    (
        re.compile(
            r"\b(violent|violence|murder|homicide|rape|assault|terrorist|trafficking)\b", re.I
        ),
        4,
        "violence/crime vocabulary",
    ),
    (
        re.compile(r"\b(dark age|invasion|invader|enemy within)\b", re.I),
        5,
        "apocalyptic/invasion framing",
    ),
]

HIGH_SIGNAL_TAGS = {
    "genre:music-video",
    "genre:parody",
    "genre:dystopian",
    "genre:war-movie",
    "genre:utopian",
    "video:montage",
    "video:text-overlay",
    "audio:music-likely",
    "media:ai-generated",
    "slogan:find-and-kill",
    "media-status:graphic-content",
}

IN_SCOPE_ACCOUNT_CATEGORIES = {"core", "government", "officials"}

TEXT_REPLACEMENTS = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2022": "-",
        "\u25a0": "-",
        "\u25aa": "-",
        "\u2026": "...",
        "\u2192": "->",
        "\u00a0": " ",
    }
)
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_text(value: Any, limit: int | None = None) -> str:
    text = str(value or "")
    text = text.translate(TEXT_REPLACEMENTS)
    text = CONTROL_RE.sub(" ", text)
    text = text.encode("latin-1", errors="ignore").decode("latin-1")
    text = re.sub(r"\s+", " ", text).strip()
    if limit is not None and len(text) > limit:
        return text[: limit - 1].rstrip() + "..."
    return text


def is_useful_snippet(text: str) -> bool:
    if not text:
        return False
    letters = sum(1 for char in text if char.isalpha())
    alnum = sum(1 for char in text if char.isalnum())
    if len(text) >= 50 and letters / max(alnum, 1) < 0.25:
        return False
    return letters >= 3 or len(text) < 20


def tag_values(values: Any) -> list[str]:
    out: list[str] = []
    for entry in values or []:
        tag = entry.get("tag") if isinstance(entry, dict) else str(entry or "")
        tag = str(tag or "").strip()
        if tag and tag not in out:
            out.append(tag)
    return out


def tag_reasons(tags: set[str], weights: dict[str, int]) -> list[str]:
    return [tag for tag in sorted(tags) if tag in weights]


def read_parquet(path: Path) -> pl.DataFrame:
    if not path.exists():
        return pl.DataFrame()
    return pl.read_parquet(path)


def first_nonempty(values: list[str], limit: int = 240) -> str:
    for value in values:
        text = clean_text(value, limit)
        if is_useful_snippet(text):
            return text
    return ""


def load_text_sidecar(path: Path, text_column: str) -> dict[str, list[str]]:
    df = read_parquet(path)
    out: dict[str, list[str]] = defaultdict(list)
    if df.is_empty() or text_column not in df.columns:
        return out
    for row in df.select(["tweet_id", text_column]).iter_rows(named=True):
        text = clean_text(row.get(text_column))
        if is_useful_snippet(text):
            out[str(row["tweet_id"])].append(text)
    return out


def load_vision() -> dict[str, list[str]]:
    df = read_parquet(TAGS_DIR / "media_vision.parquet")
    out: dict[str, list[str]] = defaultdict(list)
    if df.is_empty():
        return out
    for row in df.select(["tweet_id", "description", "summary_text"]).iter_rows(named=True):
        parts = [clean_text(row.get("description")), clean_text(row.get("summary_text"))]
        text = " | ".join(part for part in parts if part)
        if text:
            out[str(row["tweet_id"])].append(text)
    return out


def load_audio_music() -> dict[str, dict[str, Any]]:
    df = read_parquet(TAGS_DIR / "audio_music.parquet")
    out: dict[str, dict[str, Any]] = defaultdict(lambda: {"max_music_score": None, "tags": set()})
    if df.is_empty():
        return out
    for row in df.select(["tweet_id", "music_score", "status", "tags"]).iter_rows(named=True):
        tweet_id = str(row["tweet_id"])
        score = row.get("music_score")
        if score is not None:
            current = out[tweet_id]["max_music_score"]
            out[tweet_id]["max_music_score"] = max(current or 0.0, float(score))
        out[tweet_id]["tags"].update(tag_values(row.get("tags")))
        if row.get("status"):
            out[tweet_id].setdefault("statuses", set()).add(str(row["status"]))
    return out


def load_thumbnails() -> dict[str, str]:
    out: dict[str, str] = {}
    for path in (TAGS_DIR / "keyframes.parquet", TAGS_DIR / "photo_thumbnails.parquet"):
        df = read_parquet(path)
        if df.is_empty() or "thumbnail_path" not in df.columns:
            continue
        columns = ["tweet_id", "thumbnail_path", "status"]
        for row in df.select([c for c in columns if c in df.columns]).iter_rows(named=True):
            if row.get("status") and str(row["status"]) != "ok":
                continue
            rel = clean_text(row.get("thumbnail_path"))
            if not rel:
                continue
            thumb = (REPO_ROOT / rel).resolve()
            if thumb.exists() and str(row["tweet_id"]) not in out:
                out[str(row["tweet_id"])] = str(thumb)
    return out


def load_core_audit() -> dict[str, dict[str, Any]]:
    path = TAGS_DIR / "core_video_audit.csv"
    if not path.exists():
        return {}
    df = pl.read_csv(path, infer_schema_length=2000)
    out: dict[str, dict[str, Any]] = {}
    for row in df.iter_rows(named=True):
        tweet_id = str(row.get("tweet_id") or "")
        if not tweet_id:
            continue
        existing = out.get(tweet_id)
        if existing is None or int(row.get("priority") or 9999) < int(
            existing.get("priority") or 9999
        ):
            out[tweet_id] = row
    return out


def load_reviewed_creative() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "reviewed_types": [],
            "genre_refs": [],
            "summary": [],
            "notable_text": [],
            "script": [],
            "engagement": None,
        }
    )
    produced_path = TAGS_DIR / "produced_videos.csv"
    if produced_path.exists():
        df = pl.read_csv(produced_path, infer_schema_length=1000)
        for row in df.iter_rows(named=True):
            tweet_id = str(row.get("tweet_id") or "")
            if not tweet_id:
                continue
            item = out[tweet_id]
            item["reviewed_types"].append("reviewed:produced-video")
            if row.get("content_type"):
                item["reviewed_types"].append(clean_text(row["content_type"]))
            if row.get("genre_tags"):
                item["genre_refs"].extend(
                    [part.strip() for part in str(row["genre_tags"]).split(";") if part.strip()]
                )
            if row.get("set_to_music") is True or str(row.get("set_to_music")).lower() == "true":
                item["genre_refs"].append("review:set-to-music")
            for source_key, target_key in (
                ("summary", "summary"),
                ("notable_text", "notable_text"),
                ("script", "script"),
            ):
                text = clean_text(row.get(source_key))
                if text:
                    item[target_key].append(text)
            if row.get("engagement") is not None:
                item["engagement"] = max(item["engagement"] or 0, int(row["engagement"]))

    meme_path = TAGS_DIR / "meme_images.csv"
    if meme_path.exists():
        df = pl.read_csv(meme_path, infer_schema_length=1000)
        for row in df.iter_rows(named=True):
            tweet_id = str(row.get("tweet_id") or "")
            if not tweet_id:
                continue
            item = out[tweet_id]
            item["reviewed_types"].append("reviewed:creative-still")
            if row.get("content_type"):
                item["reviewed_types"].append(clean_text(row["content_type"]))
            if row.get("genre_refs"):
                item["genre_refs"].append(clean_text(row["genre_refs"]))
            for source_key, target_key in (
                ("description", "summary"),
                ("notable_text", "notable_text"),
            ):
                text = clean_text(row.get(source_key))
                if text:
                    item[target_key].append(text)
            if row.get("engagement") is not None:
                item["engagement"] = max(item["engagement"] or 0, int(row["engagement"]))

    return dict(out)


def load_account_categories() -> dict[str, str]:
    path = DATA_DIR / "account_categories.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    categories = data.get("categories") if isinstance(data, dict) else {}
    out: dict[str, str] = {}
    if isinstance(categories, dict):
        for handle, meta in categories.items():
            if isinstance(meta, dict):
                out[str(handle)] = str(meta.get("category") or "public")
    return out


def score_patterns(
    text: str, patterns: list[tuple[re.Pattern[str], int, str]]
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    for pattern, weight, reason in patterns:
        if pattern.search(text):
            score += weight
            reasons.append(reason)
    return score, reasons


def engagement_score(row: dict[str, Any]) -> float:
    total = 0.0
    for key, weight in (("like_count", 1.0), ("retweet_count", 2.0), ("quote_count", 2.0)):
        value = row.get(key)
        if value is not None:
            total += max(0, int(value)) * weight
    views = row.get("view_count")
    if views is not None:
        total += max(0, int(views)) / 200.0
    return math.log10(total + 1.0)


def classify_candidate(row: dict[str, Any], sidecars: dict[str, Any]) -> dict[str, Any] | None:
    tweet_id = str(row.get("tweet_id") or "")
    account_handle = str(row.get("account_handle") or "")
    tags = set(tag_values(row.get("tags")))
    audio = sidecars["audio"].get(tweet_id, {})
    tags.update(audio.get("tags") or set())

    text = clean_text(row.get("text_resolved") or row.get("text"))
    vision_snippet = first_nonempty(sidecars["vision"].get(tweet_id, []))
    ocr_snippet = first_nonempty(sidecars["ocr"].get(tweet_id, []))
    transcript_snippet = first_nonempty(sidecars["transcripts"].get(tweet_id, []))
    reviewed = sidecars["reviewed"].get(tweet_id, {})
    account_category = sidecars["account_categories"].get(account_handle, "public")
    if account_category not in IN_SCOPE_ACCOUNT_CATEGORIES and not reviewed:
        return None
    reviewed_summary = first_nonempty(reviewed.get("summary", []), 320)
    reviewed_notable = first_nonempty(reviewed.get("notable_text", []), 240)
    reviewed_script = first_nonempty(reviewed.get("script", []), 240)
    combined = " ".join(
        [
            text,
            vision_snippet,
            ocr_snippet,
            transcript_snippet,
            reviewed_summary,
            reviewed_notable,
            reviewed_script,
        ]
    )

    creative_score = sum(CREATIVE_TAG_WEIGHTS.get(tag, 0) for tag in tags)
    concern_score = sum(CONCERN_TAG_WEIGHTS.get(tag, 0) for tag in tags)
    pattern_creative_score, creative_patterns = score_patterns(combined, CREATIVE_PATTERNS)
    pattern_concern_score, concern_patterns = score_patterns(combined, CONCERN_PATTERNS)
    creative_score += pattern_creative_score
    concern_score += pattern_concern_score

    core = sidecars["core_audit"].get(tweet_id, {})
    if core:
        bucket = str(core.get("bucket") or "")
        if bucket in {"produced-video", "genre-experiment"}:
            creative_score += 3
        if core.get("genre_tags"):
            creative_score += 2
    else:
        bucket = ""

    if reviewed:
        creative_score += 16
        if any("produced" in value for value in reviewed.get("reviewed_types", [])):
            creative_score += 3
        if any("set-to-music" in value for value in reviewed.get("genre_refs", [])):
            creative_score += 3
        if any(
            needle
            in " ".join(reviewed.get("reviewed_types", []) + reviewed.get("summary", [])).lower()
            for needle in ("asmr", "deportation", "detention", "chain", "shackle", "mass deport")
        ):
            concern_score += 8

    media = row.get("media") or []
    has_video = any(
        isinstance(item, dict) and item.get("media_type") in {"video", "animated_gif"}
        for item in media
    )
    has_photo = any(isinstance(item, dict) and item.get("media_type") == "photo" for item in media)
    if has_video:
        creative_score += 1
    if has_photo and ("media:ai-generated" in tags or "video:text-overlay" in tags):
        creative_score += 1

    if creative_score <= 0 and concern_score < 10:
        return None

    creative_reasons = tag_reasons(tags, CREATIVE_TAG_WEIGHTS) + creative_patterns
    concern_reasons = tag_reasons(tags, CONCERN_TAG_WEIGHTS) + concern_patterns
    if reviewed:
        creative_reasons.extend(sorted(set(reviewed.get("reviewed_types", [])))[:4])
        creative_reasons.extend(sorted(set(reviewed.get("genre_refs", [])))[:4])
        if reviewed_summary:
            creative_reasons.append("hand-reviewed media description")
    music_score = audio.get("max_music_score")
    if music_score is not None and float(music_score) >= 0.60:
        creative_reasons.append(f"audio music score {float(music_score):.2f}")

    confidence = "medium"
    if tags.intersection(HIGH_SIGNAL_TAGS) or core.get("bucket") == "genre-experiment" or reviewed:
        confidence = "high"
    if creative_score < 5 and not tags.intersection(HIGH_SIGNAL_TAGS):
        confidence = "low"

    total_score = creative_score * 1.35 + concern_score + engagement_score(row)
    if creative_score and concern_score:
        total_score += 5
    if reviewed and reviewed.get("engagement"):
        total_score += math.log10(float(reviewed["engagement"]) + 1.0) * 6.0

    labels: list[str] = []
    for tag in sorted(tags):
        if tag.startswith(("genre:", "video:", "audio:", "parody:", "artist:", "media:ai")):
            labels.append(tag)
    if core.get("bucket"):
        labels.append(f"audit:{core['bucket']}")
    if reviewed:
        labels.extend(sorted(set(reviewed.get("reviewed_types", [])))[:6])
        labels.extend(sorted(set(reviewed.get("genre_refs", [])))[:6])

    return {
        "tweet_id": tweet_id,
        "account_handle": row.get("account_handle"),
        "account_category": account_category,
        "posted_at": row.get("posted_at"),
        "tweet_url": row.get("tweet_url"),
        "tweet_text": clean_text(text, 520),
        "creative_score": round(creative_score, 2),
        "concern_score": round(concern_score, 2),
        "total_score": round(total_score, 2),
        "confidence": confidence,
        "bucket": bucket,
        "labels": labels[:18],
        "creative_reasons": sorted(set(creative_reasons))[:14],
        "concern_reasons": sorted(set(concern_reasons))[:14],
        "vision_snippet": first_nonempty([reviewed_summary, vision_snippet]),
        "ocr_snippet": ocr_snippet,
        "transcript_snippet": transcript_snippet,
        "reviewed_notable": reviewed_notable,
        "thumbnail_path": sidecars["thumbnails"].get(tweet_id),
        "engagement": {
            "likes": row.get("like_count"),
            "retweets": row.get("retweet_count"),
            "quotes": row.get("quote_count"),
            "views": row.get("view_count"),
        },
    }


def build_candidates() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    catalog = pl.read_parquet(DATA_DIR / "catalog.parquet")
    sidecars = {
        "vision": load_vision(),
        "ocr": load_text_sidecar(TAGS_DIR / "image_ocr.parquet", "text"),
        "transcripts": load_text_sidecar(TAGS_DIR / "transcripts.parquet", "text"),
        "audio": load_audio_music(),
        "thumbnails": load_thumbnails(),
        "core_audit": load_core_audit(),
        "reviewed": load_reviewed_creative(),
        "account_categories": load_account_categories(),
    }
    candidates: list[dict[str, Any]] = []
    for row in catalog.iter_rows(named=True):
        item = classify_candidate(row, sidecars)
        if item is not None:
            candidates.append(item)
    candidates.sort(key=lambda item: (-float(item["total_score"]), str(item["posted_at"] or "")))

    tag_counts: Counter[str] = Counter()
    confidence_counts: Counter[str] = Counter()
    account_counts: Counter[str] = Counter()
    for item in candidates:
        confidence_counts.update([str(item["confidence"])])
        account_counts.update([str(item.get("account_handle") or "")])
        tag_counts.update(item.get("labels") or [])

    core_audit_df = pl.read_csv(TAGS_DIR / "core_video_audit.csv", infer_schema_length=2000)
    bucket_counts = core_audit_df.group_by("bucket").len()
    metadata = {
        "generated_at": now_iso(),
        "source_commit": current_commit(),
        "catalog_rows": catalog.height,
        "candidate_count": len(candidates),
        "high_confidence_count": confidence_counts.get("high", 0),
        "core_video_audit_rows": core_audit_df.height,
        "core_video_audit_buckets": {
            str(row["bucket"]): int(row["len"]) for row in bucket_counts.iter_rows(named=True)
        },
        "tag_counts": dict(tag_counts.most_common(40)),
        "account_counts": dict(account_counts.most_common(25)),
        "sidecar_rows": sidecar_row_counts(),
    }
    return candidates, metadata


def current_commit() -> str:
    import subprocess

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def sidecar_row_counts() -> dict[str, int]:
    out: dict[str, int] = {}
    for path in sorted(TAGS_DIR.glob("*.parquet")):
        try:
            out[path.name] = pl.scan_parquet(path).select(pl.len()).collect().item()
        except Exception:
            out[path.name] = -1
    return out


def build_pdf(candidates: list[dict[str, Any]], metadata: dict[str, Any], out_path: Path) -> None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
        )
    except ImportError as exc:
        raise SystemExit(
            "reportlab is required. Run with: "
            "uv run --with reportlab --with pillow python -m scripts.build_creative_content_report"
        ) from exc

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="TitleCenter",
            parent=styles["Title"],
            alignment=TA_CENTER,
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Small",
            parent=styles["BodyText"],
            fontSize=8,
            leading=10,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Tiny",
            parent=styles["BodyText"],
            fontSize=7,
            leading=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Section",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            spaceBefore=10,
            spaceAfter=6,
        )
    )

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=letter,
        leftMargin=0.55 * inch,
        rightMargin=0.55 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
        title="Creative Content and Brutality-Normalization Dossier",
    )

    story: list[Any] = []
    story.append(
        Paragraph("Creative Content and Brutality-Normalization Dossier", styles["TitleCenter"])
    )
    story.append(
        Paragraph(
            esc(
                f"Generated {metadata['generated_at']} from commit {metadata['source_commit']}. "
                "This is an editorial research report, not a canonical tag layer."
            ),
            styles["Small"],
        )
    )
    story.append(Spacer(1, 8))
    story.append(Paragraph("Coverage", styles["Section"]))
    story.append(
        Paragraph(
            esc(
                f"Catalog rows scanned: {metadata['catalog_rows']:,}. Candidate rows: "
                f"{metadata['candidate_count']:,}; high-confidence rows: "
                f"{metadata['high_confidence_count']:,}. Core video audit rows: "
                f"{metadata['core_video_audit_rows']:,}."
            ),
            styles["BodyText"],
        )
    )
    sidecar_text = ", ".join(
        f"{name}: {count:,}" for name, count in metadata["sidecar_rows"].items()
    )
    story.append(Paragraph(esc("Sidecars used: " + sidecar_text), styles["Small"]))

    story.append(Paragraph("Top Highlights", styles["Section"]))
    for item in candidates[:12]:
        story.extend(candidate_block(item, styles, include_image=True))

    story.append(PageBreak())
    story.append(
        Paragraph("Creative, Hype Edit, Montage, and Meme-Like Candidates", styles["Section"])
    )
    creative = sorted(
        [item for item in candidates if item["creative_score"] > 0],
        key=lambda item: (-float(item["creative_score"]), -float(item["total_score"])),
    )
    story.extend(candidate_table(creative[:70], styles))

    story.append(PageBreak())
    story.append(
        Paragraph(
            "Surreal, Disturbing, or Brutality-Normalizing Research Flags",
            styles["Section"],
        )
    )
    story.append(
        Paragraph(
            esc(
                "These are not canonical tags. They flag posts whose creative form, slogans, "
                "audio/video presentation, or enforcement/crime framing may warrant human review "
                "for surrealism, callousness, violence, or normalization of brutality."
            ),
            styles["BodyText"],
        )
    )
    concern = sorted(
        [item for item in candidates if item["concern_score"] > 0],
        key=lambda item: (-float(item["concern_score"]), -float(item["total_score"])),
    )
    story.extend(candidate_table(concern[:70], styles))

    story.append(PageBreak())
    story.append(Paragraph("Category Counts", styles["Section"]))
    tag_rows = [["Label", "Count"]]
    tag_rows.extend([short_cell(k, styles), str(v)] for k, v in metadata["tag_counts"].items())
    story.append(styled_table(tag_rows, [4.8 * inch, 1.0 * inch]))
    story.append(Spacer(1, 8))
    story.append(Paragraph("Top Accounts in Candidate Set", styles["Section"]))
    account_rows = [["Account", "Count"]]
    account_rows.extend(
        [short_cell(k, styles), str(v)] for k, v in metadata["account_counts"].items()
    )
    story.append(styled_table(account_rows, [4.8 * inch, 1.0 * inch]))

    def footer(canvas: Any, document: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.grey)
        canvas.drawString(0.55 * inch, 0.32 * inch, "vidproject/x creative content dossier")
        canvas.drawRightString(7.95 * inch, 0.32 * inch, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def esc(value: Any) -> str:
    return html.escape(clean_text(value))


def short_cell(value: Any, styles: dict[str, Any], limit: int = 140) -> Any:
    from reportlab.platypus import Paragraph

    return Paragraph(esc(clean_text(value, limit)), styles["Tiny"])


def candidate_block(
    item: dict[str, Any], styles: dict[str, Any], *, include_image: bool
) -> list[Any]:
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import Image, KeepTogether, Paragraph, Spacer, Table, TableStyle

    title = (
        f"{item.get('account_handle')} | {clean_text(item.get('posted_at'))[:10]} | "
        f"{item.get('tweet_id')} | C {item['creative_score']} / R {item['concern_score']}"
    )
    body = [
        Paragraph(f"<b>{esc(title)}</b>", styles["Small"]),
        Paragraph(esc(item.get("tweet_text")), styles["Small"]),
        Paragraph(esc("Labels: " + ", ".join(item.get("labels") or [])), styles["Tiny"]),
        Paragraph(
            esc("Creative: " + "; ".join(item.get("creative_reasons") or ["none"])),
            styles["Tiny"],
        ),
        Paragraph(
            esc("Concern: " + "; ".join(item.get("concern_reasons") or ["none"])),
            styles["Tiny"],
        ),
        Paragraph(esc(str(item.get("tweet_url") or "")), styles["Tiny"]),
    ]
    if item.get("vision_snippet"):
        body.append(Paragraph(esc("Media: " + item["vision_snippet"]), styles["Tiny"]))
    if item.get("reviewed_notable"):
        body.append(Paragraph(esc("Notable text: " + item["reviewed_notable"]), styles["Tiny"]))
    if item.get("ocr_snippet"):
        body.append(Paragraph(esc("OCR: " + item["ocr_snippet"]), styles["Tiny"]))
    if item.get("transcript_snippet"):
        body.append(Paragraph(esc("Transcript: " + item["transcript_snippet"]), styles["Tiny"]))

    thumb_flow: Any = ""
    thumb = item.get("thumbnail_path")
    if include_image and thumb and Path(str(thumb)).exists():
        try:
            thumb_flow = Image(str(thumb), width=1.0 * inch, height=0.75 * inch)
        except Exception:
            thumb_flow = ""

    table = Table([[thumb_flow, body]], colWidths=[1.1 * inch, 6.1 * inch])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return [KeepTogether([table, Spacer(1, 5)])]


def candidate_table(items: list[dict[str, Any]], styles: dict[str, Any]) -> list[Any]:
    rows: list[list[Any]] = [["Account/date", "Tweet/evidence", "Why flagged"]]
    for item in items:
        first = f"{item.get('account_handle')}\n{clean_text(item.get('posted_at'))[:10]}\n{item.get('tweet_id')}"
        evidence = f"{item.get('tweet_text')}\n{item.get('tweet_url')}"
        if item.get("vision_snippet"):
            evidence += f"\nMedia: {item['vision_snippet']}"
        if item.get("reviewed_notable"):
            evidence += f"\nNotable text: {item['reviewed_notable']}"
        why = (
            f"C {item['creative_score']} / R {item['concern_score']} / {item['confidence']}\n"
            f"{', '.join(item.get('labels') or [])}\n"
            f"Creative: {'; '.join(item.get('creative_reasons') or ['none'])}\n"
            f"Concern: {'; '.join(item.get('concern_reasons') or ['none'])}"
        )
        rows.append(
            [
                short_cell(first, styles, 80),
                short_cell(evidence, styles, 360),
                short_cell(why, styles, 260),
            ]
        )
    return [styled_table(rows, [1.35 * 72, 3.45 * 72, 2.4 * 72])]


def styled_table(rows: list[list[Any]], widths: list[float]) -> Any:
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef7")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.2, colors.lightgrey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def write_json(candidates: list[dict[str, Any]], metadata: dict[str, Any], path: Path) -> None:
    payload = {"metadata": metadata, "candidates": candidates}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pdf-out",
        type=Path,
        default=OUTPUT_DIR / "creative-content-dossier-2026-06-16.pdf",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=OUTPUT_DIR / "creative-content-dossier-2026-06-16.candidates.json",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.pdf_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    candidates, metadata = build_candidates()
    write_json(candidates, metadata, args.json_out)
    build_pdf(candidates, metadata, args.pdf_out)
    LOG.info(
        "creative content report written",
        pdf=str(args.pdf_out),
        json=str(args.json_out),
        candidates=len(candidates),
    )


if __name__ == "__main__":
    main()
