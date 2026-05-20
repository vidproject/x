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
    ("homicide", r"\bhomicides?\b"),
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

HOMICIDE_SUBTYPE_VOCAB: tuple[tuple[str, str], ...] = (("murder", r"\bmurder(?:s|ed|ers?|ing)?\b"),)

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
PATTERN_ACTION_SELF_DEPORTATION = _compile(
    r"\bself[- ]deport(?:ed|ing|ation)?\b"
    r"|\bvoluntar(?:y|ily)\s+depart(?:ure|ed|ing)?\b"
    r"|\bleave voluntarily\b"
    r"|\breport\s+(?:your|their|his|her|an|the)?\s*departure\b"
    r"|\btake\s+control\s+of\s+(?:your|their|his|her)\s+departure\b"
)
PATTERN_ACTION_DEPORTATION = _compile(
    r"(?<!self-)(?<!self )\b(deport(?:ed|ing|ation)?|remov(?:ed|al)|repatriat(?:ed|ion))\b"
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
    r"apprenticeships?|small businesses?|tax cuts?|taxes|inflation|prices?|"
    r"cost of living|tariffs?|trade deficit|supply chains?|GDP|stock market|markets?)\b"
)
PATTERN_TOPIC_MILITARY = _compile(
    r"\b(military|armed forces|servicemembers?|service members?|veterans?|troops?|"
    r"soldiers?|sailors?|airmen|marines|guardsmen|army|navy|air force|space force|"
    r"marine corps|coast guard|national guard|USAF|USSF|USMC|pentagon|"
    r"department of defense|DoD|DOD|veterans affairs|combat|battlefield|"
    r"war zone|deployed|deployment)\b"
)
BRANCH_VOCAB: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("army", _compile(r"\b(?:(?:u\.s\.\s+)?army|soldiers?)\b")),
    ("navy", _compile(r"\b(?:(?:u\.s\.\s+)?navy|sailors?)\b")),
    ("air-force", _compile(r"\b(?:(?:u\.s\.\s+)?air\s+force|usaf|airm[ae]n)\b")),
    ("space-force", _compile(r"\b(?:(?:u\.s\.\s+)?space\s+force|ussf)\b")),
    ("marines", _compile(r"\b(?:marine\s+corps|u\.s\.\s+marines?|usmc|marines)\b")),
    ("coast-guard", _compile(r"\b(?:coast\s+guard|coast\s+guards?m[ae]n)\b")),
    ("national-guard", _compile(r"\b(?:national\s+guard|national\s+guards?m[ae]n)\b")),
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
    ("military", PATTERN_TOPIC_MILITARY),
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
    r"|\bin\s+jesus(?:'s|')?\s+name\b"
)
PATTERN_THEME_RELIGION = _compile(
    r"\breligio(?:n|us)\b"
    r"|\bfaith(?:ful|s|[- ]based)?\b"
    r"|\b(?:worship|church(?:es)?|synagogue|mosque|temple|chaplain)\b"
    r"|\b(?:prayer|prayers|pray|praying|prayed)\b"
    r"|\b(?:bless|blessed|blessing|blessings)\b"
    r"|\b(?:god|lord|jesus|christ|christian(?:ity|s)?|judeo[- ]christian)\b"
    r"|\b(?:bible|biblical|scripture|scriptural)\b"
)
PATTERN_THEME_RELIGION_EXPLETIVE = _compile(
    r"\b(?:oh\s+my\s+god|omg|god\s*damn(?:ed|it)?|goddamn(?:ed)?|"
    r"for\s+god'?s\s+sake|good\s+lord|thank\s+god)\b"
)
RELIGION_EXPLETIVE_WINDOW_CHARS = 18
PATTERN_THEME_TRANSGENDER = _compile(
    r"\btransgender\b"
    r"|\bgender[- ](?:ideology|identity|affirming|transition|dysphoria)\b"
    r"|\b(?:biological|transgender)\s+"
    r"(?:males?|females?|man|woman|men|women|boys?|girls?)\b"
    r"|\b(?:men|males|boys)\s+in\s+(?:women[\u2019']?s|girls[\u2019']?)\s+sports\b"
    r"|\b(?:men|males|boys)\s+(?:competing|playing|participating)\s+"
    r"(?:in|against|with)\s+(?:women|girls|female\s+athletes?)\b"
    r"|\b(?:protect|save|defend)\s+(?:women[\u2019']?s|girls[\u2019']?)\s+sports\b"
    r"|\btitle\s+ix\b.{0,80}\b(?:transgender|gender|women[\u2019']?s\s+sports|girls[\u2019']?\s+sports)\b"
    r"|\b(?:transgender|gender|women[\u2019']?s\s+sports|girls[\u2019']?\s+sports)\b.{0,80}\btitle\s+ix\b"
)
PATTERN_SUBJECT_CBP_HOME_APP = _compile(
    r"\b@?CBP\s+Home\s+App\b"
    r"|\b@?CBP\s+Home\b"
    r"|\bDHS\.?GOV/CBPHOME\b"
    r"|\b(?:dhs|cbp)\.gov/(?:cbp[-_]?home|projecthomecoming)\b"
)
PATTERN_THEME_CBP_HOME = _compile(
    r"\bCBP\s+Home\b"
    r"|\bProject\s+Homecoming\b"
    r"|\bself[- ]deport(?:ed|ing|ation)?\b"
    r"|\bfree\s+(?:flight|plane\s+ticket|ticket)\s+home\b"
    r"|\bcomplimentary\s+plane\s+ticket\s+home\b"
    r"|\bexit\s+bonus\b"
    r"|\btake\s+control\s+of\s+(?:your|their|his|her)\s+departure\b"
    r"|\b(?:illegal\s+aliens?|aliens?|migrants?|CBP\s+Home|self[- ]deport|deport)\b"
    r".{0,120}\bgo\s+home\b"
    r"|\bgo\s+home\b.{0,120}\b"
    r"(?:illegal\s+aliens?|aliens?|migrants?|CBP\s+Home|self[- ]deport|deport)\b"
)
PATTERN_SUBJECT_CELEBRITY = _compile(
    r"\bSydney\s+Sweeney\b"
    r"|\b(?:actress|influencer|celebrity|pop\s+star|movie\s+star|Hollywood\s+star)"
    r"\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b"
    r"|\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+,\s+(?:an?\s+)?"
    r"(?:actress|influencer|celebrity|pop\s+star|movie\s+star)\b"
)
PATTERN_THEME_POP_CULTURE_ENFORCEMENT = _compile(
    r"\bSydney\s+Sweeney\b"
    r"|\bAmerican\s+Eagle\b.{0,100}\b(?:ICE|DHS|CBP|deport|illegal\s+alien|border)\b"
    r"|\b(?:ICE|DHS|CBP|deport|illegal\s+alien|border)\b.{0,100}\bAmerican\s+Eagle\b"
    r"|\bgood\s+genes\b.{0,100}\b(?:jeans|ICE|DHS|CBP|deport|border|illegal\s+alien)\b"
)
# --- video:<kind> --------------------------------------------------------
#
# Video-nature heuristics. Federal accounts post a few recognizable
# species: bodycam / raid footage, polished PSAs and ad spots, music
# videos set to enforcement footage, news-clip embeds, sit-down
# interviews. The viewer wants to filter by these. We tag from the tweet
# body (and OCR overlay when available) — the gate in `tag_text` only
# applies these tags when the tweet actually has a video attached, so
# textual matches on a text-only retweet don't pollute the namespace.

