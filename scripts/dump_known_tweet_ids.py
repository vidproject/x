"""Dump already-archived tweet IDs for incremental skimming.

The browser skimmer (``tools/x-skim.mjs --known-ids-file``) can stop scrolling a
timeline once it reaches tweets already in the archive, instead of re-fetching
the whole timeline on every run. This script produces that newline-delimited ID
list from the canonical catalog, optionally filtered to one account so a
per-handle refresh skim only needs that account's backlog.

Logs go to stderr, so the stdout form is safe to pipe straight into a file.

Run with::

    uv run python -m scripts.dump_known_tweet_ids --handle DHSgov --out .skim/known/DHSgov.txt
    uv run python -m scripts.dump_known_tweet_ids > .skim/known/all.txt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

from scripts._logging import configure

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
CATALOG_PATH = DATA_DIR / "catalog.parquet"


def known_tweet_ids(catalog: pl.DataFrame, handle: str | None = None) -> list[str]:
    """Return sorted, unique tweet IDs from the catalog, optionally per-handle."""
    if catalog.is_empty() or "tweet_id" not in catalog.columns:
        return []
    frame = catalog
    if handle:
        if "account_handle" not in frame.columns:
            return []
        frame = frame.filter(pl.col("account_handle") == handle)
    ids = (
        frame.select(pl.col("tweet_id").cast(pl.Utf8))
        .drop_nulls()
        .unique()
        .to_series()
        .to_list()
    )
    return sorted(i for i in ids if i)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handle", help="Restrict to one account_handle.")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=CATALOG_PATH,
        help=f"Catalog parquet to read (default {CATALOG_PATH}).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Write IDs here (one per line). Defaults to stdout.",
    )
    args = parser.parse_args(argv)

    if not args.catalog.exists():
        LOG.error("catalog parquet not found", path=str(args.catalog))
        return 1

    catalog = pl.read_parquet(args.catalog)
    ids = known_tweet_ids(catalog, args.handle)
    payload = "".join(f"{i}\n" for i in ids)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
        LOG.info(
            "wrote known tweet IDs",
            count=len(ids),
            out=str(args.out),
            handle=args.handle or "*",
        )
    else:
        sys.stdout.write(payload)

    return 0


if __name__ == "__main__":
    sys.exit(main())
