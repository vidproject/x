"""Helpers for deciding which `_misc` rows are in archival scope."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
ACCOUNT_CATEGORIES_PATH = DATA_DIR / "account_categories.json"
MISC_HANDLE = "_misc"

GOVERNMENT_MISC_CATEGORIES = frozenset({"core", "government", "officials"})


def load_account_categories(path: Path | None = None) -> dict[str, str]:
    path = path or ACCOUNT_CATEGORIES_PATH
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    categories = data.get("categories")
    if not isinstance(categories, dict):
        return {}
    out: dict[str, str] = {}
    for handle, meta in categories.items():
        if not isinstance(meta, dict):
            continue
        category = str(meta.get("category") or "").strip()
        if category:
            out[str(handle).lstrip("@")] = category
    return out


def is_government_or_official_category(category: str) -> bool:
    return category in GOVERNMENT_MISC_CATEGORIES


def account_handle_is_government_or_official(
    handle: str, categories: dict[str, str] | None = None
) -> bool:
    categories = categories if categories is not None else load_account_categories()
    return is_government_or_official_category(categories.get(str(handle).lstrip("@"), ""))


def misc_row_is_government_or_official(
    row: dict[str, Any], categories: dict[str, str] | None = None
) -> bool:
    handle = str(row.get("account_handle") or "").lstrip("@")
    return account_handle_is_government_or_official(handle, categories)


def row_is_in_media_scope(
    row: dict[str, Any],
    *,
    handle: str,
    categories: dict[str, str] | None = None,
) -> bool:
    if handle != MISC_HANDLE:
        return True
    return misc_row_is_government_or_official(row, categories)


def dump_misc_government_tweet_ids(
    *,
    data_dir: Path = DATA_DIR,
    categories_path: Path = ACCOUNT_CATEGORIES_PATH,
    out_path: Path,
) -> int:
    categories = load_account_categories(categories_path)
    parquet_path = data_dir / f"{MISC_HANDLE}.parquet"
    tweet_ids: list[str] = []
    if parquet_path.exists():
        df = pl.read_parquet(parquet_path, columns=["tweet_id", "account_handle"])
        for row in df.iter_rows(named=True):
            if misc_row_is_government_or_official(row, categories):
                tweet_id = str(row.get("tweet_id") or "").strip()
                if tweet_id:
                    tweet_ids.append(tweet_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "".join(f"{tweet_id}\n" for tweet_id in sorted(set(tweet_ids))),
        encoding="utf-8",
    )
    return len(set(tweet_ids))
