"""Build a corpus-wide account category sidecar.

``config/accounts.yaml`` only lists accounts the extension actively
captures into per-handle parquet files. The archive also contains many
accounts in ``data/_misc.parquet`` because tracked accounts replied to,
quoted, retweeted, or mentioned them. This script classifies those
observed accounts from ``data/users.json`` so the viewer can distinguish
government agencies, federal executive officials, and public figures
without turning every referenced person into a tracked capture target.

Run with: ``uv run python -m scripts.build_account_categories``
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from scripts._logging import configure
from scripts.build_viewer_preview import stabilize_volatile

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
CONFIG_PATH = REPO_ROOT / "config" / "accounts.yaml"
USERS_PATH = DATA_DIR / "users.json"
OUT_PATH = DATA_DIR / "account_categories.json"

SCHEMA_VERSION = 1
VALID_CATEGORIES = ("core", "government", "officials", "public_figures", "public")

FEDERAL_OFFICIAL_RE = re.compile(
    r"\b("
    r"(?:acting\s+)?secretary|attorney\s+general|administrator|commissioner|director|"
    r"assistant\s+to\s+the\s+president|special\s+assistant\s+to\s+the\s+president|"
    r"white\s+house\s+(?:press\s+)?secretary|chief\s+of\s+staff|deputy\s+chief\s+of\s+staff|"
    r"u\.?s\.?\s+attorney|inspector\s+general|border\s+czar|first\s+lady|"
    r"director\s+of\s+national\s+intelligence|dni\b"
    r")\b",
    re.I,
)
PUBLIC_FIGURE_RE = re.compile(
    r"\b("
    r"senator|u\.?s\.?\s+senator|congress(?:man|woman)?|member\s+of\s+congress|"
    r"represent(?:ative|ing)|\brep\.?\b|governor|lt\.?\s+governor|mayor|"
    r"speaker\s+of\s+the\s+house|majority\s+leader|minority\s+leader|"
    r"state\s+representative|state\s+senator|delegate|candidate|running\s+for"
    r")\b",
    re.I,
)
AGENCY_OR_OFFICE_RE = re.compile(
    r"\b("
    r"official\s+(?:x\s+)?account|official\s+(?:twitter\s+)?account|official\s+page|"
    r"department|agency|administration|office\s+of|task\s+force|committee|"
    r"u\.?s\.?\s+(?:coast\s+guard|marshals?|attorney|embassy)|"
    r"customs\s+and\s+border\s+protection|border\s+patrol|homeland\s+security|"
    r"immigration\s+and\s+customs\s+enforcement|citizenship\s+and\s+immigration\s+services|"
    r"federal\s+bureau\s+of\s+investigation|department\s+of\s+justice|"
    r"transportation\s+security\s+administration|central\s+command|southern\s+command"
    r")\b",
    re.I,
)
NEWS_OR_MEDIA_RE = re.compile(
    r"\b(reporter|journalist|correspondent|anchor|news|magazine|politico|reuters|fox|cnn|msnbc|cbs|abc|nbc)\b",
    re.I,
)
FORMER_PERSONAL_SERVICE_RE = re.compile(
    r"\b(?:ret\.?|retired|former|ex)[-\s]+(?:deputy\s+)?"
    r"(?:u\.?s\.?\s+)?(?:marshal|sheriff|police|officer|agent|investigator|army|navy|marine|air\s+force|soldier)\b"
    r"|\b(?:deputy\s+)?(?:u\.?s\.?\s+)?marshal\s+ret\.?\b",
    re.I,
)
MILITARY_SERVICE_RE = re.compile(
    r"\b("
    r"veteran|vet\b|retired\s+(?:u\.?s\.?\s+)?(?:army|navy|marine|air\s+force|space\s+force)|"
    r"(?:u\.?s\.?\s+)?(?:army|navy|marines?|air\s+force|space\s+force)\b"
    r")",
    re.I,
)
POLICE_SERVICE_RE = re.compile(
    r"\b("
    r"police|sheriff|law\s+enforcement|border\s+patrol\s+agent|"
    r"(?:deputy\s+)?(?:u\.?s\.?\s+)?marshal|criminal\s+investigator"
    r")\b",
    re.I,
)
RETIRED_POLICE_SERVICE_RE = re.compile(
    r"\b(?:ret\.?|retired|former|ex)[-\s]+(?:deputy\s+)?"
    r"(?:police|sheriff|law\s+enforcement|border\s+patrol\s+agent|"
    r"(?:u\.?s\.?\s+)?marshal|criminal\s+investigator)\b"
    r"|\b(?:deputy\s+)?(?:u\.?s\.?\s+)?marshal\s+ret\.?\b",
    re.I,
)
RETIRED_GOVERNMENT_SERVICE_RE = re.compile(
    r"\b(?:ret\.?|retired|former|ex)[-\s]+"
    r"(?:secretary|administrator|commissioner|director|u\.?s\.?\s+attorney|"
    r"inspector\s+general|civil\s+servant|federal\s+employee|government\s+official)\b",
    re.I,
)


def load_config_accounts() -> dict[str, dict[str, str]]:
    if not CONFIG_PATH.exists():
        return {}
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    out: dict[str, dict[str, str]] = {}
    for entry in data.get("accounts", []):
        if not isinstance(entry, dict):
            continue
        handle = str(entry.get("handle") or "").strip()
        if not handle:
            continue
        category = str(entry.get("category") or "core").strip()
        if category not in VALID_CATEGORIES:
            category = "core"
        out[handle] = {
            "category": category,
            "label": str(entry.get("label") or handle).strip(),
            "source": "config/accounts.yaml",
            "reason": "tracked-account override",
        }
    return out


def load_users() -> dict[str, dict[str, Any]]:
    if not USERS_PATH.exists():
        return {}
    data = json.loads(USERS_PATH.read_text(encoding="utf-8"))
    users = data.get("users", data)
    return users if isinstance(users, dict) else {}


def observed_handles() -> tuple[set[str], Counter[str]]:
    handles: set[str] = set()
    counts: Counter[str] = Counter()
    for path in DATA_DIR.glob("*.parquet"):
        if path.name == "catalog.parquet":
            continue
        df = pl.read_parquet(path)
        for col in ("account_handle", "reply_to_account"):
            if col not in df.columns:
                continue
            for value in df[col].drop_nulls().to_list():
                handle = str(value or "").lstrip("@")
                if handle:
                    handles.add(handle)
                    counts[handle] += 1
        if "mentions" in df.columns:
            for values in df["mentions"].to_list():
                if not isinstance(values, list):
                    continue
                for value in values:
                    handle = str(value or "").lstrip("@")
                    if handle:
                        handles.add(handle)
                        counts[handle] += 1
    return handles, counts


def classify(handle: str, user: dict[str, Any]) -> dict[str, str] | None:
    display_name = str(user.get("display_name") or handle)
    description = str(user.get("description") or "")
    verified_type = str(user.get("verified_type") or "")
    text = " ".join([handle, display_name, description])
    text_l = text.lower()

    if NEWS_OR_MEDIA_RE.search(text) and verified_type != "Government":
        return None

    if PUBLIC_FIGURE_RE.search(text):
        return {
            "category": "public_figures",
            "label": display_name,
            "source": "data/users.json",
            "reason": "elected/public-office language in profile",
        }

    if FEDERAL_OFFICIAL_RE.search(text):
        return {
            "category": "officials",
            "label": display_name,
            "source": "data/users.json",
            "reason": "federal executive official language in profile",
        }

    if verified_type == "Government" or AGENCY_OR_OFFICE_RE.search(text):
        if verified_type != "Government" and FORMER_PERSONAL_SERVICE_RE.search(text):
            return None
        if "personal account" in text_l and not AGENCY_OR_OFFICE_RE.search(display_name):
            return None
        return {
            "category": "government",
            "label": display_name,
            "source": "data/users.json",
            "reason": "government verified type or official agency/office language",
        }

    return None


def service_badges(user: dict[str, Any]) -> list[str]:
    display_name = str(user.get("display_name") or "")
    description = str(user.get("description") or "")
    verified_type = str(user.get("verified_type") or "")
    text = " ".join([display_name, description])
    badges: list[str] = []
    if MILITARY_SERVICE_RE.search(text):
        badges.append("veteran")
    if RETIRED_POLICE_SERVICE_RE.search(text):
        badges.append("retired-police")
    elif POLICE_SERVICE_RE.search(text):
        badges.append("police")
    if verified_type != "Government" and RETIRED_GOVERNMENT_SERVICE_RE.search(text):
        badges.append("retired-government")
    return badges


def build() -> dict[str, Any]:
    config_accounts = load_config_accounts()
    users = load_users()
    handles, counts = observed_handles()
    handles.update(users.keys())
    categories: dict[str, dict[str, Any]] = {}

    for handle in sorted(handles):
        if handle in config_accounts:
            categories[handle] = {**config_accounts[handle], "observations": counts.get(handle, 0)}
            continue
        user = users.get(handle)
        if not isinstance(user, dict):
            continue
        result = classify(handle, user)
        badges = service_badges(user)
        if not result and not badges:
            continue
        if not result:
            result = {
                "category": "public",
                "label": str(user.get("display_name") or handle),
                "source": "data/users.json",
                "reason": "service-history badge only",
            }
        if badges:
            result = {**result, "badges": badges}
        categories[handle] = {**result, "observations": counts.get(handle, 0)}

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "config/accounts.yaml + data/users.json + data/*.parquet",
        "category_counts": dict(
            sorted(Counter(v["category"] for v in categories.values()).items())
        ),
        "categories": categories,
    }


def write(payload: dict[str, Any], path: Path = OUT_PATH) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Classification is fully deterministic from the parquet corpus + users.json,
    # so the only field that changes on an unchanged corpus is ``generated_at``.
    # Reuse the committed timestamp in that case to avoid a churn commit (and the
    # Pages redeploy it triggers) on every ingest run.
    payload = stabilize_volatile(path, payload)
    tmp = path.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Build and report without writing.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build()
    if not args.check:
        write(payload)
    LOG.info(
        "account categories built",
        handles=len(payload["categories"]),
        category_counts=payload["category_counts"],
        written=not args.check,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
