# Immigration Social Media Archive

Public archive of immigration-related posts from federal X accounts.

[Open the searchable archive](https://vidproject.github.io/x/)

This repo is the database. A browser extension captures public X timeline data in the browser, commits raw JSON to GitHub, and GitHub Actions turns it into Parquet, archived media assets, tag sidecars, and a static viewer.

No X API credentials. No X Developer Agreement. Capture uses what the public web UI served at the time.

The viewer is published to GitHub Pages by `.github/workflows/pages.yml` on every push to `master` that touches `index.html`, `viewer/**`, `data/**`, `extension.zip`, or `extension-chrome.zip`. Repo settings need **Pages -> Build and deployment -> Source: GitHub Actions** for the workflow to actually deploy.

The extension zips are rebuilt automatically by the `build-extension` workflow whenever the extension changes, committed back to the repo, and published by Pages:

- Firefox: **[vidproject.github.io/x/extension.zip](https://vidproject.github.io/x/extension.zip)**
- Chrome: **[vidproject.github.io/x/extension-chrome.zip](https://vidproject.github.io/x/extension-chrome.zip)**

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

GitHub Pages publishes the viewer and extension zips through `.github/workflows/pages.yml` when `index.html`, `viewer/**`, `data/**`, `extension.zip`, or `extension-chrome.zip` changes. Repo settings must use:

`Pages -> Build and deployment -> Source: GitHub Actions`

## Browser Extension

The extension captures public X posts and commits structured JSON to this repository.

### Firefox

1. Download the latest auto-built [`extension.zip`](https://vidproject.github.io/x/extension.zip) and unzip it.
2. In Firefox, open `about:debugging`.
3. Select `This Firefox`.
4. Select `Load Temporary Add-on`.
5. Pick `manifest.json` from the unzipped extension folder.

### Chrome

1. Download the latest auto-built [`extension-chrome.zip`](https://vidproject.github.io/x/extension-chrome.zip) and unzip it.
2. In Chrome, open `chrome://extensions`.
3. Enable `Developer mode`.
4. Select `Load unpacked`.
5. Pick the unzipped extension folder.

The sidebar includes a **Low-bandwidth X tabs** option. When enabled, the
extension blocks images, video/audio resources, fonts, and known X/Twitter
video chunk URLs inside open X/Twitter tabs while leaving GraphQL/API capture
and background archive downloads alone.

After loading either build:

1. Open the extension sidebar.
2. Open `Settings`.
3. Paste a fine-grained GitHub PAT.
4. Visit a tracked account on `x.com`, for example <https://x.com/DHSgov>.

Temporary Firefox extensions disappear when Firefox closes. Reinstalling takes about ten seconds.

If you reload the extension while X tabs are open, those tabs may keep old content scripts. The extension does reinject its page hook on wake, but the cleanest test path is to close X tabs, reload the extension, and let `Capture now` open a fresh tab.

## Low-Overhead Skim Shell

For account skims where the extension UI is more browser than you need, the repo
also includes a standalone Chrome/Edge shell that talks directly to the Chrome
DevTools Protocol. It opens X with a persistent local profile, blocks images,
video/audio, fonts, stylesheets, and common tracking hosts by default, scrolls
the target page, clicks visible retry prompts, and writes the served X GraphQL
responses to local JSONL.

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

If the CDP/manual shell itself is needed for inspection, use
`--manual --allow-styles`; it captures network traffic but does not scroll or
click retry prompts.

Output goes under `.skim/raw/` and the browser profile lives under
`.skim/profile/`; both are ignored by git. The JSONL is intentionally separate
from canonical `raw/` captures because it preserves raw GraphQL responses and
candidate tweet/media IDs rather than extension-normalized tweet envelopes. Use
it for low-bandwidth discovery, gap checks, and deciding what the normal archive
collector should fetch next.

By default the skim shell is stricter than the extension's low-bandwidth mode.
If a page needs a blocked class of asset to paginate, selectively relax it:

```bash
npm run skim:x -- --handle DHSgov --allow-styles
npm run skim:x -- --handle DHSgov --allow-images --metadata-only
```

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
- `data/tags/keyframes.parquet`: video keyframe metadata and tiny poster thumbnails from `scripts/extract_video_frames.py`.
- `data/tags/image_ocr.parquet`: Tesseract OCR text from archived photos and extracted video keyframes from `scripts/tag_image_ocr.py`.
- `data/tags/audio_music.parquet`: ffmpeg-only audio stream/music-likelihood tags from `scripts/detect_audio_music.py`.
- `data/tags/media_llm.parquet`: optional paid OpenAI image/video keyframe descriptions from `scripts/tag_media_llm.py`; Gemini is used only as a narrow watermark/provenance verifier for suspected AI-generated media.
- `data/tags/news_mentions.parquet`: exact X/Twitter status-URL mentions of core tweets in a local news article export from `scripts/news_mentions.py`.
- `data/account_categories.json`: corpus-wide public figure / government / official categories from `scripts/build_account_categories.py`.
- `config/tag_overrides.yaml`: editor-confirmed tags for cases the capture layer cannot prove from canonical fields alone.

The viewer joins sidecars by `tweet_id`. Missing sidecars are tolerated.

Tag namespaces use the form `namespace:slug`. The namespace is the broad category. The slug is the subtype. The viewer groups tag filters by namespace so a user can filter whole categories or specific subtypes.

The immigration-reporting tag is `action:report-immigrants`. Generic non-immigration reporting can use other `action:report-*` tags later.

## Media Recognition

`scripts.describe_media` is the first recognition layer. It is deliberately cheap. It uses archived media metadata, source alt text, dimensions, duration, byte count, tweet context, and curated manual media-review observations. It does not infer visual content from pixels unless a reviewed observation or later OCR/vision sidecar supplies that evidence.

Each media row carries cache and provenance fields: `input_hash`, `model`, `model_version`, `prompt_hash`, `confidence`, `cost_estimate_usd`, `status`, `source_fields`, and `error`.

This gives later OCR, transcript, keyframe, CLIP, audio, or vision-model jobs a stable place to write results without changing canonical capture data. Items that need deeper inspection get tentative `media:needs-vision`.

`scripts.extract_video_frames` pulls bounded keyframes from archived videos and also writes a tiny 96px JPEG poster under `data/thumbnails/video/` for the viewer. The table uses those posters before falling back to larger frame paths, so video thumbnails are automatic and cheap to load.

`scripts.tag_image_ocr` is the first true pixel-reading image layer. It OCRs archived photos and the keyframes extracted in the same workflow run, then `scripts.tag_lexical` imports that recovered text so image-only slogans, agency names, religious language, and other text-overlay tags are searchable and filterable.

`scripts.detect_audio_music` is the first audio pass. It uses ffprobe/ffmpeg only: detect whether an archived video has audio, decode a short mono sample, compute simple energy/zero-crossing features, and emit conservative `audio:has-audio`, `audio:no-audio`, `audio:silent`, and tentative `audio:music-likely` tags. The lexical layer still uses video text and direct replies as additional cheap context when people explicitly reference the song, soundtrack, or background music.

`scripts.tag_media_llm` is the paid image/video recognition tier. OpenAI (`OPENAI_API_KEY`) is the first-line recognizer for archived photos and bounded video keyframes. Gemini (`GEMINI_API_KEY` or `GOOGLE_API_KEY`) is called only when the OpenAI result already suspects `media:ai-generated`, and then only as a narrow watermark/provenance verifier capped at 5 calls per minute. The tier emits neutral descriptions plus tags for produced-video structure (`media:produced-video`, `media:montage`, `media:text-overlay`, `media:voiceover`, `video:*` genre labels), visible slogans, evidence-supported `speaker:*`, and tentative `media:ai-generated` when synthetic cues are visible. The workflow caps this tier with `llm_max_items` and `llm_budget_usd`.

`scripts.build_core_video_audit` joins core-account videos against keyframes, OCR, audio, metadata vision, paid LLM, manual-review, and lexical tags. It writes `data/tags/core_video_audit.json` and `data/tags/core_video_audit.csv`, prioritized for produced-video and genre review (`genre:music-video`, `genre:dystopian`, `genre:war-movie`, `genre:utopian`, recruitment, advertisement, and PSA).

The audit also emits queue files for GitHub-side recovery of likely produced or genre-relevant videos whose media is still missing: `data/tags/core_produced_missing_tweet_ids.txt` and `data/tags/core_produced_missing_media_ids.txt`. Dispatch `archive-media` with those files, or push changes to them, to have GitHub fetch the queued media instead of using local bandwidth.

## News Mentions

`scripts.news_mentions` checks whether archived core tweets are cited by news coverage using a deterministic local article export. It accepts JSON, JSONL, or CSV records with fields such as `url`, `title`, `description`, `body`, `content`, or `text`, then matches exact `x.com/<handle>/status/<tweet_id>`, `twitter.com/<handle>/status/<tweet_id>`, and `x.com/i/web/status/<tweet_id>` URLs. Tests and normal offline runs need no network. For cheap ad-hoc discovery, run `uv run python -m scripts.news_mentions --discover-web google-news-rss --max-web-tweets 100 --matched-only`; this queries Google News RSS, or `--discover-web gdelt` for GDELT, for exact status URL strings and records returned article metadata at lower confidence.

When `data/news/articles.jsonl` exists, the ingest workflow refreshes `data/tags/news_mentions.parquet`; otherwise it skips the step unless a manual workflow dispatch `news_discover` provider is selected. Mentioned tweets receive `news:mentioned` and `news:covered` tags that the viewer loads like other optional sidecars.

## Pipeline

```text
extension
  raw/*.json
    scripts.ingest
      data/*.parquet
      data/manifest.json
    scripts.tag_lexical
      data/tags/lexical.parquet
    scripts.build_account_categories
      data/account_categories.json
    scripts.archive_media
      GitHub Release assets
      data/*.parquet media URLs
    scripts.describe_media
      data/tags/media_vision.parquet
    scripts.extract_video_frames
      data/tags/keyframes.parquet
      data/thumbnails/video/*.jpg
    scripts.tag_image_ocr
      data/tags/image_ocr.parquet
    scripts.detect_audio_music
      data/tags/audio_music.parquet
    scripts.tag_media_llm
      data/tags/media_llm.parquet
    scripts.build_core_video_audit
      data/tags/core_video_audit.json
      data/tags/core_video_audit.csv
    scripts.news_mentions
      data/tags/news_mentions.parquet
    scripts.tag_lexical
      data/tags/lexical.parquet with media/audio-description tags
    GitHub Pages
      viewer
```

Main commands:

```bash
uv run python -m scripts.ingest
uv run python -m scripts.tag_lexical
uv run python -m scripts.build_account_categories
uv run python -m scripts.archive_media
uv run python -m scripts.describe_media
uv run python -m scripts.extract_video_frames
uv run python -m scripts.tag_image_ocr
uv run python -m scripts.detect_audio_music
uv run python -m scripts.tag_media_llm --max-items 20 --budget-usd 2.00
uv run python -m scripts.build_core_video_audit
uv run python -m scripts.news_mentions --articles data/news/articles.jsonl
npm run lint
npm run typecheck
```

## Coverage

This block is regenerated by `scripts/update_readme.py` after ingest. Do not edit inside the markers.

<!-- COVERAGE:START -->

| Handle | Label | Tweets | First post | Latest post | Latest capture | Media | Videos |
| ------ | ----- | -----: | ---------- | ----------- | -------------- | ----: | -----: |
| `@USCIS` | U.S. Citizenship and Immigration Services | 768 | 2021-06-09 | 2026-05-18 | 2026-05-21 | 505 | 85 |
| `@ICEgov` | U.S. Immigration and Customs Enforcement | 479 | 2016-03-28 | 2026-05-21 | 2026-05-21 | 359 | 75 |
| `@CBP` | U.S. Customs and Border Protection | 515 | 2016-03-21 | 2026-05-20 | 2026-05-21 | 270 | 47 |
| `@RapidResponse47` | Rapid Response 47 | 459 | 2025-04-18 | 2026-05-20 | 2026-05-21 | 383 | 308 |
| `@StephenM` | Stephen Miller | 1,084 | 2021-01-20 | 2026-05-20 | 2026-05-21 | 91 | 24 |
| `@WhiteHouse` | The White House | 281 | 2025-04-16 | 2026-05-20 | 2026-05-21 | 225 | 59 |
| `@DHSgov` | Department of Homeland Security | 4,497 | 2016-01-11 | 2026-05-21 | 2026-05-21 | 2,600 | 526 |
| `@USDOL` | U.S. Department of Labor | 848 | 2025-01-14 | 2026-05-20 | 2026-05-21 | 574 | 76 |
| `@POTUS` | President of the United States | 101 | 2025-01-20 | 2026-05-19 | 2026-05-19 | 40 | 13 |
| `@GregoryKBovino` | Gregory Bovino | 1,257 | 2026-04-20 | 2026-05-20 | 2026-05-21 | 22 | 4 |
| `@PressSec` | White House Press Secretary | 137 | 2025-02-17 | 2026-05-15 | 2026-05-21 | 23 | 7 |
| `@RealTomHoman` | Thomas D. Homan | 528 | 2023-01-21 | 2024-11-12 | 2026-05-20 | 176 | 97 |
| `@_misc` | Miscellaneous (replies / quotes / retweets of non-tracked accounts) | 5,525 | 2016-01-13 | 2026-05-21 | 2026-05-21 | 2,702 | 710 |

_Generated 2026-05-21T16:42:27Z._

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
