from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from scripts import news_mentions
from scripts._schema import TWEET_SCHEMA
from tests.conftest import make_tweet


def _write_core_config(path: Path) -> Path:
    config = path / "accounts.yaml"
    config.write_text(
        "accounts:\n"
        "  - handle: DHSgov\n"
        "    label: DHS\n"
        "    category: core\n"
        "  - handle: OtherGov\n"
        "    label: Other\n"
        "    category: government\n",
        encoding="utf-8",
    )
    return config


def _write_parquet(path: Path, rows: list[dict[str, object]]) -> None:
    pl.DataFrame(rows, schema=TWEET_SCHEMA, strict=False).write_parquet(path)


def test_status_url_match_handles_x_and_twitter_variants() -> None:
    tweet = make_tweet("1234567890", handle="DHSgov")
    article = {
        "source": "Example News",
        "title": "DHS post draws coverage",
        "url": "https://example.test/story",
        "published_at": "2026-05-20T10:00:00Z",
        "body": (
            "The story embedded https://twitter.com/DHSgov/status/1234567890 "
            "and later mirrored https://x.com/i/web/status/1234567890?s=20."
        ),
    }

    mention = news_mentions.mention_for_article(tweet, article)

    assert mention is not None
    assert mention["source"] == "Example News"
    assert mention["confidence"] == 1.0
    assert mention["confirmed"] is True
    assert mention["match_type"] == "local-exact-status-url"
    assert mention["matched_fields"] == ["body"]
    assert len(mention["matched_terms"]) == 2


def test_status_url_match_handles_bare_encoded_nested_and_renamed_handles() -> None:
    tweet = make_tweet("1234567890", handle="DHSgov")
    article = {
        "source": {"name": "Wire"},
        "headline": "Embed roundup",
        "links": [
            {"href": ("https%3A%2F%2Ftwitter.com%2FSomeOldHandle%2Fstatuses%2F1234567890%3Fs%3D20")}
        ],
        "body": "Mirror: x.com/DHSgov/status/1234567890.",
    }

    mention = news_mentions.mention_for_article(tweet, article)

    assert mention is not None
    assert mention["source"] == "Wire"
    assert set(mention["matched_fields"]) == {"body", "links[0].href"}
    assert "https://twitter.com/SomeOldHandle/status/1234567890?s=20" in mention["matched_terms"]


