"""Campaign helper: verify sterility of review descriptions.

Checks two things:

1. The new produced_review / meme_image_review JSON files do not contain any
   banned term (case-insensitive) or em/en dash.
2. The applied opus-vision-review rows in media_vision.parquet are clean.

Banned (case-insensitive): propaganda, humaniz*, glorif*, spectacle,
youth-appeal, "out of scope", "target audience", "is_designed=", "intent:".
Also banned: en dash and em dash characters.

Exit code 0 when clean, 1 when any violation is found.

Run with::

    uv run python -m scripts._campaign_sterility            # check JSONs + parquet
    uv run python -m scripts._campaign_sterility --json-only
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Any

import polars as pl

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"
PRODUCED_REVIEW_DIR = TAGS_DIR / "produced_review"
MEME_REVIEW_DIR = TAGS_DIR / "meme_image_review"
MEDIA_VISION_PATH = TAGS_DIR / "media_vision.parquet"

BANNED = [
    "propaganda",
    "humaniz",
    "glorif",
    "spectacle",
    "youth-appeal",
    "out of scope",
    "target audience",
    "is_designed=",
    "intent:",
]
# Built via chr() so the source file itself stays ASCII-clean (and free of the
# very characters it forbids). U+2013 EN DASH, U+2014 EM DASH.
EN_DASH = chr(0x2013)
EM_DASH = chr(0x2014)
DASHES = [EN_DASH, EM_DASH]


def _scan(text: str) -> list[str]:
    hits: list[str] = []
    low = text.lower()
    for b in BANNED:
        if b in low:
            hits.append(b)
    for d in DASHES:
        if d in text:
            hits.append("en-dash" if d == EN_DASH else "em-dash")
    return hits


def _json_text(rec: dict[str, Any]) -> str:
    """All free-text fields a review JSON contributes to the description."""
    parts: list[str] = [
        str(rec.get("summary") or ""),
        str(rec.get("description") or ""),
        str(rec.get("notable_text") or ""),
    ]
    for shot in rec.get("script") or []:
        if isinstance(shot, dict):
            parts.append(str(shot.get("scene") or ""))
            parts.append(str(shot.get("timestamp") or ""))
    return "  ".join(parts)


def check_jsons() -> int:
    violations = 0
    files = sorted(glob.glob(str(PRODUCED_REVIEW_DIR / "*.json"))) + sorted(
        glob.glob(str(MEME_REVIEW_DIR / "*.json"))
    )
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            print(f"UNREADABLE {path}: {e}")
            violations += 1
            continue
        hits = _scan(_json_text(rec))
        if hits:
            violations += 1
            print(f"VIOLATION {Path(path).name}: {sorted(set(hits))}")
    print(f"json review files scanned: {len(files)}; violations: {violations}")
    return violations


def check_parquet() -> int:
    if not MEDIA_VISION_PATH.exists():
        print("media_vision.parquet missing")
        return 0
    df = pl.read_parquet(MEDIA_VISION_PATH).filter(pl.col("model") == "opus-vision-review")
    violations = 0
    for row in df.iter_rows(named=True):
        for field in ("description", "summary_text"):
            hits = _scan(str(row.get(field) or ""))
            if hits:
                violations += 1
                print(
                    f"VIOLATION row tweet={row.get('tweet_id')} "
                    f"media={row.get('media_id')} {field}: {sorted(set(hits))}"
                )
    print(f"opus-vision-review rows scanned: {df.height}; violations: {violations}")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument("--parquet-only", action="store_true")
    args = parser.parse_args(argv)
    total = 0
    if not args.parquet_only:
        total += check_jsons()
    if not args.json_only:
        total += check_parquet()
    if total == 0:
        print("STERILITY OK: 0 violations")
        return 0
    print(f"STERILITY FAILED: {total} violations")
    return 1


if __name__ == "__main__":
    sys.exit(main())
