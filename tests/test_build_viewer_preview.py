from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from scripts import ingest
from scripts._schema import LEXICAL_TAG_SCHEMA
from scripts.build_viewer_preview import canonical_parquet_paths, write_catalog, write_previews
from tests.conftest import make_tweet


def test_write_previews_slices_newest_rows(tmp_repo: Path) -> None:
    rows = [
        make_tweet("1", posted_at="2025-04-10T00:00:00Z", text="old"),
        make_tweet("2", posted_at="2025-04-11T00:00:00Z", text="middle"),
        make_tweet("3", posted_at="2025-04-12T00:00:00Z", text="new"),
    ]
    ingest.build_dataframe(rows).write_parquet(tmp_repo / "data" / "test-handle.parquet")

    write_previews(tmp_repo / "data", limits=(2, 3), generated_at="2026-05-22T00:00:00Z")

    small = json.loads((tmp_repo / "data" / "preview-2.json").read_text(encoding="utf-8"))
    large = json.loads((tmp_repo / "data" / "preview-3.json").read_text(encoding="utf-8"))
    assert small["generated_at"] == "2026-05-22T00:00:00Z"
    assert small["row_count"] == 2
    assert [row["tweet_id"] for row in small["rows"]] == ["3", "2"]
    assert [row["tweet_id"] for row in large["rows"]] == ["3", "2", "1"]


def test_canonical_parquet_paths_excludes_generated_catalog(tmp_repo: Path) -> None:
    rows = [make_tweet("1")]
    ingest.build_dataframe(rows).write_parquet(tmp_repo / "data" / "test-handle.parquet")
    ingest.build_dataframe(rows).write_parquet(tmp_repo / "data" / "catalog.parquet")

    assert [path.name for path in canonical_parquet_paths(tmp_repo / "data")] == [
        "test-handle.parquet"
    ]


def test_write_previews_includes_matching_tag_slices(tmp_repo: Path) -> None:
    rows = [
        make_tweet("1", posted_at="2025-04-10T00:00:00Z"),
        make_tweet("2", posted_at="2025-04-12T00:00:00Z"),
    ]
    ingest.build_dataframe(rows).write_parquet(tmp_repo / "data" / "test-handle.parquet")
    tags_dir = tmp_repo / "data" / "tags"
    tags_dir.mkdir()
    pl.DataFrame(
        [
            {
                "tweet_id": "2",
                "account_handle": "test-handle",
                "tagger_version": "test",
                "tagged_at": "2026-05-22T00:00:00Z",
                "tags": [
                    {
                        "tag": "topic:immigration",
                        "tentative": False,
                        "source": "auto",
                        "span_start": None,
                        "span_end": None,
                    }
                ],
            }
        ],
        schema=LEXICAL_TAG_SCHEMA,
    ).write_parquet(tags_dir / "lexical.parquet")

    write_previews(tmp_repo / "data", limits=(1,), generated_at="2026-05-22T00:00:00Z")

    payload = json.loads((tmp_repo / "data" / "preview-1.json").read_text(encoding="utf-8"))
    assert payload["row_count"] == 1
    assert payload["rows"][0]["tweet_id"] == "2"
    assert payload["tags"]["2"][0]["tag"] == "topic:immigration"


def test_write_catalog_covers_all_rows_with_locators_and_tags(tmp_repo: Path) -> None:
    rows = [
        make_tweet("1", posted_at="2025-04-10T00:00:00Z", text="old"),
        make_tweet("2", posted_at="2025-04-12T00:00:00Z", text="new"),
    ]
    ingest.build_dataframe(rows).sort("posted_at", descending=True).write_parquet(
        tmp_repo / "data" / "test-handle.parquet"
    )
    tags_dir = tmp_repo / "data" / "tags"
    tags_dir.mkdir()
    pl.DataFrame(
        [
            {
                "tweet_id": "1",
                "account_handle": "test-handle",
                "tagger_version": "test",
                "tagged_at": "2026-05-22T00:00:00Z",
                "tags": [
                    {
                        "tag": "crime:homicide",
                        "tentative": False,
                        "source": "auto",
                        "span_start": None,
                        "span_end": None,
                    }
                ],
            }
        ],
        schema=LEXICAL_TAG_SCHEMA,
    ).write_parquet(tags_dir / "lexical.parquet")

    write_catalog(tmp_repo / "data", generated_at="2026-05-22T00:00:00Z")

    payload = json.loads((tmp_repo / "data" / "catalog.json").read_text(encoding="utf-8"))
    catalog_rows = pl.read_parquet(tmp_repo / "data" / "catalog.parquet").to_dicts()
    assert payload["generated_at"] == "2026-05-22T00:00:00Z"
    assert payload["row_count"] == 2
    assert payload["date_range"] == {"start": "2025-04-10", "end": "2025-04-12"}
    assert payload["parquet"] == "data/catalog.parquet"
    assert "rows" not in payload
    assert [row["tweet_id"] for row in catalog_rows] == ["2", "1"]
    assert catalog_rows[0]["__catalog"] == {
        "handle": "test-handle",
        "parquet": "data/test-handle.parquet",
        "row_index": 0,
    }
    assert catalog_rows[1]["tags"][0]["tag"] == "crime:homicide"
