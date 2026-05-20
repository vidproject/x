"""Layer-1 lexical tagger.

Reads every per-account parquet under `data/`, runs deterministic
regex / structural rules against each tweet's `text_resolved`
(falling back to `text`), and writes
`data/tags/lexical.parquet` (one row per tweet, list of tag entries).

Idempotent: re-running rebuilds the parquet from scratch. The
canonical tweet parquets are never modified. The viewer joins on
tweet_id at load time. See `docs/TAGGING.md` for the architecture.

Run with:  uv run python -m scripts.tag_lexical
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import yaml

from ._logging import configure
from ._schema import LEXICAL_TAG_SCHEMA, empty_lexical_tag_dataframe

LOG = configure()

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TAGS_DIR = DATA_DIR / "tags"
CONFIG_PATH = REPO_ROOT / "config" / "tag_taxonomy.yaml"
ACCOUNTS_CONFIG_PATH = REPO_ROOT / "config" / "accounts.yaml"

TAGGER_VERSION = "lexical-v1"

# Countries the auto-tagger validates `origin:<X>` and `country:<X>`
# matches against. Sovereign UN-recognized state names only; common
# adjectival forms and synonyms folded in (US, USA, UK, etc.). Kept
# inline so the tagger has zero non-stdlib non-polars dependencies.
COUNTRIES: tuple[str, ...] = (
    "Afghanistan",
    "Albania",
    "Algeria",
    "Andorra",
    "Angola",
    "Argentina",
    "Armenia",
    "Australia",
    "Austria",
    "Azerbaijan",
    "Bahamas",
    "Bahrain",
    "Bangladesh",
    "Barbados",
    "Belarus",
    "Belgium",
    "Belize",
    "Benin",
    "Bhutan",
    "Bolivia",
    "Bosnia",
    "Botswana",
    "Brazil",
    "Brunei",
    "Bulgaria",
    "Burkina Faso",
    "Burundi",
    "Cambodia",
    "Cameroon",
    "Canada",
    "Chad",
    "Chile",
    "China",
    "Colombia",
    "Comoros",
    "Congo",
    "Costa Rica",
    "Croatia",
    "Cuba",
    "Cyprus",
    "Czechia",
    "Czech Republic",
    "Denmark",
    "Djibouti",
    "Dominica",
    "Dominican Republic",
    "Ecuador",
    "Egypt",
    "El Salvador",
    "Eritrea",
    "Estonia",
    "Eswatini",
    "Ethiopia",
    "Fiji",
    "Finland",
    "France",
    "Gabon",
    "Gambia",
    "Georgia",
    "Germany",
    "Ghana",
    "Greece",
    "Grenada",
    "Guatemala",
    "Guinea",
    "Guyana",
    "Haiti",
    "Honduras",
    "Hungary",
    "Iceland",
    "India",
    "Indonesia",
    "Iran",
    "Iraq",
    "Ireland",
    "Israel",
    "Italy",
    "Jamaica",
    "Japan",
    "Jordan",
    "Kazakhstan",
    "Kenya",
    "Kiribati",
    "Kosovo",
    "Kuwait",
    "Kyrgyzstan",
    "Laos",
    "Latvia",
    "Lebanon",
    "Lesotho",
    "Liberia",
    "Libya",
    "Liechtenstein",
    "Lithuania",
    "Luxembourg",
    "Madagascar",
    "Malawi",
    "Malaysia",
    "Maldives",
    "Mali",
    "Malta",
    "Mauritania",
    "Mauritius",
    "Mexico",
    "Moldova",
    "Monaco",
    "Mongolia",
    "Montenegro",
    "Morocco",
    "Mozambique",
    "Myanmar",
    "Burma",
    "Namibia",
    "Nauru",
    "Nepal",
    "Netherlands",
    "New Zealand",
    "Nicaragua",
    "Niger",
    "Nigeria",
    "Norway",
    "Oman",
    "Pakistan",
    "Palau",
    "Panama",
    "Paraguay",
    "Peru",
    "Philippines",
    "Poland",
    "Portugal",
    "Qatar",
    "Romania",
    "Russia",
    "Rwanda",
    "Samoa",
    "Senegal",
    "Serbia",
    "Seychelles",
    "Sierra Leone",
    "Singapore",
    "Slovakia",
    "Slovenia",
    "Somalia",
    "South Africa",
    "South Korea",
    "South Sudan",
    "Spain",
    "Sri Lanka",
    "Sudan",
    "Suriname",
    "Sweden",
    "Switzerland",
    "Syria",
    "Taiwan",
    "Tajikistan",
    "Tanzania",
    "Thailand",
    "Togo",
    "Tonga",
    "Trinidad",
    "Tobago",
    "Tunisia",
    "Turkey",
    "Turkmenistan",
    "Uganda",
    "Ukraine",
    "United Arab Emirates",
    "UAE",
    "United Kingdom",
    "UK",
    "United States",
    "USA",
    "Uruguay",
    "Uzbekistan",
    "Vanuatu",
    "Venezuela",
    "Vietnam",
    "Yemen",
    "Zambia",
    "Zimbabwe",
)
COUNTRY_LOWER: frozenset[str] = frozenset(c.lower() for c in COUNTRIES)

US_STATES: tuple[str, ...] = (
    "Alabama",
    "Alaska",
    "Arizona",
    "Arkansas",
    "California",
    "Colorado",
    "Connecticut",
    "Delaware",
    "Florida",
    "Georgia",
    "Hawaii",
    "Idaho",
    "Illinois",
    "Indiana",
    "Iowa",
    "Kansas",
    "Kentucky",
    "Louisiana",
    "Maine",
    "Maryland",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Mississippi",
    "Missouri",
    "Montana",
    "Nebraska",
    "Nevada",
    "New Hampshire",
    "New Jersey",
    "New Mexico",
    "New York",
    "North Carolina",
    "North Dakota",
    "Ohio",
    "Oklahoma",
    "Oregon",
    "Pennsylvania",
    "Rhode Island",
    "South Carolina",
    "South Dakota",
    "Tennessee",
    "Texas",
    "Utah",
    "Vermont",
    "Virginia",
    "Washington",
    "West Virginia",
    "Wisconsin",
    "Wyoming",
)
STATE_LOWER: frozenset[str] = frozenset(s.lower() for s in US_STATES)

# Vocabulary for `crime:<TYPE>`. Each entry is (slug, pattern). The slug
# becomes the tag suffix (`crime:assault`, `crime:fentanyl`, etc.).
CRIME_VOCAB: tuple[tuple[str, str], ...] = (
    ("rape", r"\brap(?:e|ed|ing|ist)\b"),
    ("sodomy", r"\bsodom(?:y|ize|ized)\b"),
    ("murder", r"\bmurder(?:ed|er|ing)?\b"),
    ("burglary", r"\bburglar(?:y|ize|ized|ies)\b"),
    ("theft", r"\btheft\b|\bsteal(?:ing)?\b|\bstole\b"),
    ("robbery", r"\brobber(?:y|ies)\b|\brobbed\b"),
    ("assault", r"\bassault(?:ed|ing|s)?\b"),
    ("battery", r"\bbatter(?:y|ies|ed)\b"),
    ("fentanyl", r"\bfentanyl\b"),
    ("cocaine", r"\bcocaine\b"),
    ("meth", r"\bmethamphetamine\b|\bmeth\b"),
    ("trafficking", r"\btraffick(?:ing|er|ers)\b"),
    ("dui", r"\bDUI\b|\bDWI\b"),
    ("kidnap", r"\bkidnap(?:ped|ping|per)?\b"),
    ("child", r"\bchild (?:abuse|sex|pornography|molest)|child sexual\b"),
    ("gang", r"\bgang (?:member|members|affiliation)\b|\bgang-related\b"),
    ("ms13", r"\bMS-?13\b"),
    ("tren-de-aragua", r"\bTren de Aragua\b|\bTdA\b"),
    ("narcotics", r"\bnarcotic[s]?\b"),
    ("fraud", r"\bfraud(?:ulent|ster)?\b"),
    ("arson", r"\barson(?:ist)?\b"),
    ("weapon", r"\bweapon[s]?\b|\billegal firearm[s]?\b"),
    ("firearm", r"\bfirearm[s]?\b"),
)

# Handles whose mention earns an `agency:<HANDLE>` tag. Distinct from the
# author handle: a tweet from @POTUS that mentions @ICEgov gets
# `agency:ICEgov`, regardless of who wrote it.
AGENCY_HANDLES: frozenset[str] = frozenset(
    {
        "ICEgov",
        "CBP",
        "USCIS",
        "DHSgov",
        "WhiteHouse",
        "POTUS",
        "PressSec",
        "USDOL",
        "RapidResponse47",
        "HSI_HQ",
        "USBPChief",
        "SecMullinDHS",
        "AGPamBondi",
        "StateDept",
        "DOJgov",
        "FBI",
        "ERO_LosAngeles",
        "ERO_HQ",
    }
)

# Detection rules for *non-immigration* signals. If any of these match a
# tweet, we suppress the default `topic:immigration`. The corpus is
# overwhelmingly about immigration enforcement; defaulting to ON is
# strictly higher recall than trying to infer relevance from sparse text.
NON_IMMIGRATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(weather|hurricane|tornado|earthquake|wildfire)\b", re.I),
    re.compile(r"\b(NCAA|Super Bowl|World Series|playoffs?)\b", re.I),
    re.compile(r"\b(birthday|anniversary)\b", re.I),
)

# A "sticky default" applied to every tweet from these account categories
# unless a NON_IMMIGRATION_PATTERN matches. See the `topic:immigration`
# entry in `config/tag_taxonomy.yaml`.
IMMIGRATION_DEFAULT_CATEGORIES: frozenset[str] = frozenset({"core", "government", "officials"})


# ---------------------------------------------------------------------------
# Static patterns loaded once.
# ---------------------------------------------------------------------------


def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern.strip(), re.I)


PATTERN_FRAME_CRIMINAL = _compile(
    r"\b(criminal illegal aliens?|illegal aliens?|criminal aliens?|aggravated felons?|convicted (?:for|of))\b"
)
PATTERN_ACTION_DETENTION = _compile(
    r"\b(arrest(?:ed|ing)?|detain(?:ed|ing)?|apprehend(?:ed|ing)?|in custody|nab(?:bed)?)\b"
)
PATTERN_ACTION_DEPORTATION = _compile(
    r"\b(deport(?:ed|ing|ation)?|remov(?:ed|al)|repatriat(?:ed|ion))\b"
)
ICE_OR_DHS_TARGET = (
    r"(?:ICE|I\.C\.E\.|Immigration and Customs Enforcement|"
    r"U\.S\.\s+Immigration\s+and\s+Customs\s+Enforcement|DHS|"
    r"Department of Homeland Security|Homeland Security)"
)
ICE_DIRECT_TARGET = (
    r"(?:ICE|I\.C\.E\.|Immigration and Customs Enforcement|"
    r"U\.S\.\s+Immigration\s+and\s+Customs\s+Enforcement)"
)
ICE_REPORT_SUBJECT = (
    r"(?:illegal\s+(?:alien|immigrant)s?|criminal\s+aliens?|"
    r"undocumented\s+(?:immigrant|alien)s?|immigration\s+(?:crime|violation)s?|"
    r"alien(?:s|['\u2019]s)?|immigrants?|migrants?)"
)
ICE_REPORT_PHONE = r"(?:866[-\s]?DHS[-\s]?2[-\s]?ICE|866[-\s]?347[-\s]?2423)"
PATTERN_ACTION_REPORT_TO_ICE = _compile(
    rf"\b(?:"
    rf"(?:call|contact)\s+(?:{ICE_DIRECT_TARGET}|{ICE_REPORT_PHONE})\b"
    rf"(?=.{{0,180}}\b{ICE_REPORT_SUBJECT}\b)"
    rf"|(?:report|tip|submit|send|notify)\b.{{0,80}}\b{ICE_REPORT_SUBJECT}\b"
    rf".{{0,80}}\b(?:to|at|with)\s+(?:{ICE_OR_DHS_TARGET}|{ICE_REPORT_PHONE})\b"
    rf"|(?:submit|send)\s+(?:a\s+)?tip\b.{{0,80}}\b(?:to|with)\s+"
    rf"(?:{ICE_OR_DHS_TARGET}|{ICE_REPORT_PHONE})\b"
    rf"(?=.{{0,120}}\b{ICE_REPORT_SUBJECT}\b)"
    rf"|{ICE_REPORT_SUBJECT}\b.{{0,240}}\b(?:call|contact)\s+"
    rf"(?:(?:our|the)\s+)?(?:{ICE_DIRECT_TARGET}|ICE\s+)?"
    rf"(?:tip\s*line|hotline|{ICE_REPORT_PHONE})\b"
    rf")"
)
PATTERN_TOPIC_ECONOMY = _compile(
    r"\b(econom(?:y|ic)|jobs?|job growth|workers?|workforce|wages?|labor market|"
    r"employment|unemployment|hiring|manufactur(?:e|ing)|business(?:es)?|"
    r"apprenticeships?|small businesses?|tax cuts?)\b"
)
PATTERN_TOPIC_LAUDATORY = _compile(
    r"\b(accomplishments?|wins?|success(?:es)?|historic|record[- ]breaking|"
    r"promises? made[,;:]?\s+promises? kept|delivering|delivered|momentum|"
    r"golden age|winning)\b"
)
GENERAL_TOPIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("immigration", _compile(r"\b(immigration|migrant|border|illegal alien|asylum)\b")),
    ("crime", _compile(r"\b(crime|criminal|drugs?|fentanyl|gang|violence|murder)\b")),
    ("economy", PATTERN_TOPIC_ECONOMY),
    ("fraud", _compile(r"\b(fraud|waste|abuse|corruption|scam)\b")),
    ("security", _compile(r"\b(security|terror|war|china|cartel|threat)\b")),
    ("costs", _compile(r"\b(inflation|prices?|taxes|cost of living)\b")),
)
PATTERN_THEME_BORDER = _compile(r"\b(border|southwest border|crossing|border wall)\b")
PATTERN_THEME_SANCTUARY = _compile(
    r"\b(sanctuary (?:cit(?:y|ies)|jurisdiction|polic(?:y|ies))|sanctuary state)\b"
)
PATTERN_THEME_WORKSITE = _compile(r"\b(worksite|workplace|I-9|E-Verify|employer (?:audit|raid))\b")
PATTERN_THEME_HOMELAND = re.compile(
    r"\b(?i:our|the|this|america's)\s+Homeland\b"
    r"|\b(?i:protect|protecting|secure|securing|defend|defending|safeguard|safeguarding)"
    r"\s+(?:(?i:our|the|this|america's)\s+)?Homeland\b"
    r"|\b(?i:safe|secure)\s+Homeland\b"
)
PATTERN_THEME_NATIVISM = _compile(
    r"\bnative[- ]born\s+(?:americans?|workers?|citizens?)\b"
    r"|\bamerican[- ]born\s+(?:americans?|workers?|citizens?)\b"
    r"|\bforeign[- ]born\s+workers?\b"
    r"|\bforeign\s+(?:workers?|labor)\b.{0,140}\b(?:flood|cheap|displac|replac|"
    r"betray|job market|american\s+(?:workers?|jobs?)|americans?\s+first)\b"
    r"|\b(?:american\s+(?:workers?|jobs?)|americans?\s+first|job market)\b.{0,140}"
    r"\b(?:foreign\s+(?:workers?|labor)|foreign[- ]born\s+workers?)\b"
    r"|\bglobalism has failed\b|\bamericanism will prevail\b"
)
PATTERN_THEME_CHRISTIANITY = _compile(
    r"\bchristian(?:ity|s)?\b"
    r"|\bjudeo[- ]christian\b"
    r"|\bchristian\s+(?:faith|values?|church|churches|heritage|nation)\b"
    r"|\bjesus(?:\s+christ)?\b"
    r"|\bchrist\s+(?:is\s+king|the\s+king|our\s+lord)\b"
    r"|\b(?:bible|biblical|scripture|scriptural)\b"
)
PATTERN_STATUS_COPYRIGHT_REMOVAL = _compile(r"\b(copyright|dmca)\b")
PATTERN_SLOGAN_NICE = _compile(r"\b(NICE day|NICE morning|ICE is NICE|NICE city)\b")
PATTERN_SLOGAN_WORST = _compile(r"\bWORST OF THE WORST\b")
PATTERN_SLOGAN_REPORTRECON = _compile(r"\bReport\.\s*Recon\.\s*Raid\.")
PATTERN_GENRE_STATISTICS = _compile(
    r"\b\d[\d,]*\s+(?:arrest|removal|deportat|encounter|alien|illegal|criminal|gang|fentanyl)"
)
# Imperative-mood markers at the start of a sentence (lowercase or
# title-case). Used for `genre:directive`.
PATTERN_GENRE_DIRECTIVE = _compile(
    r"(?:^|[.!?]\s+)(Apply|Report|Call|Visit|Leave|Self[- ]deport|Go to|Submit|Sign up|Tip|Click|Tap|See|Read)\b"
)
PATTERN_ANGEL_FAMILY = _compile(
    r"\bangel (?:famil(?:y|ies)|mom|dad|parent|mother|father|wife|husband|son|daughter|child)\b"
)
PATTERN_NATIVE_BORN_CITIZEN = _compile(
    r"\bnative[- ]born\s+(?:citizens?|americans?|u\.?s\.?\s+citizens?|people|workers|taxpayers)\b"
)
# "from <Country>," — anchors the COUNTRY validator. The preposition is
# scoped-case-insensitive ((?i:...)) so "From"/"FROM"/"from" all match,
# but the country-name capture stays case-sensitive. Combining the two
# under `re.I` over-matches: lowercase prepositions then satisfy the
# second `[A-Z][a-zA-Z]+` slot too ("From USA to" -> "USA to").
PATTERN_ORIGIN_CANDIDATE = re.compile(r"\b(?i:from)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\s*,")
# Any sovereign-state mention after a small set of prepositions. Kept
# conservative because raw country names false-positive easily ("Chad",
# "Georgia") if you allow bare occurrences.
PATTERN_COUNTRY_CANDIDATE = re.compile(
    r"\b(?i:from|in|to|of|with|by)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\b"
)
# "<Place>, <State>" — anchors the STATE validator.
PATTERN_STATE_CANDIDATE = re.compile(
    r"\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?,\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\b"
)


# ---------------------------------------------------------------------------
# Per-tweet tagger.
# ---------------------------------------------------------------------------


def tag_text(
    text: str,
    *,
    tweet_type: str | None,
    mentions: list[str] | None,
    media_count: int,
    account_category: str,
    ocr_text: str = "",
    is_unavailable: bool = False,
    unavailable_text: str = "",
) -> list[dict[str, Any]]:
    """Apply every deterministic rule to a single tweet, returning a
    list of tag-entry dicts in the shape expected by
    `LEXICAL_TAG_SCHEMA`.

    `text` is the tweet body. `ocr_text` is OCR text extracted from
    attached media by the (separate) image-OCR tagger; when present, it
    is concatenated to `text` before the regex pass so a graphic that
    reads "DEPORT THE INVASION" earns the same `action:deportation` /
    `topic:immigration` tags as if the words had been typed into the
    tweet body. OCR text also feeds the immigration-signal check that
    decides whether `topic:immigration` is confirmed or tentative.

    Spans on emitted tags index into the **combined** buffer (`text +
    " ¶ " + ocr_text`), so they're stable per-tweet but not directly
    comparable across the original text/OCR boundary."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()  # dedupe identical (tag, span) pairs

    def add(
        tag: str,
        *,
        span: tuple[int, int] | None = None,
        tentative: bool = False,
    ) -> None:
        key = f"{tag}@{span[0] if span else ''}"
        if key in seen:
            return
        seen.add(key)
        entries.append(
            {
                "tag": tag,
                "tentative": True if tentative else None,
                "source": "auto",
                "span_start": span[0] if span else None,
                "span_end": span[1] if span else None,
            }
        )

    # format:* — purely structural, derived from tweet_type
    if tweet_type == "retweet":
        add("format:retweet")
    elif tweet_type == "quote":
        add("format:quote")
    elif tweet_type == "reply":
        add("format:reply")

    # agency:<HANDLE> — derived from mentions[]
    for mention in mentions or []:
        if mention in AGENCY_HANDLES:
            add(f"agency:{mention}")

    if is_unavailable:
        add("status:unavailable")
        if PATTERN_STATUS_COPYRIGHT_REMOVAL.search(unavailable_text):
            add("status:copyright-removal")

    # Concatenate OCR text (when present) so a poster's stamped slogan
    # earns the same tags as if it had been typed into the tweet body.
    # The separator (" ¶ ") doesn't appear in any of our patterns and
    # keeps regex matches from spanning the boundary accidentally.
    body = (f"{text} ¶ {ocr_text}" if text else ocr_text) if ocr_text else text

    if not body:
        # Even with no text we still get format: + agency: + sticky
        # default. Skip the regex pass.
        _maybe_immigration_default(entries, account_category, "", add)
        return entries
    text = body

    # Single-shot regex tags
    for pat, tag in (
        (PATTERN_FRAME_CRIMINAL, "frame:criminal"),
        (PATTERN_ACTION_DETENTION, "action:detention"),
        (PATTERN_ACTION_DEPORTATION, "action:deportation"),
        (PATTERN_ACTION_REPORT_TO_ICE, "action:report-immigrants"),
        (PATTERN_TOPIC_ECONOMY, "topic:economy"),
        (PATTERN_TOPIC_LAUDATORY, "topic:laudatory"),
        (PATTERN_THEME_BORDER, "theme:border"),
        (PATTERN_THEME_SANCTUARY, "theme:sanctuary-cities"),
        (PATTERN_THEME_WORKSITE, "theme:worksite-enforcement"),
        (PATTERN_THEME_HOMELAND, "theme:homeland"),
        (PATTERN_THEME_NATIVISM, "theme:nativism"),
        (PATTERN_THEME_CHRISTIANITY, "theme:christianity"),
        (PATTERN_SLOGAN_NICE, "slogan:nice"),
        (PATTERN_SLOGAN_WORST, "slogan:worst"),
        (PATTERN_SLOGAN_REPORTRECON, "slogan:reportrecon"),
        (PATTERN_GENRE_STATISTICS, "genre:statistics"),
        (PATTERN_GENRE_DIRECTIVE, "genre:directive"),
        (PATTERN_ANGEL_FAMILY, "subject:angel-family"),
        (PATTERN_NATIVE_BORN_CITIZEN, "subject:native-born-citizen"),
    ):
        m = pat.search(text)
        if m:
            add(tag, span=m.span())

    if _general_topic_score(text) >= 3:
        add("topic:general")

    # crime:<TYPE> — every distinct match emits one tag entry.
    for slug, pat_str in CRIME_VOCAB:
        for m in re.finditer(pat_str, text, re.I):
            add(f"crime:{slug}", span=m.span())

    # origin:<COUNTRY> — validated against the sovereign-state vocab.
    for m in PATTERN_ORIGIN_CANDIDATE.finditer(text):
        candidate = (m.group(1) or "").strip()
        if candidate.lower() in COUNTRY_LOWER:
            add(f"origin:{_normalize_country(candidate)}", span=m.span(1))

    # country:<NAME> — broader: any contextual country mention.
    for m in PATTERN_COUNTRY_CANDIDATE.finditer(text):
        candidate = (m.group(1) or "").strip()
        if candidate.lower() in COUNTRY_LOWER:
            add(f"country:{_normalize_country(candidate)}", span=m.span(1))

    # state:<NAME> — the "<City>, <State>" pattern, validated.
    for m in PATTERN_STATE_CANDIDATE.finditer(text):
        candidate = (m.group(1) or "").strip()
        if candidate.lower() in STATE_LOWER:
            add(f"state:{_normalize_state(candidate)}", span=m.span(1))

    # shape:lineup — composite: replies that hit frame:criminal with 1 photo.
    if (
        tweet_type == "reply"
        and any(e["tag"] == "frame:criminal" for e in entries)
        and media_count == 1
    ):
        add("shape:lineup")

    # subject:enforcement-op heuristic from the deterministic rules above:
    # if both action:detention and frame:criminal fire, that's strong enough
    # to set this without tentative.
    if any(e["tag"] == "action:detention" for e in entries) and any(
        e["tag"] == "frame:criminal" for e in entries
    ):
        add("subject:enforcement-op")

    _maybe_immigration_default(entries, account_category, text, add)
    return entries


