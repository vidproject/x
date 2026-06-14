# Immigration Social Media Archive

A public, searchable archive of immigration-related posts from US federal X (Twitter) accounts — DHS, ICE, CBP, USCIS, the White House, and other agencies and principals.

[Open the searchable archive](https://vidproject.github.io/x/)

This repository contains the archive and the tooling around it: browser capture, raw JSON, canonical Parquet files, media archival, annotation sidecars, and the static viewer published through GitHub Pages.

**How capture works.** A browser extension reads the public X web timeline as you view it, normalizes what the site serves, and commits raw JSON to this repo. There is no X API and no X Developer Agreement — capture only uses the public web data the browser already received. A Python pipeline (`scripts/*`) then turns that raw JSON into per-account Parquet files, copies attached media into GitHub Releases assets, derives optional tag sidecars (OCR, transcripts, vision descriptions, keyframes), and builds a static viewer.

**The viewer** is a single-page site published to GitHub Pages. It lets anyone full-text search, filter, chart, and export the archive in the browser, with no backend.

The viewer is published by `.github/workflows/pages.yml` on every push to `master` that touches `index.html`, `viewer/**`, `data/**`, `extension.zip`, or `extension-chrome.zip`. Repo settings need **Pages -> Build and deployment -> Source: GitHub Actions** for the workflow to actually deploy.

The extension zips are rebuilt automatically by the `build-extension` workflow whenever the extension changes, committed back to the repo, and published by Pages:

- Firefox: **[vidproject.github.io/x/extension.zip](https://vidproject.github.io/x/extension.zip)**
- Chrome: **[vidproject.github.io/x/extension-chrome.zip](https://vidproject.github.io/x/extension-chrome.zip)**

## Scope

The tracked accounts below are generated from `config/accounts.yaml` by `scripts/update_readme.py`. Do not edit inside the markers.

<!-- CORE_ACCOUNTS:START -->

**Core federal immigration, enforcement, and White House accounts:**

- `@DHSgov` — Department of Homeland Security
- `@ICEgov` — U.S. Immigration and Customs Enforcement
- `@CBP` — U.S. Customs and Border Protection
- `@USCIS` — U.S. Citizenship and Immigration Services
- `@WhiteHouse` — The White House
- `@PressSec` — White House Press Secretary
- `@POTUS` — President of the United States
- `@USDOL` — U.S. Department of Labor
- `@RapidResponse47` — Rapid Response 47
- `@StephenM` — Stephen Miller
- `@GregoryKBovino` — Gregory Bovino
- `@RealTomHoman` — Thomas D. Homan
- `@SecMullinDHS` — Secretary Markwayne Mullin
- `@USBPChief` — U.S. Border Patrol Chief
- `@CBPAMO` — CBP Air and Marine Operations
- `@USCG` — U.S. Coast Guard
- `@USCISJoe` — USCIS Director Joseph B. Edlow
- `@Sonderling47` — Acting Secretary Keith Sonderling
- `@OFOEAC` — CBP Office of Field Operations Executive Assistant Commissioner
- `@USBPChiefELC` — El Centro Sector Border Patrol
- `@HSI_HQ` — Homeland Security Investigations
- `@FPSDHS` — Federal Protective Service
- `@CBPCommissioner` — CBP Commissioner Rodney Scott
- `@CBPJobs` — CBP Jobs
- `@EROAtlanta` — ICE Atlanta
- `@EROBaltimore` — ICE Baltimore
- `@ERO__Phoenix` — ICE ERO Phoenix
- `@EROLosAngeles` — ICE ERO Los Angeles
- `@EROBoston` — ICE ERO Boston
- `@EROBuffalo` — ERO Buffalo
- `@ERODenver` — ICE Denver
- `@ERODetroit` — ICE Detroit
- `@EROElPaso` — ERO El Paso
- `@EroHarlingen` — ICE Harlingen
- `@EROHouston` — ICE Houston
- `@EROMiami` — ERO Miami
- `@ERONewark` — ICE ERO Newark
- `@ERONewOrleans` — ICE ERO New Orleans
- `@ERONewYork` — ERO New York City
- `@EROPhiladelphia` — ERO Philadelphia
- `@EROSaintPaul` — ICE Saint Paul
- `@EROSaltLakeCity` — ICE ERO Salt Lake City
- `@EROSanAntonio` — ICE San Antonio
- `@EROSanDiego` — ICE San Diego
- `@EROSanFrancisco` — ERO San Francisco
- `@EROSeattle` — ICE Seattle
- `@EROWashington` — ICE Washington D.C.

**Other government accounts:**

- `@TSA` — Transportation Security Administration
- `@fema` — Federal Emergency Management Agency
- `@Readygov` — Ready.gov
- `@SecretService` — U.S. Secret Service
- `@CISAgov` — Cybersecurity and Infrastructure Security Agency
- `@DHSBlueCampaign` — DHS Blue Campaign
- `@DeptofWar` — Department of War
- `@DOWResponse` — DOW Rapid Response
- `@StateDept` — Department of State
- `@FBI` — Federal Bureau of Investigation
- `@TheJusticeDept` — U.S. Department of Justice
- `@ATFHQ` — ATF Headquarters

**Federal officials (personal accounts):**

- `@SecWar` — Secretary of War Pete Hegseth
- `@PeteHegseth` — Pete Hegseth
- `@SecRubio` — Secretary Marco Rubio
- `@FBIDirectorKash` — FBI Director Kash Patel
- `@AGPamBondi` — Attorney General Pamela Bondi

The archive also preserves the replies, quotes, retweets, and public accounts that appear in captured threads (consolidated into `data/_misc.parquet`).

<!-- CORE_ACCOUNTS:END -->

## Viewer

The viewer starts with `data/catalog.parquet`, a lightweight full-archive catalog for global search, tags, filters, charts, and the date histogram without downloading every full account Parquet. `data/catalog.json` is only the tiny summary/poster map. Full tweet records hydrate lazily as rows come into view, are opened, or are reached from a shared link. Click the lightning button only to download every account Parquet listed in `data/manifest.json` for fast full-record browsing. Search runs in the browser. Filters support account, account category, date, tweet type, media type, tag, and column values. The URL updates with the current view, so filtered pages can be shared.

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

Media crawl follows attached media from the captured tweet data and stores archived assets in GitHub Releases. Large handles are sharded across per-handle releases: `media-<handle>` first, then `media-<handle>-0002`, `media-<handle>-0003`, and so on. The canonical Parquet row records the Release URL only after upload succeeds or after the asset is found in a real release listing.

## Tags

Tags are downstream annotations. They are not written into the canonical tweet Parquets.

Current sidecars:

- `data/tags/lexical.parquet`: regex and structural tags from `scripts/tag_lexical.py`.
- `data/tags/media_vision.parquet`: media descriptions from `scripts/describe_media.py`.
- `data/tags/keyframes.parquet`: video keyframe metadata and tiny poster thumbnails from `scripts/extract_video_frames.py`.
- `data/tags/photo_thumbnails.parquet`: tiny downscaled photo thumbnails (under `data/thumbnails/photo/`) from `scripts/extract_photo_thumbnails.py`, so archived photos are locally inspectable for `media:needs-vision` review.
- `data/tags/image_ocr.parquet`: Tesseract OCR text from archived photos and extracted video keyframes from `scripts/tag_image_ocr.py`.
- `data/tags/audio_music.parquet`: ffmpeg-only audio stream/music-likelihood tags from `scripts/detect_audio_music.py`.
- `data/tags/transcripts.parquet`: local, free speech-to-text of archived videos from `scripts/detect_audio_music.py`'s sibling `scripts/transcribe_audio.py` (optional `faster-whisper`; no API keys).
- `data/tags/news_mentions.parquet`: exact X/Twitter status-URL mentions of core tweets in a local news article export from `scripts/news_mentions.py`.
- `data/tags/core_quote_tweets.json` / `.csv`: archived quote tweets that target core-account tweets from `scripts.build_core_quote_index`.
- `data/account_categories.json`: corpus-wide public figure / government / official categories from `scripts/build_account_categories.py`.
- `config/tag_overrides.yaml`: editor-confirmed tags for cases the capture layer cannot prove from canonical fields alone.

The viewer joins sidecars by `tweet_id`. Missing sidecars are tolerated.

Tag namespaces use the form `namespace:slug`. The namespace is the broad category. The slug is the subtype. The viewer groups tag filters by namespace so a user can filter whole categories or specific subtypes.

The immigration-reporting tag is `action:report-immigrants`. Generic non-immigration reporting can use other `action:report-*` tags later.

## Media Recognition

`scripts.describe_media` is the first recognition layer. It is deliberately cheap. It uses archived media metadata, source alt text, dimensions, duration, byte count, tweet context, and curated manual media-review observations. It does not infer visual content from pixels unless a reviewed observation or later OCR/vision sidecar supplies that evidence.

Each media row carries cache and provenance fields: `input_hash`, `model`, `model_version`, `prompt_hash`, `confidence`, `cost_estimate_usd`, `status`, `source_fields`, and `error`.

This gives later OCR, transcript, keyframe, CLIP, audio, or external analysis jobs a stable place to write results without changing canonical capture data. Items that need deeper inspection get tentative `media:needs-vision`.

`scripts.extract_video_frames` pulls bounded keyframes from archived videos and also writes a tiny 96px JPEG poster under `data/thumbnails/video/` for the viewer. The table uses those posters before falling back to larger frame paths, so video thumbnails are automatic and cheap to load.

`scripts.tag_image_ocr` is the first true pixel-reading image layer. It OCRs archived photos and the keyframes extracted in the same workflow run, then `scripts.tag_lexical` imports that recovered text so image-only slogans, agency names, religious language, and other text-overlay tags are searchable and filterable.

`scripts.detect_audio_music` is the first audio pass. It uses ffprobe/ffmpeg only: detect whether an archived video has audio, decode a short mono sample, compute simple energy/zero-crossing features, and emit conservative `audio:has-audio`, `audio:no-audio`, `audio:silent`, and tentative `audio:music-likely` tags. The lexical layer still uses video text and direct replies as additional cheap context when people explicitly reference the song, soundtrack, or background music.

`scripts.transcribe_audio` is the first true speech layer (Layer 3c). It fetches each archived video, decodes a bounded mono sample with ffmpeg, and transcribes it with a local, free recognizer (`faster-whisper`) — no paid API and no credentials. The recognizer is optional: it is imported lazily and the run records `skipped-no-asr` when it is not installed (`uv sync --group asr` installs it; CI runs it in `archive-media`). `scripts.tag_lexical` folds the recovered transcript text into its regex pass exactly like OCR, so spoken slogans, agency names, and other speech become searchable and taggable.

`media:ai-generated` is emitted by `scripts.tag_lexical` from explicit, high-precision textual signals (e.g. "AI-generated", "deepfake", "made with AI", "Midjourney", "synthetic media") in the tweet body, OCR, or transcript. It is tentative by default — per `docs/TAGGING.md` the tag is only firm with C2PA/watermark provenance, which this layer does not have — and bare "AI" mentions deliberately do not fire it.

`data/tags/produced_likely_unprocessed_{tweet,media}_ids.txt` lists archived videos that carry a produced/genre text signal but have no keyframes yet, so keyframe coverage can be widened for the likely-produced set without processing the whole archive. Run `uv run python -m scripts.extract_video_frames --tweet-ids-file data/tags/produced_likely_unprocessed_tweet_ids.txt` (or dispatch `archive-media` with it) to extract just those.

External LLM review is intentionally kept outside this repository. Curated results can be folded back through `data/tags/manual_media_review_queue.json` or another reviewed sidecar without storing provider credentials or running paid model calls from CI.

`scripts.build_core_video_audit` joins core-account videos against keyframes, OCR, audio, metadata vision, manual-review, and lexical tags. It writes `data/tags/core_video_audit.json` and `data/tags/core_video_audit.csv`, prioritized for produced-video and genre review (`genre:music-video`, `genre:dystopian`, `genre:war-movie`, `genre:utopian`, recruitment, advertisement, and PSA).

The audit also emits queue files for GitHub-side recovery of core-account videos whose media is still missing: `data/tags/core_produced_missing_tweet_ids.txt` and `data/tags/core_produced_missing_media_ids.txt`. The files preserve audit priority order while the workflow caps each run, so the backlog drains through GitHub without using local bandwidth.

`scripts.build_core_quote_index` builds a neutral review index of archived quote tweets that target core-account tweets. It writes `data/tags/core_quote_tweets.json` and `data/tags/core_quote_tweets.csv`, including an author summary and optional focus handles for targeted follow-up.

## News Mentions

`scripts.news_mentions` checks whether archived core tweets are cited by news coverage using a deterministic local article export when one exists. It accepts JSON, JSONL, or CSV records with fields such as `url`, `title`, `description`, `body`, `content`, or `text`, then matches exact `x.com/<handle>/status/<tweet_id>`, `twitter.com/<handle>/status/<tweet_id>`, and `x.com/i/web/status/<tweet_id>` URLs. Tests and normal offline runs can still use `--discover-web none` to avoid network. For cheap discovery, run `uv run python -m scripts.news_mentions --discover-web google-news-rss --max-web-tweets 100 --matched-only`; this checks Google News RSS only for core tweets missing from the local article export. Use `--discover-web gdelt` to query GDELT instead.

The ingest workflow now defaults to `google-news-rss`, capped by `news_max_web_tweets`, and uses `data/news/articles.jsonl` as an optional first-pass corpus. Mentioned tweets receive `news:mentioned` and `news:covered` tags that the viewer loads like other optional sidecars.

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
      GitHub Release asset shards
      data/*.parquet media URLs
    scripts.describe_media
      data/tags/media_vision.parquet
    scripts.extract_video_frames
      data/tags/keyframes.parquet
      data/thumbnails/video/*.jpg
    scripts.extract_photo_thumbnails
      data/tags/photo_thumbnails.parquet
      data/thumbnails/photo/*.jpg
    scripts.tag_image_ocr
      data/tags/image_ocr.parquet
    scripts.detect_audio_music
      data/tags/audio_music.parquet
    scripts.transcribe_audio
      data/tags/transcripts.parquet
    scripts.build_core_video_audit
      data/tags/core_video_audit.json
      data/tags/core_video_audit.csv
    scripts.build_core_quote_index
      data/tags/core_quote_tweets.json
      data/tags/core_quote_tweets.csv
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
uv run python -m scripts.extract_photo_thumbnails
uv run python -m scripts.tag_image_ocr
uv run python -m scripts.detect_audio_music
uv run --group asr python -m scripts.transcribe_audio
uv run python -m scripts.build_core_video_audit
uv run python -m scripts.build_core_quote_index
uv run python -m scripts.news_mentions --articles data/news/articles.jsonl
npm run lint
npm run typecheck
```

## Coverage

This block is regenerated by `scripts/update_readme.py` after ingest. Do not edit inside the markers.

<!-- COVERAGE:START -->

| Handle | Label | Tweets | First post | Latest post | Latest capture | Media | Videos |
| ------ | ----- | -----: | ---------- | ----------- | -------------- | ----: | -----: |
| `@DHSgov` | Department of Homeland Security | 5,489 | 2016-01-11 | 2026-06-11 | 2026-06-14 | 3,441 | 796 |
| `@ICEgov` | U.S. Immigration and Customs Enforcement | 2,098 | 2016-03-28 | 2026-06-09 | 2026-06-14 | 1,789 | 337 |
| `@CBP` | U.S. Customs and Border Protection | 1,297 | 2016-03-21 | 2026-05-29 | 2026-06-14 | 1,224 | 173 |
| `@USCIS` | U.S. Citizenship and Immigration Services | 1,218 | 2021-04-12 | 2026-06-02 | 2026-06-14 | 1,110 | 95 |
| `@WhiteHouse` | The White House | 1,503 | 2025-01-24 | 2026-06-11 | 2026-06-13 | 1,406 | 346 |
| `@PressSec` | White House Press Secretary | 488 | 2025-01-24 | 2026-05-29 | 2026-06-13 | 112 | 34 |
| `@POTUS` | President of the United States | 164 | 2025-01-20 | 2026-05-29 | 2026-06-13 | 114 | 36 |
| `@USDOL` | U.S. Department of Labor | 1,018 | 2024-07-25 | 2026-06-05 | 2026-06-14 | 717 | 94 |
| `@RapidResponse47` | Rapid Response 47 | 1,533 | 2025-01-29 | 2026-06-10 | 2026-06-13 | 1,399 | 1,097 |
| `@StephenM` | Stephen Miller | 1,226 | 2021-01-20 | 2026-05-30 | 2026-06-13 | 100 | 26 |
| `@GregoryKBovino` | Gregory Bovino | 1,350 | 2026-04-20 | 2026-06-02 | 2026-06-13 | 26 | 6 |
| `@RealTomHoman` | Thomas D. Homan | 553 | 2023-01-21 | 2026-05-12 | 2026-06-13 | 182 | 97 |
| `@SecMullinDHS` | Secretary Markwayne Mullin | 72 | 2026-03-24 | 2026-05-30 | 2026-06-13 | 70 | 23 |
| `@USBPChief` | U.S. Border Patrol Chief | 69 | 2018-11-26 | 2026-06-10 | 2026-06-14 | 85 | 22 |
| `@CBPAMO` | CBP Air and Marine Operations | 38 | 2020-12-15 | 2026-05-29 | 2026-06-14 | 53 | 15 |
| `@USCG` | U.S. Coast Guard | 769 | 2016-03-21 | 2026-05-22 | 2026-06-14 | 1,144 | 213 |
| `@USCISJoe` | USCIS Director Joseph B. Edlow | 28 | 2025-07-23 | 2026-05-22 | 2026-06-11 | 21 | 13 |
| `@Sonderling47` | Acting Secretary Keith Sonderling | 76 | 2025-03-14 | 2026-05-30 | 2026-06-13 | 66 | 27 |
| `@OFOEAC` | CBP Office of Field Operations Executive Assistant Commissioner | 861 | 2020-02-25 | 2026-05-25 | 2026-06-13 | 1,051 | 116 |
| `@USBPChiefELC` | El Centro Sector Border Patrol | 33 | 2021-03-16 | 2026-05-26 | 2026-06-14 | 36 | 7 |
| `@HSI_HQ` | Homeland Security Investigations | 126 | 2021-12-22 | 2026-05-25 | 2026-06-14 | 77 | 13 |
| `@TSA` | Transportation Security Administration | 145 | 2016-03-28 | 2026-05-26 | 2026-06-14 | 110 | 21 |
| `@fema` | Federal Emergency Management Agency | 693 | 2018-08-21 | 2026-05-25 | 2026-06-14 | 872 | 44 |
| `@Readygov` | Ready.gov | 3 | 2022-03-21 | 2024-12-27 | 2026-06-14 | 3 | 0 |
| `@SecretService` | U.S. Secret Service | 35 | 2016-07-25 | 2026-05-25 | 2026-05-26 | 44 | 11 |
| `@CISAgov` | Cybersecurity and Infrastructure Security Agency | 39 | 2018-10-17 | 2026-05-23 | 2026-06-14 | 34 | 1 |
| `@DHSBlueCampaign` | DHS Blue Campaign | 30 | 2018-08-30 | 2026-02-17 | 2026-06-14 | 36 | 1 |
| `@FPSDHS` | Federal Protective Service | 28 | 2022-08-12 | 2026-05-11 | 2026-06-14 | 14 | 0 |
| `@CBPCommissioner` | CBP Commissioner Rodney Scott | 148 | 2021-01-22 | 2026-06-10 | 2026-06-14 | 97 | 11 |
| `@CBPJobs` | CBP Jobs | 181 | 2017-12-06 | 2026-05-28 | 2026-06-14 | 164 | 17 |
| `@EROAtlanta` | ICE Atlanta | 21 | 2020-12-08 | 2025-04-28 | 2026-06-14 | 6 | 0 |
| `@EROBaltimore` | ICE Baltimore | 5 | 2025-02-25 | 2026-02-03 | 2026-05-22 | 8 | 1 |
| `@ERO__Phoenix` | ICE ERO Phoenix | 26 | 2024-05-10 | 2025-04-15 | 2026-06-14 | 3 | 0 |
| `@EROLosAngeles` | ICE ERO Los Angeles | 99 | 2021-04-22 | 2026-04-29 | 2026-06-14 | 76 | 1 |
| `@EROBoston` | ICE ERO Boston | 85 | 2022-11-29 | 2026-05-13 | 2026-06-14 | 71 | 0 |
| `@EROBuffalo` | ERO Buffalo | 59 | 2021-04-01 | 2025-02-25 | 2026-06-14 | 20 | 1 |
| `@ERODenver` | ICE Denver | 139 | 2020-12-09 | 2026-05-07 | 2026-06-14 | 71 | 0 |
| `@ERODetroit` | ICE Detroit | 38 | 2022-05-23 | 2025-03-25 | 2026-06-14 | 11 | 0 |
| `@EROElPaso` | ERO El Paso | 1 | 2025-06-26 | 2025-06-26 | 2026-05-22 | 1 | 0 |
| `@EroHarlingen` | ICE Harlingen | 84 | 2021-11-09 | 2025-06-26 | 2026-06-14 | 17 | 0 |
| `@EROHouston` | ICE Houston | 92 | 2021-11-04 | 2026-03-31 | 2026-06-14 | 29 | 2 |
| `@EROMiami` | ERO Miami | 85 | 2022-10-04 | 2026-06-10 | 2026-06-14 | 26 | 0 |
| `@ERONewark` | ICE ERO Newark | 93 | 2021-04-16 | 2026-05-11 | 2026-06-14 | 25 | 0 |
| `@ERONewOrleans` | ICE ERO New Orleans | 135 | 2020-12-15 | 2026-06-03 | 2026-06-14 | 65 | 0 |
| `@ERONewYork` | ERO New York City | 9 | 2024-07-01 | 2026-04-20 | 2026-06-14 | 10 | 1 |
| `@EROPhiladelphia` | ERO Philadelphia | 11 | 2025-01-15 | 2026-05-11 | 2026-05-22 | 12 | 0 |
| `@EROSaintPaul` | ICE Saint Paul | 63 | 2021-12-10 | 2025-07-27 | 2026-06-14 | 9 | 0 |
| `@EROSaltLakeCity` | ICE ERO Salt Lake City | 76 | 2022-11-16 | 2026-04-07 | 2026-06-14 | 57 | 1 |
| `@EROSanAntonio` | ICE San Antonio | 86 | 2021-09-27 | 2026-04-01 | 2026-06-14 | 14 | 2 |
| `@EROSanDiego` | ICE San Diego | 96 | 2020-12-04 | 2026-05-07 | 2026-06-14 | 53 | 1 |
| `@EROSanFrancisco` | ERO San Francisco | 24 | 2020-12-03 | 2026-05-12 | 2026-06-14 | 15 | 0 |
| `@EROSeattle` | ICE Seattle | 20 | 2025-02-14 | 2026-04-16 | 2026-05-22 | 25 | 1 |
| `@EROWashington` | ICE Washington D.C. | 82 | 2021-03-15 | 2026-04-08 | 2026-06-14 | 46 | 0 |
| `@DeptofWar` | Department of War | 671 | 2016-07-25 | 2026-06-12 | 2026-06-14 | 1,131 | 301 |
| `@SecWar` | Secretary of War Pete Hegseth | 64 | 2025-02-11 | 2026-05-30 | 2026-06-13 | 99 | 43 |
| `@PeteHegseth` | Pete Hegseth | 104 | 2025-04-29 | 2026-06-08 | 2026-06-13 | 12 | 2 |
| `@DOWResponse` | DOW Rapid Response | 496 | 2025-03-01 | 2026-06-11 | 2026-06-13 | 462 | 380 |
| `@StateDept` | Department of State | 663 | 2021-01-13 | 2026-05-28 | 2026-06-14 | 673 | 559 |
| `@SecRubio` | Secretary Marco Rubio | 371 | 2025-01-22 | 2026-05-26 | 2026-06-13 | 463 | 57 |
| `@FBI` | Federal Bureau of Investigation | 659 | 2018-10-25 | 2026-06-02 | 2026-06-14 | 838 | 40 |
| `@FBIDirectorKash` | FBI Director Kash Patel | 25 | 2025-03-27 | 2026-05-26 | 2026-06-13 | 16 | 6 |
| `@TheJusticeDept` | U.S. Department of Justice | 664 | 2020-12-10 | 2026-06-11 | 2026-06-14 | 781 | 144 |
| `@AGPamBondi` | Attorney General Pamela Bondi | 15 | 2025-03-31 | 2026-03-30 | 2026-06-13 | 1 | 1 |
| `@ATFHQ` | ATF Headquarters | 2 | 2021-04-15 | 2026-05-12 | 2026-06-14 | 2 | 0 |
| `@_misc` | Miscellaneous (replies / quotes / retweets of non-tracked accounts) | 7,190 | 2016-01-13 | 2026-06-12 | 2026-06-14 | 3,524 | 872 |

_Generated 2026-06-14T02:27:30Z._

<!-- COVERAGE:END -->

### Coverage gaps

The block below is regenerated from the data by `scripts/update_readme.py`. Do not edit inside the markers.

<!-- GAPS:START -->

<details>
<summary>Known coverage gaps and caveats</summary>

- **Media not yet archived to GitHub Releases:** 10,451 of 24,269 media items are still `pending` (13,817 archived, 57%). Until a media item is uploaded its row keeps only the original (expiring) X CDN URL.
- **Failed media archives:** 1 media items have `archive_status = failed` after retries and may be unrecoverable.
- **Long-form tweets awaiting full text:** 112 rows are `is_truncated` (captured as a 280-character head) and queued for detail-page refetch.
- **Tweets X no longer serves:** 2 archived rows are now flagged unavailable (suspended, deleted, or otherwise removed upstream); the captured copy is retained.
- **Deletions detected after capture:** 2,514 tweets were seen live and later detected as deleted; the archived copy is kept.
- **Accounts with no recent posts (>30d):** `@Readygov` (533d), `@EROBuffalo` (473d), `@ERODetroit` (445d), `@ERO__Phoenix` (424d), `@EROAtlanta` (411d), and 22 more. These may be quiet accounts or stalled captures.

_Generated 2026-06-14T02:27:30Z._

</details>

<!-- GAPS:END -->

## Data Rules

- Canonical Parquet rows mirror what X served at capture time.
- Parse failures go to `raw/_quarantine/`.
- Parquet rewrites are atomic.
- Release uploads must succeed, or the asset must be found in a release listing, before a row records the asset URL.
- Credentials stay out of the repo.
- Annotation is reversible and separate from capture.

## Documentation

- [Data schema](docs/SCHEMA.md)
- [Tagging system](docs/TAGGING.md)

## License

Property of the University of California.