PATTERN_VIDEO_BODYCAM = _compile(
    r"\bbody[- ]?cam(?:era)?\b"
    r"|\b(?:bwc|body-worn\s+camera)\b"
    r"|\b(?:ice|cbp|hsi|border\s+patrol)\s+(?:raid|arrest|operation|takedown|sting)\b.{0,80}\b(?:footage|video|clip|caught\s+on)\b"
    r"|\b(?:raid|arrest|takedown|sting|chase|pursuit)\s+footage\b"
    r"|\bcaught\s+on\s+(?:camera|video|tape)\b"
)
PATTERN_VIDEO_INTERVIEW = _compile(
    r"\binterview(?:ed|ing|s)?\s+(?:with|on|about|by)\b"
    r"|\b(?:sit[- ]down|one[- ]on[- ]one)\s+(?:with|interview)\b"
    r"|\b(?:full|exclusive|extended)\s+interview\b"
    r"|\b(?:secretary|director|administrator|chief|spokesperson|senator|representative|congressman|congresswoman|press\s+secretary)\s+\w+\s+(?:joins?|joined|spoke|speaks|sat\s+down)\b"
    r"|\b(?:joins?|joined)\s+(?:fox|cnn|msnbc|cbs|abc|nbc|newsmax|oan|cspan|c-span|newsnation|the\s+\w+\s+show)\b"
)
PATTERN_VIDEO_MUSIC_VIDEO = _compile(
    r"\bmusic\s+video\b"
    r"|\bset\s+to\s+(?:music|the\s+song|the\s+track)\b"
    r"|\b(?:🎵|🎶)\b"
    r"|\b(?:song|anthem|ballad)\s+(?:by|about|for)\b"
    r"|\btrack\s+by\b"
)
PATTERN_VIDEO_NEWS_CLIP = _compile(
    r"\b(?:fox\s+(?:news|business)|cnn|msnbc|cbs|abc|nbc|newsmax|oan|cspan|c-span|newsnation|"
    r"the\s+\w+\s+report|the\s+\w+\s+show|nightly\s+news|morning\s+joe|hannity|tucker|maddow|"
    r"reuters|bloomberg|wsj|wall\s+street\s+journal|new\s+york\s+times|washington\s+post|"
    r"daily\s+wire|breitbart)\b"
    r"|\bvia\s+@?(?:foxnews|cnn|msnbc|cbsnews|abcnews|nbcnews)\b"
    r"|\b(?:reporting|reports|reported)\s+(?:on|that|from)\b"
)
PATTERN_VIDEO_PSA = _compile(
    r"\b(?:psa|public\s+service\s+announcement)\b"
    r"|\b(?:learn\s+more|find\s+out\s+more|more\s+info|visit\s+(?:our\s+)?website)\s+at\b"
    r"|\bdid\s+you\s+know(?:\s+that)?\b"
    r"|\b(?:know\s+(?:your\s+rights|the\s+facts|the\s+signs)|protect\s+yourself|stay\s+safe|"
    r"report\s+(?:a|suspicious))\b"
    r"|\bcall\s+(?:1-?800|1-?888|1-?877|1-?866)[- ]?\d"
)
PATTERN_VIDEO_SPEECH = _compile(
    r"\b(?:gives|delivers|delivered|gave|giving|delivering)\s+(?:a\s+)?(?:speech|address|remarks|statement)\b"
    r"|\b(?:remarks|speech|statement|address)\s+(?:by|from|at|to|on)\b"
    r"|\b(?:press\s+(?:conference|briefing|gaggle))\b"
    r"|\b(?:oval\s+office|rose\s+garden|east\s+room|state\s+dining\s+room)\b"
)
PATTERN_VIDEO_AD = _compile(
    r"\b(?:new\s+(?:ad|advert|spot|commercial))\b"
    r"|\b(?:campaign|recruitment)\s+(?:ad|spot|video)\b"
    r"|\bjoin\s+(?:ice|cbp|hsi|the\s+(?:ice|cbp)\s+(?:team|family))\b"
    r"|\bapply\s+(?:today|now)\s+at\b"
)

