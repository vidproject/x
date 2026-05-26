# Tagging system

The archive layers a tag overlay on top of the canonical tweet
captures. Tags describe **what a tweet contains** — a country name, a
crime word, a mugshot-shape image — never editorial judgements about
the tweet. They live in **sidecar parquets** under `data/tags/`, joined
into the viewer at load time on `tweet_id`. The canonical per-account
parquets (`data/<handle>.parquet`) are never modified by the taggers.

This design was sketched in `docs/TAGGING.md` on the
`claude/tweet-tagging-strategy-UR9sz` branch. This document is the
implementation hand-off for the layers that have actually shipped.

## Layers, status

| Layer | Source                                                                   | Output                                                      | Status                                                                                                    |
| ----- | ------------------------------------------------------------------------ | ----------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| 0     | passthrough — existing `hashtags`, `card.title/description`, URL domains | viewer columns / facets                                     | viewer pulls `tags_str` from `hashtags`; not yet broadened                                                |
| 1     | regex / structural rules on `text_resolved` (+ OCR when present)         | `data/tags/lexical.parquet`                                 | **shipped** — see `scripts/tag_lexical.py`                                                                |
| 2     | ffmpeg keyframe extraction (5 evenly-spaced frames per archived video)   | `data/tags/keyframes.parquet` (+ `data/derived/keyframes/`) | **shipped** — see `scripts/extract_video_frames.py`                                                       |
| 3m    | archived media metadata + source alt text                                | `data/tags/media_vision.parquet`                            | **shipped** — see `scripts/describe_media.py`                                                             |
| 3n    | local news-corpus exact status-URL matching                              | `data/tags/news_mentions.parquet`                           | **shipped** — see `scripts/news_mentions.py`                                                              |
| 3a    | CLIP zero-shot image labels                                              | `data/tags/image_clip.parquet`                              | not started; consumes the keyframe sidecar from Layer 2                                                   |
| 3b    | OCR for in-image text (Tesseract)                                        | `data/tags/image_ocr.parquet`                               | **shipped** — see `scripts/tag_image_ocr.py`; consumes archived photos and Layer 2 keyframes              |
| 3c    | Audio stream/music heuristic (ffprobe/ffmpeg)                            | `data/tags/audio_music.parquet`                             | **shipped** — see `scripts/detect_audio_music.py`; detects audio/no-audio/silent and tentative music      |
| 3t    | Audio transcripts (faster-whisper, optional local ASR)                   | `data/tags/transcripts.parquet`                             | **shipped** — see `scripts/transcribe_audio.py`; transcripts feed Layer 1 the same way OCR does           |
| 4     | External or curated vision/LLM review for high-value media               | optional `data/tags/media_llm.parquet` or manual queue      | not shipped as an in-repo runner; curated observations live in `data/tags/manual_media_review_queue.json` |

## Tag schema (`data/tags/lexical.parquet`)

```
tweet_id        : str
account_handle  : str
tagger_version  : str    ("lexical-v2")
tagged_at       : str    (ISO timestamp)
tags            : list<struct{
                    tag         : str         "namespace:slug"
                    tentative   : bool?       true = open to correction
                    source      : str?        "auto" | "human" | "suggestion"
                    span_start  : int64?      char offset in combined buffer
                    span_end    : int64?
                  }>
```

The tagger is idempotent: re-running `python -m scripts.tag_lexical`
rebuilds the parquet from scratch.

## Media recognition sidecar (`data/tags/media_vision.parquet`)

`scripts.describe_media` writes one row per archived media item. The
current implementation is the cheap first pass: it uses captured
metadata, source alt text, archive state, duration, dimensions, byte
count, and tweet context. It does not claim visual content that was not
already present in the capture.

The row is shaped for later video recognition work. It carries an
`input_hash`, `model`, `model_version`, `prompt_hash`, `confidence`,
`cost_estimate_usd`, `status`, `source_fields`, and `error`. Future OCR,
transcript, keyframe, CLIP, or vision-model jobs can reuse the same
sidecar, skip unchanged inputs, and enforce per-run budgets.

The sidecar emits media tags such as `media:video` and `media:photo`,
plus media-status tags such as `media-status:archived`,
`media-status:has-alt-text`, and tentative `media-status:needs-vision`.
The viewer merges those tags with the lexical tags and shows searchable
media descriptions in the table, CSV export, and sidepanel.

## External image/video review sidecars

