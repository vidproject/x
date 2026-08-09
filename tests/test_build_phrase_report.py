from __future__ import annotations

from scripts.build_phrase_report import match_phrases


def keys(text: str) -> set[str]:
    return set(match_phrases(text))


def test_matches_plural_hyphenated_and_line_break_variants() -> None:
    text = (
        "Criminal aliens, a criminal-illegal-alien, and illegal\naliens. "
        "Angel Moms stood with Angel-Families."
    )
    assert keys(text) == {
        "criminal-alien",
        "illegal-alien",
        "angel-mother",
        "angel-family",
    }


def test_criminal_illegal_alien_overlaps_illegal_alien_family() -> None:
    matches = match_phrases("A criminal illegal alien was arrested.")
    assert set(matches) == {"criminal-alien", "illegal-alien"}
    assert matches["criminal-alien"][0][0] == "criminal illegal alien"
    assert matches["illegal-alien"][0][0] == "illegal alien"


def test_angel_mother_includes_mom_but_not_unrelated_words() -> None:
    assert "angel-mother" in keys("Angel Mother, Angel Mothers, Angel Mom, Angel Moms")
    assert not keys("An angelic mother discussed alienation and a family angel.")
