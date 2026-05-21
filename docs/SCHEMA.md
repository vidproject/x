# Data schema

Canonical tweet and media schemas are defined in `extension/src/lib/types.ts`
and asserted by `scripts/ingest.py`. This file is the human-readable
reference and is filled out in Phase 8.

See also: the `Canonical tweet schema` block in the project specification.

## News mentions sidecar

`data/tags/news_mentions.parquet` is an optional annotation sidecar
written by `scripts.news_mentions`. It is keyed by `tweet_id` and scans
only core-account tweets against a caller-supplied local article export.

Columns:

- `tweet_id`, `account_handle`, `tweet_url`, `posted_at`
- `input_hash`, `generated_at`, `detector`, `detector_version`
- `mention_count`
- `articles`: list of `{source, title, url, published_at, matched_fields, matched_terms, confidence}`
- `status`: `mentioned` or `no-match`
- `tags`: normal tag-entry structs, currently `news:mentioned` and `news:covered`
- `cost_estimate_usd`, `error`
