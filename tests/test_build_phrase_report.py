from __future__ import annotations

import csv
from pathlib import Path

from scripts.build_phrase_report import match_phrases, write_report


def keys(text: str) -> set[str]:
    return set(match_phrases(text))


def test_matches_plural_hyphenated_and_line_break_variants() -> None:
    text = (
        "Criminal aliens, a criminal-illegal-alien, and illegal\naliens. "
        "Angel Moms stood with Angel-Families."
    )
    assert keys(text) == {
        "criminal-alien",
        "illegal-alien",
        "angel-mother",
        "angel-family",
    }


def test_criminal_illegal_alien_overlaps_illegal_alien_family() -> None:
    matches = match_phrases("A criminal illegal alien was arrested.")
    assert set(matches) == {"criminal-alien", "illegal-alien"}
    assert matches["criminal-alien"][0][0] == "criminal illegal alien"
    assert matches["illegal-alien"][0][0] == "illegal alien"


def test_angel_mother_includes_mom_but_not_unrelated_words() -> None:
    assert "angel-mother" in keys("Angel Mother, Angel Mothers, Angel Mom, Angel Moms")
    assert not keys("An angelic mother discussed alienation and a family angel.")


def test_timeseries_csv_uses_daily_buckets(tmp_path: Path) -> None:
    report = {
        "scope": {
            "earliest": "2025-02-17T00:00:00.000Z",
            "latest": "2025-02-19T23:59:59.000Z",
        },
        "tweets": [
            {
                "tweet_id": "1",
                "posted_at": "2025-02-18T18:45:43.000Z",
                "account_group": "White House",
                "account_handle": "WhiteHouse",
                "phrases": ["illegal-alien"],
                "variants": {"illegal-alien": ["illegal alien"]},
                "evidence": [
                    {
                        "phrase": "illegal-alien",
                        "source": "tweet-text",
                        "snippet": "illegal alien",
                    }
                ],
                "text": "illegal alien",
                "tweet_url": "https://x.com/WhiteHouse/status/1",
                "media": [],
            }
        ]
    }

    write_report(report, tmp_path)

    with (tmp_path / "timeseries.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["date"] for row in rows} == {"2025-02-17", "2025-02-18", "2025-02-19"}
    illegal_alien = next(
        row
        for row in rows
        if row["date"] == "2025-02-18" and row["phrase"] == "illegal-alien"
    )
    assert illegal_alien == {
        "date": "2025-02-18",
        "phrase": "illegal-alien",
        "DHS": "0",
        "ICE": "0",
        "CBP": "0",
        "White House": "1",
        "all_accounts": "1",
    }
