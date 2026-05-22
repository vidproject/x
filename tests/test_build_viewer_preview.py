from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from scripts import ingest
from scripts._schema import LEXICAL_TAG_SCHEMA
from scripts.build_viewer_preview import write_previews
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
