"""Regression coverage for the Layer-1 lexical tagger.

Tests assert that the deterministic rules in `scripts/tag_lexical.py`
fire (or stay silent) on representative text. Country / state / crime
vocab lists are covered with one positive case per family rather than
exhaustively — when a vocab entry is added to those tables, this file
doesn't have to grow proportionally.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from scripts.tag_lexical import tag_one_parquet, tag_text


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


def test_immigration_default_off_for_public_authors_without_signal() -> None:
    out = tag_text(
        "Happy birthday to Secretary Smith.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="public",
    )
    assert "topic:immigration" not in _tags(out)


def test_public_ice_arrest_text_marks_immigration_signal() -> None:
    out = tag_text(
        "ICE arrested another felon today.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="public",
    )
    tags = _tags(out)
    assert "agency:ICEgov" in tags
    assert "topic:immigration" in tags


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


def test_agency_tags_derive_from_text_and_alias_mentions() -> None:
    out = tag_text(
        "FBI, the Department of Justice, DEA, and the U.S. Marshals announced the case.",
        tweet_type="original",
        mentions=["FBIDirectorKash", "TheJusticeDept", "DEAHQ", "USMarshalsHQ"],
        media_count=0,
        account_category="public",
    )
    tags = _tags(out)
    assert "agency:FBI" in tags
    assert "agency:DOJgov" in tags
    assert "agency:DEAHQ" in tags
    assert "agency:USMarshalsHQ" in tags


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


def test_inheritance_language_needs_nativism_context() -> None:
    coded = tag_text(
        "This country is our inheritance, and Americans must defend the Homeland.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="public",
    )
    tags = _tags(coded)
    assert "theme:nativism" in tags
    assert "topic:immigration" in tags

    probate = tag_text(
        "The court discussed inheritance taxes and probate deadlines.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="public",
    )
    assert "theme:nativism" not in _tags(probate)


def test_forefathers_language_can_trigger_nativism_context() -> None:
    out = tag_text(
        "This nation is the inheritance of our forefathers, and citizens must defend it.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="public",
    )
    tags = _tags(out)
    assert "theme:nativism" in tags
    assert "topic:immigration" in tags


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
    tags = _tags(out)
    assert "theme:christianity" in tags
    assert "theme:religion" in tags


def test_religion_theme_matches_civil_religion_phrases() -> None:
    """Federal accounts routinely invoke God / blessings / prayers without
    explicitly naming Christianity. Those should land in theme:religion without
    forcing theme:christianity."""
    samples = [
        # Real tweet from DHSgov (id 2056894104106086877) the lexical tagger
        # previously missed entirely.
        "God has blessed us to call the greatest nation in history home.",
        "GOD BLESS AMERICA AND THE PATRIOTS DEFENDING OUR HOMELAND",
        "Our prayers are with the family.",
        "Praying for the victims and their loved ones.",
        "Praise be to God.",
        "Sending prayers to everyone affected.",
    ]
    for text in samples:
        out = tag_text(
            text,
            tweet_type="original",
            mentions=[],
            media_count=0,
            account_category="core",
        )
        tags = _tags(out)
        assert "theme:religion" in tags, text
        assert "theme:christianity" not in tags, text


def test_christianity_theme_adds_religion_when_appropriate() -> None:
    out = tag_text(
        "In Jesus' name we pray.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    tags = _tags(out)
    assert "theme:christianity" in tags
    assert "theme:religion" in tags


def test_religion_theme_matches_non_expletive_god_and_religious_terms() -> None:
    samples = [
        "May God protect our agents.",
        "Faith leaders gathered for a national day of prayer.",
        "Religious liberty matters.",
    ]
    for text in samples:
        out = tag_text(
            text,
            tweet_type="original",
            mentions=[],
            media_count=0,
            account_category="public",
        )
        assert "theme:religion" in _tags(out), text


def test_religion_theme_ignores_expletive_god_phrases() -> None:
    samples = [
        "oh my god, that was a mess.",
        "God damn it, fix this.",
        "good lord, what a mess.",
        "Thank god it's Friday.",
    ]
    for text in samples:
        out = tag_text(
            text,
            tweet_type="original",
            mentions=[],
            media_count=0,
            account_category="public",
        )
        tags = _tags(out)
        assert "theme:religion" not in tags, text
        assert "theme:christianity" not in tags, text


def test_transgender_theme_matches_gender_identity_and_sports_frames() -> None:
    samples = [
        "The order protects women's sports from biological males competing against women.",
        "No men in women's sports.",
        "The agency rescinded gender-identity guidance under Title IX.",
        "This policy rejects radical gender ideology.",
        "Transgender athletes remain covered by the guidance.",
    ]
    for text in samples:
        out = tag_text(
            text,
            tweet_type="original",
            mentions=[],
            media_count=0,
            account_category="public",
        )
        assert "theme:transgender" in _tags(out), text


def test_video_kind_tags_only_fire_when_video_present() -> None:
    """video:* kind tags are gated on the tweet having at least one video
    media item. Text matches alone do not earn the tag."""
    text_only = tag_text(
        "Watch the new bodycam footage from yesterday's raid.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
        video_count=0,
    )
    assert "video:bodycam" not in _tags(text_only)

    with_video = tag_text(
        "Watch the new bodycam footage from yesterday's raid.",
        tweet_type="original",
        mentions=[],
        media_count=1,
        account_category="core",
        video_count=1,
        video_max_duration_sec=42.0,
    )
    tags = _tags(with_video)
    assert "video:bodycam" in tags
    assert "video:medium" in tags  # 30 < 42 ≤ 120


def test_music_likely_tag_uses_video_text_and_reply_context() -> None:
    own_text = tag_text(
        "New montage set to music.",
        tweet_type="original",
        mentions=[],
        media_count=1,
        account_category="public",
        video_count=1,
    )
    assert "audio:music-likely" in _tags(own_text)

    reply_context = tag_text(
        "Watch this update.",
        tweet_type="original",
        mentions=[],
        media_count=1,
        account_category="public",
        video_count=1,
        reply_context_text="What is the song name? The soundtrack is great.",
    )
    assert "audio:music-likely" in _tags(reply_context)

    no_video = tag_text(
        "Watch this update.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="public",
        video_count=0,
        reply_context_text="What is the song name?",
    )
    assert "audio:music-likely" not in _tags(no_video)

    imported_media_tag = tag_text(
        "Watch this update.",
        tweet_type="original",
        mentions=[],
        media_count=1,
        account_category="public",
        video_count=1,
        media_tags=[{"tag": "media:music-video", "source": "manual-media-review"}],
    )
    assert "audio:music-likely" in _tags(imported_media_tag)


def test_video_duration_buckets() -> None:
    short = tag_text(
        "video",
        tweet_type="original",
        mentions=[],
        media_count=1,
        account_category="core",
        video_count=1,
        video_max_duration_sec=15.0,
    )
    medium = tag_text(
        "video",
        tweet_type="original",
        mentions=[],
        media_count=1,
        account_category="core",
        video_count=1,
        video_max_duration_sec=90.0,
    )
    long_video = tag_text(
        "video",
        tweet_type="original",
        mentions=[],
        media_count=1,
        account_category="core",
        video_count=1,
        video_max_duration_sec=300.0,
    )
    assert "video:short" in _tags(short)
    assert "video:medium" in _tags(medium)
    assert "video:long" in _tags(long_video)


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


def test_manual_tag_overrides_are_added_to_parquet_rows() -> None:
    df = pl.DataFrame(
        [
            {
                "tweet_id": "override-1",
                "account_handle": "DHSgov",
                "tweet_type": "original",
                "text": "ICE is HOT.",
                "text_resolved": "ICE is HOT.",
                "mentions": [],
                "media": [],
                "unavailable_detected_at": None,
                "unavailable_reason": None,
                "unavailable_text": None,
                "community_note": None,
            }
        ]
    )
    path = Path(".pytest_cache") / "manual-tag-override-DHSgov.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.write_parquet(path)
        rows = tag_one_parquet(
            path,
            {"DHSgov": "core"},
            "2026-05-20T00:00:00Z",
            tag_overrides={"override-1": ["status:copyright-removal"]},
        )
        tags = _tags(rows[0]["tags"])
        assert "status:copyright-removal" in tags
    finally:
        path.unlink(missing_ok=True)


def test_audio_sidecar_tags_are_imported_to_parquet_rows() -> None:
    df = pl.DataFrame(
        [
            {
                "tweet_id": "audio-1",
                "account_handle": "DHSgov",
                "tweet_type": "original",
                "text": "New video.",
                "text_resolved": "New video.",
                "mentions": [],
                "media": [{"media_type": "video", "duration_sec": 20.0}],
                "unavailable_detected_at": None,
                "unavailable_reason": None,
                "unavailable_text": None,
                "community_note": None,
            }
        ]
    )
    path = Path(".pytest_cache") / "audio-sidecar-DHSgov.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.write_parquet(path)
        rows = tag_one_parquet(
            path,
            {"DHSgov": "core"},
            "2026-05-20T00:00:00Z",
            audio_context_map={
                "audio-1": {
                    "tags": [
                        {
                            "tag": "audio:music-likely",
                            "tentative": True,
                            "source": "audio-heuristic",
                        }
                    ]
                }
            },
        )
        tags = _tags(rows[0]["tags"])
        assert "audio:music-likely" in tags
    finally:
        path.unlink(missing_ok=True)


def test_legal_prosecution_and_civil_lawsuit_tags() -> None:
    criminal = tag_text(
        "The defendant was indicted, charged with a felony, convicted, and sentenced.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="public",
    )
    civil = tag_text(
        "The state filed a civil lawsuit seeking an injunction and a temporary restraining order.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="public",
    )
    assert "legal:criminal-prosecution" in _tags(criminal)
    assert "legal:civil-lawsuit" in _tags(civil)


def test_community_note_status_tag() -> None:
    out = tag_text(
        "A post with reader context.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
        community_note={"note_id": "note-abc", "summary": "Context"},
    )
    assert "status:community-note" in _tags(out)


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


def test_explicit_military_language_emits_military_topic() -> None:
    out = tag_text(
        "Military veterans and service members attended the briefing.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="public",
    )
    tags = _tags(out)
    assert "topic:military" in tags
    assert not any(t.startswith("branch:") for t in tags)


def test_military_branch_mentions_emit_branch_and_military_topic() -> None:
    examples = (
        ("The Army deployed soldiers overseas.", "branch:army"),
        ("The Navy honored sailors at the ceremony.", "branch:navy"),
        ("The USAF recognized airmen for their service.", "branch:air-force"),
        ("The USSF launched a new mission.", "branch:space-force"),
        ("The Marine Corps honored Marines today.", "branch:marines"),
        ("The Coast Guard rescued families after the storm.", "branch:coast-guard"),
        ("The National Guard deployed today.", "branch:national-guard"),
    )
    for text, expected in examples:
        out = tag_text(
            text,
            tweet_type="original",
            mentions=[],
            media_count=0,
            account_category="public",
        )
        tags = _tags(out)
        assert expected in tags, text
        assert "topic:military" in tags, text
        assert "topic:immigration" not in tags, text


def test_slogan_patterns_fire() -> None:
    for text, expected in (
        ("Have a NICE day, America.", "slogan:nice"),
        ("ICE is targeting the WORST OF THE WORST.", "slogan:worst"),
        ("Report. Recon. Raid. That's the workflow.", "slogan:reportrecon"),
        ("An illegal alien was arrested today.", "slogan:illegal-alien"),
        ("ILLEGAL ALIENS should leave now.", "slogan:illegal-alien"),
        ("A criminal illegal alien was arrested today.", "slogan:criminal-illegal-alien"),
        ("CRIMINAL ILLEGAL ALIENS were removed today.", "slogan:criminal-illegal-alien"),
        ("FREE TICKET HOME! Sign up for CBP Home today.", "slogan:free-ticket-home"),
        ("Illegal aliens should use CBP Home and go home.", "slogan:go-home"),
        ("PROJECT HOMECOMING is expanding.", "slogan:project-homecoming"),
        ("MAKE AMERICA GREAT AGAIN.", "slogan:maga"),
        ("MAHA means Make America Healthy Again.", "slogan:maha"),
        ("America First is the policy.", "slogan:america-first"),
        ("Welcome to the Golden Age.", "slogan:golden-age"),
        ("Save America now.", "slogan:save-america"),
        ("Law and Order is back.", "slogan:law-and-order"),
        ("Peace Through Strength.", "slogan:peace-through-strength"),
        ("Promises Made, Promises Kept.", "slogan:promises-kept"),
        ("Mass deportation is the plan.", "slogan:mass-deportation"),
    ):
        out = tag_text(
            text, tweet_type="original", mentions=[], media_count=0, account_category="core"
        )
        assert expected in _tags(out), expected


def test_criminal_illegal_alien_slogan_also_marks_generic_phrase() -> None:
    out = tag_text(
        "A criminal illegal alien was arrested today.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    tags = _tags(out)
    assert "slogan:criminal-illegal-alien" in tags
    assert "slogan:illegal-alien" in tags
    assert "frame:criminal" in tags
    assert "topic:immigration" in tags


def test_migrant_and_immigrant_phrases_promote_immigration_topic() -> None:
    for text, expected in (
        ("Migrant workers were detained near the border.", "phrase:migrant"),
        ("The immigrant community asked for answers.", "phrase:immigrant"),
        ("Migrants and immigrants were mentioned in the report.", "phrase:migrant"),
    ):
        out = tag_text(
            text,
            tweet_type="original",
            mentions=[],
            media_count=0,
            account_category="public",
        )
        tags = _tags(out)
        assert expected in tags
        assert "topic:immigration" in tags
        imm = next(e for e in out if e["tag"] == "topic:immigration")
        assert not imm["tentative"]


def test_generic_maga_slogan_does_not_force_immigration() -> None:
    out = tag_text(
        "MAKE AMERICA GREAT AGAIN.",
        tweet_type="original",
        mentions=[],
        media_count=1,
        account_category="public",
        media_text="A campaign-style graphic with President Trump and the words MAKE AMERICA GREAT AGAIN.",
        media_tags=[{"tag": "subject:official", "source": "manual-media-review"}],
    )
    tags = _tags(out)
    assert "slogan:maga" in tags
    assert "subject:official" in tags
    assert "topic:general" in tags
    assert "topic:immigration" not in tags


def test_media_description_text_can_drive_immigration_slogans() -> None:
    out = tag_text(
        "",
        tweet_type="original",
        mentions=[],
        media_count=1,
        account_category="public",
        media_text=(
            "A social card says ICE arrested an illegal alien felon after release "
            "from a sanctuary city."
        ),
        media_tags=[{"tag": "media:text-overlay", "source": "manual-media-review"}],
    )
    tags = _tags(out)
    assert "media:text-overlay" in tags
    assert "agency:ICEgov" in tags
    assert "slogan:illegal-alien" in tags
    assert "theme:sanctuary-cities" in tags
    assert "topic:immigration" in tags


def test_speaker_tags_require_named_speech_context() -> None:
    examples = (
        (
            "First Lady Melania Trump delivers an announcement that the House passed the bill.",
            "speaker:First Lady Melania Trump",
        ),
        (".@SecMullinDHS delivered remarks at the Coast Guard Academy.", "speaker:Secretary Mullin"),
        ("Remarks by @POTUS in the Oval Office.", "speaker:President Trump"),
        (
            "Vice President Vance joined Fox News for an interview on border security.",
            "speaker:Vice President Vance",
        ),
        ('Tom Homan said, "We are enforcing the law."', "speaker:Tom Homan"),
        ("Stephen Miller gives remarks on immigration policy.", "speaker:Stephen Miller"),
        ("Gregory Bovino spoke at a press conference in Los Angeles.", "speaker:Gregory Bovino"),
    )
    for text, expected in examples:
        out = tag_text(
            text,
            tweet_type="original",
            mentions=[],
            media_count=0,
            account_category="public",
        )
        assert expected in _tags(out), text


def test_speaker_tags_can_use_ocr_or_media_description_text() -> None:
    out = tag_text(
        "",
        tweet_type="original",
        mentions=[],
        media_count=1,
        account_category="public",
        media_text="Manual review observation: Tom Homan at a podium delivering remarks.",
    )
    assert "speaker:Tom Homan" in _tags(out)

    ocr = tag_text(
        "Watch live.",
        tweet_type="original",
        mentions=[],
        media_count=1,
        account_category="public",
        ocr_text="INTERVIEW WITH STEPHEN MILLER",
    )
    assert "speaker:Stephen Miller" in _tags(ocr)


def test_speaker_tags_do_not_fire_on_name_mentions_without_speech_context() -> None:
    examples = (
        "U.S. House passes the act championed by First Lady Melania Trump.",
        ".@SecMullinDHS arrives to the United States Coast Guard Academy.",
        "@DHSgov @POTUS @SecMullinDHS That's truly fact.",
        "President Trump signed the order today.",
        "Vice President Vance attended the meeting.",
        "Tom Homan and Stephen Miller met with Gregory Bovino.",
    )
    for text in examples:
        out = tag_text(
            text,
            tweet_type="original",
            mentions=[],
            media_count=0,
            account_category="public",
        )
        assert not any(tag.startswith("speaker:") for tag in _tags(out)), text


def test_intrinsic_immigration_tags_promote_immigration_topic() -> None:
    for text in (
        "An illegal alien was arrested today.",
        "A criminal illegal alien was arrested today.",
        "PROJECT HOMECOMING gives aliens a free flight home.",
    ):
        out = tag_text(
            text,
            tweet_type="original",
            mentions=[],
            media_count=0,
            account_category="public",
        )
        assert "topic:immigration" in _tags(out), text


def test_cbp_home_theme_pairs_with_slogans_and_self_deport_action() -> None:
    out = tag_text(
        "PROJECT HOMECOMING. Use the CBP Home app to self-deport and get a free flight home.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    tags = _tags(out)
    assert "subject:cbp-home-app" in tags
    assert "theme:cbp-home" in tags
    assert "slogan:project-homecoming" in tags
    assert "slogan:free-ticket-home" in tags
    assert "action:self-deportation" in tags
    assert "action:deportation" not in tags
    assert "topic:immigration" in tags


def test_self_deportation_does_not_flatten_into_deportation() -> None:
    self_deport = tag_text(
        "Now is the time to self-deport using the CBP Home App.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    tags = _tags(self_deport)
    assert "action:self-deportation" in tags
    assert "action:deportation" not in tags

    forced = tag_text(
        "If you do not self-deport, you will be arrested and deported.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    forced_tags = _tags(forced)
    assert "action:self-deportation" in forced_tags
    assert "action:deportation" in forced_tags


def test_pop_culture_enforcement_and_celebrity_tags() -> None:
    out = tag_text(
        "Sydney Sweeney has good genes. DHS says illegal aliens should leave.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    tags = _tags(out)
    assert "subject:celebrity" in tags
    assert "theme:pop-culture-enforcement" in tags


def test_pop_culture_celebrity_false_positives_stay_silent() -> None:
    legal_actor = tag_text(
        "The victim was under 13 years old and the actor is more than two years older.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="core",
    )
    assert "subject:celebrity" not in _tags(legal_actor)

    bio_noise = tag_text(
        "Independent and tweeting without the celebrity status.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="public",
    )
    tags = _tags(bio_noise)
    assert "subject:celebrity" not in tags
    assert "theme:pop-culture-enforcement" not in tags

    retail_only = tag_text(
        "American Eagle released a new campaign.",
        tweet_type="original",
        mentions=[],
        media_count=0,
        account_category="public",
    )
    assert "theme:pop-culture-enforcement" not in _tags(retail_only)


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


def test_homicide_murder_subtype_matches_plain_murder_and_homicide_terms() -> None:
    samples = [
        "That murder was absolutely preventable.",
        "The murder of Stephanie Minter should never have happened.",
        "The victim was murdered last year.",
        "The murderer was arrested.",
        "These murderers cannot hide.",
        "The suspect was charged with homicide.",
    ]
    for text in samples:
        out = tag_text(
            text,
            tweet_type="original",
            mentions=[],
            media_count=0,
            account_category="public",
        )
        tags = _tags(out)
        assert "crime:homicide" in tags, text
        if "murder" in text.lower():
            assert "homicide:murder" in tags, text


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
