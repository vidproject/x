"""Bundle the Firefox extension source into ``extension.zip`` at repo root.

Pipeline:
  1. Clean ``extension/dist/``.
  2. Run esbuild on each TypeScript entry point, bundling into a single JS
     file per entry. Bundles include all imports from ``src/lib/*``.
  3. Copy manifest, HTML, CSS, and icons into ``dist/``.
  4. Zip ``dist/`` contents into ``extension.zip`` at the repo root.

Designed to be invoked via ``npm run build:extension`` or ``just
build-extension``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension"
SRC = EXT / "src"
DIST = EXT / "dist"
OUT_ZIP = ROOT / "extension.zip"

ENTRY_POINTS: list[tuple[str, str]] = [
    # (src path relative to extension/src, output filename in dist/)
    ("background.ts", "background.js"),
    ("content.ts", "content.js"),
    ("page-hook.ts", "page-hook.js"),
    ("sidebar.ts", "sidebar.js"),
    ("options.ts", "options.js"),
]

ASSETS: list[Path] = [
    EXT / "manifest.json",
    EXT / "sidebar.html",
    EXT / "options.html",
]

ASSET_DIRS: list[Path] = [
    EXT / "styles",
    EXT / "icons",
]


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, check=False)
    if result.returncode != 0:
        sys.exit(result.returncode)


def clean() -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    if OUT_ZIP.exists():
        OUT_ZIP.unlink()


def bundle() -> None:
    # Resolve the local esbuild binary.
    esbuild = ROOT / "node_modules" / ".bin" / "esbuild"
    if not esbuild.exists():
        sys.exit(
            "esbuild not found in node_modules/.bin. "
            "Run `npm install` (or `just setup`) before building."
        )

    for src_name, out_name in ENTRY_POINTS:
        src = SRC / src_name
        out = DIST / out_name
        # Use ESM for entries we load via `type: module`/`<script type=module>`,
        # IIFE for ones loaded as classic scripts (content + page-hook). The
        # page-hook executes in the page world and shouldn't introduce module
        # syntax (some pages have CSP that blocks it).
        fmt = "iife" if src_name in {"content.ts", "page-hook.ts"} else "esm"
        cmd = [
            str(esbuild),
            str(src),
            "--bundle",
            f"--outfile={out}",
            f"--format={fmt}",
            "--target=es2022",
            "--platform=browser",
            "--log-level=warning",
            "--sourcemap=inline",
        ]
        run(cmd)


def copy_assets() -> None:
    for path in ASSETS:
        shutil.copy2(path, DIST / path.name)
    for d in ASSET_DIRS:
        shutil.copytree(d, DIST / d.name, dirs_exist_ok=True)


def write_zip() -> None:
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for root in sorted(DIST.rglob("*")):
            if root.is_dir():
                continue
            arcname = root.relative_to(DIST).as_posix()
            zf.write(root, arcname)
    size = OUT_ZIP.stat().st_size
    print(f"wrote {OUT_ZIP} ({size:,} bytes)")


def verify_manifest() -> None:
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    required_files = [
        manifest["background"]["scripts"][0],
        manifest["content_scripts"][0]["js"][0],
        manifest["web_accessible_resources"][0]["resources"][0],
        manifest["sidebar_action"]["default_panel"],
        manifest["options_ui"]["page"],
    ]
    missing = [f for f in required_files if not (DIST / f).exists()]
    if missing:
        sys.exit(f"manifest references missing files in dist/: {missing}")


def main() -> None:
    clean()
    bundle()
    copy_assets()
    verify_manifest()
    write_zip()


if __name__ == "__main__":
    main()
