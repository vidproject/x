# Tagging strategy — design hand-off

> Written by an instance of Claude looking at the corpus as of
> 2026-05-20, to hand off to the working instance once it is unstalled.
> Nothing in `data/`, `raw/`, `seen/`, or `scripts/` has been changed —
> this file is the only artifact. Read it, push back on anything that
> doesn't fit the project's principles, then implement.

## 1. The problem

The archive now holds **~4,300 tweets** across ten parquet files, with
~2,700 media items (2,002 photos, 711 videos, 14 GIFs) and roughly a
third of media (~1,000 items) already mirrored to GitHub Release
assets. The viewer can filter by handle, date, tweet type, and
free-text — but free-text only reaches the `text` / `text_resolved` /
`hashtags` / `mentions` / `account_handle` fields. That leaves three
big gaps:

1. **Image-heavy, text-light posts.** ~125 tweets carry only a t.co
   link plus 1-3 words ("ICE is NICE.", "Report. Recon. Raid.",
   "https://t.co/…"). The viewer treats them as essentially empty
   even though the media is the entire payload.

2. **Templated text that *should* be a structured field.** DHS's
   reply chains follow a tight grammar — "*Name*, a criminal illegal
   alien from *Country*, convicted for *Crime* in *City*, *State*."
   A free-text search for `"Mexico"` returns 89 hits today; a
   structured `origin_country` facet would let a journalist pull all
   89 with one click and graph them over time.

3. **Cross-account themes.** "Sanctuary cities," "border wall,"
   "worksite enforcement," "Tren de Aragua" — recurring topics that
   span handles. None of these are surfaced as filters.

The corpus itself is small enough (4k tweets, 2.7k media) that we
can afford to be thorough.

## 2. Operating principles (these aren't negotiable)

These come from the README's "Operating principles" and the project
spec; every design choice below respects them.

- **Capture honestly.** Canonical tweet rows in `data/<handle>.parquet`
  mirror what X served the browser. Tagging is downstream.
  → **Tags live in sidecar parquets, never as new columns on the
  canonical tweet rows.** Re-ingest never touches them. Re-tagging
  never touches the canonicals.

- **Determinism over cleverness.** Boring, explicit code.
  → Lexical / regex / passthrough tags are the first layer. They
  cover most of the value and are reviewable by reading a Python
  file. ML / vision only fills the gap.

- **Never silently drop data.** Failures are quarantined, not eaten.
  → Tagger failures (model crash, decode error, OCR garbage) write
  an error row with `status="failed"`, not nothing.

- **Atomicity.** Parquet writes are `tmp.parquet` → `os.rename`.
  → Match this in `scripts/tag_*.py`.

- **No editorial filter.** Capture mirrors X; tags must too. No
  "true / false," "harmful / not harmful," "biased / not biased"
  judgements. Tags describe **what the tweet contains** (a mugshot,
  a country name, an arrest verb) — not whether it should exist.

## 3. Architectural shape

```
data/
  <handle>.parquet            # untouched: canonical tweets
  tags/
    lexical.parquet           # tweet_id → list[tag] from Layer 1
    image_clip.parquet        # media_id → list[(label, score)] from Layer 3a
    image_ocr.parquet         # media_id → text from Layer 3b
    text_topic.parquet        # tweet_id → topic label from Layer 2 (optional)
    manifest.json             # versions + run timestamps per layer

config/
  tag_taxonomy.yaml           # checked-in taxonomy + label lists for vision
```

Why sidecars instead of new columns on the canonical:

- Re-tag with a new model = rewrite one file, not all ten.
- Ingest stays a single-purpose script; no model imports leak in.
- Sidecars can be regenerated from `data/<handle>.parquet` +
  archived media in Releases — the canonical is the source of truth.
- A consumer who only wants the raw archive (researcher, FOIA
  records, court-of-record use) can ignore `data/tags/` entirely.

The viewer joins sidecars in at load time on `tweet_id` and
exposes a new **Tags** column (filterable) and a new media-detail
section showing CLIP labels + OCR text.

## 4. Layers, in order of how cheap they are

Each layer is a separate script under `scripts/`, runnable
independently, idempotent on re-run, atomic on write. Build them in
order — each one's output is searchable / shippable before the next
exists.

