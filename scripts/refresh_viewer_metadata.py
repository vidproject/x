"""Refresh lightweight viewer metadata after Parquet-producing jobs.

Archive/media jobs can edit existing Parquet files without running the full
raw ingest. This script updates the root manifest, user profile sidecar,
full lightweight catalog, and legacy preview JSON files so the static viewer's
cache-busting and quick boot data stay aligned with the committed database.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from scripts import ingest
from scripts.build_viewer_preview import write_catalog, write_previews


def manifest_accounts_for_existing_data(
    accounts_path: Path, previous_manifest: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    tracked_accounts = ingest.load_accounts(accounts_path)
    by_handle = {a["handle"]: a for a in tracked_accounts}
    existing_order = existing_manifest_order(previous_manifest)
    ordered_handles = [h for h in existing_order if h in by_handle]
    ordered_handles.extend(
        a["handle"] for a in tracked_accounts if a["handle"] not in ordered_handles
    )
    accounts = [dict(by_handle[handle]) for handle in ordered_handles]
    if (ingest.DATA_DIR / f"{ingest.MISC_HANDLE}.parquet").exists():
        accounts.append(
            {
                "handle": ingest.MISC_HANDLE,
                "label": ingest.MISC_LABEL,
                "category": ingest.MISC_CATEGORY,
            }
        )
    return accounts


def existing_manifest_order(payload: dict[str, Any] | None) -> list[str]:
    if payload is None:
        return []
    return [
        str(account.get("handle"))
        for account in payload.get("accounts", [])
        if isinstance(account, dict) and account.get("handle") != ingest.MISC_HANDLE
    ]


def refresh(accounts_path: Path = ingest.CONFIG_PATH) -> dict[str, object]:
    previous_manifest = read_json_or_none(ingest.DATA_DIR / "manifest.json")
    manifest = ingest.build_manifest(
        manifest_accounts_for_existing_data(accounts_path, previous_manifest)
    )
    manifest = stable_generated_at(previous_manifest, manifest)
    ingest.write_manifest(manifest)
    write_users_stable(ingest.aggregate_users(), str(manifest["generated_at"]))
    write_catalog(ingest.DATA_DIR, generated_at=str(manifest["generated_at"]))
    write_previews(ingest.DATA_DIR, generated_at=str(manifest["generated_at"]))
    return manifest


def read_json_or_none(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def stable_generated_at(previous: dict[str, Any] | None, payload: dict[str, Any]) -> dict[str, Any]:
    if previous is None:
        return payload
    if without_generated_at(previous) == without_generated_at(payload):
        return {
            **payload,
            "generated_at": previous.get("generated_at", payload.get("generated_at")),
        }
    return payload


def without_generated_at(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {k: without_generated_at(v) for k, v in payload.items() if k != "generated_at"}
    if isinstance(payload, list):
        return [without_generated_at(v) for v in payload]
    return payload


def write_users_stable(users: dict[str, dict[str, Any]], generated_at: str) -> None:
    payload = stable_generated_at(
        read_json_or_none(ingest.DATA_DIR / "users.json"),
        {
            "generated_at": generated_at,
            "users": users,
        },
    )
    tmp = ingest.DATA_DIR / "users.tmp.json"
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, ingest.DATA_DIR / "users.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accounts", type=Path, default=ingest.CONFIG_PATH)
    args = parser.parse_args(argv)
    refresh(args.accounts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
