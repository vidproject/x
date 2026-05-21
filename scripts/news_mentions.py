"""Detect core tweets cited by news coverage.

The default path is deliberately cheap and reproducible: provide a JSON, JSONL,
or CSV file containing news articles, and the script looks for exact X/Twitter
status URLs for archived core tweets. For ad-hoc discovery, optional web modes
query free public news indexes for exact status URL strings and record returned
article metadata with lower confidence.

Run with:

    uv run python -m scripts.news_mentions --articles data/news/articles.jsonl
    uv run python -m scripts.news_mentions --discover-web google-news-rss --max-web-tweets 100
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

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
DETECTOR_VERSION = "news-mentions-v2"
GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
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

type NewsSearchFn = Any


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


def status_url_terms(tweet: dict[str, Any]) -> list[str]:
    """Return exact URL strings worth searching for this tweet."""
    tweet_id = str(tweet.get("tweet_id") or "").strip()
    handle = str(tweet.get("account_handle") or "").strip()
    if not tweet_id or not handle:
        return []
    return [
        f"https://x.com/{handle}/status/{tweet_id}",
        f"https://twitter.com/{handle}/status/{tweet_id}",
        f"https://x.com/i/web/status/{tweet_id}",
        f"https://twitter.com/i/web/status/{tweet_id}",
    ]


def gdelt_query_for_tweet(tweet: dict[str, Any]) -> str:
    # GDELT accepts quoted phrases and OR; drop the scheme so http/https and
    # embed-normalized URLs still have a chance to match.
    terms = [term.replace("https://", "") for term in status_url_terms(tweet)]
    return " OR ".join(f'"{term}"' for term in terms)


def gdelt_search(
    query: str,
    *,
    max_records: int,
    timeout_sec: float,
) -> list[dict[str, Any]]:
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(max(1, min(max_records, 250))),
        "sort": "datedesc",
    }
    url = f"{GDELT_DOC_API}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": "imm-archive-news-mentions/1.0"})
    with urlopen(request, timeout=timeout_sec) as response:  # nosec B310 - user-requested public API.
        payload = json.loads(response.read().decode("utf-8"))
    articles = payload.get("articles") if isinstance(payload, dict) else None
    return [item for item in articles or [] if isinstance(item, dict)]


def google_news_rss_search(
    query: str,
    *,
    max_records: int,
    timeout_sec: float,
) -> list[dict[str, Any]]:
    params = {
        "q": query,
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    }
    url = f"{GOOGLE_NEWS_RSS}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": "imm-archive-news-mentions/1.0"})
    with urlopen(request, timeout=timeout_sec) as response:  # nosec B310 - user-requested public RSS.
        root = ElementTree.fromstring(response.read())
    out: list[dict[str, Any]] = []
    for item in root.findall("./channel/item"):
        source = item.find("source")
        out.append(
            {
                "source": source.text if source is not None else "",
                "title": item.findtext("title") or "",
                "url": item.findtext("link") or "",
                "published_at": item.findtext("pubDate") or "",
            }
        )
        if len(out) >= max_records:
            break
    return out


def mention_for_search_article(
    tweet: dict[str, Any],
    article: dict[str, Any],
    *,
    matched_terms: list[str],
    matched_field: str,
) -> dict[str, Any] | None:
    url = string_field(article, "url")
    title = string_field(article, "title")
    if not url and not title:
        return None
    source = (
        string_field(article, "sourceCommonName")
        or string_field(article, "domain")
        or string_field(article, "source")
    )
    published_at = (
        string_field(article, "seendate")
        or string_field(article, "published_at")
        or string_field(article, "published")
        or string_field(article, "date")
    )
    return {
        "source": source or None,
        "title": title or None,
        "url": url or None,
        "published_at": published_at or None,
        "matched_fields": [matched_field],
        "matched_terms": matched_terms,
        "confidence": 0.75,
    }


def discover_web_mentions_for_tweet(
    tweet: dict[str, Any],
    *,
    provider: str,
    searcher: NewsSearchFn | None = None,
    max_records: int,
    timeout_sec: float,
) -> tuple[list[dict[str, Any]], str | None]:
    if provider == "none":
        return [], None
    if provider not in {"gdelt", "google-news-rss"}:
        return [], f"unsupported news discovery provider: {provider}"
    query = gdelt_query_for_tweet(tweet)
    if not query:
        return [], None
    terms = status_url_terms(tweet)
    if provider == "google-news-rss":
        search = searcher or google_news_rss_search
        matched_field = "google-news-rss-query"
    else:
        search = searcher or gdelt_search
        matched_field = "gdelt-query"
    try:
        articles = search(query, max_records=max_records, timeout_sec=timeout_sec)
    except (OSError, TimeoutError, URLError, json.JSONDecodeError, ElementTree.ParseError) as exc:
        return [], str(exc)
    mentions = [
        mention
        for article in articles
        if (
            mention := mention_for_search_article(
                tweet,
                article,
                matched_terms=terms,
                matched_field=matched_field,
            )
        )
        is not None
    ]
    return dedupe_mentions(mentions), None


def dedupe_mentions(mentions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for mention in mentions:
        key = (
            str(mention.get("url") or ""),
            str(mention.get("title") or ""),
            str(mention.get("published_at") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(mention)
    return out


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
    extra_mentions: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    local_mentions = [
        mention
        for article in articles
        if (mention := mention_for_article(tweet, article)) is not None
    ]
    mentions = dedupe_mentions([*local_mentions, *(extra_mentions or [])])
    tags = [tag_entry("news:mentioned"), tag_entry("news:covered")] if mentions else []
    status = "mentioned" if mentions else ("error" if error else "no-match")
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
        "error": error,
    }


def build_rows(
    parquets: list[Path],
    articles: list[dict[str, Any]],
    *,
    core_handles: set[str],
    generated_at: str,
    matched_only: bool,
    discover_web: str = "none",
    max_web_tweets: int = 0,
    web_max_records: int = 5,
    web_timeout_sec: float = 12.0,
    web_delay_sec: float = 0.25,
    web_searcher: NewsSearchFn | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    stats = Counter[str]()
    corpus_hash = article_corpus_hash(articles)
    for tweet in iter_core_tweets(parquets, core_handles):
        stats["core_tweets_scanned"] += 1
        web_mentions: list[dict[str, Any]] = []
        web_error: str | None = None
        if discover_web != "none" and stats["web_tweets_scanned"] < max_web_tweets:
            stats["web_tweets_scanned"] += 1
            web_mentions, web_error = discover_web_mentions_for_tweet(
                tweet,
                provider=discover_web,
                searcher=web_searcher,
                max_records=web_max_records,
                timeout_sec=web_timeout_sec,
            )
            stats["web_article_mentions"] += len(web_mentions)
            if web_error:
                stats["web_errors"] += 1
                LOG.warning(
                    "news discovery failed",
                    tweet_id=str(tweet.get("tweet_id") or ""),
                    provider=discover_web,
                    error=web_error,
                )
            if web_delay_sec > 0:
                time.sleep(web_delay_sec)
        row = build_row(
            tweet,
            articles,
            generated_at=generated_at,
            corpus_hash=corpus_hash,
            extra_mentions=web_mentions,
            error=web_error,
        )
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
    path.parent.mkdir(parents=True, exist_ok=True)
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
    parser.add_argument(
        "--discover-web",
        choices=("none", "gdelt", "google-news-rss"),
        default="none",
        help="Optionally query a free news index for exact status URL strings.",
    )
    parser.add_argument(
        "--max-web-tweets",
        type=int,
        default=100,
        help="Maximum core tweets to query through --discover-web.",
    )
    parser.add_argument(
        "--web-max-records",
        type=int,
        default=5,
        help="Maximum news-index articles to keep per tweet query.",
    )
    parser.add_argument(
        "--web-timeout-sec",
        type=float,
        default=12.0,
        help="Timeout for each news-index request.",
    )
    parser.add_argument(
        "--web-delay-sec",
        type=float,
        default=1.0,
        help="Delay between news-index requests.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    should_scan = args.articles.exists() or args.discover_web != "none"
    if should_scan:
        core_handles = load_core_handles()
        articles = load_articles(args.articles) if args.articles.exists() else []
        rows, stats = build_rows(
            discover_canonical_parquets(),
            articles,
            core_handles=core_handles,
            generated_at=generated_at,
            matched_only=bool(args.matched_only),
            discover_web=str(args.discover_web),
            max_web_tweets=max(0, int(args.max_web_tweets)),
            web_max_records=max(1, int(args.web_max_records)),
            web_timeout_sec=max(1.0, float(args.web_timeout_sec)),
            web_delay_sec=max(0.0, float(args.web_delay_sec)),
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
