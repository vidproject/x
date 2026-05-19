"""Mark tweets that have disappeared from recent timeline scans.

A tweet is considered "live" if its ID appears in the union of the last
``WINDOW_RUNS`` (default 3) seen-set files for its account. If a tweet was
previously live but is absent from every one of those runs *and* it has been
at least ``GRACE_DAYS`` (default 7) since the last run that saw it, we set
``deletion_detected_at`` to the timestamp of the first run we missed it.

The check is intentionally conservative: small seen-sets (under
``MIN_SCAN_SIZE`` ids) are treated as partial scans and ignored, so a brief
manual visit to an account page doesn't trigger false positives.

This script is deliberately silent in the UI by design — deletion is a
secondary signal exposed only via a togglable column in the viewer.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from scripts._logging import configure

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
SEEN_DIR = REPO_ROOT / "seen"
DATA_DIR = REPO_ROOT / "data"

WINDOW_RUNS = 3
GRACE_DAYS = 7
MIN_SCAN_SIZE = 50  # below this, treat the run as partial and ignore


def iter_seen(handle: str) -> Iterator[Path]:
    d = SEEN_DIR / handle
    if not d.exists():
        return
    yield from sorted(d.glob("*.json"))


def parse_seen(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        LOG.warning("seen file not parseable", path=str(path), error=str(e))
        return None
    if not isinstance(raw, dict) or "tweet_ids_observed" not in raw:
        return None
    if not isinstance(raw["tweet_ids_observed"], list):
        return None
    return raw


def collect_recent_runs(handle: str, now: datetime) -> list[dict[str, Any]]:
    """Return the most recent ``WINDOW_RUNS`` seen runs that pass the size
    floor, newest first.
    """
    runs: list[dict[str, Any]] = []
    for path in reversed(list(iter_seen(handle))):
        parsed = parse_seen(path)
        if parsed is None:
            continue
        if len(parsed["tweet_ids_observed"]) < MIN_SCAN_SIZE:
            continue
        runs.append(parsed)
        if len(runs) >= WINDOW_RUNS:
            break
    return runs


def mark_deletions(handle: str, now: datetime, dry_run: bool = False) -> int:
    parquet = DATA_DIR / f"{handle}.parquet"
    if not parquet.exists():
        return 0
    runs = collect_recent_runs(handle, now)
    if len(runs) < WINDOW_RUNS:
        LOG.info("not enough qualifying scans for deletion check", handle=handle, runs=len(runs))
        return 0
    live_ids: set[str] = set()
    earliest_run_ts: str | None = None
    for r in runs:
        ts = r.get("captured_at")
        if isinstance(ts, str) and (earliest_run_ts is None or ts < earliest_run_ts):
            earliest_run_ts = ts
        live_ids.update(str(i) for i in r["tweet_ids_observed"])
    if earliest_run_ts is None:
        return 0

    df = pl.read_parquet(parquet)
    if df.height == 0:
        return 0

    # Only consider tweets the account itself authored AND that we last saw
    # before earliest_run_ts.
    grace_threshold = (now - timedelta(days=GRACE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    candidate = (
        (pl.col("account_handle") == handle)
        & pl.col("last_seen_at").is_not_null()
        & (pl.col("last_seen_at") < earliest_run_ts)
        & (pl.col("last_seen_at") < grace_threshold)
        & pl.col("deletion_detected_at").is_null()
        & ~pl.col("tweet_id").is_in(list(live_ids))
    )
    detected_at = earliest_run_ts
    new_df = df.with_columns(
        pl.when(candidate)
        .then(pl.lit(detected_at))
        .otherwise(pl.col("deletion_detected_at"))
        .alias("deletion_detected_at")
    )
    n_marked = (
        new_df.filter(pl.col("deletion_detected_at") == detected_at).height
        - df.filter(pl.col("deletion_detected_at") == detected_at).height
    )
    if n_marked > 0:
        LOG.info("marked deletions", handle=handle, count=n_marked, at=detected_at)
        if not dry_run:
            tmp = parquet.with_suffix(".tmp.parquet")
            new_df.write_parquet(tmp, compression="zstd", statistics=True)
            os.replace(tmp, parquet)
    return n_marked


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--handle")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    now = datetime.now(UTC)
    handles: list[str]
    if args.handle:
        handles = [args.handle]
    else:
        handles = sorted(p.name for p in DATA_DIR.glob("*.parquet"))
        handles = [h[: -len(".parquet")] for h in handles if h != "manifest.json"]
    total = 0
    for h in handles:
        total += mark_deletions(h, now=now, dry_run=args.dry_run)
    LOG.info("deletion sweep complete", handles=len(handles), marked=total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