There is no paid model runner shipped in this repository. External
vision/LLM review is intentionally kept out of CI and out of the public
viewer; reviewed observations can be folded back through
`data/tags/manual_media_review_queue.json` or through an optional sidecar
such as `data/tags/media_llm.parquet` if one is produced outside the repo.

Reviewed media descriptions should use the current production namespaces:
`video:produced`, `video:montage`, `video:text-overlay`,
`video:voiceover`, supported `genre:*` labels such as `genre:psa`,
`genre:advertisement`, `genre:recruitment`, `genre:music-video`,
`genre:war-movie`, `genre:utopian`, and `genre:dystopian`, and `video:*`
source/kind labels such as `video:bodycam`, `video:news-clip`, and
`video:speech`. `speaker:*` tags require tweet text, visible captions,
alt text, transcripts, or other explicit context identifying the speaker.

The tag `media:ai-generated` is tentative unless it is based on a
provenance signal such as C2PA, watermark text, or another explicit
AI-generation marker. A true C2PA/SynthID batch detector should be added
as a separate provenance sidecar if a usable API becomes available.

## Keyframe sidecar (`data/tags/keyframes.parquet`)

`scripts.extract_video_frames` is the Layer-2 step that ffmpeg-extracts
5 evenly-spaced JPEG keyframes from every archived video/animated-gif
and records the catalog. The JPEGs themselves live under
`data/derived/keyframes/<media_sha256>/` (gitignored — deterministic from
the archived video and the extractor version, so downstream layers
re-extract on demand if the dir is missing).

Each row carries `media_sha256` (the cache key), `release_asset_url`,
the probed video duration/dimensions, and a `frames: list<struct>` with
per-frame `index`, `timestamp_sec`, `path`, `sha256`, `width`, `height`,
`bytes`. The status column distinguishes successful extraction from
`fetch-failed`, `ffprobe-failed`, `ffmpeg-failed`, `video-too-large`,
`no-frames`, and `skipped-no-ffmpeg` — only `ok` rows are cached against
re-runs; failures are re-attempted.

This is the catalog Layer 3a (CLIP labels) and 3b (OCR) will consume:
both layers iterate frames by sha256, hash their inputs, and write their
own sidecars keyed off the frame hash. No tweet parquets are modified,
no API costs are incurred, and the work scales linearly with new
archived videos.

## Core video audit (`data/tags/core_video_audit.json`)

`scripts.build_core_video_audit` is the working queue for produced-video
research across all `core` accounts. It joins each core-account video or
animated GIF against lexical tags, manual media review, metadata vision,
paid LLM rows, audio/music detection, OCR, and keyframes. It writes a rich
JSON artifact and a spreadsheet-friendly CSV at
`data/tags/core_video_audit.csv`.

The audit assigns buckets such as `genre-experiment`, `produced-video`,
`needs-recognition`, `missing-media`, and `ordinary-video`, plus concrete
`missing_steps` like `extract-keyframes`, `detect-audio`,
`describe-with-vision`, and `assign-produced-video-genre`. This keeps the
review surface focused on music-video, dystopian, war-movie, utopian,
recruitment, advertisement, and PSA experiments before more scraping is
considered.

For GitHub-side media recovery, the audit also writes
`data/tags/core_produced_missing_tweet_ids.txt` and
`data/tags/core_produced_missing_media_ids.txt`. Those files are intended
for the `archive-media` workflow queue path, so likely produced-video and
very high-engagement core-video candidates can be fetched by GitHub Actions
without local video bandwidth.

## News-mentions sidecar (`data/tags/news_mentions.parquet`)

`scripts.news_mentions` writes one row per scanned core tweet, keyed by
`tweet_id`. Its input is a deterministic local news article export
(`data/news/articles.jsonl` by convention, or a JSON/JSONL/CSV path,
directory, or `--article-glob`). The loader handles common article
containers such as `articles`, `items`, `entries`, `response.docs`, CSV
BOMs, nested link arrays, HTML entities, and URL-encoded status links.
The matcher only counts exact status URLs for archived core tweets,
including `x.com/<handle>/status/<id>`,
`twitter.com/<handle>/status/<id>`, `x.com/i/web/status/<id>`, bare
`x.com/...` strings, `status`/`statuses`, and historical/renamed handle
variants where the tweet id is still exact.

