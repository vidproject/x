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
    assert mention["matched_fields"] == ["body"]
    assert len(mention["matched_terms"]) == 2


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
            articles_path,
        )
    finally:
        news_mentions.MANIFEST_PATH = old_manifest

    df = pl.read_parquet(out_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert df.height == 1
    assert df.row(0, named=True)["status"] == "mentioned"
    assert manifest["layers"]["news_mentions"]["article_count"] == 1
    assert manifest["layers"]["news_mentions"]["tag_frequency"]["news:mentioned"] == 1
