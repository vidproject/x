# Immigration Social Media Archive

Public research archive of immigration-related posts captured from X/Twitter.

[Open the searchable viewer](https://vidproject.github.io/x/)

This repository contains the archive and the tooling around it: browser capture, raw JSON, canonical Parquet files, media archival, annotation sidecars, and the static viewer published through GitHub Pages.

## Scope

The tracked account list lives in `config/accounts.yaml`. Current core handles:

- `@DHSgov`
- `@ICEgov`
- `@CBP`
- `@USCIS`
- `@WhiteHouse`
- `@PressSec`
- `@POTUS`
- `@USDOL`
- `@RapidResponse47`
- `@StephenM`
- `@GregoryKBovino`
- `@RealTomHoman`

Replies, quotes, retweets, and non-tracked accounts encountered in captured threads are preserved in `data/_misc.parquet`.

## Repository Layout

- `raw/`: browser-extension capture payloads.
- `data/*.parquet`: canonical per-account tweet tables.
- `data/catalog.parquet`: compact whole-archive catalog used by the viewer.
- `data/catalog.json`: small catalog manifest and poster map.
- `data/manifest.json`: account-level counts, date ranges, and capture metadata.
- `data/tags/*.parquet`: downstream annotation sidecars.
- `data/relationships/retweets.parquet`: retweet relationship table.
- `extension/`: Firefox/Chrome extension source.
- `viewer/`: static browser viewer.
- `scripts/`: ingest, tagging, media, OCR, audio, news, and README tooling.
- `tools/`: low-overhead X skim tools that use Chrome DevTools Protocol.

Canonical Parquet rows are the record of what X served at capture time. Tags and media analysis are separate, reversible overlays.

## Viewer

The viewer is a static browser app. It starts with `data/catalog.json` and `data/catalog.parquet`, so global search, filters, charts, and the date histogram work without downloading every full account table. Full tweet records hydrate when a row is opened, scrolled into view, or reached from a shared URL. The lightning control downloads all account Parquets listed in `data/manifest.json` for faster full-record browsing.

Search covers tweet text, resolved links, handles, mentions, tags, and media descriptions. Filters cover account, account category, date, tweet type, media type, tag, and visible column values. CSV export uses the current filtered rows. The URL tracks the current view, so filtered pages can be shared.

Tags are grouped by namespace in the filter UI. Tentative tags remain visible but are marked as tentative.

GitHub Pages publishes the viewer through `.github/workflows/pages.yml` on pushes to `master` or `main` that touch `index.html`, `viewer/**`, `data/**`, `extension.zip`, `extension-chrome.zip`, or the Pages workflow. Repository settings must use:

`Pages -> Build and deployment -> Source: GitHub Actions`

## Browser Extension

The extension captures public X/Twitter posts from browser tabs and commits structured JSON to this repository. It also maintains local state for queued refetches, media crawl targets, thread-opening work, recent sightings, and per-account capture counters.

Built extension zips are committed by `.github/workflows/release.yml` when extension source changes:

- Firefox: [vidproject.github.io/x/extension.zip](https://vidproject.github.io/x/extension.zip)
- Chrome: [vidproject.github.io/x/extension-chrome.zip](https://vidproject.github.io/x/extension-chrome.zip)

### Firefox

1. Download `extension.zip` and unzip it.
2. Open `about:debugging`.
3. Select `This Firefox`.
4. Select `Load Temporary Add-on`.
5. Pick `manifest.json` from the unzipped folder.

Temporary Firefox extensions disappear when Firefox closes.

### Chrome

1. Download `extension-chrome.zip` and unzip it.
2. Open `chrome://extensions`.
3. Enable `Developer mode`.
4. Select `Load unpacked`.
5. Pick the unzipped folder.

### Configuration

After loading either build:

1. Open the extension sidebar.
2. Open `Settings`.
3. Paste a fine-grained GitHub personal access token.
4. Visit a tracked account on `x.com`, for example <https://x.com/DHSgov>.

If X tabs were already open when the extension was reloaded, close and reopen them before testing capture. The service worker does reinject the page hook on wake, but fresh tabs are the cleaner path.

### Low-Bandwidth Mode

The sidebar has a `Low-bandwidth X tabs` toggle. When enabled, the extension blocks images, video/audio resources, fonts, and known X/Twitter video chunk URLs inside open X/Twitter tabs. GraphQL/API capture and GitHub archive downloads continue to work.

## Personal Access Token

Use a fine-grained GitHub personal access token scoped only to this repository.

| Permission | Access |
| ---------- | ------ |
| Repository Contents | Read and write |
| Repository Metadata | Read |

Create it at <https://github.com/settings/personal-access-tokens/new>.

The token is stored in `browser.storage.local`. Anyone with filesystem access to the browser profile can read it. Do not use a classic `repo` token.

## Low-Overhead Skim Shell

For quick account skims, the repository includes a standalone Chrome/Edge shell that talks to the Chrome DevTools Protocol. It uses a persistent local profile, blocks heavy assets by default, scrolls the target page, clicks visible retry prompts, and writes served X GraphQL responses to local JSONL.

First run it visibly and log in to X if the profile is new:

```bash
npm run skim:x -- --login-browser
```

Then run skims against profile pages, media tabs, or reply views:

```bash
npm run skim:x -- --handle DHSgov --seconds 180 --scrolls 80
npm run skim:x -- --url https://x.com/DHSgov/with_replies --seconds 240
npm run skim:x -- --url https://x.com/DHSgov/with_replies --seek-year 2025 --seconds 600
npm run skim:x -- --url https://x.com/DHSgov/media --metadata-only
```

For manual inspection through the same shell, use:

```bash
npm run skim:x -- --manual --allow-styles
```

Output goes under `.skim/raw/`; the browser profile lives under `.skim/profile/`. Both are ignored by git. The JSONL is separate from canonical `raw/` captures because it preserves raw GraphQL responses and candidate tweet/media IDs, not normalized tweet envelopes.

The skim shell is stricter than the extension low-bandwidth mode. If a page needs a blocked asset class to paginate, relax it explicitly:

```bash
npm run skim:x -- --handle DHSgov --allow-styles
npm run skim:x -- --handle DHSgov --allow-images --metadata-only
```

## Pipeline

Primary workflow:

```text
extension
  raw/*.json
    scripts.ingest
      data/*.parquet
      data/catalog.parquet
      data/catalog.json
      data/manifest.json
    scripts.detect_deletions
    scripts.build_account_categories
    scripts.news_mentions
    scripts.tag_lexical
    scripts.refresh_viewer_metadata
    scripts.update_readme
  scripts.archive_media
    GitHub Release assets
    data/*.parquet media URLs
    scripts.describe_media
    scripts.extract_video_frames
    scripts.tag_image_ocr
    scripts.detect_audio_music
    scripts.build_core_video_audit
    scripts.tag_lexical
  GitHub Pages
```

GitHub Actions:

- `.github/workflows/ci.yml`: Python lint, format check, mypy; Node lint, typecheck, Prettier.
- `.github/workflows/release.yml`: builds and commits Firefox/Chrome extension zips.
- `.github/workflows/ingest.yml`: ingests raw captures, refreshes deterministic metadata, tags, news mentions, catalog files, and README coverage.
- `.github/workflows/archive-media.yml`: archives media to GitHub Releases, runs media sidecars, and refreshes media-derived tags.
- `.github/workflows/pages.yml`: deploys the viewer and extension zips to GitHub Pages.

Local commands:

```bash
uv sync
npm install

uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
npm run lint
npm run typecheck
npx prettier --check .

uv run python -m scripts.ingest
uv run python -m scripts.detect_deletions
uv run python -m scripts.build_account_categories
uv run python -m scripts.archive_media
uv run python -m scripts.describe_media
uv run python -m scripts.extract_video_frames
uv run python -m scripts.tag_image_ocr
uv run python -m scripts.detect_audio_music
uv run python -m scripts.build_core_video_audit
uv run python -m scripts.news_mentions --articles data/news/articles.jsonl
uv run python -m scripts.refresh_viewer_metadata
uv run python -m scripts.update_readme
```

The `justfile` wraps the common `setup`, `lint`, `test`, `fmt`, `build-extension`, and `ingest` tasks.

## Tags

Tags are annotations, not capture data. They live in sidecar Parquets under `data/tags/` and are joined by `tweet_id` in the viewer. Missing sidecars are tolerated.

Current sidecars and files:

- `data/tags/lexical.parquet`: regex, structural, OCR-fed, audio-fed, and manually reviewed lexical tags from `scripts/tag_lexical.py`.
- `data/tags/media_vision.parquet`: archived media metadata, source alt text, manual review observations, and low-cost media descriptions from `scripts/describe_media.py`.
- `data/tags/keyframes.parquet`: bounded video keyframe metadata and tiny poster thumbnails from `scripts.extract_video_frames`.
- `data/tags/image_ocr.parquet`: Tesseract OCR from archived photos and extracted video keyframes from `scripts.tag_image_ocr`.
- `data/tags/audio_music.parquet`: ffprobe/ffmpeg audio stream and music-likelihood tags from `scripts.detect_audio_music`.
- `data/tags/news_mentions.parquet`: exact X/Twitter status-URL mentions in local or configured news discovery results from `scripts/news_mentions.py`.
- `data/tags/core_video_audit.json` and `.csv`: review queue for core-account video analysis from `scripts/build_core_video_audit.py`.
- `data/account_categories.json`: account categories from `scripts/build_account_categories.py`.
- `config/tag_overrides.yaml`: editor-confirmed corrections where canonical fields are not enough.

Tag names use `namespace:slug`. The namespace is the broad category; the slug is the narrower tag. See `config/tag_taxonomy.yaml` and `docs/TAGGING.md`.

Uncertain model-derived or review-derived labels should be stored as tentative. Deterministic tags should stay deterministic.

## Media Recognition

The shipped media pipeline is deliberately low-cost and auditable:

- `scripts.describe_media` uses archived media metadata, source alt text, dimensions, duration, byte count, tweet context, and curated manual-review observations. It does not claim visual content from pixels unless OCR, reviewed observations, or another sidecar supplies that evidence.
- `scripts.extract_video_frames` extracts bounded keyframes from archived videos and writes small poster thumbnails for the viewer.
- `scripts.tag_image_ocr` reads archived photos and video keyframes with Tesseract, then feeds recovered text back into lexical tagging.
- `scripts.detect_audio_music` uses ffprobe/ffmpeg only. It emits conservative `audio:has-audio`, `audio:no-audio`, `audio:silent`, and tentative `audio:music-likely` tags.
- `scripts.build_core_video_audit` joins video rows against keyframes, OCR, audio, metadata vision, manual review, and lexical tags. It also writes queue files for GitHub-side recovery of likely produced or genre-relevant videos whose media is still missing.

Provider-backed visual review should enter through reviewed sidecars or overrides. It should not mutate canonical Parquet rows.

## News Mentions

`scripts.news_mentions` checks whether archived core tweets are cited by news coverage. It accepts JSON, JSONL, CSV, directories, or globs of article records with fields such as `url`, `title`, `description`, `body`, `content`, or `text`.

The matcher counts exact status URL variants only:

- `x.com/<handle>/status/<tweet_id>`
- `twitter.com/<handle>/status/<tweet_id>`
- `x.com/i/web/status/<tweet_id>`

Normal offline runs can use `--discover-web none`. For low-cost discovery, use Google News RSS or GDELT:

```bash
uv run python -m scripts.news_mentions --articles data/news/articles.jsonl --discover-web google-news-rss --max-web-tweets 100 --matched-only
uv run python -m scripts.news_mentions --articles data/news/articles.jsonl --discover-web gdelt --max-web-tweets 100 --matched-only
```

Confirmed matches receive `news:mentioned` and `news:covered`. Vague text or title similarity does not emit firm news tags.

## Coverage

This block is regenerated by `scripts/update_readme.py` after ingest. Do not edit inside the markers.

<!-- COVERAGE:START -->

| Handle | Label | Tweets | First post | Latest post | Latest capture | Media | Videos |
| ------ | ----- | -----: | ---------- | ----------- | -------------- | ----: | -----: |
| `@ICEgov` | U.S. Immigration and Customs Enforcement | 479 | 2016-03-28 | 2026-05-21 | 2026-05-21 | 359 | 75 |
| `@PressSec` | White House Press Secretary | 137 | 2025-02-17 | 2026-05-15 | 2026-05-21 | 23 | 7 |
| `@USDOL` | U.S. Department of Labor | 848 | 2025-01-14 | 2026-05-20 | 2026-05-21 | 574 | 76 |
| `@RapidResponse47` | Rapid Response 47 | 459 | 2025-04-18 | 2026-05-20 | 2026-05-21 | 383 | 308 |
| `@WhiteHouse` | The White House | 281 | 2025-04-16 | 2026-05-20 | 2026-05-21 | 225 | 59 |
| `@StephenM` | Stephen Miller | 1,084 | 2021-01-20 | 2026-05-20 | 2026-05-21 | 91 | 24 |
| `@DHSgov` | Department of Homeland Security | 4,497 | 2016-01-11 | 2026-05-21 | 2026-05-21 | 2,600 | 526 |
| `@CBP` | U.S. Customs and Border Protection | 515 | 2016-03-21 | 2026-05-20 | 2026-05-21 | 270 | 47 |
| `@GregoryKBovino` | Gregory Bovino | 1,257 | 2026-04-20 | 2026-05-20 | 2026-05-21 | 22 | 4 |
| `@USCIS` | U.S. Citizenship and Immigration Services | 768 | 2021-06-09 | 2026-05-18 | 2026-05-21 | 505 | 85 |
| `@RealTomHoman` | Thomas D. Homan | 528 | 2023-01-21 | 2024-11-12 | 2026-05-20 | 176 | 97 |
| `@POTUS` | President of the United States | 101 | 2025-01-20 | 2026-05-19 | 2026-05-19 | 40 | 13 |
| `@_misc` | Miscellaneous (replies / quotes / retweets of non-tracked accounts) | 5,525 | 2016-01-13 | 2026-05-21 | 2026-05-21 | 2,702 | 710 |

_Generated 2026-05-22T04:30:49Z._

<!-- COVERAGE:END -->

## Data Rules

- Canonical rows mirror what X served at capture time.
- Parse failures go to `raw/_quarantine/`.
- Parquet rewrites are atomic.
- Release uploads must succeed before a row records the archived asset URL.
- Credentials stay out of the repository.
- Annotation is reversible and separate from capture.

## Documentation

- [Data schema](docs/SCHEMA.md)
- [Tagging system](docs/TAGGING.md)

## License

Property of the University of California.