def _general_topic_score(text: str) -> int:
    """Count broad problem domains in a multi-issue / grievance-style post."""
    return sum(1 for _slug, pat in GENERAL_TOPIC_PATTERNS if pat.search(text))


# Tag names whose presence on a tweet promotes `topic:immigration` from
# a tentative-by-account default to a confirmed-by-text classification.
# Anything that explicitly references the immigration domain (origin
# country pattern, deportation verb, border keyword, ICE/CBP/DHS handle,
# the criminal-alien frame, etc.) clears the bar.
IMMIGRATION_CONFIRMING_PREFIXES: tuple[str, ...] = (
    "frame:",
    "action:",
    "origin:",
    "country:",
    "theme:border",
    "theme:sanctuary",
    "theme:worksite",
    "theme:nativism",
    "slogan:",
    "shape:",
    "subject:enforcement-op",
)
IMMIGRATION_CONFIRMING_EXACT: frozenset[str] = frozenset(
    {
        "agency:ICEgov",
        "agency:CBP",
        "agency:DHSgov",
        "agency:HSI_HQ",
        "agency:USBPChief",
    }
)
# Last-ditch keyword check — picks up image-heavy / template-light
# tweets that the namespace rules above don't flag but that still
# clearly read as immigration content ("ICE arrested", "illegal alien",
# "the border", standalone "immigration"). Kept narrow on purpose.
PATTERN_IMMIGRATION_KEYWORD = _compile(
    r"\b(immigration|immigrant|migrant|asylum|illegal alien|the border|"
    r"border patrol|ICE\b|CBP\b)\b"
)


