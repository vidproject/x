"""Bundle the browser extension source into a loadable WebExtension package.

Pipeline:
  1. Clean ``extension/dist/``.
  2. Run esbuild on each TypeScript entry point, bundling into a single JS
     file per entry. Bundles include all imports from ``src/lib/*``.
  3. Copy/transform manifest, HTML, CSS, and icons into the dist folder.
  4. Zip the dist contents at the repo root.

Designed to be invoked via ``npm run build:extension`` or ``just
build-extension``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "extension"
SRC = EXT / "src"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
DEFAULT_BROWSER = "firefox"

ENTRY_POINTS: list[tuple[str, str]] = [
    # (src path relative to extension/src, output filename in dist/)
    ("background.ts", "background.js"),
    ("content.ts", "content.js"),
    ("page-hook.ts", "page-hook.js"),
    ("sidebar.ts", "sidebar.js"),
    ("options.ts", "options.js"),
]

ASSETS: list[Path] = [
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


def dist_for(browser: str) -> Path:
    return EXT / ("dist" if browser == "firefox" else f"dist-{browser}")


def zip_for(browser: str) -> Path:
    return ROOT / ("extension.zip" if browser == "firefox" else f"extension-{browser}.zip")


def clean(dist: Path, out_zip: Path) -> None:
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)
    if out_zip.exists():
        out_zip.unlink()


def bundle(dist: Path) -> None:
    # Resolve the local esbuild binary.
    esbuild = ROOT / "node_modules" / ".bin" / "esbuild"
    if sys.platform == "win32":
        esbuild = esbuild.with_suffix(".cmd")
    if not esbuild.exists():
        sys.exit(
            "esbuild not found in node_modules/.bin. "
            "Run `npm install` (or `just setup`) before building."
        )

    for src_name, out_name in ENTRY_POINTS:
        src = SRC / src_name
        out = dist / out_name
        # Use ESM for entries we load via `type: module`/`<script type=module>`,
        # IIFE for ones loaded as classic scripts (content + page-hook). The
        # page-hook executes in the page world and shouldn't introduce module
        # syntax (some pages have CSP that blocks it).
        fmt = "iife" if src_name in {"content.ts", "page-hook.ts"} else "esm"
        cmd = [
            str(esbuild),
            f"./{src.relative_to(EXT).as_posix()}",
            "--bundle",
            f"--outfile={out.relative_to(EXT).as_posix()}",
            f"--format={fmt}",
            "--banner:js=var browser=globalThis.browser??globalThis.chrome;",
            "--target=es2022",
            "--platform=browser",
            "--log-level=warning",
            "--sourcemap=inline",
        ]
        run(cmd, cwd=EXT)


def load_manifest(browser: str) -> dict[str, object]:
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    if browser == "firefox":
        return manifest
    if browser != "chrome":
        sys.exit(f"unsupported browser target: {browser}")

    background = manifest.get("background")
    if not isinstance(background, dict):
        sys.exit("manifest background must be an object")
    scripts = background.get("scripts")
    if not isinstance(scripts, list) or not scripts:
        sys.exit("manifest background.scripts must list background.js")

    manifest.pop("browser_specific_settings", None)
    manifest["background"] = {
        "service_worker": scripts[0],
        "type": background.get("type", "module"),
    }

    sidebar = manifest.pop("sidebar_action", None)
    if isinstance(sidebar, dict) and isinstance(sidebar.get("default_panel"), str):
        manifest["side_panel"] = {"default_path": sidebar["default_panel"]}

    permissions = manifest.get("permissions")
    if isinstance(permissions, list) and "sidePanel" not in permissions:
        permissions.append("sidePanel")

    options = manifest.get("options_ui")
    if isinstance(options, dict):
        options.pop("browser_style", None)

    return manifest


def copy_assets(dist: Path, browser: str) -> None:
    manifest = load_manifest(browser)
    (dist / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    for path in ASSETS:
        shutil.copy2(path, dist / path.name)
    for d in ASSET_DIRS:
        shutil.copytree(d, dist / d.name, dirs_exist_ok=True)


def write_zip(dist: Path, out_zip: Path) -> None:
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for root in sorted(dist.rglob("*")):
            if root.is_dir():
                continue
            arcname = root.relative_to(dist).as_posix()
            info = zipfile.ZipInfo(arcname, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, root.read_bytes())
    size = out_zip.stat().st_size
    print(f"wrote {out_zip} ({size:,} bytes)")


def verify_manifest(dist: Path, browser: str) -> None:
    manifest = json.loads((dist / "manifest.json").read_text(encoding="utf-8"))
    background = manifest["background"]
    background_file = (
        background["scripts"][0] if browser == "firefox" else background["service_worker"]
    )
    panel_file = (
        manifest["sidebar_action"]["default_panel"]
        if browser == "firefox"
        else manifest["side_panel"]["default_path"]
    )
    required_files = [
        background_file,
        manifest["content_scripts"][0]["js"][0],
        manifest["web_accessible_resources"][0]["resources"][0],
        panel_file,
        manifest["options_ui"]["page"],
    ]
    missing = [f for f in required_files if not (dist / f).exists()]
    if missing:
        sys.exit(f"manifest references missing files in dist/: {missing}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", choices=["firefox", "chrome"], default=DEFAULT_BROWSER)
    args = parser.parse_args()

    dist = dist_for(args.browser)
    out_zip = zip_for(args.browser)
    clean(dist, out_zip)
    bundle(dist)
    copy_assets(dist, args.browser)
    verify_manifest(dist, args.browser)
    write_zip(dist, out_zip)


if __name__ == "__main__":
    main()
