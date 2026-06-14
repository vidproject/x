"""Write tweet IDs for `_misc` rows authored by government/official accounts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts._logging import configure
from scripts._misc_scope import DATA_DIR, dump_misc_government_tweet_ids

LOG = configure()

DEFAULT_OUT = DATA_DIR / "tags" / "_misc_government_tweet_ids.txt"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output file (default {DEFAULT_OUT.relative_to(DATA_DIR.parent)}).",
    )
    args = parser.parse_args(argv)
    count = dump_misc_government_tweet_ids(out_path=args.out)
    LOG.info("misc government tweet id scope written", path=str(args.out), tweet_ids=count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
