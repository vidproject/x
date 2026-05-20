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

| Layer | Source                                                                   | Output                                                 | Status                                                                                                       |
| ----- | ------------------------------------------------------------------------ | ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| 0     | passthrough — existing `hashtags`, `card.title/description`, URL domains | viewer columns / facets                                | viewer pulls `tags_str` from `hashtags`; not yet broadened                                                   |
| 1     | regex / structural rules on `text_resolved` (+ OCR when present)         | `data/tags/lexical.parquet`                            | **shipped** — see `scripts/tag_lexical.py`                                                                   |
| 2     | text topic classifier (zero-shot / API)                                  | `data/tags/text_topic.parquet`                         | not started                                                                                                  |
| 3m    | archived media metadata + source alt text                                | `data/tags/media_vision.parquet`                       | **shipped** — see `scripts/describe_media.py`                                                                |
| 3a    | CLIP zero-shot image labels                                              | `data/tags/image_clip.parquet`                         | not started                                                                                                  |
| 3b    | OCR for in-image text (Tesseract → PaddleOCR fallback)                   | `data/tags/image_ocr.parquet`                          | not started; **the lexical tagger already integrates with it via `load_ocr_map()` once the parquet appears** |
| 3c    | Video keyframes / transcripts / OCR                                      | enriches `media_vision`, `image_clip`, and `image_ocr` | not started; `media:needs-vision` marks the queue                                                            |
| 4     | vision LLM for high-value items                                          | merged into 1 + 3a namespaces                          | not started                                                                                                  |

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

## Tag namespaces

See `config/tag_taxonomy.yaml` for the authoritative list. Quick map:

| Namespace  | What it labels                         | Example                |
| ---------- | -------------------------------------- | ---------------------- |
| `subject:` | who/what the post is about             | `subject:detainee`     |
| `genre:`   | communicative function                 | `genre:statistics`     |
| `media:`   | content of attached media (Layer 3a)   | `media:photo-detainee` |
| `format:`  | structural (derived from `tweet_type`) | `format:retweet`       |
| `frame:`   | recurring rhetorical scaffolds         | `frame:criminal`       |
| `action:`  | enforcement verbs                      | `action:deportation`   |
| `topic:`   | themes                                 | `topic:border`         |
| `origin:`  | "from <country>," pattern              | `origin:Mexico`        |
| `country:` | any contextual country mention         | `country:Mexico`       |
| `state:`   | "<place>, <state>" pattern             | `state:Texas`          |
| `crime:`   | crime type vocabulary                  | `crime:assault`        |
| `agency:`  | mentioned enforcement-adjacent handle  | `agency:ICEgov`        |
| `slogan:`  | DHS branded phrases                    | `slogan:nice`          |
| `shape:`   | composite (e.g. mugshot-reply form)    | `shape:lineup`         |

## The `topic:immigration` default

The corpus is overwhelmingly about immigration. Trying to infer
relevance from sparse tweet text (image-heavy posts, three-word
slogans) costs recall. So:

- Every tweet from a tracked-tier account (`core` / `government` /
  `officials`) is tagged `topic:immigration`, **unless** an obvious
  non-immigration signal blocks it (birthday, weather, sports).
- The tag is emitted **confirmed** when the text (or OCR) carries any
  explicit immigration signal: a `frame:`, `action:`, `origin:`,
  `country:`, `topic:border/sanctuary/worksite`, a known agency
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

## Run order

```
# 1. Capture flows in via the Firefox extension as today.
# 2. After every push to master:
uv run python -m scripts.ingest          # canonical parquets + manifest
uv run python -m scripts.tag_lexical     # data/tags/lexical.parquet

# 3. After media archival:
uv run python -m scripts.describe_media  # data/tags/media_vision.parquet

# 4. (Future) After frame/OCR passes:
# uv run python -m scripts.tag_image_ocr   # data/tags/image_ocr.parquet
# uv run python -m scripts.tag_image_clip  # data/tags/image_clip.parquet
# 5. Re-run scripts/tag_lexical so OCR text feeds Layer-1 rules.
```

The viewer fetches the shipped sidecars in `data/tags/` on load and
gracefully degrades when one is missing.