def test_load_articles_accepts_directory_and_nested_json_exports(tmp_path: Path) -> None:
    articles_dir = tmp_path / "articles"
    articles_dir.mkdir()
    (articles_dir / "feed.json").write_text(
        json.dumps(
            {
                "response": {
                    "docs": [
                        {
                            "source": "Nested",
                            "title": "One",
                            "body": "https://x.com/DHSgov/status/111",
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    (articles_dir / "more.csv").write_text(
        "\ufeffsource,title,body\nCSV,Two,https://x.com/DHSgov/status/222\n",
        encoding="utf-8",
    )

    articles = news_mentions.load_articles(articles_dir)

    assert [article["title"] for article in articles] == ["One", "Two"]


def test_build_rows_scans_only_core_tweets_from_local_articles(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config_path = _write_core_config(tmp_path)
    core_tweet = make_tweet("1111111111", handle="DHSgov")
    government_tweet = make_tweet("2222222222", handle="OtherGov")
    _write_parquet(data_dir / "DHSgov.parquet", [core_tweet])
    _write_parquet(data_dir / "OtherGov.parquet", [government_tweet])
    articles = [
        {
            "source": "Wire",
            "title": "Coverage",
            "body": "A news article cited https://x.com/DHSgov/status/1111111111.",
        },
        {
            "source": "Wire",
            "title": "Other coverage",
            "body": "This cited https://x.com/OtherGov/status/2222222222.",
        },
    ]

    rows, stats = news_mentions.build_rows(
        sorted(data_dir.glob("*.parquet")),
        articles,
        core_handles=news_mentions.load_core_handles(config_path),
        generated_at="2026-05-20T00:00:00Z",
        matched_only=False,
    )

    assert stats["core_tweets_scanned"] == 1
    assert stats["mentioned_tweets"] == 1
    assert [row["tweet_id"] for row in rows] == ["1111111111"]
    assert rows[0]["mention_count"] == 1
    assert {entry["tag"] for entry in rows[0]["tags"]} == {"news:covered", "news:mentioned"}


def test_jsonl_loader_and_parquet_output_need_no_network(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    tags_dir = data_dir / "tags"
    data_dir.mkdir()
    tags_dir.mkdir()
    articles_path = tmp_path / "articles.jsonl"
    out_path = tags_dir / "news_mentions.parquet"
    manifest_path = tags_dir / "manifest.json"
    articles_path.write_text(
        json.dumps(
            {
                "source": "Local News",
                "title": "Local export",
                "content": "Embedded post: https://x.com/DHSgov/status/3333333333",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _write_parquet(data_dir / "DHSgov.parquet", [make_tweet("3333333333", handle="DHSgov")])

    articles = news_mentions.load_articles(articles_path)
    rows, stats = news_mentions.build_rows(
        [data_dir / "DHSgov.parquet"],
        articles,
        core_handles={"DHSgov"},
        generated_at="2026-05-20T00:00:00Z",
        matched_only=True,
    )
    news_mentions.write_parquet(rows, out_path)
    old_manifest = news_mentions.MANIFEST_PATH
    try:
        news_mentions.MANIFEST_PATH = manifest_path
        news_mentions.update_manifest(
            rows,
            stats,
            "2026-05-20T00:00:00Z",
            [articles_path],
        )
    finally:
        news_mentions.MANIFEST_PATH = old_manifest

    df = pl.read_parquet(out_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert df.height == 1
    assert df.row(0, named=True)["status"] == "mentioned"
    assert manifest["layers"]["news_mentions"]["article_count"] == 1
    assert manifest["layers"]["news_mentions"]["tag_frequency"]["news:mentioned"] == 1


def test_search_result_article_links_are_marked_lower_confidence() -> None:
    tweet = make_tweet("1234567890", handle="DHSgov")
    article = {
        "source": "Search",
        "title": "Search-derived article",
        "url": "https://example.test/story",
        "links": ["https://x.com/DHSgov/status/1234567890"],
        "coverage_basis": "search_result_for_exact_status_url",
        "match_method": "google-exact-status-url-search",
    }

    mention = news_mentions.mention_for_article(tweet, article)

    assert mention is not None
    assert mention["match_type"] == "local-search-result-exact-status-url"
    assert mention["confidence"] == 0.85
    assert mention["confirmed"] is True


def test_gdelt_query_uses_exact_status_url_variants() -> None:
    tweet = make_tweet("4444444444", handle="DHSgov")

    query = news_mentions.gdelt_query_for_tweet(tweet)

    assert '"x.com/DHSgov/status/4444444444"' in query
    assert '"twitter.com/DHSgov/status/4444444444"' in query
    assert '"x.com/i/web/status/4444444444"' in query


def test_gdelt_discovery_can_crosslink_without_local_article_export(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config_path = _write_core_config(tmp_path)
    _write_parquet(data_dir / "DHSgov.parquet", [make_tweet("5555555555", handle="DHSgov")])
    seen_queries: list[str] = []

    def fake_search(query: str, *, max_records: int, timeout_sec: float) -> list[dict[str, object]]:
        seen_queries.append(query)
        assert max_records == 3
        assert timeout_sec == 2.0
        return [
            {
                "sourceCommonName": "Example News",
                "title": "DHS tweet cited",
                "url": "https://example.test/dhs-tweet",
                "seendate": "20260520T120000Z",
            }
        ]

    rows, stats = news_mentions.build_rows(
        [data_dir / "DHSgov.parquet"],
        [],
        core_handles=news_mentions.load_core_handles(config_path),
        generated_at="2026-05-20T00:00:00Z",
        matched_only=True,
        discover_web="gdelt",
        max_web_tweets=1,
        web_max_records=3,
        web_timeout_sec=2.0,
        web_delay_sec=0.0,
        web_searcher=fake_search,
    )

    assert seen_queries
    assert stats["web_tweets_scanned"] == 1
    assert stats["web_article_mentions"] == 1
    assert rows[0]["tweet_id"] == "5555555555"
    assert rows[0]["mention_count"] == 1
    assert rows[0]["articles"][0]["matched_fields"] == ["gdelt-query"]
    assert rows[0]["articles"][0]["confidence"] == 0.85
    assert rows[0]["articles"][0]["confirmed"] is True
    assert {entry["tag"] for entry in rows[0]["tags"]} == {"news:covered", "news:mentioned"}


def test_web_discovery_only_checks_tweets_missing_from_local_articles(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_parquet(
        data_dir / "DHSgov.parquet",
        [
            make_tweet("1111111111", handle="DHSgov"),
            make_tweet("2222222222", handle="DHSgov"),
        ],
    )
    articles = [
        {
            "source": "Local News",
            "title": "Local match",
            "body": "Embedded post: https://x.com/DHSgov/status/1111111111",
        }
    ]
    seen_queries: list[str] = []

    def fake_search(query: str, *, max_records: int, timeout_sec: float) -> list[dict[str, object]]:
        seen_queries.append(query)
        return [
            {
                "source": "RSS News",
                "title": "RSS match",
                "url": "https://example.test/rss-match",
                "published_at": "2026-05-20T12:00:00Z",
            }
        ]

    rows, stats = news_mentions.build_rows(
        [data_dir / "DHSgov.parquet"],
        articles,
        core_handles={"DHSgov"},
        generated_at="2026-05-20T00:00:00Z",
        matched_only=True,
        discover_web="google-news-rss",
        max_web_tweets=10,
        web_delay_sec=0.0,
        web_searcher=fake_search,
    )

    assert len(seen_queries) == 1
    assert "2222222222" in seen_queries[0]
    assert "1111111111" not in seen_queries[0]
    assert stats["local_article_mentions"] == 1
    assert stats["web_tweets_scanned"] == 1
    assert stats["mentioned_tweets"] == 2
    assert [row["tweet_id"] for row in rows] == ["1111111111", "2222222222"]
    assert rows[0]["articles"][0]["match_type"] == "local-exact-status-url"
    assert rows[1]["articles"][0]["matched_fields"] == ["google-news-rss-query"]


def test_query_export_sorts_high_value_core_tweets(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    high = make_tweet("7777777777", handle="DHSgov")
    high["like_count"] = 10
    high["retweet_count"] = 20
    low = make_tweet("6666666666", handle="DHSgov")
    low["like_count"] = 1
    _write_parquet(data_dir / "DHSgov.parquet", [low, high])
    out_path = tmp_path / "queries.csv"

    rows = news_mentions.build_query_export_rows(
        [data_dir / "DHSgov.parquet"],
        core_handles={"DHSgov"},
        limit=1,
    )
    news_mentions.write_query_export(rows, out_path)

    written = out_path.read_text(encoding="utf-8")
    assert rows[0]["tweet_id"] == "7777777777"
    assert '"x.com/DHSgov/status/7777777777"' in rows[0]["exact_url_query"]
    assert "7777777777" in written