Normal offline runs do not call a paid API or any network service. For
cheap ad-hoc discovery, `--discover-web google-news-rss` queries Google
News RSS, or `--discover-web gdelt` queries the free GDELT Doc API, for
exact status URL strings, capped by `--max-web-tweets`. Those results
are recorded with `match_type` and `matched_fields` set to the provider
query rather than a local article body, so the provenance is visible and
distinct from locally-audited exact URL evidence. If the article export
is absent and web discovery is disabled, the GitHub workflow skips the
step. Confirmed matched tweets receive `news:mentioned` and
`news:covered` tags, plus article provenance (`source`, `title`, `url`,
`published_at`, `match_type`, `matched_fields`, `matched_terms`,
confidence, and `confirmed`). Vague text/title similarity does not emit
firm news tags. The viewer loads this sidecar opportunistically and
merges those tags into the normal tag filter/search surface, with article
links in the sidepanel and CSV export.

For cheap future discovery without tagging anything, write a transparent
candidate list:

```bash
uv run python -m scripts.news_mentions --write-query-export data/news/core_tweet_news_queries.csv
```

The CSV ranks core tweets by engagement/media priority and includes exact
status-URL search strings plus a separate context query for human or
external RSS/search tooling. The context query is never used by the
tagger to infer coverage.

For later video-enrichment passes, use descriptive production labels:
`video:produced`, `video:montage`, `video:text-overlay`,
`video:voiceover`, and the relevant `genre:*` tag. These tags should be
based on observed video/audio structure: editing, music, multi-shot
sequences, visible text, and narration. Speaker attribution uses
`speaker:<title or name>`. A speaker may be tagged only when the tweet
text, source alt text, transcript/captions, or captured replies/comments
support it; otherwise write "unknown speaker" in the description or omit
the speaker field.

## Tag namespaces

See `config/tag_taxonomy.yaml` for the authoritative list. Quick map:

| Namespace       | What it labels                         | Example                        |
| --------------- | -------------------------------------- | ------------------------------ |
| `subject:`      | who/what the post is about             | `subject:detainee`             |
| `genre:`        | produced-video genre / aesthetic       | `genre:recruitment`            |
| `media:`        | attached media type or provenance      | `media:video`                  |
| `media-status:` | media archival / recognition state     | `media-status:needs-vision`    |
| `review:`       | curated review workflow markers        | `review:produced-video`        |
| `speaker:`      | evidence-supported speaker attribution | `speaker:Secretary Noem`       |
| `format:`       | structural (derived from `tweet_type`) | `format:retweet`               |
| `status:`       | availability / moderation state        | `status:copyright-removal`     |
| `frame:`        | recurring rhetorical scaffolds         | `frame:criminal`               |
| `action:`       | enforcement verbs                      | `action:deportation`           |
| `topic:`        | broad subject areas; additive          | `topic:immigration`            |
| `event:`        | named event/conflict groupings         | `event:palestine`              |
| `theme:`        | rhetorical / ideological frames        | `theme:nativism`               |
| `policy:`       | specific policy areas                  | `policy:border`                |
| `religion:`     | religion-specific subcategories        | `religion:christianity`        |
| `origin:`       | "from <country>," pattern              | `origin:Mexico`                |
| `country:`      | any contextual country mention         | `country:Mexico`               |
| `state:`        | "<place>, <state>" pattern             | `state:Texas`                  |
| `crime:`        | crime type vocabulary                  | `crime:assault`                |
| `agency:`       | mentioned enforcement-adjacent handle  | `agency:ICEgov`                |
| `slogan:`       | DHS branded phrases                    | `slogan:nice`                  |
| `phrase:`       | recurring domain terms                 | `phrase:migrant`               |
| `military:`     | military branch subtopics              | `military:navy`                |
| `news:`         | local article export cited this tweet  | `news:mentioned`               |
| `audio:`        | audio-track / sound properties         | `audio:music-likely`           |
| `video:`        | video kind / duration bucket           | `video:bodycam`                |
| `legal:`        | legal posture / litigation type        | `legal:birthright-citizenship` |
| `parody:`       | franchise-specific parody              | `parody:star-wars`             |
| `artist:`       | named cultural figures / musicians     | `artist:taylor-swift`          |

## Namespace guide — decision tree

Use this section when you are not sure which namespace to use for a new slug.
The active namespaces group into five purposes.

### "Aboutness" — what is the post about?

The five aboutness namespaces are the most commonly confused. Pick the **narrowest fit**:

| Namespace  | Use this when…                                                                                                                                                                                                       | Contrast                                                                                                                                                        |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `topic:`   | The post is broadly about a subject domain — immigration, economy, military. Additive; a post can carry multiple topic tags.                                                                                         | The coarsest level; fires even when framing is neutral.                                                                                                         |
| `theme:`   | The post deploys a specific ideological or rhetorical frame — nativism, martyrdom, border, sanctuary-cities. More precise than `topic:`; describes _how_ the subject is treated, not just _what_.                    | `topic:immigration` fires on any immigration content; `theme:nativism` fires only when the framing positions native-born Americans as threatened by immigrants. |
| `frame:`   | A recurring **structural scaffold** — currently only `frame:criminal` for the templated mugshot-reply form. Note: `frame:` is a candidate for absorption into `theme:`; new scaffolds should go into `theme:` first. | Unlike `theme:`, `frame:` implies a highly specific template match, not just rhetorical tone.                                                                   |
| `subject:` | The post features a specific **person or named entity** — a detainee, an Angel Family, a celebrity. Answers "who or what is depicted?"                                                                               | `subject:` is about presence/depiction; `topic:` is about subject domain; `theme:` is about rhetorical stance.                                                  |
| `event:`   | The post references a **named, bounded event or conflict** — a city disturbance, a named conflict (Gaza).                                                                                                            | Use `event:` only for clusters with a defined name; prefer `topic:` + `country:` for general geographic coverage.                                               |

### "Media form" — what kind of media is attached?

| Namespace       | Use this when…                                                                                                                                                                              | Contrast                                                                                                                                             |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `media:`        | Describing the **type or provenance** of an attached media item — photo, video, animated GIF, or AI-generation provenance.                                                                  | Keep pipeline state in `media-status:*` and produced-video structure in `video:*` / `genre:*`.                                                       |
| `media-status:` | Describing **archive and recognition state** — archived, described, needs OCR, needs vision, source alt text, or graphic-content flag.                                                      | State flags are separate from content labels so researchers can filter gaps directly.                                                                |
| `video:`        | Describing the **kind or duration** of a video — bodycam footage, interview, speech, news clip; or a duration bucket (short/medium/long). Only emitted when the tweet actually has a video. | `media:video` = "there is a video"; `video:bodycam` = "that video is bodycam footage."                                                               |
| `audio:`        | Describing **audio-track properties** — whether audio is present, silent, likely musical, or confirmed music.                                                                               | `audio:` describes the track; `genre:music-video` describes the video's production form.                                                             |
| `genre:`        | Describing the **produced-video genre or aesthetic** — music video, PSA, recruitment ad, war-movie style, utopian, dystopian. Applies to intentionally edited / produced videos.            | `video:` = raw footage type; `genre:` = produced aesthetic category.                                                                                 |
| `parody:`       | Identifying the **specific franchise** being parodied — `parody:star-wars`, `parody:rocky`, etc. Always co-emitted with `genre:parody`.                                                     | `genre:parody` = "this is a parody"; `parody:<franchise>` = "…of this franchise."                                                                    |
| `artist:`       | Naming a **specific cultural figure or musician** referenced in text, OCR, or transcript. Covers both the artist name and signature songs.                                                  | `artist:` requires an explicit name/song match; `audio:music` or `audio:music-likely` fires on the presence of music without identifying the artist. |

### "Origin / place" — where are people or events located?

| Namespace  | Use this when…                                                                                                                                                        | Contrast                                                                                                                       |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `origin:`  | The post attributes a person's **country of origin** using the "from \<COUNTRY\>" templated pattern, validated against a sovereign-state vocabulary.                  | Narrow: requires the attributed-origin framing.                                                                                |
| `country:` | The post **mentions** a sovereign-state name in any context — not necessarily an origin claim. Validated against the same vocabulary but with looser proximity rules. | Superset of `origin:`. Use `origin:` when the DHS templated reply form fires; `country:` when the country is merely mentioned. |
| `state:`   | The post includes a **U.S.-state place reference** in the "\<city\>, \<state\>" pattern, validated against the 50-state list.                                         | `state:` is U.S. states only; `country:` covers international.                                                                 |

### "Phrases" — recurring text patterns

