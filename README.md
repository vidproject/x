# Immigration Social Media Archive

A public, append-only archive of social-media posts (especially videos) published by U.S. federal agencies of the second Trump administration on the subject of immigration. The covered accounts are operated by DHS, ICE, CBP, USCIS, the White House, the Press Secretary, the President, and the Department of Labor.

This is journalism / public-records work. Capture happens via the publicly visible web only — no X Developer Agreement, no X API credentials. The repository itself is the database: structured tweet JSON is committed by a Firefox extension, ingested by GitHub Actions into per-account Parquet files, mirrored to GitHub Releases for media, and served as a static GitHub Pages viewer.

**[Open the searchable archive →](https://vidproject.github.io/x/)** _(live once Phase 3 completes)_

## How to use the viewer

The viewer is a static page that loads each account's Parquet file in your browser and lets you search, filter, and export. No login, no tracking. Pick one or more accounts from the **Database Downloads** menu in the header, then use the filter bar to narrow by date, account, media type, or free-text search. The URL updates as you filter, so any view can be bookmarked or shared.

## For contributors: the Firefox extension

The extension captures public X posts from the configured accounts as you browse and commits the structured JSON directly to this repository.

1. **Download [`extension.zip`](./extension.zip) and unzip it.**
2. In Firefox, open `about:debugging` → **This Firefox** → **Load Temporary Add-on…** → pick `manifest.json` from the unzipped folder.
3. Open the extension's sidebar (click its toolbar icon — the sidebar **stays open as you browse**), open **Settings**, and paste a GitHub PAT.
4. Visit a tracked account on `x.com`, e.g. <https://x.com/DHSgov>. Within a few seconds the sidebar's activity tail will show captures committing.

Firefox removes temporary add-ons when it closes — reinstalling takes about ten seconds.

If you reload the extension while X tabs are already open, those tabs keep their old content scripts; the new build only takes full effect on tabs opened after the reload. The extension does re-inject its page-hook into existing tabs on every wake, but the cleanest behaviour is to close X tabs before reloading and let `Capture now` open a fresh one.

### Generating the right PAT

Use a **fine-grained** Personal Access Token, not a classic one, with **only this repository** selected and only the minimum permissions:

| Permission            | Access         |
| --------------------- | -------------- |
| Repository → Contents | Read and write |
| Repository → Metadata | Read           |

Generate one at <https://github.com/settings/personal-access-tokens/new>. The PAT is stored in `browser.storage.local`; anyone with filesystem access to your Firefox profile can read it, so use a single-repo fine-grained token, never a classic `repo`-scoped one.

## Coverage

_Coverage tables are regenerated automatically by `scripts/update_readme.py` after each ingest run. The section below will populate once captures begin._

<!-- COVERAGE:START -->

| Handle | Label | Tweets | First post | Latest post | Latest capture | Media | Videos |
| ------ | ----- | -----: | ---------- | ----------- | -------------- | ----: | -----: |
| `@DHSgov` | Department of Homeland Security | 350 | 2026-03-05 | 2026-05-19 | 2026-05-19 | 222 | 94 |
| `@ICEgov` | U.S. Immigration and Customs Enforcement | 5 | 2026-04-28 | 2026-05-19 | 2026-05-19 | 7 | 2 |
| `@CBP` | U.S. Customs and Border Protection | 4 | 2026-05-11 | 2026-05-19 | 2026-05-19 | 3 | 3 |
| `@USCIS` | U.S. Citizenship and Immigration Services | 0 | — | — | — | 0 | 0 |
| `@WhiteHouse` | The White House | 5 | 2026-04-27 | 2026-05-19 | 2026-05-19 | 4 | 1 |
| `@PressSec` | White House Press Secretary | 5 | 2026-04-23 | 2026-04-30 | 2026-05-19 | 0 | 0 |
| `@POTUS` | President of the United States | 0 | — | — | — | 0 | 0 |
| `@USDOL` | U.S. Department of Labor | 0 | — | — | — | 0 | 0 |
| `@BorderHawkNews` | BorderHawkNews | 1 | 2026-05-08 | 2026-05-08 | 2026-05-19 | 0 | 0 |
| `@CBPAMO` | CBPAMO | 1 | 2026-05-12 | 2026-05-12 | 2026-05-19 | 4 | 0 |
| `@EROPhiladelphia` | EROPhiladelphia | 1 | 2026-05-11 | 2026-05-11 | 2026-05-19 | 1 | 0 |
| `@FPSDHS` | FPSDHS | 1 | 2026-05-11 | 2026-05-11 | 2026-05-19 | 1 | 0 |
| `@PattyMorin` | PattyMorin | 1 | 2026-05-09 | 2026-05-09 | 2026-05-19 | 1 | 0 |
| `@RapidResponse47` | RapidResponse47 | 1 | 2026-05-11 | 2026-05-11 | 2026-05-19 | 0 | 0 |
| `@TSA` | TSA | 2 | 2026-05-06 | 2026-05-11 | 2026-05-19 | 2 | 1 |
| `@WHTaskForceFIFA` | WHTaskForceFIFA | 1 | 2026-05-09 | 2026-05-09 | 2026-05-19 | 1 | 0 |

_Generated 2026-05-19T18:48:00Z._

<!-- COVERAGE:END -->

## Architecture

```
Firefox extension  ──HTTPS PAT──▶  GitHub repo  ──push──▶  GitHub Actions  ──commit──▶  GitHub Pages
   page-hook                          raw/*.json              ingest.py                   index.html
   normalize                          config/accounts.yaml    archive_media.py            viewer/
   sidebar                            data/*.parquet          submit_wayback.py
   github client                      data/manifest.json      detect_deletions.py
                                      Releases (media)        update_readme.py
```

See the project specification for a fuller diagram.

## Documentation

- [Data schema](docs/SCHEMA.md)

## Operating principles

- **Determinism over cleverness.** Boring, explicit code.
- **Never silently drop data.** Parse failures are quarantined to `raw/_quarantine/` and surfaced in the sidebar.
- **Atomicity.** Parquet rewrites are `tmp.parquet` → `os.rename`; Release uploads must succeed before the Parquet row records the asset URL.
- **Don't store credentials in the repo.** PAT lives in `browser.storage.local`; Internet Archive keys live in GitHub Actions secrets.
- **Capture honestly.** Tweets in `data/` mirror what X served the browser at capture time. No massaging, no relevance filtering, no political characterization. Annotation is a separate, downstream concern.

## License

Property of the University of California.
