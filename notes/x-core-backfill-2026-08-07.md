# X core-account backfill, 2026-08-07

## Scope

- Bring `WhiteHouse`, `ICEgov`, `DHSgov`, and `CBP` forward through the collection time on 2026-08-07.
- Backfill White House records to 2016-01-01 using the account that actually authored each administration's posts:
  - `ObamaWhiteHouse`: 2016-01-01 through 2017-01-20
  - `WhiteHouse45`: 2017-01-20 through 2021-01-20
  - `WhiteHouse46`: 2021-01-20 through 2025-01-20
  - `WhiteHouse`: 2025-01-20 onward
- After the White House lineage is complete, backfill `ICEgov`, `DHSgov`, and `CBP` to 2016-01-01.

## Published current update

- Raw capture commit: `4a911d71`
- Ingest-generated commit: `54d86eb7`
- Rate-limit timer fix: `639ea967`
- Curated 32 weekly capture summaries with `SearchTimeline`, saturated completion, no response errors, and no 429 events.
- Converted 3,175 normalized response records into 426 raw files. Catalog ingest performs tweet-ID deduplication.

Integrated account files after ingest:

| Account    |  Rows | Earliest archived row | Latest archived row  |
| ---------- | ----: | --------------------- | -------------------- |
| WhiteHouse | 3,503 | 2025-01-20T17:41:50Z  | 2026-08-06T23:33:54Z |
| ICEgov     | 3,131 | 2016-03-28T16:15:02Z  | 2026-08-06T22:15:14Z |
| DHSgov     | 7,026 | 2016-01-11T19:45:09Z  | 2026-08-06T23:23:35Z |
| CBP        | 2,245 | 2016-03-21T22:17:41Z  | 2026-08-06T20:34:54Z |

The pre-2025 minima for ICEgov, DHSgov, and CBP are sparse legacy captures, not proof of continuous coverage.

## Collector corrections

`tools/x-skim.mjs` now:

- Counts stagnant scrolls only after the browser is actually near the bottom of the page. This prevents a dense SearchTimeline page from being declared saturated after several short scrolls.
- Adds an X rate-limit sleep back to the page deadline. A 15-minute reset wait therefore pauses the effective collection timer instead of truncating the active date window.

Syntax, Prettier, and ESLint checks passed for the collector. Repository-wide Prettier continues to flag the pre-existing `creative/app.js`; that file was not changed for this acquisition.

## White House historical command

```bash
node tools/skim-batch-windows.mjs \
  --job ObamaWhiteHouse:2016-01-01:2017-01-20:week \
  --job WhiteHouse45:2017-01-20:2021-01-20:week \
  --job WhiteHouse46:2021-01-20:2025-01-20:week \
  --source-profile .skim/profile \
  --profile-root .skim/history-whitehouse-2016-2025/profiles \
  --out .skim/history-whitehouse-2016-2025/raw \
  --log-dir .skim/history-whitehouse-2016-2025/logs \
  --concurrency 1 \
  --seconds 240 \
  --scrolls 140 \
  --scroll-delay-ms 700 \
  --scroll-factor 1.5 \
  --rate-limit-floor 3 \
  --max-backoff-ms 900000 \
  --latest \
  --include-native-retweets \
  --headless
```

There are 473 weekly White House lineage windows. A window is eligible for conversion only when its summary has at least one `SearchTimeline` response, `stopped_reason` is `saturated`, `response_errors` is empty, and `rate_limit.throttle_events` is zero. Failed or incomplete windows must be rerun before conversion.

## First historical tranche

The Obama archive phase completed all 55 expected weekly windows from 2016-01-01 through 2017-01-20:

- 55 summaries and 55 expected windows
- no missing or extra windows
- no invalid summaries
- 280 SearchTimeline pages
- 5,959 candidate IDs before author filtering
- 3,547 unique authored `ObamaWhiteHouse` posts after normalization and deduplication
- authored date range: 2016-01-01T16:19:12Z through 2017-01-20T15:31:41Z

A saturated direct-profile traversal was also retained as supplemental `WhiteHouse45` coverage:

- 41 UserTweets pages
- zero response errors and zero 429 events
- 805 unique authored `WhiteHouse45` posts
- authored date range: 2019-08-28T23:40:01Z through 2021-01-20T15:29:59Z

The WhiteHouse45 profile timeline stopped serving older posts at August 2019 even though browser-bottom saturation was reached. It is therefore supplemental coverage, not a replacement for bounded weekly searches back to 2017-01-20.

Additional saturated profile/media sources were collected while SearchTimeline was rate-limited:

| Source               | Unique authored IDs | Earliest             | Latest               | Notes                                                |
| -------------------- | ------------------: | -------------------- | -------------------- | ---------------------------------------------------- |
| WhiteHouse45 media   |                 796 | 2020-09-13T16:32:01Z | 2021-01-20T14:54:46Z | 548 IDs were absent from the general-profile capture |
| WhiteHouse46 profile |                 773 | 2024-08-11T19:21:00Z | 2025-01-20T15:20:41Z | General profile timeline                             |
| WhiteHouse46 media   |                 800 | 2024-05-09T21:34:15Z | 2025-01-20T15:20:41Z | All 800 normalized posts have media                  |

The WhiteHouse46 profile and media sources overlap on 471 IDs and have a union of 1,102 unique authored posts. Canonical ingest deduplicates these records by tweet ID while retaining capture provenance.

The first 52 bounded WhiteHouse45 weeks, from 2017-01-20 through 2018-01-19, also completed:

- 52 summaries and 52 expected windows
- no missing or extra windows
- no invalid summaries
- 173 SearchTimeline pages
- 3,327 candidate IDs before author filtering
- 1,657 unique authored WhiteHouse45 posts after normalization and deduplication

The normalized authored range is 2017-01-20T17:03:30Z through 2018-01-20T05:16:52Z. Thirteen records fall on January 19-20 UTC after the nominal `until:2018-01-19` query boundary, consistent with X applying search-date boundaries in the browser session timezone. Adjacent weekly windows overlap this boundary in practice, so these valid posts were retained.

Saturated replies views added further coverage:

| Source               | Pages | Unique authored IDs | Replies | Earliest             | Latest               |                         Additive IDs |
| -------------------- | ----: | ------------------: | ------: | -------------------- | -------------------- | -----------------------------------: |
| WhiteHouse45 replies |    55 |               1,100 |      53 | 2019-08-28T23:40:01Z | 2021-01-20T15:29:59Z |   213 beyond its profile/media union |
| WhiteHouse46 replies |   161 |               3,068 |     238 | 2023-08-09T19:09:44Z | 2025-01-20T15:20:41Z | 1,972 beyond its profile/media union |

Both replies traversals reached browser-bottom saturation with no response errors or 429 events. A collector reporting bug labeled the earlier WhiteHouse45 run as `UserTweets` because that substring was checked before `UserTweetsAndReplies`; the captured request URLs and response bodies identify the correct endpoint. The endpoint matching order was fixed before the WhiteHouse46 run.
