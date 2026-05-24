"""Apply the manual tag-audit decisions onto the catalog + preview JSONs.

This is the write-back step for the manual tag-audit campaign. It mirrors the
overlay mechanics of ``scripts/tag_review_curation.py`` but, unlike that purely
additive curation pass, it applies both ADDITIONS and REMOVALS recorded by the
reviewing subagents.

Inputs: one JSON decision file per tweet under ``data/tags/tag_audit/<id>.json``
(the ``_bundles/`` subdir and files beginning with ``_`` are ignored). Decision
schema (extra keys are tolerated)::

    {
      "tweet_id": "123",
      "reviewer": "...",
      "reviewed_at": "ISO8601",
      "immigration": "confirm" | "remove" | "add" | "n/a",
      "add_tags": ["theme:nativism", ...],
      "remove_tags": ["crime:fraud", ...],
      "notes": "...",
      "evidence": "..."
    }

``immigration`` is a convenience field folded into add/remove of
``topic:immigration``:

* ``"add"``     -> ensure topic:immigration in add_tags
* ``"remove"``  -> ensure topic:immigration in remove_tags
* ``"confirm"`` / ``"n/a"`` -> no implicit change (a confirm is recorded as a
  re-stamp to source manual-audit so provenance is visible, but only if the tag
  is already present; it never adds a tag that isn't there).

Application rules (idempotent):

* ADD: insert the tag with ``source: "manual-audit"`` if not already present.
  If present, leave the existing entry untouched (we don't clobber another
  tagger's provenance) UNLESS it is an immigration ``confirm`` re-stamp.
* REMOVE: drop the tag entirely from the tweet's tag list.
* A tag that is in both add and remove for the same tweet is treated as a
  remove (explicit removal wins) and a warning is logged.
* Never touches the ``media-status:*`` namespace even if a decision file names
  one (that namespace is owned by a different agent); such add/remove entries
  are skipped with a warning.

Outputs (mirrors tag_review_curation):

1. ``data/tags/tag_audit.parquet`` — sidecar of the applied audit decisions in
   the lexical-tag schema, for reproducibility / re-overlay on rebuild.
2. ``data/catalog.parquet`` — overlaid in place (adds + removes).
3. ``data/preview-*.json`` — overlaid in place (adds + removes).

Corrupt / half-written decision files are skipped with a warning. Run with::

    uv run python -m scripts.apply_tag_audit            # apply everything
    uv run python -m scripts.apply_tag_audit --dry-run  # report, write nothing
    uv run python -m scripts.apply_tag_audit --sidecar-only
"""

from __future__ import annotations

import argparse
import glob
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from ._logging import configure
from ._schema import LEXICAL_TAG_SCHEMA

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"
AUDIT_DIR = TAGS_DIR / "tag_audit"

SIDECAR_PATH = TAGS_DIR / "tag_audit.parquet"
CATALOG_PARQUET_PATH = DATA_DIR / "catalog.parquet"

SOURCE = "manual-audit"
TAGGER_VERSION = "manual-audit-v1"

# Namespace owned by the concurrent media-status agent; never apply here.
PROTECTED_NAMESPACES = ("media-status",)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_tag(tag: Any) -> str:
    return str(tag or "").strip()


def _protected(tag: str) -> bool:
    return any(tag == ns or tag.startswith(ns + ":") for ns in PROTECTED_NAMESPACES)


