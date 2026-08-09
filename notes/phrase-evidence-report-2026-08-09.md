# Phrase evidence report, 2026-08-09

## Scope

- Accounts: DHSgov, ICEgov, CBP, and the White House archive lineage
  (ObamaWhiteHouse, WhiteHouse45, WhiteHouse46, WhiteHouse).
- Catalog date range: 2016-01-01 through 2026-08-06.
- Catalog posts searched: 73,431.
- Direct evidence sources: authored post text, OCR from archived photos, OCR from
  archived video keyframes, and archived-video audio transcripts.
- Model-written media descriptions are excluded because they are not verbatim
  evidence.

## Phrase families

- `criminal-alien`: criminal alien(s), criminal illegal alien(s).
- `illegal-alien`: illegal alien(s).
- `angel-mother`: angel mother(s), angel mom(s).
- `angel-family`: angel family, angel families.
- Whitespace, line breaks, hyphens, and Unicode dash separators are accepted.
- `criminal illegal alien` counts in both the criminal-alien and illegal-alien
  families. Phrase and evidence-source totals therefore overlap.

## Unique tweet totals

- Any requested phrase: 4,106.
- Criminal alien: 2,563.
- Illegal alien: 3,414.
- Angel mother: 26.
- Angel families: 45.
- Matching tweets with archived media: 2,791.

## Build and verification

Build command:

```bash
python3 -m scripts.build_phrase_report
```

Outputs are under `phrases/`: `data.json`, `tweets.csv`, and `timeseries.csv`.
The time-series export contains one row per phrase for every calendar day in
the report range, including days with zero hits.
The GitHub Pages viewer is published at `/phrases/` and links each match to the
main archive record, the original X URL, and any archived media.

Checks run:

```bash
uv run pytest tests/test_build_phrase_report.py
uv run ruff check scripts/build_phrase_report.py tests/test_build_phrase_report.py
uv run mypy scripts/build_phrase_report.py
node --check phrases/app.js
```

Headless Chrome was also used at desktop and 390x844 phone sizes. The report
loaded all 4,106 records, drew a nonblank daily timeline, displayed an exact
date/count tooltip, collapsed the totals table to the selected account on
mobile, applied a phrase/account URL filter, and expanded an archived image
from the result list.

The chart can also group the same records by Monday-Sunday week or calendar
month. Clicking a point sets the date filters to that day, week, or month and
opens the matching tweet list.

Chart positions with zero hits resolve to the nearest nonzero interval. The
resolved bar is enlarged in place before selection. A two-handle slider below
the chart adjusts the same date range as the From and To fields.