def _has_immigration_signal(entries: list[dict[str, Any]], text: str) -> bool:
    """True if the tweet carries any explicit immigration-domain signal.
    Drives the confirmed-vs-tentative split for `topic:immigration`."""
    for e in entries:
        tag = e["tag"]
        if tag in IMMIGRATION_CONFIRMING_EXACT:
            return True
        for pref in IMMIGRATION_CONFIRMING_PREFIXES:
            if tag.startswith(pref):
                return True
    return bool(PATTERN_IMMIGRATION_KEYWORD.search(text))


def _has_non_immigration_topic(entries: list[dict[str, Any]]) -> bool:
    return any(e["tag"].startswith("topic:") and e["tag"] != "topic:immigration" for e in entries)


def _maybe_immigration_default(
    entries: list[dict[str, Any]],
    account_category: str,
    text: str,
    add: Callable[..., None],
) -> None:
    """Apply `topic:immigration` to every tweet from a tracked-tier
    author unless an obvious non-immigration signal blocks it.
    Confirmed when the tweet carries any explicit immigration-domain
    marker; tentative otherwise (image-heavy, template-light tweets
    where the author identity is the only signal we have).

    See the `topic:immigration` entry in `config/tag_taxonomy.yaml` for
    the rationale."""
    if account_category not in IMMIGRATION_DEFAULT_CATEGORIES:
        return
    has_signal = _has_immigration_signal(entries, text)
    if has_signal:
        add("topic:immigration")
        return
    for pat in NON_IMMIGRATION_PATTERNS:
        if pat.search(text):
            return
    if not _has_non_immigration_topic(entries):
        add("topic:immigration", tentative=True)