def collect_decisions() -> dict[str, dict[str, Any]]:
    """Return {tweet_id: {"add": set[str], "remove": set[str], "confirm": set[str], "handle": str}}."""
    by_tweet: dict[str, dict[str, Any]] = {}
    if not AUDIT_DIR.is_dir():
        LOG.warning("tag-audit: decision dir missing", dir=str(AUDIT_DIR))
        return by_tweet
    files = sorted(glob.glob(str(AUDIT_DIR / "*.json")))
    corrupt = skipped = loaded = 0
    for path in files:
        name = Path(path).name
        if name.startswith("_"):
            continue  # _suggestions.jsonl handled separately; _* are not decisions
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (json.JSONDecodeError, OSError) as err:
            corrupt += 1
            LOG.warning("tag-audit: skipping unreadable decision", path=path, error=str(err))
            continue
        if not isinstance(rec, dict):
            skipped += 1
            continue
        tid = _norm_tag(rec.get("tweet_id"))
        if not tid:
            skipped += 1
            LOG.warning("tag-audit: decision missing tweet_id", path=path)
            continue

        add = {_norm_tag(t) for t in (rec.get("add_tags") or []) if _norm_tag(t)}
        remove = {_norm_tag(t) for t in (rec.get("remove_tags") or []) if _norm_tag(t)}
        confirm: set[str] = set()

        imm = str(rec.get("immigration") or "").strip().lower()
        if imm == "add":
            add.add("topic:immigration")
        elif imm == "remove":
            remove.add("topic:immigration")
        elif imm == "confirm":
            confirm.add("topic:immigration")

        # Drop protected-namespace tags from both sets.
        for s, label in ((add, "add"), (remove, "remove")):
            bad = {t for t in s if _protected(t)}
            if bad:
                LOG.warning(
                    "tag-audit: skipping protected tags", path=path, where=label, tags=sorted(bad)
                )
                s -= bad

        # Remove wins over add for the same tag.
        conflict = add & remove
        if conflict:
            LOG.warning(
                "tag-audit: add/remove conflict, removing wins", path=path, tags=sorted(conflict)
            )
            add -= conflict

        slot = by_tweet.setdefault(
            tid, {"add": set(), "remove": set(), "confirm": set(), "handle": ""}
        )
        slot["add"] |= add
        slot["remove"] |= remove
        slot["confirm"] |= confirm
        handle = _norm_tag(rec.get("account_handle") or rec.get("handle"))
        if handle and not slot["handle"]:
            slot["handle"] = handle
        loaded += 1

    LOG.info(
        "tag-audit: collected decisions",
        files=len(files),
        loaded=loaded,
        corrupt=corrupt,
        skipped=skipped,
    )
    return by_tweet


