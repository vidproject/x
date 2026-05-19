"""Regenerate the auto-managed coverage section of README.md.

Reads ``data/manifest.json`` and rewrites the contents between the
``<!-- COVERAGE:START -->`` and ``<!-- COVERAGE:END -->`` markers with a
per-account stats table. Idempotent: a no-op run leaves the file unchanged.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from scripts._logging import configure

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "data" / "manifest.json"
README_PATH = REPO_ROOT / "README.md"
START_MARKER = "<!-- COVERAGE:START -->"
END_MARKER = "<!-- COVERAGE:END -->"


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
    text = readme_path.read_text(encoding="utf-8")
    if START_MARKER not in text or END_MARKER not in text:
        LOG.error(
            "README is missing coverage markers; skipping",
            start=START_MARKER,
            end=END_MARKER,
        )
        return False
    pre, _, rest = text.partition(START_MARKER)
    _, _, post = rest.partition(END_MARKER)
    new_body = "\n\n" + build_table(manifest).rstrip() + "\n\n"
    new_text = pre + START_MARKER + new_body + END_MARKER + post
    if new_text == text:
        LOG.info("README coverage section unchanged")
        return False
    tmp = readme_path.with_suffix(".tmp.md")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(readme_path)
    LOG.info("README coverage section updated")
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