PATTERN_STATUS_COPYRIGHT_REMOVAL = _compile(r"\b(copyright|dmca)\b")
PATTERN_SLOGAN_NICE = _compile(r"\b(NICE day|NICE morning|ICE is NICE|NICE city)\b")
PATTERN_SLOGAN_WORST = _compile(r"\bWORST OF THE WORST\b")
PATTERN_SLOGAN_REPORTRECON = _compile(r"\bReport\.\s*Recon\.\s*Raid\.")
PATTERN_SLOGAN_CRIMINAL_ILLEGAL_ALIEN = _compile(r"\bcriminal\s+illegal\s+aliens?\b")
PATTERN_SLOGAN_ILLEGAL_ALIEN = _compile(r"\billegal\s+aliens?\b")
PATTERN_SLOGAN_FREE_TICKET_HOME = _compile(
    r"\bFREE\s+(?:TICKET|FLIGHT|PLANE\s+TICKET)\s+HOME\b"
    r"|\bfree\s+(?:ticket|flight|plane\s+ticket)\s+home\b"
    r"|\bcomplimentary\s+plane\s+ticket\s+home\b"
)
PATTERN_SLOGAN_GO_HOME = _compile(
    r"\b(?:illegal\s+aliens?|aliens?|migrants?|CBP\s+Home|self[- ]deport|deport)\b"
    r".{0,120}\bgo\s+home\b"
    r"|\bgo\s+home\b.{0,120}\b"
    r"(?:illegal\s+aliens?|aliens?|migrants?|CBP\s+Home|self[- ]deport|deport)\b"
)
PATTERN_SLOGAN_PROJECT_HOMECOMING = _compile(r"\bPROJECT\s+HOMECOMING\b")
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
    community_note: dict[str, Any] | None = None,
    video_count: int = 0,
    video_max_duration_sec: float | None = None,
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
    if community_note:
        add("status:community-note")

    # Concatenate OCR text (when present) so a poster's stamped slogan
    # earns the same tags as if it had been typed into the tweet body.
    # The separator (" ¶ ") doesn't appear in any of our patterns and
    # keeps regex matches from spanning the boundary accidentally.
    body = (f"{text} ¶ {ocr_text}" if text else ocr_text) if ocr_text else text

    if not body:
        # Even with no text we still get format: + agency: + sticky
        # default. Skip the regex pass.
        _ensure_intrinsic_parent_topics(entries, "", add)
        _maybe_immigration_default(entries, account_category, "", add)
        return entries
    text = body

    # Single-shot regex tags
    for pat, tag in (
        (PATTERN_FRAME_CRIMINAL, "frame:criminal"),
        (PATTERN_ACTION_DETENTION, "action:detention"),
        (PATTERN_ACTION_SELF_DEPORTATION, "action:self-deportation"),
        (PATTERN_ACTION_DEPORTATION, "action:deportation"),
        (PATTERN_ACTION_REPORT_TO_ICE, "action:report-immigrants"),
        (PATTERN_SUBJECT_CBP_HOME_APP, "subject:cbp-home-app"),
        (PATTERN_SUBJECT_CELEBRITY, "subject:celebrity"),
        (PATTERN_TOPIC_ECONOMY, "topic:economy"),
        (PATTERN_TOPIC_MILITARY, "topic:military"),
        (PATTERN_TOPIC_LAUDATORY, "topic:laudatory"),
        (PATTERN_THEME_BORDER, "theme:border"),
        (PATTERN_THEME_SANCTUARY, "theme:sanctuary-cities"),
        (PATTERN_THEME_WORKSITE, "theme:worksite-enforcement"),
        (PATTERN_THEME_HOMELAND, "theme:homeland"),
        (PATTERN_THEME_NATIVISM, "theme:nativism"),
        (PATTERN_THEME_CHRISTIANITY, "theme:christianity"),
        (PATTERN_THEME_TRANSGENDER, "theme:transgender"),
        (PATTERN_THEME_CBP_HOME, "theme:cbp-home"),
        (PATTERN_THEME_POP_CULTURE_ENFORCEMENT, "theme:pop-culture-enforcement"),
        (PATTERN_SLOGAN_NICE, "slogan:nice"),
        (PATTERN_SLOGAN_WORST, "slogan:worst"),
        (PATTERN_SLOGAN_REPORTRECON, "slogan:reportrecon"),
        (PATTERN_SLOGAN_CRIMINAL_ILLEGAL_ALIEN, "slogan:criminal-illegal-alien"),
        (PATTERN_SLOGAN_ILLEGAL_ALIEN, "slogan:illegal-alien"),
        (PATTERN_SLOGAN_FREE_TICKET_HOME, "slogan:free-ticket-home"),
        (PATTERN_SLOGAN_GO_HOME, "slogan:go-home"),
        (PATTERN_SLOGAN_PROJECT_HOMECOMING, "slogan:project-homecoming"),
        (PATTERN_GENRE_STATISTICS, "genre:statistics"),
        (PATTERN_GENRE_DIRECTIVE, "genre:directive"),
        (PATTERN_ANGEL_FAMILY, "subject:angel-family"),
        (PATTERN_NATIVE_BORN_CITIZEN, "subject:native-born-citizen"),
    ):
        m = pat.search(text)
        if m:
            add(tag, span=m.span())

    if m := _theme_religion_match(text):
        add("theme:religion", span=m.span())

    # branch:<BRANCH> - military branch subtopics. These are narrower than
    # topic:military; the parent is enforced below for branch-only aliases.
    for slug, pat in BRANCH_VOCAB:
        for m in pat.finditer(text):
            add(f"branch:{slug}", span=m.span())

    if _general_topic_score(text) >= 3:
        add("topic:general")

    # crime:<TYPE> — every distinct match emits one tag entry.
    for slug, pat_str in CRIME_VOCAB:
        for m in re.finditer(pat_str, text, re.I):
            add(f"crime:{slug}", span=m.span())
    for slug, pat_str in HOMICIDE_SUBTYPE_VOCAB:
        for m in re.finditer(pat_str, text, re.I):
            add("crime:homicide", span=m.span())
            add(f"homicide:{slug}", span=m.span())

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

    # video:<kind> + video:duration-bucket — only when a video is attached.
    # Apply *all* matching kinds so a single tweet can carry e.g.
    # video:bodycam + video:music-video (the song-set-to-raid-footage genre).
    # Duration bucket is derived from the longest video on the tweet:
    #   short  : ≤ 30s
    #   medium : 30s < d ≤ 120s
    #   long   : > 120s
    # We tag the bucket regardless of whether a kind matched so the viewer
    # can filter "all videos under 30s" without depending on text cues.
    if video_count > 0:
        for pat, tag in (
            (PATTERN_VIDEO_BODYCAM, "video:bodycam"),
            (PATTERN_VIDEO_INTERVIEW, "video:interview"),
            (PATTERN_VIDEO_MUSIC_VIDEO, "video:music-video"),
            (PATTERN_VIDEO_NEWS_CLIP, "video:news-clip"),
            (PATTERN_VIDEO_PSA, "video:psa"),
            (PATTERN_VIDEO_SPEECH, "video:speech"),
            (PATTERN_VIDEO_AD, "video:ad"),
        ):
            m = pat.search(text)
            if m:
                add(tag, span=m.span())
        if video_max_duration_sec is not None and video_max_duration_sec > 0:
            if video_max_duration_sec <= 30:
                add("video:short")
            elif video_max_duration_sec <= 120:
                add("video:medium")
            else:
                add("video:long")

    _ensure_intrinsic_parent_topics(entries, text, add)
    _maybe_immigration_default(entries, account_category, text, add)
    return entries


