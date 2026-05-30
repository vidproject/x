from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from scripts._misc_scope import (
    dump_misc_government_tweet_ids,
    load_account_categories,
    misc_row_is_government_or_official,
)
from scripts._schema import TWEET_SCHEMA
from tests.conftest import make_tweet


def test_misc_scope_uses_government_official_and_core_categories(tmp_path: Path) -> None:
    categories_path = tmp_path / "account_categories.json"
    categories_path.write_text(
        json.dumps(
            {
                "categories": {
                    "Agency": {"category": "government"},
                    "Official": {"category": "officials"},
                    "Core": {"category": "core"},
                    "Figure": {"category": "public_figures"},
                    "Public": {"category": "public"},
                }
            }
        ),
        encoding="utf-8",
    )
    categories = load_account_categories(categories_path)

    assert misc_row_is_government_or_official({"account_handle": "Agency"}, categories)
    assert misc_row_is_government_or_official({"account_handle": "Official"}, categories)
    assert misc_row_is_government_or_official({"account_handle": "Core"}, categories)
    assert not misc_row_is_government_or_official({"account_handle": "Figure"}, categories)
    assert not misc_row_is_government_or_official({"account_handle": "Public"}, categories)


def test_dump_misc_government_tweet_ids(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    categories_path = data_dir / "account_categories.json"
    categories_path.write_text(
        json.dumps(
            {
                "categories": {
                    "Agency": {"category": "government"},
                    "Official": {"category": "officials"},
                    "Public": {"category": "public"},
                }
            }
        ),
        encoding="utf-8",
    )
    rows = [
        make_tweet("1", handle="Agency"),
        make_tweet("2", handle="Official"),
        make_tweet("3", handle="Public"),
    ]
    pl.DataFrame(rows, schema=TWEET_SCHEMA, strict=False).write_parquet(data_dir / "_misc.parquet")
    out_path = data_dir / "tags" / "_misc_government_tweet_ids.txt"

    count = dump_misc_government_tweet_ids(
        data_dir=data_dir, categories_path=categories_path, out_path=out_path
    )

    assert count == 2
    assert out_path.read_text(encoding="utf-8").splitlines() == ["1", "2"]