### Layer 0 — Passthrough (zero new code)

The fields are already in the parquet; just expose them as filters
in `viewer/table.js`. **Do this first** — it's a UI change, not a
tagging change, but it adds value before any of the heavier stuff
ships.

- **`hashtags`** — render as a `Tags` column today. The MiniSearch
  index already includes `tags_str` (`viewer/store.js:54`); just add
  a column and a header filter.
- **Card title/description** — when `row.card` is set, fold its
  `title` and `description` into the search corpus.
- **Outbound URL domains** — derive `url_domains` at load time
  (parse `urls[].expanded`). Adds a "domain" facet (whitehouse.gov,
  youtube.com, etc.).
- **`possibly_sensitive`** — already in the schema, never surfaced.

### Layer 1 — Lexical / regex tags (deterministic, ~30 minutes of CPU)

A new module `scripts/tag_lexical.py` that reads all parquets, runs
a battery of regexes against `text_resolved` (falling back to
`text`), and writes `data/tags/lexical.parquet` with columns:

```
tweet_id           : str
tagger_version     : str  (e.g. "lexical-v1")
tagged_at          : str  (ISO timestamp)
tags               : list[struct{name, value, span_start, span_end}]
```

`tags` is a list because a tweet can be `crime_type=fraud`,
`crime_type=assault`, `state=Texas`, `country=Mexico`, etc. all at
once. Storing `span_start`/`span_end` lets the viewer highlight what
matched, which is much more debuggable than an opaque label.

**Regexes that are already validated on this corpus** (counts from
the live data, May 2026):

| Tag namespace        | Pattern (case-insensitive)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | Hits  | Notes |
|----------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------:|-------|
| `frame:criminal`     | `\b(criminal illegal alien\|illegal alien\|criminal alien\|aggravated felon\|convicted (?:for\|of))\b`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | 558   | Tags the heavily-templated "Name, a criminal illegal alien from X" replies. |
| `action:arrest`      | `\b(arrest(?:ed\|ing)?\|detain(?:ed\|ing)?\|apprehend(?:ed\|ing)?\|in custody\|nab(?:bed)?)\b`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | 381   |  |
| `action:deport`      | `\b(deport(?:ed\|ing\|ation)?\|remov(?:ed\|al)\|repatriat(?:ed\|ion))\b`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | 200   |  |
| `topic:border`       | `\b(border\|southwest\|crossing\|wall)\b`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | 112   |  |
| `country:<NAME>`     | `\bfrom ([A-Z][a-zA-Z]+(?: [A-Z][a-zA-Z]+)?)\s*,`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | 233   | Validates against a fixed list of 195 sovereign-state names so "from John, his neighbor said …" doesn't false-positive. |
| `crime:<TYPE>`       | `\b(rape\|sodomy\|murder\|burglary\|theft\|robbery\|assault\|battery\|fentanyl\|cocaine\|methamphetamine\|trafficking\|DUI\|DWI\|kidnap\|child\|gang\|MS-13\|tren de aragua\|narcot\|fraud\|arson\|weapon\|firearm)\b`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | many  | One match → one tag entry, so a single tweet can produce e.g. `crime:rape`, `crime:child`, `crime:weapon`. |
| `state:<NAME>`       | `\bin [A-Z][a-zA-Z]+(?: [A-Z][a-zA-Z]+)?,\s+(Texas\|California\|…)` against the 50-state list                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | many  | Distribution: TX 46, CA 41, NY 25, FL 23, VA 14, NC 12, … |
| `agency:<HANDLE>`    | Pull `@ICEgov`, `@CBP`, `@HSI_HQ`, `@ERO__*`, `@USBPChief`, `@DHSgov`, `@WhiteHouse`, `@POTUS`, `@PressSec`, `@SecMullinDHS`, etc. from `mentions[]` (already a list field; no regex needed).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | —     | Adds an "ICE was tagged" facet alongside "ICE was the author." |
| `slogan:nice`        | `\b(NICE day\|NICE morning\|ICE is NICE\|NICE city)`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | small | DHS branded-meme; ~10 hits but a distinct content category. |
| `slogan:worst`       | `\bWORST OF THE WORST\b`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | small |  |
| `slogan:reportrecon` | `\bReport\.\s*Recon\.\s*Raid\.`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | small |  |
| `shape:templated_reply` | `tweet_type == 'reply'` **and** matches `frame:criminal` **and** has 1 photo                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | 200+  | Composite tag — flags the highly templated mugshot-reply form. |