def _theme_religion_match(text: str) -> re.Match[str] | None:
    """Return the first religious-language match outside common outbursts."""
    for match in PATTERN_THEME_RELIGION.finditer(text):
        start, end = match.span()
        window = text[
            max(0, start - RELIGION_EXPLETIVE_WINDOW_CHARS) : min(
                len(text), end + RELIGION_EXPLETIVE_WINDOW_CHARS
            )
        ]
        if PATTERN_THEME_RELIGION_EXPLETIVE.search(window):
            continue
        return match
    return None


def _general_topic_score(text: str) -> int:
    """Count broad problem domains in a multi-issue / grievance-style post."""
    return sum(1 for _slug, pat in GENERAL_TOPIC_PATTERNS if pat.search(text))


INTRINSIC_PARENT_TOPICS_EXACT: dict[str, tuple[str, ...]] = {
    "action:deportation": ("topic:immigration",),
    "action:self-deportation": ("topic:immigration",),
    "action:report-immigrants": ("topic:immigration",),
    "agency:ICEgov": ("topic:immigration",),
    "agency:CBP": ("topic:immigration",),
    "agency:DHSgov": ("topic:immigration",),
    "agency:HSI_HQ": ("topic:immigration",),
    "agency:USBPChief": ("topic:immigration",),
    "frame:criminal": ("topic:immigration",),
    "shape:lineup": ("topic:immigration",),
    "subject:angel-family": ("topic:immigration",),
    "subject:cbp-home-app": ("topic:immigration",),
    "subject:enforcement-op": ("topic:immigration",),
    "theme:border": ("topic:immigration",),
    "theme:cbp-home": ("topic:immigration",),
    "theme:nativism": ("topic:immigration",),
    "theme:pop-culture-enforcement": ("topic:immigration",),
    "theme:sanctuary-cities": ("topic:immigration",),
    "theme:worksite-enforcement": ("topic:economy", "topic:immigration"),
    "slogan:criminal-illegal-alien": ("topic:immigration",),
    "slogan:free-ticket-home": ("topic:immigration",),
    "slogan:go-home": ("topic:immigration",),
    "slogan:illegal-alien": ("topic:immigration",),
    "slogan:project-homecoming": ("topic:immigration",),
}
INTRINSIC_PARENT_TOPICS_PREFIXES: tuple[tuple[str, str], ...] = (("origin:", "topic:immigration"),)
PATTERN_EXPLICIT_IMMIGRATION_TOPIC = _compile(
    r"\b(immigration|immigrants?|migrants?|asylum|illegal\s+(?:alien|immigrant)s?|"
    r"undocumented\s+(?:alien|immigrant)s?|border patrol|CBP\s+Home|"
    r"deport(?:ed|ing|ation)?|removals?)\b"
)


