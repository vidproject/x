"""Build an index of quote tweets that target core-account tweets.

The archive captures both tracked-account tweets and the public context that
appears around them. This script joins every archived quote tweet against the
core-account tweet table so reviewers can audit who is quote-tweeting core
accounts without hand-picking a single outside author.

Run with:

    uv run python -m scripts.build_core_quote_index
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
CONFIG_PATH = REPO_ROOT / "config" / "accounts.yaml"
TAGS_DIR = DATA_DIR / "tags"
JSON_OUT = TAGS_DIR / "core_quote_tweets.json"
CSV_OUT = TAGS_DIR / "core_quote_tweets.csv"

SKIP_DATA_FILES = {
    "catalog.parquet",
}


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_accounts(path: Path = CONFIG_PATH) -> list[dict[str, str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    accounts: list[dict[str, str]] = []
    for item in data.get("accounts", []):
        if not isinstance(item, dict) or not item.get("handle"):
            continue
        accounts.append(
            {
                "handle": str(item["handle"]),
                "category": str(item.get("category") or "core"),
                "label": str(item.get("label") or item["handle"]),
            }
        )
    return accounts


def parquet_paths(data_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in data_dir.glob("*.parquet")
        if path.name not in SKIP_DATA_FILES and not path.name.endswith(".retweets.parquet")
    )


def scan_tweets(paths: list[Path]) -> pl.LazyFrame:
    if not paths:
        return pl.LazyFrame(schema={"tweet_id": pl.Utf8})
    return pl.scan_parquet([str(path) for path in paths], missing_columns="insert")


def compact_media(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "media_id": item.get("media_id"),
                "media_type": item.get("media_type"),
                "release_asset_url": item.get("release_asset_url"),
                "sha256": item.get("sha256"),
                "duration_sec": item.get("duration_sec"),
                "alt_text": item.get("alt_text"),
                "archive_status": item.get("archive_status"),
            }
        )
    return out


def category_for(handle: str | None, categories: dict[str, str]) -> str:
    if not handle:
        return "public"
    return categories.get(handle.lower(), "public")


def build_index(data_dir: Path, focus_handles: set[str]) -> dict[str, Any]:
    accounts = load_accounts()
    categories = {a["handle"].lower(): a["category"] for a in accounts}
    core_handles = {a["handle"].lower() for a in accounts if a["category"] == "core"}
    paths = parquet_paths(data_dir)
    tweets = scan_tweets(paths)

    core = (
        tweets.filter(pl.col("account_handle").str.to_lowercase().is_in(core_handles))
        .select(
            [
                pl.col("tweet_id").alias("core_tweet_id"),
                pl.col("account_handle").alias("core_handle"),
                pl.col("posted_at").alias("core_posted_at"),
                pl.col("tweet_url").alias("core_tweet_url"),
                pl.coalesce([pl.col("text_resolved"), pl.col("text")]).alias("core_text"),
                pl.col("media").alias("core_media"),
                pl.col("like_count").alias("core_like_count"),
                pl.col("retweet_count").alias("core_retweet_count"),
                pl.col("reply_count").alias("core_reply_count"),
                pl.col("quote_count").alias("core_quote_count"),
            ]
        )
        .unique("core_tweet_id", keep="first")
    )

    quotes = tweets.filter(
        (pl.col("tweet_type") == "quote")
        & pl.col("quoted_tweet_id").is_not_null()
        & (pl.col("quoted_tweet_id") != "")
    ).select(
        [
            pl.col("tweet_id").alias("quote_tweet_id"),
            pl.col("account_handle").alias("quote_author_handle"),
            pl.col("posted_at").alias("quote_posted_at"),
            pl.col("tweet_url").alias("quote_tweet_url"),
            pl.coalesce([pl.col("text_resolved"), pl.col("text")]).alias("quote_text"),
            pl.col("quoted_tweet_id"),
            pl.col("media").alias("quote_media"),
            pl.col("like_count").alias("quote_like_count"),
            pl.col("retweet_count").alias("quote_retweet_count"),
            pl.col("reply_count").alias("quote_reply_count"),
            pl.col("quote_count").alias("quote_quote_count"),
            pl.col("capture_run_id").alias("quote_capture_run_id"),
        ]
    )

    joined = (
        quotes.join(core, left_on="quoted_tweet_id", right_on="core_tweet_id", how="inner")
        .sort(["quote_author_handle", "quote_posted_at", "quote_tweet_id"])
        .collect()
    )

    rows: list[dict[str, Any]] = []
    for row in joined.iter_rows(named=True):
        quote_author = row.get("quote_author_handle")
        core_handle = row.get("core_handle")
        rows.append(
            {
                "quote_tweet_id": row.get("quote_tweet_id"),
                "quote_author_handle": quote_author,
                "quote_author_category": category_for(quote_author, categories),
                "quote_posted_at": row.get("quote_posted_at"),
                "quote_tweet_url": row.get("quote_tweet_url"),
                "quote_text": row.get("quote_text"),
                "quote_media": compact_media(row.get("quote_media")),
                "quote_engagement": {
                    "likes": row.get("quote_like_count"),
                    "retweets": row.get("quote_retweet_count"),
                    "replies": row.get("quote_reply_count"),
                    "quotes": row.get("quote_quote_count"),
                },
                "quoted_core_tweet_id": row.get("quoted_tweet_id"),
                "core_handle": core_handle,
                "core_category": category_for(core_handle, categories),
                "core_posted_at": row.get("core_posted_at"),
                "core_tweet_url": row.get("core_tweet_url"),
                "core_text": row.get("core_text"),
                "core_media": compact_media(row.get("core_media")),
                "core_engagement": {
                    "likes": row.get("core_like_count"),
                    "retweets": row.get("core_retweet_count"),
                    "replies": row.get("core_reply_count"),
                    "quotes": row.get("core_quote_count"),
                },
                "capture_run_id": row.get("quote_capture_run_id"),
            }
        )

    author_summary: list[dict[str, Any]] = []
    by_author: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_author.setdefault(str(row["quote_author_handle"]).lower(), []).append(row)

    for author_key, author_rows in by_author.items():
        handles_quoted = sorted({str(r["core_handle"]) for r in author_rows if r.get("core_handle")})
        dates = sorted(str(r["quote_posted_at"]) for r in author_rows if r.get("quote_posted_at"))
        sample = author_rows[:5]
        author_summary.append(
            {
                "quote_author_handle": author_rows[0]["quote_author_handle"],
                "quote_author_category": author_rows[0]["quote_author_category"],
                "quote_count": len(author_rows),
                "core_handles_quoted": handles_quoted,
                "first_quote_posted_at": dates[0] if dates else None,
                "last_quote_posted_at": dates[-1] if dates else None,
                "focus_handle": author_key in focus_handles,
                "sample_quote_tweet_ids": [r["quote_tweet_id"] for r in sample],
            }
        )
    author_summary.sort(
        key=lambda r: (
            -int(r["quote_count"]),
            str(r["quote_author_handle"]).lower(),
        )
    )

    seen_authors = {str(row["quote_author_handle"]).lower() for row in author_summary}
    for handle in sorted(focus_handles - seen_authors):
        author_summary.append(
            {
                "quote_author_handle": handle,
                "quote_author_category": category_for(handle, categories),
                "quote_count": 0,
                "core_handles_quoted": [],
                "first_quote_posted_at": None,
                "last_quote_posted_at": None,
                "focus_handle": True,
                "sample_quote_tweet_ids": [],
            }
        )

    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "detector": "scripts.build_core_quote_index",
        "detector_version": "1",
        "data_dir": str(data_dir.relative_to(REPO_ROOT)),
        "core_handles": sorted(core_handles),
        "focus_handles": sorted(focus_handles),
        "stats": {
            "quote_rows": len(rows),
            "quote_authors": len(by_author),
            "core_tweets_quoted": len({r["quoted_core_tweet_id"] for r in rows}),
        },
        "authors": author_summary,
        "rows": rows,
    }


def write_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def csv_cell(value: Any) -> str:
    if value is None:
        return ""
    normalized = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in normalized.split("\n"))


def write_csv(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "quote_author_handle",
        "quote_author_category",
        "quote_posted_at",
        "quote_tweet_id",
        "quote_tweet_url",
        "quote_text",
        "core_handle",
        "core_posted_at",
        "quoted_core_tweet_id",
        "core_tweet_url",
        "core_text",
        "core_media_ids",
        "core_media_types",
        "core_release_asset_urls",
        "core_quote_count",
    ]
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in payload["rows"]:
            media = row.get("core_media") or []
            writer.writerow(
                {
                    "quote_author_handle": row.get("quote_author_handle"),
                    "quote_author_category": row.get("quote_author_category"),
                    "quote_posted_at": row.get("quote_posted_at"),
                    "quote_tweet_id": row.get("quote_tweet_id"),
                    "quote_tweet_url": row.get("quote_tweet_url"),
                    "quote_text": csv_cell(row.get("quote_text")),
                    "core_handle": row.get("core_handle"),
                    "core_posted_at": row.get("core_posted_at"),
                    "quoted_core_tweet_id": row.get("quoted_core_tweet_id"),
                    "core_tweet_url": row.get("core_tweet_url"),
                    "core_text": csv_cell(row.get("core_text")),
                    "core_media_ids": " ".join(str(m.get("media_id") or "") for m in media),
                    "core_media_types": " ".join(str(m.get("media_type") or "") for m in media),
                    "core_release_asset_urls": " ".join(
                        str(m.get("release_asset_url") or "") for m in media
                    ),
                    "core_quote_count": (row.get("core_engagement") or {}).get("quotes"),
                }
            )
    os.replace(tmp, path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--out-json", type=Path, default=JSON_OUT)
    parser.add_argument("--out-csv", type=Path, default=CSV_OUT)
    parser.add_argument(
        "--focus-handle",
        action="append",
        default=[],
        help="Handle to mark in the author summary even if no rows are present.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    focus_handles = {str(h).strip().lstrip("@").lower() for h in args.focus_handle if str(h).strip()}
    payload = build_index(args.data_dir, focus_handles)
    write_json(payload, args.out_json)
    write_csv(payload, args.out_csv)
    print(
        json.dumps(
            {
                "json": str(args.out_json),
                "csv": str(args.out_csv),
                **payload["stats"],
                "focus_handles": payload["focus_handles"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
