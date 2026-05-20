"""Regression coverage for the Layer-1 lexical tagger.

Tests assert that the deterministic rules in `scripts/tag_lexical.py`
fire (or stay silent) on representative text. Country / state / crime
vocab lists are covered with one positive case per family rather than
exhaustively — when a vocab entry is added to those tables, this file
doesn't have to grow proportionally.
"""

from __future__ import annotations

from typing import Any

from scripts.tag_lexical import tag_text


def _tags(out: list[dict[str, Any]]) -> set[str]:
    return {str(e["tag"]) for e in out}


def test_format_tags_derive_from_tweet_type() -> None:
    for tt, expected in (
        ("retweet", "format:retweet"),
        ("quote", "format:quote"),
        ("reply", "format:reply"),
    ):
        out = tag_text(
            "anything", tweet_type=tt, mentions=[], media_count=0, account_category="core"
        )
        assert expected in _tags(out)


def test_format_tag_absent_for_original_tweets() -> None:
    out = tag_text(
        "regular post", tweet_type="original", mentions=[], media_count=0, account_category="core"
    )
    assert not any(t.startswith("format:") for t in _tags(out))


def test_immigration_default_applies_to_tracked_categories() -> None:
    out = tag_text(
        "Border Patrol agents detained 3.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    assert "topic:immigration" in _tags(out)


def test_immigration_confirmed_when_explicit_signal_present() -> None:
    # "Border" + "detained" both fire confirming rules.
    out = tag_text(
        "Border Patrol agents detained 3 illegal aliens.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    imm = next(e for e in out if e["tag"] == "topic:immigration")
    assert not imm["tentative"]


def test_immigration_tentative_for_template_light_tweets() -> None:
    # No origin / no enforcement verb / no immigration keyword. Pure
    # branding text from a tracked-tier author => tentative immigration.
    out = tag_text(
        "Have a great Tuesday, everyone!",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    imm = next(e for e in out if e["tag"] == "topic:immigration")
    assert imm["tentative"] is True


def test_immigration_default_suppressed_on_obvious_off_topic_signals() -> None:
    out = tag_text(
        "Happy birthday to Secretary Smith.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    assert "topic:immigration" not in _tags(out)


def test_immigration_default_off_for_public_authors() -> None:
    out = tag_text(
        "ICE arrested another felon today.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="public",
    )
    assert "topic:immigration" not in _tags(out)


def test_frame_criminal_and_action_combo_marks_enforcement_op() -> None:
    out = tag_text(
        "John Smith, a criminal illegal alien from Mexico, convicted of assault, "
        "was arrested by ICE on Friday in Houston, Texas.",
        tweet_type="reply",
        mentions=[],
        media_count=1,
        account_category="core",
    )
    tags = _tags(out)
    assert "frame:criminal" in tags
    assert "action:detention" in tags
    assert "subject:enforcement-op" in tags
    # Vocabulary-validated extractions.
    assert "origin:Mexico" in tags
    assert "country:Mexico" in tags
    assert "state:Texas" in tags
    assert "crime:assault" in tags
    # Composite: reply + frame:criminal + exactly 1 photo.
    assert "shape:lineup" in tags


def test_action_report_immigrants_matches_direct_reporting_appeals() -> None:
    examples = (
        "Call ICE at 866-DHS-2-ICE to report illegal aliens in your community.",
        "Report criminal aliens to ICE today.",
        "Submit a tip to DHS about immigration violations.",
    )
    for text in examples:
        out = tag_text(
            text, tweet_type="original", mentions=[], media_count=0, account_category="core"
        )
        assert "action:report-immigrants" in _tags(out), text


def test_action_report_immigrants_avoids_generic_ice_mentions() -> None:
    examples = (
        "ICE reported that it arrested three people yesterday.",
        "Call your senator about ICE oversight.",
        "The ICE report on immigrant removals was released today.",
        "Submit a tip to DHS about disaster fraud.",
        "If you or someone you know was victimized by this predator, contact ICE: 866-DHS-2ICE.",
    )
    for text in examples:
        out = tag_text(
            text, tweet_type="original", mentions=[], media_count=0, account_category="core"
        )
        assert "action:report-immigrants" not in _tags(out), text


def test_shape_lineup_requires_all_three_conditions() -> None:
    # Right text + photo but not a reply.
    out = tag_text(
        "Jane Doe, a criminal illegal alien, was arrested.",
        tweet_type="original",
        mentions=[],
        media_count=1,
        account_category="core",
    )
    assert "shape:lineup" not in _tags(out)
    # Right text + reply but no photo.
    out = tag_text(
        "Jane Doe, a criminal illegal alien, was arrested.",
        tweet_type="reply",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    assert "shape:lineup" not in _tags(out)


def test_agency_tag_derives_from_mentions() -> None:
    out = tag_text(
        "Working with @ICEgov and @CBP to keep America safe.",
        tweet_type="original",
        mentions=["ICEgov", "CBP", "JohnDoe1234"],
        media_count=0,
        account_category="core",
    )
    tags = _tags(out)
    assert "agency:ICEgov" in tags
    assert "agency:CBP" in tags
    # Non-agency handles in mentions don't earn an agency: tag.
    assert "agency:JohnDoe1234" not in tags


def test_angel_family_keyword_match() -> None:
    out = tag_text(
        "Honoring this Angel Mom who lost her son to a violent illegal alien.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    assert "subject:angel-family" in _tags(out)


def test_native_born_citizen_keyword_match() -> None:
    out = tag_text(
        "President Trump is ensuring net job growth goes to NATIVE-BORN AMERICANS.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    tags = _tags(out)
    assert "subject:native-born-citizen" in tags
    assert "theme:nativism" in tags
    assert "topic:economy" in tags
    assert "topic:immigration" in tags
    imm = next(e for e in out if e["tag"] == "topic:immigration")
    assert not imm["tentative"]


def test_homeland_theme_matches_capital_h_homeland_framing() -> None:
    out = tag_text(
        "Our mission is securing the Homeland and protecting American communities.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    assert "theme:homeland" in _tags(out)


def test_homeland_theme_ignores_lowercase_generic_homeland() -> None:
    out = tag_text(
        "Our mission is homeland security and protecting American communities.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    assert "theme:homeland" not in _tags(out)


def test_nativism_theme_matches_labor_contrast_frame_with_multiple_topics() -> None:
    out = tag_text(
        "Foreign-born workers gained jobs while American-Born workers lost jobs.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    tags = _tags(out)
    assert "theme:nativism" in tags
    assert "topic:economy" in tags
    assert "topic:immigration" in tags


def test_nativism_theme_avoids_generic_american_worker_posts() -> None:
    out = tag_text(
        "USDOL is fighting for American workers and expanding apprenticeships.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    tags = _tags(out)
    assert "theme:nativism" not in tags
    assert "topic:economy" in tags
    assert "topic:immigration" not in tags


def test_christianity_theme_matches_explicit_christian_language() -> None:
    out = tag_text(
        "We defend Christian values and religious liberty.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    assert "theme:christianity" in _tags(out)


def test_unavailable_copyright_status_tags() -> None:
    out = tag_text(
        "",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="public",
        is_unavailable=True,
        unavailable_text="This media has been removed in response to a copyright report.",
    )
    tags = _tags(out)
    assert "status:unavailable" in tags
    assert "status:copyright-removal" in tags


def test_laudatory_topic_matches_accomplishment_posts() -> None:
    out = tag_text(
        "Promises made, promises kept: historic wins and record-breaking results.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    assert "topic:laudatory" in _tags(out)


def test_general_topic_matches_multi_problem_posts() -> None:
    out = tag_text(
        "Crime, inflation, fraud, and border chaos are hurting families.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    assert "topic:general" in _tags(out)


def test_slogan_patterns_fire() -> None:
    for text, expected in (
        ("Have a NICE day, America.", "slogan:nice"),
        ("ICE is targeting the WORST OF THE WORST.", "slogan:worst"),
        ("Report. Recon. Raid. That's the workflow.", "slogan:reportrecon"),
    ):
        out = tag_text(
            text, tweet_type="original", mentions=[], media_count=0, account_category="core"
        )
        assert expected in _tags(out), expected


def test_genre_statistics_requires_digits_with_keyword() -> None:
    yes = tag_text(
        "ICE made 1,234 arrests this week.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    assert "genre:statistics" in _tags(yes)
    no = tag_text(
        "We did some arrests this week.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    assert "genre:statistics" not in _tags(no)


def test_origin_only_fires_for_valid_country() -> None:
    out = tag_text(
        "John, a criminal alien from Acmeland, was arrested.",
        tweet_type="reply",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    # Acmeland isn't in the validated country list, so no origin: tag.
    assert not any(t.startswith("origin:") for t in _tags(out))


def test_ocr_text_confirms_immigration_and_emits_tags() -> None:
    # Tweet body is just a slogan with no immigration signal; the OCR'd
    # overlay carries the actual evidence — "DEPORT ILLEGAL ALIENS"
    # plus a country name and a state.
    out = tag_text(
        "America First.",
        tweet_type="original",
        mentions=[],
        media_count=1,
        account_category="core",
        ocr_text="DEPORT ILLEGAL ALIENS from Mexico arrested in Houston, Texas.",
    )
    tags = _tags(out)
    assert "action:deportation" in tags
    assert "frame:criminal" in tags  # "illegal aliens" — plural form now covered
    assert "country:Mexico" in tags
    assert "state:Texas" in tags
    # The presence of explicit signals (via OCR) should promote
    # topic:immigration out of tentative.
    imm = next(e for e in out if e["tag"] == "topic:immigration")
    assert not imm["tentative"]


def test_ocr_empty_falls_back_to_text_only() -> None:
    # Same body, no OCR. The original test path still works.
    out = tag_text(
        "DEPORT ILLEGAL ALIENS from Mexico in Texas.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
        ocr_text="",
    )
    assert "action:deportation" in _tags(out)


def test_country_synonyms_normalize() -> None:
    out = tag_text(
        "From USA to Mexico, enforcement works.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    tags = _tags(out)
    # USA → United-States; Mexico stays.
    assert "country:United-States" in tags
    assert "country:Mexico" in tags
