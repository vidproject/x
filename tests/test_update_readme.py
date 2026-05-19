from __future__ import annotations

from pathlib import Path

from scripts import ingest, update_readme
from tests.conftest import make_capture, make_tweet, write_capture


def test_coverage_table_written_between_markers(tmp_repo: Path) -> None:
    write_capture(
        tmp_repo, "test-handle", "01.json", make_capture([make_tweet("1"), make_tweet("2")])
    )
    ingest.main([])
    assert update_readme.update()
    readme = (tmp_repo / "README.md").read_text(encoding="utf-8")
    assert "@test-handle" in readme
    assert "<!-- COVERAGE:START -->" in readme
    assert "<!-- COVERAGE:END -->" in readme
    # Idempotent on second run.
    assert update_readme.update() is False


def test_missing_manifest_leaves_empty_section(tmp_repo: Path) -> None:
    # No ingest run yet → no manifest.
    assert update_readme.update()
    readme = (tmp_repo / "README.md").read_text(encoding="utf-8")
    assert "No captures yet" in readme
