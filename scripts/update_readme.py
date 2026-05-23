"""Regenerate the auto-managed sections of README.md.

Reads ``data/manifest.json`` (and, for the deeper gap metrics, the per-handle
``data/*.parquet`` archives) and rewrites the contents between three pairs of
managed markers, mirroring the same in-place pattern:

* ``<!-- COVERAGE:START -->`` … ``<!-- COVERAGE:END -->`` — per-account stats
  table.
* ``<!-- CORE_ACCOUNTS:START -->`` … ``<!-- CORE_ACCOUNTS:END -->`` — the list
  of tracked accounts, generated from the accounts config (via the manifest)
  rather than hand-maintained.
* ``<!-- GAPS:START -->`` … ``<!-- GAPS:END -->`` — a collapsible
  ``<details>`` block summarising known coverage gaps and caveats, computed
  from the data.

Each section is regenerated independently and only when its markers are
present, so a README that carries only some of them still updates cleanly.
Idempotent: a no-op run leaves the file unchanged and returns ``False``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts._logging import configure

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "data" / "manifest.json"
README_PATH = REPO_ROOT / "README.md"

# Stems under data/ that are not per-handle archives, so the gap pass skips
# them when scanning parquet files.
NON_HANDLE_PARQUET_STEMS = frozenset({"catalog"})
# The consolidated "everyone else" bucket. Counted in corpus-wide gap totals
# but excluded from the tracked-account list and per-account staleness checks.
MISC_HANDLE = "_misc"

# An account is flagged "stale" when its newest captured post is older than
# this many days relative to the manifest's generation time. Tracked agency
# timelines normally produce something within a week; a longer gap usually
# means the account went quiet or capture stalled.
STALE_POST_DAYS = 30

START_MARKER = "<!-- COVERAGE:START -->"
END_MARKER = "<!-- COVERAGE:END -->"
CORE_START_MARKER = "<!-- CORE_ACCOUNTS:START -->"
CORE_END_MARKER = "<!-- CORE_ACCOUNTS:END -->"
GAPS_START_MARKER = "<!-- GAPS:START -->"
GAPS_END_MARKER = "<!-- GAPS:END -->"

# Human-readable headings for the account categories declared in
# config/accounts.yaml, in the order they should appear.
CATEGORY_LABELS: list[tuple[str, str]] = [
    ("core", "Federal agencies and White House principals"),
    ("government", "Other government accounts"),
    ("officials", "Federal officials (personal accounts)"),
    ("public_figures", "Other public figures"),
]


def fmt_int(n: int | None) -> str:
    if n is None:
        return "—"
    return f"{n:,}"


def fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return iso[:10]


def parse_iso(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def build_table(manifest: dict[str, object]) -> str:
    accounts = manifest.get("accounts", [])
    if not isinstance(accounts, list) or not accounts:
        return "_No captures yet. The Firefox extension hasn't committed any tweets._"
    rows: list[str] = []
    rows.append(
        "| Handle | Label | Tweets | First post | Latest post | Latest capture | Media | Videos |"
    )
    rows.append(
        "| ------ | ----- | -----: | ---------- | ----------- | -------------- | ----: | -----: |"
    )
    for a in accounts:
        if not isinstance(a, dict):
            continue
        rows.append(
            "| `@{handle}` | {label} | {rows} | {first} | {last} | {capture} | {media} | {videos} |".format(
                handle=a.get("handle", "?"),
                label=str(a.get("label", "")).replace("|", "\\|"),
                rows=fmt_int(a.get("row_count")),
                first=fmt_date(a.get("first_post_at")),
                last=fmt_date(a.get("latest_post_at")),
                capture=fmt_date(a.get("latest_capture_at")),
                media=fmt_int(a.get("media_count")),
                videos=fmt_int(a.get("video_count")),
            )
        )
    generated_at = manifest.get("generated_at", "")
    if generated_at:
        rows.append("")
        rows.append(f"_Generated {generated_at}._")
    return "\n".join(rows)


def build_core_accounts(manifest: dict[str, object]) -> str:
    """Render the tracked-account list, grouped by category, from the manifest.

    The manifest's account entries carry the ``handle``/``label``/``category``
    that ``scripts.ingest`` copied straight from ``config/accounts.yaml``, so
    this stays in sync with the source of truth without a second hardcoded
    copy. The ``_misc`` bucket is described separately, not listed.
    """
    accounts = manifest.get("accounts", [])
    tracked = [
        a
        for a in accounts
        if isinstance(a, dict) and a.get("handle") and a.get("handle") != MISC_HANDLE
    ]
    if not tracked:
        return "_No accounts configured yet._"

    by_category: dict[str, list[dict[str, Any]]] = {}
    for a in tracked:
        by_category.setdefault(str(a.get("category") or "core"), []).append(a)

    lines: list[str] = []
    rendered: set[str] = set()

    def render_group(handles: list[dict[str, Any]]) -> None:
        for a in handles:
            handle = a.get("handle")
            label = str(a.get("label", "")).strip()
            suffix = f" — {label}" if label and label != f"@{handle}" else ""
            lines.append(f"- `@{handle}`{suffix}")

    for category, heading in CATEGORY_LABELS:
        group = by_category.get(category)
        if not group:
            continue
        rendered.add(category)
        # Only print a heading when more than one category is in play, so a
        # corpus that is purely "core" stays a clean flat list.
        lines.append(f"**{heading}:**")
        lines.append("")
        render_group(group)
        lines.append("")

    # Any category not covered by CATEGORY_LABELS still gets listed so nothing
    # silently disappears if accounts.yaml grows a new category.
    leftover = [c for c in by_category if c not in rendered]
    for category in sorted(leftover):
        lines.append(f"**{category}:**")
        lines.append("")
        render_group(by_category[category])
        lines.append("")

    # Collapse to a flat bullet list when there was only a single heading.
    headings = [ln for ln in lines if ln.startswith("**")]
    if len(headings) == 1:
        lines = [ln for ln in lines if not ln.startswith("**") and ln != ""]

    body = "\n".join(ln for ln in lines).strip()
    body += (
        "\n\nThe archive also preserves the replies, quotes, retweets, and "
        "public accounts that appear in captured threads "
        f"(consolidated into `data/{MISC_HANDLE}.parquet`)."
    )
    return body


def _scan_parquet_gaps(data_dir: Path) -> dict[str, Any]:
    """Compute media/text gap metrics that aren't in the manifest.

    Reads the per-handle parquet archives directly for media ``archive_status``
    distribution, truncated-tweet count, and tweets X no longer serves. Returns
    zeros (and ``available=False``) when polars or the parquet data is missing,
    so the caller can degrade gracefully rather than crash.
    """
    out: dict[str, Any] = {
        "available": False,
        "media_total": 0,
        "media_archived": 0,
        "media_pending": 0,
        "media_failed": 0,
        "truncated": 0,
        "unavailable": 0,
    }
    if not data_dir.exists():
        return out
    try:
        import polars as pl
    except Exception:  # pragma: no cover - polars is a hard dep in practice
        LOG.warning("polars unavailable; skipping parquet gap metrics")
        return out

    parquets = [
        p
        for p in sorted(data_dir.glob("*.parquet"))
        if p.stem not in NON_HANDLE_PARQUET_STEMS
    ]
    if not parquets:
        return out

    media_counts: dict[str, int] = {}
    truncated = 0
    unavailable = 0
    for path in parquets:
        try:
            df = pl.read_parquet(path, columns=["media", "is_truncated", "unavailable_detected_at"])
        except Exception:
            LOG.warning("could not read parquet for gap metrics", path=str(path))
            continue
        if df.height == 0:
            continue
        truncated += int(df.select(pl.col("is_truncated").fill_null(False).sum()).item() or 0)
        unavailable += int(
            df.filter(pl.col("unavailable_detected_at").is_not_null()).height
        )
        statuses = (
            df.select("media")
            .explode("media")
            .drop_nulls("media")
            .unnest("media")
            .group_by("archive_status")
            .len()
        )
        for row in statuses.iter_rows(named=True):
            key = str(row.get("archive_status") or "unknown")
            media_counts[key] = media_counts.get(key, 0) + int(row.get("len") or 0)

    media_total = sum(media_counts.values())
    out.update(
        available=True,
        media_total=media_total,
        media_archived=media_counts.get("archived", 0),
        media_pending=media_counts.get("pending", 0),
        media_failed=media_counts.get("failed", 0),
        truncated=truncated,
        unavailable=unavailable,
    )
    return out


def compute_gaps(manifest: dict[str, object], data_dir: Path) -> dict[str, Any]:
    """Gather coverage-gap metrics from the manifest plus parquet data."""
    accounts = [a for a in manifest.get("accounts", []) if isinstance(a, dict)]
    generated = parse_iso(str(manifest.get("generated_at") or "")) if accounts else None

    deleted = sum(int(a.get("deleted_count") or 0) for a in accounts)
    stale: list[tuple[str, int]] = []
    no_media: list[str] = []
    for a in accounts:
        handle = str(a.get("handle") or "")
        if not handle or handle == MISC_HANDLE:
            continue
        latest_post = parse_iso(a.get("latest_post_at"))
        if generated and latest_post:
            age = (generated - latest_post).days
            if age >= STALE_POST_DAYS:
                stale.append((handle, age))
        if int(a.get("media_count") or 0) == 0 and int(a.get("row_count") or 0) > 0:
            no_media.append(handle)
    stale.sort(key=lambda x: x[1], reverse=True)

    parquet = _scan_parquet_gaps(data_dir)

    return {
        "accounts": accounts,
        "deleted": deleted,
        "stale": stale,
        "no_media": no_media,
        **parquet,
    }


def build_gaps(manifest: dict[str, object], data_dir: Path) -> str:
    """Render the collapsible coverage-gaps block from live metrics."""
    g = compute_gaps(manifest, data_dir)
    accounts = g["accounts"]
    if not accounts:
        return (
            "<details>\n<summary>Known coverage gaps and caveats</summary>\n\n"
            "_No captures yet, so there is nothing to report._\n\n"
            "</details>"
        )

    items: list[str] = []

    if g["available"] and g["media_total"]:
        pct = 100.0 * g["media_archived"] / g["media_total"] if g["media_total"] else 0.0
        items.append(
            f"**Media not yet archived to GitHub Releases:** {fmt_int(g['media_pending'])} of "
            f"{fmt_int(g['media_total'])} media items are still `pending` "
            f"({fmt_int(g['media_archived'])} archived, {pct:.0f}%). Until a media item is "
            "uploaded its row keeps only the original (expiring) X CDN URL."
        )
        if g["media_failed"]:
            items.append(
                f"**Failed media archives:** {fmt_int(g['media_failed'])} media items have "
                "`archive_status = failed` after retries and may be unrecoverable."
            )

    if g["truncated"]:
        items.append(
            f"**Long-form tweets awaiting full text:** {fmt_int(g['truncated'])} rows are "
            "`is_truncated` (captured as a 280-character head) and queued for detail-page refetch."
        )

    if g["unavailable"]:
        items.append(
            f"**Tweets X no longer serves:** {fmt_int(g['unavailable'])} archived rows are now "
            "flagged unavailable (suspended, deleted, or otherwise removed upstream); the captured "
            "copy is retained."
        )

    if g["deleted"]:
        items.append(
            f"**Deletions detected after capture:** {fmt_int(g['deleted'])} tweets were seen live "
            "and later detected as deleted; the archived copy is kept."
        )

    if g["stale"]:
        preview = ", ".join(f"`@{h}` ({age}d)" for h, age in g["stale"][:5])
        more = "" if len(g["stale"]) <= 5 else f", and {len(g['stale']) - 5} more"
        items.append(
            f"**Accounts with no recent posts (>{STALE_POST_DAYS}d):** {preview}{more}. "
            "These may be quiet accounts or stalled captures."
        )

    if g["no_media"]:
        preview = ", ".join(f"`@{h}`" for h in g["no_media"][:8])
        items.append(f"**Accounts with archived tweets but no media yet:** {preview}.")

    if not items:
        items.append(
            "No notable gaps detected: all captured media is archived and no tweets are flagged "
            "truncated, unavailable, or deleted."
        )

    summary = "Known coverage gaps and caveats"
    body_lines = [f"- {line}" for line in items]
    generated_at = manifest.get("generated_at", "")
    tail = f"\n\n_Generated {generated_at}._" if generated_at else ""
    return (
        f"<details>\n<summary>{summary}</summary>\n\n"
        + "\n".join(body_lines)
        + tail
        + "\n\n</details>"
    )


def _replace_between(text: str, start: str, end: str, body: str) -> str | None:
    """Return ``text`` with the region between markers replaced by ``body``.

    Returns ``None`` when either marker is absent (so the caller can skip the
    section without treating it as an error).
    """
    if start not in text or end not in text:
        return None
    pre, _, rest = text.partition(start)
    _, _, post = rest.partition(end)
    new_body = "\n\n" + body.rstrip() + "\n\n"
    return pre + start + new_body + end + post


def update(readme_path: Path | None = None, manifest_path: Path | None = None) -> bool:
    readme_path = readme_path or README_PATH
    manifest_path = manifest_path or MANIFEST_PATH
    if not readme_path.exists():
        LOG.error("README.md missing", path=str(readme_path))
        return False
    if not manifest_path.exists():
        LOG.warning("manifest missing; coverage section will be empty", path=str(manifest_path))
        manifest: dict[str, object] = {"accounts": []}
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    data_dir = manifest_path.parent

    text = readme_path.read_text(encoding="utf-8")
    original = text

    if START_MARKER not in text or END_MARKER not in text:
        LOG.error(
            "README is missing coverage markers; skipping",
            start=START_MARKER,
            end=END_MARKER,
        )
        return False

    sections: list[tuple[str, str, str]] = [
        (START_MARKER, END_MARKER, build_table(manifest)),
        (CORE_START_MARKER, CORE_END_MARKER, build_core_accounts(manifest)),
        (GAPS_START_MARKER, GAPS_END_MARKER, build_gaps(manifest, data_dir)),
    ]
    for start, end, body in sections:
        replaced = _replace_between(text, start, end, body)
        if replaced is None:
            if start is not START_MARKER:
                LOG.info("section markers absent; skipping", start=start)
            continue
        text = replaced

    if text == original:
        LOG.info("README managed sections unchanged")
        return False

    tmp = readme_path.with_suffix(".tmp.md")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(readme_path)
    LOG.info("README managed sections updated")
    return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--readme", type=Path, default=None)
    p.add_argument("--manifest", type=Path, default=None)
    args = p.parse_args(argv)
    update(args.readme, args.manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
