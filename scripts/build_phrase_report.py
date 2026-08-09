"""Build the direct phrase-evidence report used by the GitHub Pages viewer.

The report counts unique tweets, not raw phrase occurrences. Evidence comes
from authored tweet text, OCR over archived photos and video keyframes, and
audio transcripts. Model-written media descriptions are deliberately excluded
from the primary counts because they are not verbatim evidence.

Run with::

    python3 -m scripts.build_phrase_report
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = REPO_ROOT / "data" / "catalog.parquet"
OCR_PATH = REPO_ROOT / "data" / "tags" / "image_ocr.parquet"
TRANSCRIPTS_PATH = REPO_ROOT / "data" / "tags" / "transcripts.parquet"
OUT_DIR = REPO_ROOT / "phrases"

ACCOUNT_GROUPS = {
    "dhsgov": "DHS",
    "icegov": "ICE",
    "cbp": "CBP",
    "obamawhitehouse": "White House",
    "whitehouse45": "White House",
    "whitehouse46": "White House",
    "whitehouse": "White House",
}
ACCOUNT_GROUP_ORDER = ("DHS", "ICE", "CBP", "White House")

WORD_SEPARATOR = r"[\s\-\u2010-\u2015]+"


@dataclass(frozen=True)
class PhraseSpec:
    key: str
    label: str
    variants: str
    pattern: re.Pattern[str]


PHRASES = (
    PhraseSpec(
        key="criminal-alien",
        label="Criminal alien",
        variants="criminal alien(s); criminal illegal alien(s)",
        pattern=re.compile(
            rf"\bcriminal{WORD_SEPARATOR}(?:illegal{WORD_SEPARATOR})?aliens?\b",
            re.IGNORECASE,
        ),
    ),
    PhraseSpec(
        key="illegal-alien",
        label="Illegal alien",
        variants="illegal alien(s)",
        pattern=re.compile(rf"\billegal{WORD_SEPARATOR}aliens?\b", re.IGNORECASE),
    ),
    PhraseSpec(
        key="angel-mother",
        label="Angel mother",
        variants="angel mother(s); angel mom(s)",
        pattern=re.compile(
            rf"\bangel{WORD_SEPARATOR}(?:mothers?|moms?)\b", re.IGNORECASE
        ),
    ),
    PhraseSpec(
        key="angel-family",
        label="Angel families",
        variants="angel family; angel families",
        pattern=re.compile(
            rf"\bangel{WORD_SEPARATOR}famil(?:y|ies)\b", re.IGNORECASE
        ),
    ),
)
PHRASE_BY_KEY = {phrase.key: phrase for phrase in PHRASES}
SOURCE_ORDER = ("tweet-text", "image-ocr", "video-frame-ocr", "transcript")


def normalize_variant(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).lower()


def match_phrases(text: str) -> dict[str, list[tuple[str, int, int]]]:
    """Return every direct phrase match with its literal variant and span."""

    matches: dict[str, list[tuple[str, int, int]]] = {}
    for phrase in PHRASES:
        found = [
            (normalize_variant(match.group(0)), match.start(), match.end())
            for match in phrase.pattern.finditer(text or "")
        ]
        if found:
            matches[phrase.key] = found
    return matches


def evidence_snippet(text: str, start: int, end: int, radius: int = 120) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    snippet = re.sub(r"\s+", " ", text[left:right]).strip()
    if left:
        snippet = f"...{snippet}"
    if right < len(text):
        snippet = f"{snippet}..."
    return snippet


def _clean_media(raw_media: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_media, list):
        return []
    media: list[dict[str, Any]] = []
    for item in raw_media:
        if not isinstance(item, dict):
            continue
        asset_url = str(item.get("release_asset_url") or "")
        if not asset_url:
            continue
        media.append(
            {
                "media_id": str(item.get("media_id") or ""),
                "media_type": str(item.get("media_type") or ""),
                "release_asset_url": asset_url,
                "duration_sec": item.get("duration_sec"),
                "alt_text": item.get("alt_text"),
            }
        )
    return media


def _new_tweet(row: dict[str, Any]) -> dict[str, Any]:
    handle = str(row.get("account_handle") or "")
    return {
        "tweet_id": str(row.get("tweet_id") or ""),
        "account_handle": handle,
        "account_group": ACCOUNT_GROUPS[handle.lower()],
        "posted_at": str(row.get("posted_at") or ""),
        "tweet_url": str(row.get("tweet_url") or ""),
        "tweet_type": str(row.get("tweet_type") or ""),
        "text": str(row.get("text_resolved") or row.get("text") or ""),
        "media": _clean_media(row.get("media")),
        "phrases": set(),
        "sources": set(),
        "variants": defaultdict(set),
        "evidence": [],
    }


def _add_evidence(
    tweet: dict[str, Any],
    *,
    source: str,
    text: str,
    media_id: str = "",
    confidence: float | None = None,
) -> None:
    for phrase_key, matches in match_phrases(text).items():
        tweet["phrases"].add(phrase_key)
        tweet["sources"].add(source)
        for variant, start, end in matches:
            tweet["variants"][phrase_key].add(variant)
            tweet["evidence"].append(
                {
                    "phrase": phrase_key,
                    "source": source,
                    "variant": variant,
                    "snippet": evidence_snippet(text, start, end),
                    "media_id": media_id,
                    "confidence": confidence,
                }
            )


def _read_rows(path: Path, columns: list[str] | None = None) -> list[dict[str, Any]]:
    table = pq.read_table(path, columns=columns)  # type: ignore[no-untyped-call]
    return cast(list[dict[str, Any]], table.to_pylist())


def build_report(
    catalog_path: Path = CATALOG_PATH,
    ocr_path: Path = OCR_PATH,
    transcripts_path: Path = TRANSCRIPTS_PATH,
) -> dict[str, Any]:
    catalog_rows = _read_rows(
        catalog_path,
        [
            "tweet_id",
            "account_handle",
            "posted_at",
            "tweet_url",
            "tweet_type",
            "text",
            "text_resolved",
            "media",
        ],
    )
    tweets: dict[str, dict[str, Any]] = {}
    scoped_tweet_count = 0
    scoped_media_tweet_count = 0
    for row in catalog_rows:
        handle = str(row.get("account_handle") or "")
        if handle.lower() not in ACCOUNT_GROUPS:
            continue
        scoped_tweet_count += 1
        catalog_tweet = _new_tweet(row)
        if catalog_tweet["media"]:
            scoped_media_tweet_count += 1
        _add_evidence(
            catalog_tweet, source="tweet-text", text=catalog_tweet["text"]
        )
        tweets[catalog_tweet["tweet_id"]] = catalog_tweet

    coverage = {
        "catalog_tweets": scoped_tweet_count,
        "tweets_with_archived_media": scoped_media_tweet_count,
        "photo_ocr_ok": 0,
        "video_frame_ocr_ok": 0,
        "transcripts_ok": 0,
    }

    for row in _read_rows(ocr_path):
        handle = str(row.get("account_handle") or "")
        if handle.lower() not in ACCOUNT_GROUPS or row.get("status") != "ok":
            continue
        source = "video-frame-ocr" if row.get("source_kind") == "keyframe" else "image-ocr"
        coverage["video_frame_ocr_ok" if source == "video-frame-ocr" else "photo_ocr_ok"] += 1
        evidence_tweet = tweets.get(str(row.get("tweet_id") or ""))
        if evidence_tweet is None:
            continue
        confidence = row.get("confidence")
        _add_evidence(
            evidence_tweet,
            source=source,
            text=str(row.get("text") or ""),
            media_id=str(row.get("media_id") or ""),
            confidence=float(confidence) if confidence is not None else None,
        )

    for row in _read_rows(transcripts_path):
        handle = str(row.get("account_handle") or "")
        if handle.lower() not in ACCOUNT_GROUPS or row.get("status") != "ok":
            continue
        coverage["transcripts_ok"] += 1
        evidence_tweet = tweets.get(str(row.get("tweet_id") or ""))
        if evidence_tweet is None:
            continue
        _add_evidence(
            evidence_tweet,
            source="transcript",
            text=str(row.get("text") or ""),
            media_id=str(row.get("media_id") or ""),
        )

    matched = [tweet for tweet in tweets.values() if tweet["phrases"]]
    for tweet in matched:
        tweet["phrases"] = sorted(tweet["phrases"], key=lambda key: list(PHRASE_BY_KEY).index(key))
        tweet["sources"] = sorted(tweet["sources"], key=SOURCE_ORDER.index)
        tweet["variants"] = {
            key: sorted(values) for key, values in sorted(tweet["variants"].items())
        }
        tweet["evidence"].sort(
            key=lambda item: (
                list(PHRASE_BY_KEY).index(item["phrase"]),
                SOURCE_ORDER.index(item["source"]),
                item["media_id"],
                item["variant"],
            )
        )
    matched.sort(key=lambda tweet: (tweet["posted_at"], tweet["tweet_id"]), reverse=True)

    summary: dict[str, Any] = {}
    for phrase in PHRASES:
        phrase_tweets = [tweet for tweet in matched if phrase.key in tweet["phrases"]]
        source_counts = {
            source: sum(
                any(
                    evidence["phrase"] == phrase.key and evidence["source"] == source
                    for evidence in tweet["evidence"]
                )
                for tweet in phrase_tweets
            )
            for source in SOURCE_ORDER
        }
        account_counts = Counter(tweet["account_group"] for tweet in phrase_tweets)
        variant_tweets: dict[str, set[str]] = defaultdict(set)
        for tweet in phrase_tweets:
            for variant in tweet["variants"].get(phrase.key, []):
                variant_tweets[variant].add(tweet["tweet_id"])
        summary[phrase.key] = {
            "unique_tweets": len(phrase_tweets),
            "by_source": source_counts,
            "by_account": {
                group: account_counts.get(group, 0) for group in ACCOUNT_GROUP_ORDER
            },
            "variants": [
                {"variant": variant, "unique_tweets": len(ids)}
                for variant, ids in sorted(
                    variant_tweets.items(), key=lambda item: (-len(item[1]), item[0])
                )
            ],
        }

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "accounts": sorted(ACCOUNT_GROUPS),
            "account_groups": list(ACCOUNT_GROUP_ORDER),
            "earliest": min(tweet["posted_at"] for tweet in tweets.values()),
            "latest": max(tweet["posted_at"] for tweet in tweets.values()),
            "method": "Direct post text, image/video-frame OCR, and audio transcripts",
            "overlap_note": (
                "A tweet can count in more than one phrase family or evidence source. "
                "Criminal illegal alien also counts as illegal alien."
            ),
        },
        "phrases": [
            {"key": phrase.key, "label": phrase.label, "variants": phrase.variants}
            for phrase in PHRASES
        ],
        "coverage": coverage,
        "summary": summary,
        "unique_matching_tweets": len(matched),
        "tweets": matched,
    }


def write_report(report: dict[str, Any], out_dir: Path = OUT_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "data.json").write_text(
        json.dumps(report, ensure_ascii=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with (out_dir / "tweets.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "tweet_id",
                "posted_at",
                "account_group",
                "account_handle",
                "phrase",
                "variants",
                "evidence_sources",
                "tweet_text",
                "evidence_snippets",
                "tweet_url",
                "archive_url",
                "media_urls",
            ]
        )
        for tweet in report["tweets"]:
            for phrase_key in tweet["phrases"]:
                evidence = [
                    item for item in tweet["evidence"] if item["phrase"] == phrase_key
                ]
                writer.writerow(
                    [
                        tweet["tweet_id"],
                        tweet["posted_at"],
                        tweet["account_group"],
                        tweet["account_handle"],
                        phrase_key,
                        " | ".join(tweet["variants"].get(phrase_key, [])),
                        " | ".join(dict.fromkeys(item["source"] for item in evidence)),
                        tweet["text"],
                        " | ".join(dict.fromkeys(item["snippet"] for item in evidence)),
                        tweet["tweet_url"],
                        f"https://vidproject.github.io/x/#tweet={tweet['tweet_id']}",
                        " | ".join(item["release_asset_url"] for item in tweet["media"]),
                    ]
                )

    daily: Counter[tuple[str, str, str]] = Counter()
    for tweet in report["tweets"]:
        day = tweet["posted_at"][:10]
        for phrase_key in tweet["phrases"]:
            daily[(day, phrase_key, tweet["account_group"])] += 1
    with (out_dir / "timeseries.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["date", "phrase", *ACCOUNT_GROUP_ORDER, "all_accounts"])
        first_day = datetime.fromisoformat(
            report["scope"]["earliest"].replace("Z", "+00:00")
        ).date()
        last_day = datetime.fromisoformat(
            report["scope"]["latest"].replace("Z", "+00:00")
        ).date()
        days: list[str] = []
        cursor = first_day
        while cursor <= last_day:
            days.append(cursor.isoformat())
            cursor += timedelta(days=1)
        for day in days:
            for phrase in PHRASES:
                counts = [daily[(day, phrase.key, group)] for group in ACCOUNT_GROUP_ORDER]
                writer.writerow([day, phrase.key, *counts, sum(counts)])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH)
    parser.add_argument("--ocr", type=Path, default=OCR_PATH)
    parser.add_argument("--transcripts", type=Path, default=TRANSCRIPTS_PATH)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_report(args.catalog, args.ocr, args.transcripts)
    write_report(report, args.out_dir)
    print(
        json.dumps(
            {
                "matching_tweets": report["unique_matching_tweets"],
                "summary": report["summary"],
                "output": str(args.out_dir),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