| Namespace | Use this when…                                                                                                                                                                      | Contrast                                                           |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| `slogan:` | The post contains a **branded or campaign phrase** closely associated with DHS, ICE, or the administration — "MAGA", "illegal alien", "criminal illegal alien", "mass deportation". | High-specificity; each slug has a dedicated pattern.               |
| `phrase:` | The post contains a **recurring domain term** that implies topic context but is not a branded slogan — "migrant(s)", "immigrant(s)".                                                | Lower specificity than `slogan:`; use for plain domain vocabulary. |

### "People and organizations"

| Namespace  | Use this when…                                                                                                                                                              | Contrast                                                                                                 |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `speaker:` | The post has evidence-supported **speaker attribution** — the speaker is identified from tweet text, captions, alt text, or transcript. Do not guess from faces or setting. | Open-ended; slug is the speaker's name or title (e.g. `speaker:Secretary Noem`).                         |
| `agency:`  | The post **mentions** an enforcement-adjacent account handle in its `mentions[]` field — ICEgov, CBP, DHSgov, HSI_HQ, etc.                                                  | Distinct from the post's own `account_handle`; lets you find tweets _about_ ICE without being _by_ ICE.  |
| `subject:` | As above (Aboutness): the post **features** a specific person or named entity.                                                                                              | `speaker:` = who is speaking; `agency:` = who is mentioned by handle; `subject:` = who/what is depicted. |

### Remaining namespaces (structural / metadata)

| Namespace   | Use this when…                                                                                                                    |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `format:`   | Describing tweet structure derived from `tweet_type` — retweet, quote, reply.                                                     |
| `status:`   | Recording availability or moderation state — unavailable, copyright-removal, community-note.                                      |
| `action:`   | An enforcement verb fires — detention, deportation, self-deportation, report-immigrants.                                          |
| `legal:`    | The post invokes a legal posture — birthright-citizenship debate, criminal prosecution, civil lawsuit.                            |
| `military:` | Narrowing a `topic:military` hit to a specific branch — army, navy, air-force, space-force, marines, coast-guard, national-guard. |
| `religion:` | Narrowing a `theme:religion` hit to a specific faith — currently only `religion:christianity`.                                    |
| `crime:`    | A crime-type vocabulary word fires — rape, murder, DUI, trafficking, etc.                                                         |
| `news:`     | An external news article's exact status URL matches this tweet — `news:mentioned` and `news:covered`.                             |

## The `topic:immigration` default

The corpus is overwhelmingly about immigration. Trying to infer
relevance from sparse tweet text (image-heavy posts, three-word
slogans) costs recall. So:

- Every tweet from a tracked-tier account (`core` / `government` /
  `officials`) is tagged `topic:immigration` when it has an explicit
  immigration signal. Sparse tracked-account posts still get a tentative
  `topic:immigration` only when no other broad `topic:*` signal fires.
  Topics are additive: a labor/immigration post can carry both
  `topic:economy` and `topic:immigration`.
- The tag is emitted **confirmed** when the text (or OCR) carries any
  explicit immigration signal: a `frame:`, `action:`, `origin:`,
  `country:`, `theme:border/sanctuary/worksite/nativism`, a known agency
  handle, the `slogan:` / `phrase:` phrases, or one of a small set of plain
  keywords (`immigration`, `migrant`, `asylum`, `illegal alien`, `the
border`, `border patrol`, bare `ICE`/`CBP`).
- Without an explicit signal, the tag is emitted **tentative** —
  visually de-emphasized in the viewer and open to correction via the
  suggestion flow.

On the live corpus this splits 50/50: ~1,589 confirmed and ~1,615
tentative `topic:immigration` tags across 3,204 tagged tweets.

`_misc` / public-tier authors don't get the default at all; their
tweets only earn `topic:immigration` if an explicit signal fires.

## Military hierarchy

`topic:military` is broad and additive. It fires on explicit armed
services language, combatant commands, DoD / Pentagon references,
service academies, deployments, troops / service members, carrier
strike groups, aircraft carriers, USS / USNS ship references, CVN hull
numbers, and similar high-signal military terms.

Military tags are narrower children. For example, `military:navy` covers
Navy / sailor language and naval carrier cues such as "Carrier Strike
Group," "aircraft carrier," "USS Nimitz," and "CVN 68"; those narrower
tags automatically imply `topic:military`. Known branch handles such as
`@USCGAcademy` also emit the relevant military tag. Combatant-command
agency tags such as `agency:Southcom`, `agency:CENTCOM`, and
`agency:DeptofWar` also imply `topic:military`.

## Unavailable / removed posts

