"""Detect core tweets cited by news coverage from a local article export.

This is deliberately cheap and reproducible: it does not call a paid API, and
it does not need network access. Provide a JSON, JSONL, or CSV file containing
news articles; the script looks for exact X/Twitter status URLs for archived
core tweets and writes ``data/tags/news_mentions.parquet``.

Run with:

    uv run python -m scripts.news_mentions --articles data/news/articles.jsonl
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from scripts._logging import configure
from scripts._schema import NEWS_MENTIONS_SCHEMA, empty_news_mentions_dataframe

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"
CONFIG_PATH = REPO_ROOT / "config" / "accounts.yaml"
DEFAULT_ARTICLES_PATH = DATA_DIR / "news" / "articles.jsonl"
OUT_PATH = TAGS_DIR / "news_mentions.parquet"
MANIFEST_PATH = TAGS_DIR / "manifest.json"

DETECTOR = "exact-status-url"
DETECTOR_VERSION = "news-mentions-v1"
ARTICLE_TEXT_FIELDS = (
    "url",
    "canonical_url",
    "title",
    "description",
    "summary",
    "body",
    "content",
    "text",
)


def discover_canonical_parquets() -> list[Path]:
    return sorted(p for p in DATA_DIR.glob("*.parquet") if p.is_file())


def load_core_handles(path: Path = CONFIG_PATH) -> set[str]:
    if not path.exists():
        return set()
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: set[str] = set()
    for item in payload.get("accounts", []):
        if not isinstance(item, dict):
            continue
        if item.get("category") == "core" and item.get("handle"):
            out.add(str(item["handle"]))
    return out


def iter_core_tweets(parquets: Iterable[Path], core_handles: set[str]) -> Iterator[dict[str, Any]]:
    for path in parquets:
        handle = path.stem
        if handle not in core_handles:
            continue
        try:
            df = pl.read_parquet(path)
        except Exception:
            LOG.exception("news mentions: could not read parquet", path=str(path))
            continue
        for row in df.iter_rows(named=True):
            tweet_id = str(row.get("tweet_id") or "")
            account_handle = str(row.get("account_handle") or handle)
            if tweet_id and account_handle in core_handles:
                yield row


def load_articles(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return list(iter_jsonl(path))
    if suffix == ".json":
        return list(iter_json(path))
    if suffix == ".csv":
        return list(iter_csv(path))
    raise ValueError(f"unsupported article file extension: {path.suffix}")


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_no}: expected JSON object")
            yield value


def iter_json(path: Path) -> Iterator[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("articles"), list):
        items = payload["articles"]
    elif isinstance(payload, dict):
        items = [payload]
    else:
        raise ValueError(f"{path}: expected object, array, or object with articles[]")
    for item in items:
        if isinstance(item, dict):
            yield item


def iter_csv(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        yield from reader


def article_identity(article: dict[str, Any]) -> tuple[str, str, str, str]:
    source = string_field(article, "source") or string_field(article, "publisher")
    title = string_field(article, "title")
    url = string_field(article, "url") or string_field(article, "canonical_url")
    published_at = (
        string_field(article, "published_at")
        or string_field(article, "published")
        or string_field(article, "date")
    )
    return source, title, url, published_at


def article_fields(article: dict[str, Any]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key in ARTICLE_TEXT_FIELDS:
        value = string_field(article, key)
        if value:
            fields[key] = value
    return fields


def string_field(article: dict[str, Any], key: str) -> str:
    value = article.get(key)
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def mention_for_article(tweet: dict[str, Any], article: dict[str, Any]) -> dict[str, Any] | None:
    tweet_id = str(tweet.get("tweet_id") or "")
    handle = str(tweet.get("account_handle") or "")
    if not tweet_id or not handle:
        return None
    fields = article_fields(article)
    if not fields:
        return None

    matched_fields: set[str] = set()
    matched_terms: set[str] = set()
    url_re = status_url_regex(tweet_id, handle)
    for field, text in fields.items():
        for match in url_re.finditer(text):
            matched_fields.add(field)
            matched_terms.add(normalize_url_term(match.group(0)))
    if not matched_terms:
        return None

    source, title, url, published_at = article_identity(article)
    return {
        "source": source or None,
        "title": title or None,
        "url": url or None,
        "published_at": published_at or None,
        "matched_fields": sorted(matched_fields),
        "matched_terms": sorted(matched_terms),
        "confidence": 1.0,
    }


def status_url_regex(tweet_id: str, handle: str) -> re.Pattern[str]:
    handle_part = re.escape(handle)
    tweet_id_part = re.escape(tweet_id)
    return re.compile(
        rf"""
        (?:
          https?://
          (?:
            (?:www\.|mobile\.)?(?:x|twitter)\.com
            /
            (?:
              {handle_part}
              |
              i/web
            )
            /status(?:es)?/
            {tweet_id_part}
          )
        )
        (?:[/?#][^\s<>"')\]]*)?
        """,
        re.IGNORECASE | re.VERBOSE,
    )


def normalize_url_term(value: str) -> str:
    return value.rstrip(".,;:!?)\"'").replace("http://", "https://")


def tag_entry(tag: str, *, source: str = "news-mentions") -> dict[str, Any]:
    return {
        "tag": tag,
        "tentative": None,
        "source": source,
        "span_start": None,
        "span_end": None,
    }


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def article_corpus_hash(articles: list[dict[str, Any]]) -> str:
    payload = [
        {
            "identity": article_identity(article),
            "fields": article_fields(article),
        }
        for article in articles
    ]
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def input_hash_for(
    tweet: dict[str, Any],
    mentions: list[dict[str, Any]],
    *,
    corpus_hash: str,
) -> str:
    payload = {
        "tweet_id": str(tweet.get("tweet_id") or ""),
        "account_handle": str(tweet.get("account_handle") or ""),
        "tweet_url": str(tweet.get("tweet_url") or ""),
        "posted_at": str(tweet.get("posted_at") or ""),
        "detector_version": DETECTOR_VERSION,
        "article_corpus_hash": corpus_hash,
        "mentions": mentions,
    }
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def build_row(
    tweet: dict[str, Any],
    articles: list[dict[str, Any]],
    *,
    generated_at: str,
    corpus_hash: str,
) -> dict[str, Any]:
    mentions = [
        mention
        for article in articles
        if (mention := mention_for_article(tweet, article)) is not None
    ]
    tags = [tag_entry("news:mentioned"), tag_entry("news:covered")] if mentions else []
    status = "mentioned" if mentions else "no-match"
    return {
        "tweet_id": str(tweet.get("tweet_id") or ""),
        "account_handle": str(tweet.get("account_handle") or ""),
        "tweet_url": str(tweet.get("tweet_url") or ""),
        "posted_at": str(tweet.get("posted_at") or ""),
        "input_hash": input_hash_for(tweet, mentions, corpus_hash=corpus_hash),
        "generated_at": generated_at,
        "detector": DETECTOR,
        "detector_version": DETECTOR_VERSION,
        "mention_count": len(mentions),
        "articles": mentions,
        "status": status,
        "tags": tags,
        "cost_estimate_usd": 0.0,
        "error": None,
    }


def build_rows(
    parquets: list[Path],
    articles: list[dict[str, Any]],
    *,
    core_handles: set[str],
    generated_at: str,
    matched_only: bool,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    stats = Counter[str]()
    corpus_hash = article_corpus_hash(articles)
    for tweet in iter_core_tweets(parquets, core_handles):
        stats["core_tweets_scanned"] += 1
        row = build_row(tweet, articles, generated_at=generated_at, corpus_hash=corpus_hash)
        if row["mention_count"]:
            stats["mentioned_tweets"] += 1
            stats["article_mentions"] += int(row["mention_count"])
        if matched_only and not row["mention_count"]:
            continue
        rows.append(row)
    stats["rows"] = len(rows)
    stats["article_count"] = len(articles)
    return rows, dict(stats)


def write_parquet(rows: list[dict[str, Any]], path: Path) -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    df = (
        pl.DataFrame(rows, schema=NEWS_MENTIONS_SCHEMA, strict=False)
        if rows
        else empty_news_mentions_dataframe()
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.write_parquet(tmp, compression="zstd")
    os.replace(tmp, path)


def update_manifest(
    rows: list[dict[str, Any]],
    stats: dict[str, int],
    generated_at: str,
    articles_path: Path,
) -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    layers = manifest.get("layers")
    if not isinstance(layers, dict):
        layers = {}
    tag_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    for row in rows:
        status_counts[str(row.get("status") or "")] += 1
        for entry in row.get("tags") or []:
            if isinstance(entry, dict) and entry.get("tag"):
                tag_counts[str(entry["tag"])] += 1
    layers["news_mentions"] = {
        "generated_at": generated_at,
        "detector": DETECTOR,
        "detector_version": DETECTOR_VERSION,
        "articles_path": str(articles_path),
        "row_count": len(rows),
        "cost_estimate_usd": 0.0,
        "status_counts": dict(sorted(status_counts.items())),
        "tag_frequency": dict(sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        **stats,
    }
    manifest["layers"] = layers
    tmp = MANIFEST_PATH.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, MANIFEST_PATH)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--articles",
        type=Path,
        default=DEFAULT_ARTICLES_PATH,
        help="Local JSON, JSONL, or CSV news article export to scan.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=OUT_PATH,
        help="Output parquet path.",
    )
    parser.add_argument(
        "--matched-only",
        action="store_true",
        help="Write only tweets with one or more news mentions.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if args.articles.exists():
        core_handles = load_core_handles()
        articles = load_articles(args.articles)
        rows, stats = build_rows(
            discover_canonical_parquets(),
            articles,
            core_handles=core_handles,
            generated_at=generated_at,
            matched_only=bool(args.matched_only),
        )
    else:
        articles = []
        rows = []
        stats = {"article_count": 0, "rows": 0, "missing_article_export": 1}
    write_parquet(rows, args.out)
    if args.out == OUT_PATH:
        update_manifest(rows, stats, generated_at, args.articles)
    LOG.info(
        "news mentions complete",
        rows=len(rows),
        articles=len(articles),
        mentioned=stats.get("mentioned_tweets", 0),
        out=str(args.out),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
