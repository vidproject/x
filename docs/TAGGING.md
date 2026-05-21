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

| Layer | Source                                                                   | Output                                                      | Status                                                                                                                                   |
| ----- | ------------------------------------------------------------------------ | ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| 0     | passthrough — existing `hashtags`, `card.title/description`, URL domains | viewer columns / facets                                     | viewer pulls `tags_str` from `hashtags`; not yet broadened                                                                               |
| 1     | regex / structural rules on `text_resolved` (+ OCR when present)         | `data/tags/lexical.parquet`                                 | **shipped** — see `scripts/tag_lexical.py`                                                                                               |
| 2     | ffmpeg keyframe extraction (5 evenly-spaced frames per archived video)   | `data/tags/keyframes.parquet` (+ `data/derived/keyframes/`) | **shipped** — see `scripts/extract_video_frames.py`                                                                                      |
| 3m    | archived media metadata + source alt text                                | `data/tags/media_vision.parquet`                            | **shipped** — see `scripts/describe_media.py`                                                                                            |
| 3n    | local news-corpus exact status-URL matching                              | `data/tags/news_mentions.parquet`                           | **shipped** — see `scripts/news_mentions.py`                                                                                             |
| 3a    | CLIP zero-shot image labels                                              | `data/tags/image_clip.parquet`                              | not started; consumes the keyframe sidecar from Layer 2                                                                                  |
| 3b    | OCR for in-image text (Tesseract → PaddleOCR fallback)                   | `data/tags/image_ocr.parquet`                               | not started; consumes Layer 2 keyframes; **the lexical tagger already integrates with it via `load_ocr_map()` once the parquet appears** |
| 3c    | Audio transcripts (whisper.cpp / faster-whisper)                         | `data/tags/audio_transcript.parquet`                        | not started; transcripts feed Layer 1 the same way OCR does                                                                              |
| 4     | vision LLM for high-value items (budget-gated)                           | merged into 1 + 3a namespaces                               | not started                                                                                                                              |

## Tag schema (`data/tags/lexical.parquet`)

```
tweet_id        : str
account_handle  : str
tagger_version  : str    ("lexical-v1")
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

The sidecar emits media tags such as `media:video`, `media:photo`,
`media:archived`, `media:has-alt-text`, and tentative
`media:needs-vision`. The viewer merges those tags with the lexical tags
and shows searchable media descriptions in the table, CSV export, and
sidepanel.

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

## News-mentions sidecar (`data/tags/news_mentions.parquet`)

`scripts.news_mentions` writes one row per scanned core tweet, keyed by
`tweet_id`. Its input is a deterministic local news article export
(`data/news/articles.jsonl` by convention, or a JSON/JSONL/CSV path
passed with `--articles`). The matcher only counts exact status URLs for
archived core tweets, including `x.com/<handle>/status/<id>`,
`twitter.com/<handle>/status/<id>`, and `x.com/i/web/status/<id>`.

The script does not call a paid API or any network service. If the
article export is absent, the GitHub workflow skips the step. If it is
present, the sidecar emits `news:mentioned` and `news:covered` tags for
matched tweets, plus article provenance (`source`, `title`, `url`,
`published_at`, `matched_fields`, `matched_terms`, and confidence). The
viewer loads this sidecar opportunistically and merges those tags into
the normal tag filter/search surface.

For later video-enrichment passes, use descriptive production labels:
`media:produced-video`, `media:music-video`, `media:montage`,
`media:text-overlay`, and `media:voiceover`. These tags should be based
on observed video/audio structure: editing, music, multi-shot sequences,
visible text, and narration. Speaker attribution uses `speaker:<title or
name>`. A speaker may be tagged only when the tweet text, source alt
text, transcript/captions, or captured replies/comments support it;
otherwise write "unknown speaker" in the description or omit the speaker
field.

## Tag namespaces

See `config/tag_taxonomy.yaml` for the authoritative list. Quick map:

| Namespace  | What it labels                         | Example                    |
| ---------- | -------------------------------------- | -------------------------- |
| `subject:` | who/what the post is about             | `subject:detainee`         |
| `genre:`   | communicative function                 | `genre:statistics`         |
| `media:`   | content of attached media (Layer 3a)   | `media:photo-detainee`     |
| `speaker:` | evidence-supported speaker attribution | `speaker:Secretary Noem`   |
| `format:`  | structural (derived from `tweet_type`) | `format:retweet`           |
| `status:`  | availability / moderation state        | `status:copyright-removal` |
| `frame:`   | recurring rhetorical scaffolds         | `frame:criminal`           |
| `action:`  | enforcement verbs                      | `action:deportation`       |
| `topic:`   | broad subject areas; additive          | `topic:immigration`        |
| `theme:`   | rhetorical / ideological frames        | `theme:nativism`           |
| `origin:`  | "from <country>," pattern              | `origin:Mexico`            |
| `country:` | any contextual country mention         | `country:Mexico`           |
| `state:`   | "<place>, <state>" pattern             | `state:Texas`              |
| `crime:`   | crime type vocabulary                  | `crime:assault`            |
| `agency:`  | mentioned enforcement-adjacent handle  | `agency:ICEgov`            |
| `slogan:`  | DHS branded phrases                    | `slogan:nice`              |
| `shape:`   | composite (e.g. mugshot-reply form)    | `shape:lineup`             |
| `news:`    | local article export cited this tweet  | `news:mentioned`           |

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
  handle, the `slogan:` phrases, or one of a small set of plain
  keywords (`immigration`, `migrant`, `asylum`, `illegal alien`, `the
border`, `border patrol`, bare `ICE`/`CBP`).
- Without an explicit signal, the tag is emitted **tentative** —
  visually de-emphasized in the viewer and open to correction via the
  suggestion flow.

On the live corpus this splits 50/50: ~1,589 confirmed and ~1,615
tentative `topic:immigration` tags across 3,204 tagged tweets.

`_misc` / public-tier authors don't get the default at all; their
tweets only earn `topic:immigration` if an explicit signal fires.

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

# 4. (Future) After frame/OCR/transcript passes:
# uv run python -m scripts.tag_image_ocr        # data/tags/image_ocr.parquet
# uv run python -m scripts.tag_image_clip       # data/tags/image_clip.parquet
# uv run python -m scripts.tag_audio_transcript # data/tags/audio_transcript.parquet
# 5. Re-run scripts/tag_lexical so OCR + transcript text feed Layer-1 rules.
```

The viewer fetches the shipped sidecars in `data/tags/` on load and
gracefully degrades when one is missing.