def _apply_to_entries(
    existing: list[Any] | None,
    *,
    add: set[str],
    remove: set[str],
    confirm: set[str],
    full_schema: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """Return (new_entries, changed). full_schema controls struct shape.

    Catalog entries are {tag, tentative, source}; preview entries are
    {tag, source} (compact). We honor whichever shape the caller asks for.
    """
    out: list[dict[str, Any]] = []
    present: set[str] = set()
    changed = False
    for entry in existing or []:
        if isinstance(entry, dict):
            name = _norm_tag(entry.get("tag"))
            e = dict(entry)
        elif isinstance(entry, str):
            name, e = entry, {"tag": entry}
        else:
            continue
        if name and name in remove:
            changed = True
            continue  # drop
        if name and name in confirm and (e.get("source") != SOURCE or e.get("tentative")):
            # Re-stamp provenance + clear tentative so a human-confirmed tag is firm.
            e["source"] = SOURCE
            if "tentative" in e or full_schema:
                e["tentative"] = None
            changed = True
        out.append(e)
        if name:
            present.add(name)
    for tag in sorted(add):
        if tag in present:
            continue
        if full_schema:
            out.append({"tag": tag, "tentative": None, "source": SOURCE})
        else:
            out.append({"tag": tag, "source": SOURCE})
        present.add(tag)
        changed = True
    return out, changed


def write_sidecar(by_tweet: dict[str, dict[str, Any]]) -> None:
    tagged_at = _now_iso()
    rows = []
    for tid, slot in sorted(by_tweet.items()):
        entries = []
        for tag in sorted(slot["add"] | slot["confirm"]):
            entries.append(
                {
                    "tag": tag,
                    "tentative": None,
                    "source": SOURCE,
                    "span_start": None,
                    "span_end": None,
                }
            )
        for tag in sorted(slot["remove"]):
            entries.append(
                {
                    "tag": f"-{tag}",  # leading minus records an intended removal
                    "tentative": None,
                    "source": SOURCE,
                    "span_start": None,
                    "span_end": None,
                }
            )
        rows.append(
            {
                "tweet_id": tid,
                "account_handle": slot["handle"],
                "tagger_version": TAGGER_VERSION,
                "tagged_at": tagged_at,
                "tags": entries,
            }
        )
    df = pl.DataFrame(rows, schema=LEXICAL_TAG_SCHEMA)
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(SIDECAR_PATH)
    LOG.info("tag-audit: wrote sidecar", path=str(SIDECAR_PATH), rows=df.height)


def overlay_catalog(by_tweet: dict[str, dict[str, Any]], *, dry_run: bool) -> tuple[int, int, int]:
    if not CATALOG_PARQUET_PATH.exists():
        LOG.warning("tag-audit: catalog parquet missing", path=str(CATALOG_PARQUET_PATH))
        return 0, 0, 0
    df = pl.read_parquet(CATALOG_PARQUET_PATH)
    if "tweet_id" not in df.columns or "tags" not in df.columns:
        LOG.warning("tag-audit: catalog missing tweet_id/tags column")
        return 0, 0, 0
    ids = df["tweet_id"].to_list()
    existing_tags = df["tags"].to_list()
    new_tags: list[list[dict[str, Any]]] = []
    touched = adds_applied = removes_applied = 0
    for tweet_id, current in zip(ids, existing_tags, strict=True):
        slot = by_tweet.get(str(tweet_id))
        if not slot:
            new_tags.append(current)
            continue
        before = {_norm_tag(e.get("tag")) for e in (current or []) if isinstance(e, dict)}
        merged, changed = _apply_to_entries(
            current,
            add=slot["add"],
            remove=slot["remove"],
            confirm=slot["confirm"],
            full_schema=True,
        )
        after = {_norm_tag(e.get("tag")) for e in merged}
        adds_applied += len(after - before)
        removes_applied += len(before - after)
        new_tags.append(merged)
        if changed:
            touched += 1
    catalog_tag_dtype = pl.List(
        pl.Struct(
            [
                pl.Field("tag", pl.Utf8),
                pl.Field("tentative", pl.Boolean),
                pl.Field("source", pl.Utf8),
            ]
        )
    )
    if not dry_run:
        df = df.with_columns(pl.Series("tags", new_tags, dtype=catalog_tag_dtype))
        df.write_parquet(CATALOG_PARQUET_PATH)
    LOG.info(
        "tag-audit: overlaid catalog",
        path=str(CATALOG_PARQUET_PATH),
        touched=touched,
        adds=adds_applied,
        removes=removes_applied,
        dry_run=dry_run,
    )
    return touched, adds_applied, removes_applied


def overlay_previews(by_tweet: dict[str, dict[str, Any]], *, dry_run: bool) -> int:
    total_touched = 0
    for path in sorted(glob.glob(str(DATA_DIR / "preview-*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (json.JSONDecodeError, OSError) as err:
            LOG.warning("tag-audit: skipping preview", path=path, error=str(err))
            continue
        rows = payload.get("rows")
        row_ids = {str(r.get("tweet_id") or "") for r in rows} if isinstance(rows, list) else None
        tags_map = payload.get("tags")
        if not isinstance(tags_map, dict):
            tags_map = {}
        touched = 0
        for tweet_id, slot in by_tweet.items():
            if row_ids is not None and tweet_id not in row_ids:
                continue
            current = tags_map.get(tweet_id)
            merged, changed = _apply_to_entries(
                current,
                add=slot["add"],
                remove=slot["remove"],
                confirm=slot["confirm"],
                full_schema=False,
            )
            if changed:
                tags_map[tweet_id] = merged
                touched += 1
        payload["tags"] = tags_map
        if not dry_run:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        LOG.info("tag-audit: overlaid preview", path=path, touched=touched, dry_run=dry_run)
        total_touched += touched
    return total_touched


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    ap.add_argument("--sidecar-only", action="store_true", help="write only the sidecar parquet")
    args = ap.parse_args(argv)

    by_tweet = collect_decisions()
    n_add = sum(len(s["add"]) for s in by_tweet.values())
    n_remove = sum(len(s["remove"]) for s in by_tweet.values())
    n_confirm = sum(len(s["confirm"]) for s in by_tweet.values())
    LOG.info(
        "tag-audit: decision totals",
        tweets=len(by_tweet),
        add_tags=n_add,
        remove_tags=n_remove,
        confirm_tags=n_confirm,
    )
    if not by_tweet:
        LOG.warning("tag-audit: no decisions found; nothing to do")
        return 0

    if not args.dry_run:
        write_sidecar(by_tweet)
    if not args.sidecar_only:
        overlay_catalog(by_tweet, dry_run=args.dry_run)
        overlay_previews(by_tweet, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