def _ensure_intrinsic_parent_topics(
    entries: list[dict[str, Any]],
    text: str,
    add: Callable[..., None],
) -> None:
    """Add broad topic parents implied by narrower deterministic tags."""
    existing_tags = {str(e["tag"]) for e in entries}
    if (
        any(tag.startswith("branch:") for tag in existing_tags)
        and "topic:military" not in existing_tags
    ):
        add("topic:military")
        existing_tags.add("topic:military")
    for tag in list(existing_tags):
        for parent in INTRINSIC_PARENT_TOPICS_EXACT.get(tag, ()):
            add(parent)
            existing_tags.add(parent)
        for prefix, parent in INTRINSIC_PARENT_TOPICS_PREFIXES:
            if tag.startswith(prefix):
                add(parent)
                existing_tags.add(parent)
    if PATTERN_EXPLICIT_IMMIGRATION_TOPIC.search(text):
        add("topic:immigration")
    if (
        any(e["tag"] == "theme:worksite-enforcement" for e in entries)
        and "topic:economy" not in existing_tags
    ):
        add("topic:economy")


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
    "theme:cbp-home",
    "slogan:",
    "shape:",
    "subject:cbp-home-app",
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
        video_count = 0
        video_max_duration_sec: float | None = None
        if isinstance(media, list):
            for m in media:
                if not isinstance(m, dict):
                    continue
                mt = m.get("media_type")
                if mt == "video" or mt == "animated_gif":
                    video_count += 1
                    dur = m.get("duration_sec")
                    if (
                        isinstance(dur, (int, float))
                        and dur > 0
                        and (video_max_duration_sec is None or dur > video_max_duration_sec)
                    ):
                        video_max_duration_sec = float(dur)
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
            community_note=r.get("community_note"),
            video_count=video_count,
            video_max_duration_sec=video_max_duration_sec,
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