A starting taxonomy lives in `config/tag_taxonomy.yaml`; everything
above should reference it rather than hard-coding strings. Updating
the taxonomy + re-running `tag_lexical.py` is the supported way to
add a tag.

**Tests** (`tests/test_tag_lexical.py`): seed a fake parquet with one
tweet per namespace and assert the expected tags come out. Keep
fixtures tiny — these tests should run in <1s.

### Layer 2 — Text topic classifier (optional, deferable)

Only worth building once Layer 1 is in production and we know which
tweets it misses. A small ~12-label taxonomy:

```
arrest-announcement      raid-announcement
policy-statement         executive-action
operational-update       data-statistic
legal-action             sanctuary-rhetoric
worksite-enforcement     recruitment
commemoration            press-event
```

Two implementation paths:

a. **Zero-shot via `transformers` + `facebook/bart-large-mnli`** —
   no training data, runs on CPU at ~1s/tweet, fully open-weights.
   Calling it on 4,300 tweets once is ~1 hour of CPU. Easy to drop.

b. **Anthropic API in batch.** Higher quality, costs ~$5-15 for the
   whole corpus, but introduces an external dependency and a budget
   line. The user has been clear about minimising external deps;
   default to (a) unless explicitly asked otherwise.

Either way, output goes to `data/tags/text_topic.parquet` with the
same shape as Layer 1 and a `confidence` float so the viewer can
hide low-confidence assignments.

### Layer 3a — CLIP / SigLIP zero-shot image tagging (the user's main ask)

Tagging the ~2,700 photos with `open_clip_torch` against a
checked-in label vocabulary. This is the **right fit** for this
corpus because:

- The visual content is stereotyped: mugshots, agents in tactical
  gear, vehicles, border landscape, propaganda graphics with text
  overlays, flag-and-eagle iconography, Trump podium photos. CLIP
  handles these classes well zero-shot.
- ViT-B/32 is ~150MB and runs ~5 images/sec on CPU; 2,700 images
  is ~10 minutes single-threaded on a laptop, much less on the CI
  runner.
- Zero-shot means no labelled training set. The label vocabulary
  lives in `config/tag_taxonomy.yaml` as a flat list of natural-
  language prompts; tweaking is a YAML edit + a re-run.

**Starter label set** (this is for the other instance to tune by
hand against a sample — don't ship without eyeballing 50 random
images per label):

```yaml
clip_labels:
  - id: mugshot
    prompt: "a frontal booking photograph of a single person"
  - id: officer-tactical
    prompt: "a law enforcement officer in tactical gear and body armor"
  - id: officer-uniform
    prompt: "a uniformed Border Patrol or ICE officer"
  - id: arrest-scene
    prompt: "a person being handcuffed or detained on the street"
  - id: detention-facility
    prompt: "an indoor detention or processing facility"
  - id: border-landscape
    prompt: "the US-Mexico border wall or desert landscape"
  - id: vehicle-tactical
    prompt: "an armored law enforcement vehicle or convoy"
  - id: helicopter-drone
    prompt: "a helicopter, drone, or aerial enforcement asset"
  - id: weapons-drugs
    prompt: "seized firearms, narcotics, or cash on a table"
  - id: propaganda-graphic
    prompt: "a graphic with large overlaid slogan text on a flag or photo"
  - id: stats-infographic
    prompt: "a data infographic with numbers and charts"
  - id: trump-podium
    prompt: "Donald Trump speaking at a podium"
  - id: official-portrait
    prompt: "an official government headshot or portrait"
  - id: capitol-whitehouse
    prompt: "the US Capitol building or the White House"
  - id: rally-crowd
    prompt: "a political rally crowd waving flags"
  - id: news-screenshot
    prompt: "a screenshot of a news article or TV broadcast"
  - id: meme-cartoon
    prompt: "a cartoon, meme, or AI-generated stylised image"
```

Output to `data/tags/image_clip.parquet`:

```
media_id        : str
tweet_id        : str            # joinable shortcut
tagger_version  : str            # e.g. "clip-vitb32-laion2b-v1"
tagged_at       : str
labels          : list[struct{id, score}]   # all labels above threshold
top_label       : str            # convenience: argmax for the Tags column
```

Threshold the soft-max output at e.g. `>0.25` to emit a label;
always emit `top_label` so the viewer has something to show.

**Input source.** Read pixels from `release_asset_url` (the
GitHub-Releases mirror). If `archive_status != "archived"`, skip
that media — Layer 3 runs *after* `archive_media.py`. Don't fetch
twimg directly; it ages out, and the whole point of the mirror is
that the archived bytes survive after.

**Failure handling.** If a download / decode fails, write a row
with `status="failed"` and the error string. Re-run picks it up
once the asset reappears.

**Tests** (`tests/test_tag_image_clip.py`): bundle three small
public-domain images (one mugshot-shape, one landscape, one
propaganda-style) in `tests/fixtures/images/`, run the tagger,
assert each gets its expected top label. Keep model loading
behind a fixture so unit tests can mock it out for speed.

### Layer 3b — OCR for in-image text

You asked for this explicitly. Two layers of OCR depending on the
image:

- **Tesseract** (`pytesseract`) for high-contrast graphic overlays.
  Fast (~50ms/image on CPU), small (~50MB binary). Works well on
  RapidResponse47-style "STOP THE INVASION" graphics, mugshot name
  banners, infographic numbers.
- **PaddleOCR** for photo-text (signs in raid photos, badges,
  paperwork captured incidentally). Heavier (~200MB), slower
  (~1s/image), more accurate.

Strategy: **run Tesseract first**, and only fall back to PaddleOCR
when (a) Tesseract returned <5 chars and (b) CLIP labelled the
image as `propaganda-graphic`, `stats-infographic`, `news-
screenshot`, or `meme-cartoon` (the cases where text-in-image is
actually expected). This keeps the heavy model off ~80% of images.

Output to `data/tags/image_ocr.parquet`:

```
media_id        : str
tweet_id        : str
ocr_engine      : str            # "tesseract" | "paddleocr"
ocr_version     : str
ocr_at          : str
text            : str            # full OCR'd text, single string, newlines preserved
confidence      : float          # mean per-token confidence
```

The viewer treats `image_ocr.text` as just another searchable text
field in MiniSearch. A search for "deport" will now match a graphic
with the word "DEPORT" in 96pt over a flag — which is exactly the
gap we have today.

**Caveats to write into the script's docstring:**

- OCR is noisy. False positives are inevitable. Surface
  `confidence` so a downstream filter can cut at e.g. `>0.5`.
- Do **not** treat OCR text as authoritative tweet content. It's a
  search aid, not a transcript. Never write it into the canonical
  `text` field.
- Mugshot graphics often have the perpetrator's name burned in.
  That's already in the tweet text in templated form; OCR is
  duplication, not new information, for those cases. That's fine —
  just don't be surprised when 80% of OCR text echoes the tweet.

### Layer 3c — Video keyframes

For the 711 videos: extract one keyframe per video at `t=1s` with
`ffmpeg -ss 1 -frames:v 1 -q:v 4 frame.jpg`, then run it through
Layer 3a + 3b exactly like a photo. Cache the keyframe next to the
video in `data/tags/keyframes/<media_id>.jpg`.

Going beyond one keyframe (storyboard, dense captioning) is **not
worth it for v1**. Government videos are visually static — a single
frame ~1s in captures the dominant content. Re-evaluate after
v1 ships.

### Layer 4 — Optional: vision LLM for high-value items

Reserve for a small subset of tweets where the cheaper layers fall
short: high engagement (top 5% by view count), deletion-detected,
flagged by a human reviewer. Drives the Anthropic API with a
structured-output prompt that emits the same tag namespaces as
Layer 1 + 3a. Budget-bounded; opt-in. Don't build until 0-3 are in
production.

## 5. Viewer changes

In order from smallest to largest, all in `viewer/`:

1. **Add a `tags` column** to `table.js::COLUMNS`. Render as comma-
   separated pills. Filterable via the existing column-header popup.