When X returns a tombstone for a tweet, the extension records an
`unavailable_tweets` event in the raw capture. Ingest folds that event
onto the existing canonical row as `unavailable_detected_at`,
`unavailable_reason`, `unavailable_text`, and
`unavailable_source_url`. The lexical tagger emits `status:unavailable`
for those rows, plus `status:copyright-removal` when the tombstone text
or reason mentions copyright / DMCA.

## OCR awareness

The lexical tagger reads `data/tags/image_ocr.parquet` if it exists
and concatenates each tweet's OCR text to the tweet body before the
regex pass. Any tag that fires on OCR text counts the same as a tag
that fires on the tweet body — for both emission and for promoting
`topic:immigration` out of tentative. The OCR sidecar's expected
schema is documented inline in `scripts/tag_lexical.py::load_ocr_map`.

This means a graphic that reads "DEPORT THE INVASION" over a flag,
with no text in the tweet body, will earn `action:deportation` and a
confirmed `topic:immigration` as soon as Layer 3b runs and writes the
OCR parquet. No change to the tagger is needed at that point.

## Account categories

`config/accounts.yaml` partitions tracked authors into:

- `core` — the seed federal-agency / WH-principal handles.
- `government` — other federal agencies.
- `officials` — federal executive officials in their personal capacity.
- `public_figures` — non-federal officials (senators, governors, …).

Everything else falls through to `public`, which is the implicit
category for `_misc.parquet` (replies / quotes / RTs the tracked
accounts touch). The viewer's **Categories** header dropdown filters
the table by category.

## Threading

Replies are grouped into threads by `conversation_id`. The viewer
displays them in two tiers:

- **Self-replies** (same `account_handle` as the master): inline
  collapsible under the master, defaulted to collapsed. This is the
  "DHS continues its own thread" case worth reading in place.
- **Everything else** (replies from other tracked handles, plus
  random `_misc` chatter): not inlined. The master row carries a
  passive `↪ N others` badge; clicking the master opens the
  sidepanel, which renders an **Other replies** section with the
  full list.

This keeps the table from getting spammed by hundreds of random
reactions to a viral DHS tweet while still preserving access to
every captured reply.

## Suggestion flow

Tag corrections come in as GitHub Discussions in the
`tag-suggestions` category. The viewer's sidepanel has a "Suggest a
tag change" button that opens a prefilled discussion with the
tweet's id, url, account, and a YAML stub for `add:` / `remove:` /
`rationale:`.

A maintainer with PAT write access reviews each open discussion and,
on accept, edits the relevant per-tweet override (planned: a small
overlay file consulted by the lexical tagger on its next run) and
closes the discussion. The extension-side polling + one-click apply
is a follow-up — the protocol it'll speak is the YAML stub above.

## Manual media-review queue

`data/tags/manual_media_review_queue.json` holds tweet-id / media-path
items where a direct inspection of the archived asset surfaced visual
signal that today's deterministic pipeline cannot recover — image-only
text overlays, news-card chyrons, composite mugshot graphics, recruitment
montages — alongside the candidate tags a vision/OCR layer would
emit. The queue is hand-curated, additive, and never mutates the
canonical parquets or the lexical / media_vision sidecars; it exists
so the future Layer-3a/3b/3c jobs have a small ground-truth set to
sanity-check their outputs against, and so pipeline gaps stay visible
between OCR/CLIP runs.

## Run order

```
# 1. Capture flows in via the Firefox extension as today.
# 2. After every push to master:
uv run python -m scripts.ingest          # canonical parquets + manifest
uv run python -m scripts.tag_lexical     # data/tags/lexical.parquet

# 3. After media archival:
uv run python -m scripts.describe_media         # data/tags/media_vision.parquet
uv run python -m scripts.extract_video_frames   # data/tags/keyframes.parquet (ffmpeg required)
uv run python -m scripts.news_mentions --articles data/news/articles.jsonl

# 4. After frame/OCR/audio passes:
uv run python -m scripts.tag_image_ocr          # data/tags/image_ocr.parquet
uv run python -m scripts.detect_audio_music     # data/tags/audio_music.parquet
uv run --group asr python -m scripts.transcribe_audio  # data/tags/transcripts.parquet
# uv run python -m scripts.tag_image_clip       # data/tags/image_clip.parquet
# 5. Re-run scripts/tag_lexical so OCR + transcript text feed Layer-1 rules.
```

The viewer fetches the shipped sidecars in `data/tags/` on load and
gracefully degrades when one is missing.
