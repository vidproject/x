"""Campaign helper: rank the needs-vision media backlog.

Read-only analysis used to drive the media-description campaign. Identifies
media rows in ``media_vision.parquet`` that still need a real vision
description (``model == 'metadata'`` and ``media_type`` is visual), joins an
immigration text signal, checks whether keyframes already exist on disk, and
ranks by engagement.

Two signal tiers:

* STRONG  - immigration-specific words appear in the transcript, the on-image
  OCR, or the tweet text itself (deport, ICE, border patrol, migrant, ...),
  OR the lexical tagger attached a topic:immigration / action:report-immigrants
  tag. These are high-confidence immigration items.
* WEAK    - only a looser lexical tag (policy:border, phrase:immigrant, an
  agency handle) matched. Surfaced separately so the driver can prefer STRONG.

Not a writer: prints / returns candidate lists so the campaign driver can
pick the next batch. Import ``rank_backlog`` or run as a module for a report.

Run with:  uv run python -m scripts._campaign_backlog [--kind video|photo]
           [--limit N] [--tier strong|any] [--no-keyframes|--with-keyframes]
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import polars as pl

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"
KEYFRAMES_DIR = DATA_DIR / "derived" / "keyframes"

MEDIA_VISION_PATH = TAGS_DIR / "media_vision.parquet"
LEXICAL_PATH = TAGS_DIR / "lexical.parquet"
OCR_PATH = TAGS_DIR / "image_ocr.parquet"
TRANSCRIPTS_PATH = TAGS_DIR / "transcripts.parquet"
CATALOG_PATH = DATA_DIR / "catalog.parquet"

VISUAL_TYPES = {"photo", "video", "animated_gif"}
VIDEO_TYPES = {"video", "animated_gif"}
VISION_REVIEW_MODEL = "opus-vision-review"
NEEDS_VISION_TAG = "media-status:needs-vision"

# Strong, immigration-specific free-text signal (transcript / OCR / tweet text).
STRONG_TEXT_RE = re.compile(
    r"\b("
    r"deport|deportation|deporting|illegal alien|illegal immigrant|illegal immigration|"
    r"undocumented|border patrol|migrant|i\.?c\.?e\.?\b|customs and border|cbp\b|"
    r"uscis|asylum|sanctuary city|sanctuary cities|criminal alien|removal operation|"
    r"self.deport|mass deportation|green card|visa overstay|border security|"
    r"secure the border|catch and release|got.?aways|border czar|"
    r"homeland security investigations|immigration enforcement| port of entry|"
    r"naturalization|citizenship ceremony|remain in mexico|title 42|"
    r"detention (center|facility)|alien enemies|tren de aragua|ms.?13|"
    r"southern border|northern border|invasion|border crossing|illegal border"
    r")\b",
    re.IGNORECASE,
)
# Strong lexical tags (immigration is the core topic of the tweet).
STRONG_TAG_PREFIX = (
    "topic:immigration",
    "action:report-immigrants",
)
# Weaker lexical tags (immigration-adjacent; may be incidental).
WEAK_TAG_PREFIX = (
    "policy:border",
    "phrase:immigrant",
    "agency:icegov",
    "agency:cbp",
)


def _tag_names(tags: Any) -> list[str]:
    out: list[str] = []
    for entry in tags or []:
        if isinstance(entry, dict) and entry.get("tag"):
            out.append(str(entry["tag"]))
        elif isinstance(entry, str):
            out.append(entry)
    return out


def _tag_tier(tags: Any) -> str:
    """'strong' / 'weak' / '' for a lexical tag list."""
    tier = ""
    for name in _tag_names(tags):
        low = name.lower()
        if any(low.startswith(p) for p in STRONG_TAG_PREFIX):
            return "strong"
        if any(low.startswith(p) for p in WEAK_TAG_PREFIX):
            tier = "weak"
    return tier


def load_tag_tiers() -> dict[str, str]:
    out: dict[str, str] = {}
    if not LEXICAL_PATH.exists():
        return out
    df = pl.read_parquet(LEXICAL_PATH, columns=["tweet_id", "tags"])
    for row in df.iter_rows(named=True):
        tier = _tag_tier(row.get("tags"))
        if tier:
            out[str(row.get("tweet_id") or "")] = tier
    return out


def load_text_strong(path: Path) -> set[str]:
    """tweet_ids whose OCR / transcript text carries a strong immigration term."""
    if not path.exists():
        return set()
    df = pl.read_parquet(path, columns=["tweet_id", "text", "status"])
    out: set[str] = set()
    for row in df.iter_rows(named=True):
        if str(row.get("status") or "") not in {"ok", "no-text"}:
            continue
        txt = str(row.get("text") or "")
        if txt and STRONG_TEXT_RE.search(txt):
            out.add(str(row.get("tweet_id") or ""))
    return out


def load_tweet_text_strong() -> set[str]:
    df = pl.read_parquet(CATALOG_PATH, columns=["tweet_id", "text", "text_resolved"])
    out: set[str] = set()
    for row in df.iter_rows(named=True):
        txt = str(row.get("text_resolved") or row.get("text") or "")
        if txt and STRONG_TEXT_RE.search(txt):
            out.add(str(row.get("tweet_id") or ""))
    return out


def load_keyframe_shas() -> set[str]:
    out: set[str] = set()
    if not KEYFRAMES_DIR.exists():
        return out
    for child in KEYFRAMES_DIR.iterdir():
        if child.is_dir() and any(child.glob("*.jpg")):
            out.add(child.name)
    return out


def load_engagement() -> dict[str, int]:
    df = pl.read_parquet(
        CATALOG_PATH, columns=["tweet_id", "like_count", "retweet_count", "view_count"]
    )
    out: dict[str, int] = {}
    for row in df.iter_rows(named=True):
        likes = int(row.get("like_count") or 0)
        rts = int(row.get("retweet_count") or 0)
        views = int(row.get("view_count") or 0)
        out[str(row.get("tweet_id") or "")] = likes + 3 * rts + views // 1000
    return out


def rank_backlog(kind: str = "video", tier: str = "any") -> list[dict[str, Any]]:
    """Return needs-vision candidates of ``kind`` with an immigration signal.

    ``tier`` = "strong" keeps only high-confidence immigration items; "any"
    includes weak lexical-tag-only matches too. Sorted by engagement desc.
    """
    df = pl.read_parquet(MEDIA_VISION_PATH)
    tag_tiers = load_tag_tiers()
    ocr_strong = load_text_strong(OCR_PATH)
    trans_strong = load_text_strong(TRANSCRIPTS_PATH)
    text_strong = load_tweet_text_strong()
    kf_shas = load_keyframe_shas()
    engagement = load_engagement()

    want_types = VIDEO_TYPES if kind == "video" else {"photo"}

    cands: list[dict[str, Any]] = []
    seen_media: set[tuple[str, str]] = set()
    for row in df.iter_rows(named=True):
        media_type = str(row.get("media_type") or "")
        if media_type not in want_types:
            continue
        if str(row.get("model") or "") == VISION_REVIEW_MODEL:
            continue
        # Only surface media the reconcile step considers genuinely undescribed
        # (carries the needs-vision tag). This excludes rows already covered by a
        # manual visual observation / alt text, so describing one truly moves the
        # described count up.
        tag_set = {
            str(e["tag"]) for e in (row.get("tags") or []) if isinstance(e, dict) and e.get("tag")
        }
        if NEEDS_VISION_TAG not in tag_set:
            continue
        tweet_id = str(row.get("tweet_id") or "")
        media_id = str(row.get("media_id") or "")
        if (tweet_id, media_id) in seen_media:
            continue
        seen_media.add((tweet_id, media_id))

        has_strong_text = (
            tweet_id in ocr_strong or tweet_id in trans_strong or tweet_id in text_strong
        )
        ttier = tag_tiers.get(tweet_id, "")
        is_strong = has_strong_text or ttier == "strong"
        is_any = is_strong or ttier == "weak"
        if not is_any:
            continue
        if tier == "strong" and not is_strong:
            continue

        sha = str(row.get("media_sha256") or "")
        signal = "+".join(
            s
            for s in (
                "tag:" + ttier if ttier else "",
                "ocr" if tweet_id in ocr_strong else "",
                "trans" if tweet_id in trans_strong else "",
                "text" if tweet_id in text_strong else "",
            )
            if s
        )
        cands.append(
            {
                "tweet_id": tweet_id,
                "account_handle": str(row.get("account_handle") or ""),
                "media_id": media_id,
                "media_type": media_type,
                "media_sha256": sha,
                "has_keyframes": sha in kf_shas,
                "engagement": engagement.get(tweet_id, 0),
                "tier": "strong" if is_strong else "weak",
                "signal": signal,
            }
        )
    cands.sort(key=lambda c: c["engagement"], reverse=True)
    return cands


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=["video", "photo"], default="video")
    parser.add_argument("--tier", choices=["strong", "any"], default="strong")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--no-keyframes", action="store_true")
    parser.add_argument("--with-keyframes", action="store_true")
    args = parser.parse_args(argv)

    cands = rank_backlog(args.kind, args.tier)
    total = len(cands)
    with_kf = sum(1 for c in cands if c["has_keyframes"])
    strong = sum(1 for c in cands if c["tier"] == "strong")
    print(
        f"{args.kind} (tier={args.tier}): {total} needs-vision "
        f"({strong} strong); {with_kf} have keyframes, {total - with_kf} need extraction"
    )
    shown = cands
    if args.no_keyframes:
        shown = [c for c in cands if not c["has_keyframes"]]
    elif args.with_keyframes:
        shown = [c for c in cands if c["has_keyframes"]]
    seen = set()
    n = 0
    for c in shown:
        if c["tweet_id"] in seen:
            continue
        seen.add(c["tweet_id"])
        print(
            f"  {c['engagement']:>8}  kf={'Y' if c['has_keyframes'] else 'N'}  "
            f"{c['tier']:<6} {c['account_handle']:<16} "
            f"{c['tweet_id']:<20} {c['media_id']:<22} [{c['signal']}]"
        )
        n += 1
        if n >= args.limit:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