2. **Load sidecars in `parquet.js`.** When loading
   `data/<handle>.parquet`, also fetch `data/tags/lexical.parquet`
   (one file, all handles) and join on `tweet_id` client-side.
3. **Expose OCR text + CLIP labels in `sidepanel.js`.** Add a new
   `Media analysis` section under each media item: top CLIP labels
   with score chips + the OCR'd text in a `<pre>`.
4. **Include sidecar text in MiniSearch.** Extend the docs in
   `store.js::ensureSearch` to also index a `media_text` field
   (concatenated OCR strings for the tweet's media). One-line
   change.

None of these alter the canonical row shape — the joined columns
exist only in the viewer's in-memory representation.

## 6. CI / scheduling

Three new workflows under `.github/workflows/`, each on the same
pattern as `archive-media.yml`:

- **`tag-lexical.yml`** — `workflow_run` on `ingest` completion.
  Cheap, run on every push. Commits `data/tags/lexical.parquet`.
- **`tag-image.yml`** — `workflow_run` on `archive-media`
  completion (so we only tag bytes that exist locally). Slower,
  also commits `data/tags/image_clip.parquet` and
  `data/tags/image_ocr.parquet`. Pin model weights with a hash.
- **`tag-text-topic.yml`** — manual `workflow_dispatch` only at
  first. Promote to scheduled once it's stable.

Concurrency groups prevent the taggers from racing each other.
Bot commit messages follow the existing `[bot] tag-<layer>: …`
convention.

## 7. What to build first (proposed order)

1. **Viewer Layer 0 + a stub `tags` column** that reads from
   hashtags. (Half a day.) Ships value without any backend.
2. **`scripts/tag_lexical.py` + `config/tag_taxonomy.yaml` + tests.**
   (One day.) Hooks up the column. By the end of day 2 the archive
   has ~30-50% of tweets meaningfully tagged.
3. **CI workflow for Layer 1.** (Half a day.) Now the tagging is
   automatic on every ingest.
4. **`scripts/tag_image_clip.py` + tests + workflow.** (Two days.)
   Pin `open_clip_torch` + a specific weights hash; commit the
   parquet to the repo (small file, big value).
5. **`scripts/tag_image_ocr.py` (tesseract path) + tests + workflow.**
   (One day.) Same shape.
6. **Video keyframes + PaddleOCR fallback.** (One day each.)
7. **Layer 2 (text topic).** Only if Layer 1 + 3 leave gaps worth
   filling.
8. **Layer 4 (vision LLM).** Only if the user explicitly asks.

## 8. Open questions for the user

Things I would not decide unilaterally — flag these before
implementation:

1. **Tag-confidence threshold default.** Do we surface every CLIP
   label above 0.25, or just the argmax? Lower → more recall, more
   noise. I'd ship at 0.30 and the argmax.
2. **Whether to include sentiment / tone.** The "no editorial
   filter" principle leans against it. Recommend skipping.
3. **Whether Layer 2 / Layer 4 are in scope at all.** I'd
   default to "Layer 1 + 3 only" for v1.
4. **Model-weights pinning.** `open_clip_torch` lets us specify
   `('ViT-B-32', 'laion2b_s34b_b79k')` — pin to that exact pair so
   re-runs are reproducible. OK to commit nothing to the repo
   itself (weights live in the HuggingFace cache during the CI run).
5. **Where the per-handle vs combined boundary sits.** I've assumed
   one combined `lexical.parquet` for all handles (small, easy
   join). For the image parquets, also combined — total size will
   be a few hundred KB even with 2,700 rows. If we ever scale 10×,
   split per-handle.

## 9. What this design deliberately does *not* do

- **Does not add columns to the canonical tweet parquet.** Tags
  are joined in by the viewer at load time.
- **Does not "filter" or "categorise" tweets out of the archive.**
  Every tweet remains visible. Tags are an index, not a censor.
- **Does not store any moral / truthfulness / harm labels.** Tags
  describe content (a country name, a crime word, a mugshot-shape
  image) — never whether the content is good, bad, true, or
  misleading. That distinction is the README's editorial line.
- **Does not call any non-archived URL.** Image taggers read from
  `release_asset_url` (GitHub-mirrored) only.
- **Does not write outside `data/tags/`.** Easy to nuke and regen.
