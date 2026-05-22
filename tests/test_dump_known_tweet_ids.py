"""Unit tests for the known-tweet-ID dump helper."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from scripts.dump_known_tweet_ids import known_tweet_ids, main


def _catalog() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "tweet_id": ["300", "100", "200", "100", "400"],
            "account_handle": ["DHSgov", "DHSgov", "ICEgov", "DHSgov", "ICEgov"],
        }
    )


def test_sorted_unique_ids() -> None:
    assert known_tweet_ids(_catalog()) == ["100", "200", "300", "400"]


def test_handle_filter() -> None:
    assert known_tweet_ids(_catalog(), "DHSgov") == ["100", "300"]
    assert known_tweet_ids(_catalog(), "ICEgov") == ["200", "400"]


def test_unknown_handle_is_empty() -> None:
    assert known_tweet_ids(_catalog(), "NoSuchHandle") == []


def test_empty_catalog() -> None:
    assert known_tweet_ids(pl.DataFrame()) == []


def test_missing_tweet_id_column() -> None:
    assert known_tweet_ids(pl.DataFrame({"other": ["x"]})) == []


def test_handle_filter_without_handle_column() -> None:
    df = pl.DataFrame({"tweet_id": ["1", "2"]})
    assert known_tweet_ids(df) == ["1", "2"]
    assert known_tweet_ids(df, "DHSgov") == []


def test_drops_null_and_blank_ids() -> None:
    df = pl.DataFrame({"tweet_id": ["1", None, "", "2"]})
    assert known_tweet_ids(df) == ["1", "2"]


def test_main_writes_file(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.parquet"
    _catalog().write_parquet(catalog_path)
    out_path = tmp_path / "known" / "DHSgov.txt"

    rc = main(["--handle", "DHSgov", "--catalog", str(catalog_path), "--out", str(out_path)])

    assert rc == 0
    assert out_path.read_text(encoding="utf-8") == "100\n300\n"


def test_main_stdout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    catalog_path = tmp_path / "catalog.parquet"
    _catalog().write_parquet(catalog_path)

    rc = main(["--catalog", str(catalog_path)])

    assert rc == 0
    assert capsys.readouterr().out == "100\n200\n300\n400\n"


def test_main_missing_catalog(tmp_path: Path) -> None:
    rc = main(["--catalog", str(tmp_path / "nope.parquet")])
    assert rc == 1
