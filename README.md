# Immigration Social Media Archive

Public archive of immigration-related posts from federal X accounts.

[Open the searchable archive](https://vidproject.github.io/x/)

This repo is the database. A Firefox extension captures public X timeline data in the browser, commits raw JSON to GitHub, and GitHub Actions turns it into Parquet, archived media assets, tag sidecars, and a static viewer.

No X API credentials. No X Developer Agreement. Capture uses what the public web UI served at the time.

The viewer is published to GitHub Pages by `.github/workflows/pages.yml` on every push to `master` that touches `index.html`, `viewer/**`, `data/**`, or `extension.zip`. Repo settings need **Pages → Build and deployment → Source: GitHub Actions** for the workflow to actually deploy.

The Firefox extension zip is rebuilt automatically by the `build-extension` workflow whenever the extension changes, committed back to `extension.zip`, and published by Pages at **[vidproject.github.io/x/extension.zip](https://vidproject.github.io/x/extension.zip)**.

## Scope

Tracked core accounts:

- `@DHSgov`
- `@ICEgov`
- `@CBP`
- `@USCIS`
- `@WhiteHouse`
- `@PressSec`
- `@POTUS`
- `@USDOL`
- `@RapidResponse47`

The archive also preserves replies, quotes, retweets, and public accounts that appear in captured threads.

## Viewer

The viewer loads every account Parquet listed in `data/manifest.json`. Search runs in the browser. Filters support account, account category, date, tweet type, media type, tag, and column values. The URL updates with the current view, so filtered pages can be shared.

Search covers tweet text, resolved links, handles, mentions, tags, and media descriptions. CSV export uses the currently filtered rows.

GitHub Pages publishes the viewer and extension zip through `.github/workflows/pages.yml` when `index.html`, `viewer/**`, `data/**`, or `extension.zip` changes. Repo settings must use:

`Pages -> Build and deployment -> Source: GitHub Actions`

## Firefox Extension

The extension captures public X posts and commits structured JSON to this repository.

1. Download the latest auto-built [`extension.zip`](https://vidproject.github.io/x/extension.zip) and unzip it.
2. In Firefox, open `about:debugging`.
3. Select `This Firefox`.
4. Select `Load Temporary Add-on`.
5. Pick `manifest.json` from the unzipped extension folder.
6. Open the extension sidebar.
7. Open `Settings`.
8. Paste a fine-grained GitHub PAT.
9. Visit a tracked account on `x.com`, for example <https://x.com/DHSgov>.

Temporary Firefox extensions disappear when Firefox closes. Reinstalling takes about ten seconds.

If you reload the extension while X tabs are open, those tabs may keep old content scripts. The extension does reinject its page hook on wake, but the cleanest test path is to close X tabs, reload the extension, and let `Capture now` open a fresh tab.

## PAT

Use a fine-grained Personal Access Token. Select only this repository.

| Permission          | Access         |
| ------------------- | -------------- |
| Repository Contents | Read and write |
| Repository Metadata | Read           |

Create it at <https://github.com/settings/personal-access-tokens/new>.

The PAT is stored in `browser.storage.local`. Anyone with filesystem access to the Firefox profile can read it. Do not use a classic `repo` token.

## Capture Notes

The sidebar can auto-scroll open X tabs. This works around profile tabs that stop paginating unless the page keeps moving. The default cadence is 6 seconds.

Long-form tweets often appear in timeline responses as a 280-character head plus a `show more` link. The normalizer marks those rows with `is_truncated=true` and queues detail-page refetch. The sidebar has a refetch button for that queue.

Media crawl follows attached media from the captured tweet data and stores archived assets in GitHub Releases. The canonical Parquet row records the Release URL only after upload succeeds.

## Tags

Tags are downstream annotations. They are not written into the canonical tweet Parquets.

Current sidecars:

- `data/tags/lexical.parquet`: regex and structural tags from `scripts/tag_lexical.py`.
- `data/tags/media_vision.parquet`: media descriptions from `scripts/describe_media.py`.

The viewer joins sidecars by `tweet_id`. Missing sidecars are tolerated.

Tag namespaces use the form `namespace:slug`. The namespace is the broad category. The slug is the subtype. The viewer groups tag filters by namespace so a user can filter whole categories or specific subtypes.

The immigration-reporting tag is `action:report-immigrants`. Generic non-immigration reporting can use other `action:report-*` tags later.

## Media Recognition

`scripts.describe_media` is the first recognition layer. It is deliberately cheap. It uses archived media metadata, source alt text, dimensions, duration, byte count, and tweet context. It does not infer visual content from pixels.

Each media row carries cache and provenance fields: `input_hash`, `model`, `model_version`, `prompt_hash`, `confidence`, `cost_estimate_usd`, `status`, `source_fields`, and `error`.

This gives later OCR, transcript, keyframe, CLIP, or vision-model jobs a stable place to write results without changing canonical capture data. Items that need deeper inspection get tentative `media:needs-vision`.

## Pipeline

```text
extension
  raw/*.json
    scripts.ingest
      data/*.parquet
      data/manifest.json
    scripts.tag_lexical
      data/tags/lexical.parquet
    scripts.archive_media
      GitHub Release assets
      data/*.parquet media URLs
    scripts.describe_media
      data/tags/media_vision.parquet
    GitHub Pages
      viewer
```

Main commands:

```bash
uv run python -m scripts.ingest
uv run python -m scripts.tag_lexical
uv run python -m scripts.archive_media
uv run python -m scripts.describe_media
npm run lint
npm run typecheck
```

## Coverage

This block is regenerated by `scripts/update_readme.py` after ingest. Do not edit inside the markers.

<!-- COVERAGE:START -->

| Handle | Label | Tweets | First post | Latest post | Latest capture | Media | Videos |
| ------ | ----- | -----: | ---------- | ----------- | -------------- | ----: | -----: |
| `@USCIS` | U.S. Citizenship and Immigration Services | 760 | 2025-04-04 | 2026-05-18 | 2026-05-20 | 486 | 84 |
| `@RapidResponse47` | Rapid Response 47 | 439 | 2025-06-08 | 2026-05-20 | 2026-05-20 | 365 | 296 |
| `@DHSgov` | Department of Homeland Security | 1,023 | 2025-04-09 | 2026-05-20 | 2026-05-20 | 722 | 233 |
| `@POTUS` | President of the United States | 101 | 2025-01-20 | 2026-05-19 | 2026-05-19 | 40 | 13 |
| `@WhiteHouse` | The White House | 262 | 2025-06-09 | 2026-05-20 | 2026-05-20 | 209 | 52 |
| `@ICEgov` | U.S. Immigration and Customs Enforcement | 275 | 2025-04-16 | 2026-05-19 | 2026-05-20 | 184 | 34 |
| `@RealTomHoman` | Thomas D. Homan | 356 | 2023-09-22 | 2024-11-12 | 2026-05-20 | 94 | 65 |
| `@PressSec` | White House Press Secretary | 133 | 2025-09-17 | 2026-05-15 | 2026-05-20 | 21 | 5 |
| `@GregoryKBovino` | Gregory Bovino | 644 | 2026-04-26 | 2026-05-20 | 2026-05-20 | 19 | 3 |
| `@USDOL` | U.S. Department of Labor | 490 | 2025-12-28 | 2026-05-19 | 2026-05-19 | 238 | 28 |
| `@CBP` | U.S. Customs and Border Protection | 491 | 2023-10-19 | 2026-05-20 | 2026-05-20 | 240 | 43 |
| `@StephenM` | Stephen Miller | 462 | 2025-10-24 | 2026-05-20 | 2026-05-20 | 37 | 10 |
| `@_misc` | Miscellaneous (replies / quotes / retweets of non-tracked accounts) | 4,084 | 2018-07-20 | 2026-05-20 | 2026-05-20 | 2,111 | 622 |

_Generated 2026-05-20T23:22:14Z._

<!-- COVERAGE:END -->

## Data Rules

- Canonical Parquet rows mirror what X served at capture time.
- Parse failures go to `raw/_quarantine/`.
- Parquet rewrites are atomic.
- Release uploads must succeed before a row records the asset URL.
- Credentials stay out of the repo.
- Annotation is reversible and separate from capture.

## Documentation

- [Data schema](docs/SCHEMA.md)
- [Tagging system](docs/TAGGING.md)

## License

Property of the University of California.
