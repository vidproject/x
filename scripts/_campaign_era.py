"""Campaign helper: era-filter the ranked needs-vision backlog.

Thin wrapper over ``scripts._campaign_backlog.rank_backlog``. The base ranker
sorts needs-vision media by engagement but does not know about the anti-immigrant
era windows the campaign prioritizes. This joins each candidate's ``posted_at``
from the catalog and keeps only items posted before 2021-01-20 OR on/after
2025-01-20, then prints them (most engaged first) so the driver can pick the
next batch.

Run with::

    uv run python -m scripts._campaign_era --kind photo --limit 30
    uv run python -m scripts._campaign_era --kind video --limit 30
"""

from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl

from scripts._campaign_backlog import rank_backlog

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
CATALOG_PATH = DATA_DIR / "catalog.parquet"

# Anti-immigrant era windows (inclusive on the start of the second window).
ERA1_END = "2021-01-20"  # before this date
ERA2_START = "2025-01-20"  # on/after this date


def _posted_at() -> dict[str, str]:
    df = pl.read_parquet(CATALOG_PATH, columns=["tweet_id", "posted_at"])
    out: dict[str, str] = {}
    for row in df.iter_rows(named=True):
        out[str(row.get("tweet_id") or "")] = str(row.get("posted_at") or "")
    return out


def in_era(posted_at: str) -> bool:
    """True when the ISO timestamp falls in an anti-immigrant era window."""
    if not posted_at:
        return False
    day = posted_at[:10]
    return day < ERA1_END or day >= ERA2_START


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=["video", "photo"], default="photo")
    parser.add_argument("--tier", choices=["strong", "any"], default="strong")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument(
        "--with-keyframes",
        action="store_true",
        help="(video) only show items that already have keyframes on disk.",
    )
    parser.add_argument(
        "--no-keyframes",
        action="store_true",
        help="(video) only show items that still need keyframe extraction.",
    )
    args = parser.parse_args(argv)

    cands = rank_backlog(args.kind, args.tier)
    posted = _posted_at()
    era = [c for c in cands if in_era(posted.get(c["tweet_id"], ""))]
    if args.with_keyframes:
        era = [c for c in era if c["has_keyframes"]]
    elif args.no_keyframes:
        era = [c for c in era if not c["has_keyframes"]]

    print(
        f"{args.kind} (tier={args.tier}) in anti-immigrant eras: "
        f"{len(era)} of {len(cands)} needs-vision"
    )
    seen: set[str] = set()
    n = 0
    for c in era:
        # videos: one row per tweet; photos: one row per media_id.
        dedup_key = c["tweet_id"] if args.kind == "video" else c["media_id"]
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        day = posted.get(c["tweet_id"], "")[:10]
        print(
            f"  {c['engagement']:>8}  {day}  kf={'Y' if c['has_keyframes'] else 'N'}  "
            f"{c['account_handle']:<16} {c['tweet_id']:<20} {c['media_id']:<22} [{c['signal']}]"
        )
        n += 1
        if n >= args.limit:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