def _normalize_country(s: str) -> str:
    """Collapse common synonyms to a canonical slug."""
    low = s.lower()
    if low in ("usa", "united states"):
        return "United-States"
    if low == "uk":
        return "United-Kingdom"
    if low == "uae":
        return "United-Arab-Emirates"
    if low == "burma":
        return "Myanmar"
    return s.replace(" ", "-")


def _normalize_state(s: str) -> str:
    return s.replace(" ", "-")


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------


def load_account_categories() -> dict[str, str]:
    """Return {handle: category}. Unknown / unlisted handles map to
    `public` at lookup time (not stored here)."""
    if not ACCOUNTS_CONFIG_PATH.exists():
        return {}
    data = yaml.safe_load(ACCOUNTS_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    out: dict[str, str] = {}
    for entry in data.get("accounts", []):
        if isinstance(entry, dict):
            handle = str(entry.get("handle", "")).strip()
            category = str(entry.get("category", "core")).strip() or "core"
            if handle:
                out[handle] = category
    return out


def load_ocr_map() -> dict[str, str]:
    """Return {tweet_id: concatenated OCR text} from the Layer-3b OCR
    sidecar parquet, when it exists. The OCR layer hasn't shipped yet;
    this loader is the integration point the lexical tagger uses to
    pick it up automatically once it does.

    Schema (when present):
        media_id : str
        tweet_id : str
        ocr_engine : str        ("tesseract" | "paddleocr")
        ocr_version : str
        ocr_at : str
        text : str              full OCR'd text, single string
        confidence : float      mean per-token confidence

    Multiple media per tweet are joined with " | " into one blob so
    every per-media match still hits the regexes; the tagger doesn't
    care which image a token came from.
    """
    p = TAGS_DIR / "image_ocr.parquet"
    if not p.exists():
        return {}
    df = pl.read_parquet(p)
    if df.is_empty() or "tweet_id" not in df.columns or "text" not in df.columns:
        return {}
    grouped = df.group_by("tweet_id").agg(pl.col("text").str.concat(" | ").alias("text"))
    out: dict[str, str] = {}
    for row in grouped.iter_rows(named=True):
        tid = str(row.get("tweet_id") or "")
        if tid:
            out[tid] = str(row.get("text") or "")
    return out


def discover_canonical_parquets() -> list[Path]:
    """Return per-account canonical parquets in `data/`, excluding any
    sidecars under `data/tags/`."""
    if not DATA_DIR.exists():
        return []
    return sorted(p for p in DATA_DIR.glob("*.parquet") if p.parent == DATA_DIR)


def tag_one_parquet(
    path: Path,
    account_categories: dict[str, str],
    tagged_at: str,
    ocr_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Tag every row in one canonical parquet. Returns list of rows
    keyed for the lexical-tag schema.

    `ocr_map` is an optional `{tweet_id: ocr_text}` overlay sourced from
    `data/tags/image_ocr.parquet`. When the OCR layer hasn't run yet,
    pass `None` and the tagger silently runs against tweet text alone.
    """
    ocr_map = ocr_map or {}
    df = pl.read_parquet(path)
    if df.is_empty():
        return []
    rows: list[dict[str, Any]] = []
    handle_stem = path.stem  # e.g. "DHSgov" or "_misc"
    for r in df.iter_rows(named=True):
        tweet_id = str(r.get("tweet_id") or "")
        if not tweet_id:
            continue
        handle = str(r.get("account_handle") or handle_stem)
        # For `_misc.parquet`, the parquet's author handle is the
        # original tweet author (a non-tracked person), so its category
        # is `public` by definition. For tracked parquets, look it up.
        category = "public" if handle_stem == "_misc" else account_categories.get(handle, "core")
        text = str(r.get("text_resolved") or r.get("text") or "")
        media = r.get("media") or []
        media_count = len(media) if isinstance(media, list) else 0
        mentions = r.get("mentions") or []
        if not isinstance(mentions, list):
            mentions = []
        unavailable_text = " ".join(
            str(r.get(col) or "")
            for col in ("unavailable_reason", "unavailable_text")
            if r.get(col)
        )
        tags = tag_text(
            text,
            tweet_type=r.get("tweet_type"),
            mentions=[str(x) for x in mentions if x],
            media_count=media_count,
            account_category=category,
            ocr_text=ocr_map.get(tweet_id, ""),
            is_unavailable=bool(r.get("unavailable_detected_at")),
            unavailable_text=unavailable_text,
        )
        rows.append(
            {
                "tweet_id": tweet_id,
                "account_handle": handle,
                "tagger_version": TAGGER_VERSION,
                "tagged_at": tagged_at,
                "tags": tags,
            }
        )
    return rows


def atomic_write_parquet(df: pl.DataFrame, path: Path) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.write_parquet(tmp, compression="zstd")
    os.replace(tmp, path)


def write_tag_manifest(stats: dict[str, Any]) -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    manifest_path = TAGS_DIR / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    layers = manifest.get("layers")
    if not isinstance(layers, dict):
        layers = {}
    layers["lexical"] = stats
    manifest = {**stats, "layers": layers}
    tmp = TAGS_DIR / "manifest.tmp.json"
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, manifest_path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--handle",
        help="Restrict to a single parquet (the handle without .parquet) for debugging.",
    )
    args = p.parse_args(argv)

    account_categories = load_account_categories()
    parquets = discover_canonical_parquets()
    if args.handle:
        parquets = [p for p in parquets if p.stem == args.handle]
    if not parquets:
        LOG.warning("no canonical parquets found", data_dir=str(DATA_DIR))
        TAGS_DIR.mkdir(parents=True, exist_ok=True)
        atomic_write_parquet(empty_lexical_tag_dataframe(), TAGS_DIR / "lexical.parquet")
        write_tag_manifest(
            {
                "tagger_version": TAGGER_VERSION,
                "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "row_count": 0,
                "parquets_scanned": [],
            }
        )
        return 0

    tagged_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    ocr_map = load_ocr_map()
    if ocr_map:
        LOG.info("loaded OCR sidecar overlay", tweets_with_ocr=len(ocr_map))
    all_rows: list[dict[str, Any]] = []
    per_file: dict[str, int] = {}
    for path in parquets:
        rows = tag_one_parquet(path, account_categories, tagged_at, ocr_map=ocr_map)
        all_rows.extend(rows)
        per_file[path.name] = len(rows)
        LOG.info("tagged parquet", file=path.name, rows=len(rows))

    df = (
        pl.DataFrame(all_rows, schema=LEXICAL_TAG_SCHEMA)
        if all_rows
        else empty_lexical_tag_dataframe()
    )
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = TAGS_DIR / "lexical.parquet"
    atomic_write_parquet(df, out_path)

    # Tag-frequency stats for the manifest — useful for spot-checking
    # rule drift over time.
    freq: dict[str, int] = {}
    for r in all_rows:
        for entry in r["tags"] or []:
            t = entry["tag"]
            freq[t] = freq.get(t, 0) + 1
    write_tag_manifest(
        {
            "tagger_version": TAGGER_VERSION,
            "generated_at": tagged_at,
            "row_count": df.height,
            "parquets_scanned": per_file,
            "tag_frequency": dict(sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))),
        }
    )
    LOG.info(
        "lexical tagger complete",
        rows=df.height,
        unique_tags=len(freq),
        out=str(out_path.relative_to(REPO_ROOT)),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
