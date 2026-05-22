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
TAG_OVERRIDES_PATH = REPO_ROOT / "config" / "tag_overrides.yaml"

TAGGER_VERSION = "lexical-v2"
GENERATED_DATA_PARQUETS = frozenset({"catalog.parquet"})

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
    ("sexual", r"\b(?:sexual\s+assault|sex\s+crime|sex\s+offen[sc]e|sex\s+abuse)\b"),
    (
        "child-sexual",
        r"\b(?:child\s+(?:sex(?:ual)?|pornography|molest(?:ation)?)|"
        r"child\s+sexual|minor\s+sex(?:ual)?|sex(?:ual)?\s+abuse\s+of\s+(?:a\s+)?minor)\b",
    ),
    ("child", r"\bchild (?:abuse|sex|pornography|molest)|child sexual\b"),
    ("gang", r"\bgang (?:member|members|affiliation)\b|\bgang-related\b"),
    ("ms13", r"\bMS-?13\b"),
    ("tren-de-aragua", r"\bTren de Aragua\b|\bTdA\b"),
    ("narcotics", r"\bnarcotic[s]?\b"),
    ("fraud", r"\bfraud(?:ulent|ster)?\b"),
    ("disobedience", r"\b(?:contempt\s+of\s+court|violat(?:e|ed|ing|ion)\s+(?:of\s+)?(?:a\s+)?court\s+order|disobey(?:ed|ing)?\s+(?:a\s+)?court\s+order)\b"),
    ("perjury", r"\bperjur(?:y|ed)\b|\blying\s+under\s+oath\b"),
    ("arson", r"\barson(?:ist)?\b"),
    ("weapon", r"\bweapon[s]?\b|\billegal firearm[s]?\b"),
    ("firearm", r"\bfirearm[s]?\b"),
)

CRIME_SUBTYPE_VOCAB: tuple[tuple[str, str], ...] = (("murder", r"\bmurder(?:s|ed|ers?|ing)?\b"),)

CRIME_PARENT_TAGS: dict[str, tuple[str, ...]] = {
    "crime:murder": ("crime:homicide",),
    "crime:rape": ("crime:sexual",),
    "crime:sodomy": ("crime:sexual",),
    "crime:child-sexual": ("crime:sexual",),
    "crime:perjury": ("crime:disobedience",),
    "crime:fentanyl": ("crime:narcotics",),
    "crime:cocaine": ("crime:narcotics",),
    "crime:meth": ("crime:narcotics",),
}

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
        "DEAHQ",
        "USMarshalsHQ",
        "HHSGov",
        "USTreasury",
        "FEMA",
        "DeptofWar",
        "CENTCOM",
        "Southcom",
        "EPA",
        "USCG",
        "USCGAcademy",
        "ComdtUSCG",
        "VComdtUSCG",
        "USCGLANTAREA",
        "USCGPACAREA",
        "USCGSoutheast",
        "USCGHeartland",
        "USCGNorCal",
        "USCG_Tri_State",
        "USArmyNorth",
        "USNationalGuard",
        "USNorthernCmd",
        "TSA",
        "SecretService",
        "CISAgov",
        "CISAInfraSec",
        "CISACyber",
        "FLETC",
        "ODNIgov",
        "NSAGov",
        "IPRCenter",
        "USAttyEssayli",
        "USAttyPirro",
        "ERO_LosAngeles",
        "ERO_HQ",
    }
)

AGENCY_MENTION_ALIASES: dict[str, str] = {
    **{handle.lower(): handle for handle in AGENCY_HANDLES},
    "fbidirectorkash": "FBI",
    "fbimostwanted": "FBI",
    "fbimnneapolis": "FBI",
    "fbiminneapolis": "FBI",
    "fbitampa": "FBI",
    "thejusticedept": "DOJgov",
    "justiceoig": "DOJgov",
    "epaleezeldin": "EPA",
    "secwar": "DeptofWar",
}

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


AGENCY_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_compile(r"\bFBI\b|\bFederal Bureau of Investigation\b"), "agency:FBI"),
    (
        _compile(r"\bDOJ\b|\bDepartment of Justice\b|\bJustice Department\b"),
        "agency:DOJgov",
    ),
    (
        _compile(
            r"\bICE\b|\bI\.C\.E\.\b|\bImmigration and Customs Enforcement\b|"
            r"\bU\.S\.\s+Immigration\s+and\s+Customs\s+Enforcement\b"
        ),
        "agency:ICEgov",
    ),
    (
        _compile(
            r"\bCBP\b|\bCustoms and Border Protection\b|\bU\.S\.\s+Border Patrol\b|"
            r"\bBorder Patrol\b"
        ),
        "agency:CBP",
    ),
    (
        _compile(r"\bUSCIS\b|\bCitizenship and Immigration Services\b"),
        "agency:USCIS",
    ),
    (
        _compile(r"\bDHS\b|\bDepartment of Homeland Security\b"),
        "agency:DHSgov",
    ),
    (
        _compile(r"\bDEA\b|\bDrug Enforcement Administration\b"),
        "agency:DEAHQ",
    ),
    (
        _compile(r"\bUSMS\b|\bU\.?S\.?\s+Marshals(?:\s+Service)?\b"),
        "agency:USMarshalsHQ",
    ),
    (
        _compile(r"\bHHS\b|\bHealth and Human Services\b"),
        "agency:HHSGov",
    ),
    (
        _compile(r"\bState Department\b|\bDepartment of State\b"),
        "agency:StateDept",
    ),
    (
        _compile(
            r"\bTreasury Department\b|\bDepartment of the Treasury\b|"
            r"\bU\.?S\.?\s+Treasury\b"
        ),
        "agency:USTreasury",
    ),
    (
        _compile(r"\bFEMA\b|\bFederal Emergency Management Agency\b"),
        "agency:FEMA",
    ),
    (_compile(r"\bEPA\b|\bEnvironmental Protection Agency\b"), "agency:EPA"),
    (_compile(r"\bDepartment of War\b|\bDept\.?\s+of\s+War\b"), "agency:DeptofWar"),
    (_compile(r"\bCENTCOM\b|\bU\.?S\.?\s+Central Command\b"), "agency:CENTCOM"),
    (_compile(r"\bSOUTHCOM\b|\bU\.?S\.?\s+Southern Command\b"), "agency:Southcom"),
    (_compile(r"\bUSCG\b|\bU\.?S\.?\s+Coast Guard\b|\bUnited States Coast Guard\b"), "agency:USCG"),
    (_compile(r"\bTSA\b|\bTransportation Security Administration\b"), "agency:TSA"),
    (_compile(r"\bU\.?S\.?\s+Secret Service\b|\bUnited States Secret Service\b"), "agency:SecretService"),
    (_compile(r"\bCISA\b|\bCybersecurity and Infrastructure Security Agency\b"), "agency:CISAgov"),
    (
        _compile(r"\bODNI\b|\bOffice of the Director of National Intelligence\b"),
        "agency:ODNIgov",
    ),
    (_compile(r"\bNSA\b|\bNational Security Agency\b"), "agency:NSAGov"),
    (
        _compile(r"\bFLETC\b|\bFederal Law Enforcement Training Centers?\b"),
        "agency:FLETC",
    ),
)


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
    r"war zone|deployed|deployment|carrier strike group|aircraft carrier|"
    r"carrier air wing|CVN\s*\d+|USS\s+[A-Z][A-Za-z0-9-]+|USNS\s+[A-Z][A-Za-z0-9-]+|"
    r"service academ(?:y|ies)|military academy|naval academy|coast guard academy|"
    r"air force academy|west point|USMA|USNA|USCGA|cadets?|midshipmen|"
    r"commander[- ]in[- ]chief|commandant)\b"
)
MILITARY_VOCAB: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("army", _compile(r"\b(?:(?:u\.s\.\s+)?army|soldiers?|west\s+point|USMA)\b")),
    (
        "navy",
        _compile(
            r"\b(?:(?:u\.s\.\s+)?navy|sailors?|naval\s+academy|USNA|midshipmen|"
            r"carrier\s+strike\s+group|aircraft\s+carrier|carrier\s+air\s+wing|"
            r"CVN\s*\d+|USS\s+[A-Z][A-Za-z0-9-]+|USNS\s+[A-Z][A-Za-z0-9-]+)\b"
        ),
    ),
    (
        "air-force",
        _compile(r"\b(?:(?:u\.s\.\s+)?air\s+force|usaf|airm[ae]n|air\s+force\s+academy)\b"),
    ),
    ("space-force", _compile(r"\b(?:(?:u\.s\.\s+)?space\s+force|ussf)\b")),
    ("marines", _compile(r"\b(?:marine\s+corps|u\.s\.\s+marines?|usmc|marines)\b")),
    (
        "coast-guard",
        _compile(r"\b(?:coast\s+guard|USCG|USCGA|coast\s+guard\s+academy|coasties?|coast\s+guards?m[ae]n)\b"),
    ),
    ("national-guard", _compile(r"\b(?:national\s+guard|national\s+guards?m[ae]n)\b")),
)
MILITARY_MENTION_ALIASES: dict[str, str] = {
    "usarmynorth": "army",
    "usnationalguard": "national-guard",
    "usguard": "national-guard",
    "usnavy": "navy",
    "usairforce": "air-force",
    "usspaceforce": "space-force",
    "usmc": "marines",
    "marines": "marines",
    "uscg": "coast-guard",
    "uscgacademy": "coast-guard",
    "comdtuscg": "coast-guard",
    "vcomdtuscg": "coast-guard",
    "uscglantarea": "coast-guard",
    "uscgpacarea": "coast-guard",
    "uscgsoutheast": "coast-guard",
    "uscgheartland": "coast-guard",
    "uscgnorcal": "coast-guard",
    "uscg_tri_state": "coast-guard",
}
PATTERN_TOPIC_LAUDATORY = _compile(
    # Laudatory = a general touting of the administration's accomplishments
    # (a "laundry list" of wins), NOT any single booster word. Bare words like
    # "historic", "delivering", "winning", "momentum", "success" produced heavy
    # false positives ("delivering remarks", "Massie winning", "vaccine
    # delivered", "historic astronauts"), so require explicit accomplishment-
    # list framing: signature slogans, check-marked bullet lists, or an
    # accomplishment noun qualified as historic/record/biggest/etc.
    r"\bpromises?\s+made[,;:.]?\s+promises?\s+kept\b"
    r"|\bgolden\s+age\s+of\s+america\b"
    r"|(?:[✅✔☑][^✅✔☑\n]{1,90}){2,}"
    r"|\bdeliver(?:s|ing|ed)\s+(?:on\s+(?:his|our|its|their|the)\s+promises\b|for\s+(?:the\s+)?american\b)"
    r"|\b(?:historic|record(?:[- ](?:breaking|setting))?|biggest|greatest|unprecedented|largest)"
    r"\s+(?:list\s+of\s+)?(?:accomplishments?|achievements?|wins?|victories|progress|results|deals?)\b"
    r"|\b(?:list|litany|series)\s+of\s+(?:accomplishments?|achievements?|wins?|victories)\b"
    r"|\b(?:the\s+administration|president|trump)(?:'s)?\s+(?:historic\s+|record\s+)?"
    r"(?:accomplishments?|achievements?|track\s+record)\b"
)
PATTERN_EVENT_PALESTINE = _compile(
    r"\b(?:palestin(?:e|ian)s?|gaza(?:n)?s?|hamas|israel[- ]hamas|"
    r"israel[- ]palestin(?:e|ian)|west\s+bank)\b"
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
    r"|\b(?:our\s+)?forefathers?\b"
)
PATTERN_NATIVISM_INHERITANCE = _compile(r"\b(?:inheritors?|inheritance|inherit(?:ed|ing)?)\b")
PATTERN_NATIVISM_INHERITANCE_CONTEXT = _compile(
    r"\b(?:american|america|nation|national|citizens?|native[- ]born|foreign[- ]born|"
    r"immigrants?|migrants?|aliens?|homeland|heritage|birthright|forefathers?|"
    r"founders?|ancestors?|descendants?|civilization|our\s+people|our\s+country|"
    r"our\s+nation|american\s+(?:workers?|jobs?))\b"
)
PATTERN_THEME_CHRISTIANITY = _compile(
    r"\bchristian(?:ity|s)?\b"
    r"|\bjudeo[- ]christian\b"
    r"|\bchristian\s+(?:faith|values?|church|churches|heritage|nation)\b"
    r"|\bjesus(?:\s+christ)?\b"
    r"|\bchrist\s+(?:is\s+king|the\s+king|our\s+lord)\b"
    r"|\b(?:bible|biblical|scripture|scriptural)\b"
    r"|\b(?:[1-3]\s+)?(?:Genesis|Exodus|Leviticus|Numbers|Deuteronomy|Joshua|Judges|Ruth|"
    r"Samuel|Kings|Chronicles|Ezra|Nehemiah|Esther|Job|Psalms?|Proverbs|Ecclesiastes|"
    r"Song\s+of\s+Songs|Isaiah|Jeremiah|Lamentations|Ezekiel|Daniel|Hosea|Joel|Amos|"
    r"Obadiah|Jonah|Micah|Nahum|Habakkuk|Zephaniah|Haggai|Zechariah|Malachi|Matthew|"
    r"Mark|Luke|John|Acts|Romans|Corinthians|Galatians|Ephesians|Philippians|"
    r"Colossians|Thessalonians|Timothy|Titus|Philemon|Hebrews|James|Peter|Jude|"
    r"Revelation)\s+\d{1,3}:\d{1,3}(?:[-\u2013]\d{1,3})?\b"
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
    r"|\b(?:women[\u2019']?s|girls[\u2019']?)\s+sports\s+(?:are\s+)?"
    r"for\s+(?:women|girls|female\s+athletes?)\b"
    r"|\b(?:protect|save|defend)\s+(?:women[\u2019']?s|girls[\u2019']?)\s+sports\b"
    r"|\btitle\s+ix\b.{0,80}\b(?:transgender|gender|women[\u2019']?s\s+sports|girls[\u2019']?\s+sports)\b"
    r"|\b(?:transgender|gender|women[\u2019']?s\s+sports|girls[\u2019']?\s+sports)\b.{0,80}\btitle\s+ix\b"
)
PATTERN_THEME_CIVIL_DISTURBANCE = _compile(
    r"\b(?:riot(?:s|ers|ing)?|civil\s+disturbance|civil\s+unrest|mass\s+unrest|"
    r"violent\s+protests?|anti[- ]ICE\s+(?:riot(?:s|ers|ing)?|protests?)|"
    r"violent\s+demonstrators?|street\s+violence|mob\s+violence)\b"
)
DISTURBANCE_CITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "los-angeles-disturbance",
        _compile(
            r"\b(?:Los\s+Angeles|L\.A\.|LA)\b.{0,180}\b(?:riot(?:s|ers|ing)?|"
            r"civil\s+disturbance|civil\s+unrest|violent\s+protests?|anti[- ]ICE\s+"
            r"(?:riot(?:s|ers|ing)?|protests?)|federal\s+building|concrete\s+at\s+DHS\s+agents)\b"
            r"|\b(?:riot(?:s|ers|ing)?|civil\s+disturbance|civil\s+unrest|violent\s+protests?|"
            r"anti[- ]ICE\s+(?:riot(?:s|ers|ing)?|protests?)|federal\s+building|"
            r"concrete\s+at\s+DHS\s+agents)\b.{0,180}\b(?:Los\s+Angeles|L\.A\.|LA)\b"
        ),
    ),
    (
        "minneapolis-disturbance",
        _compile(
            r"\bMinneapolis\b.{0,180}\b(?:Bovino|riot(?:s|ers|ing)?|civil\s+disturbance|"
            r"civil\s+unrest|violent\s+protests?|protests?|tear\s+gas|pellet\s+guns?|"
            r"heavy[- ]handed|lawsuits?|killing|killed|disaster)\b"
            r"|\b(?:Bovino|riot(?:s|ers|ing)?|civil\s+disturbance|civil\s+unrest|"
            r"violent\s+protests?|protests?|tear\s+gas|pellet\s+guns?|heavy[- ]handed|"
            r"lawsuits?|killing|killed|disaster)\b.{0,180}\bMinneapolis\b"
        ),
    ),
    (
        "portland-disturbance",
        _compile(
            r"\bPortland\b.{0,180}\b(?:riot(?:s|ers|ing)?|civil\s+disturbance|civil\s+unrest|"
            r"violent\s+protests?|anti[- ]ICE\s+(?:riot(?:s|ers|ing)?|protests?)|"
            r"ICE\s+(?:detention|facility)|Antifa|domestic\s+terrorism)\b"
            r"|\b(?:riot(?:s|ers|ing)?|civil\s+disturbance|civil\s+unrest|violent\s+protests?|"
            r"anti[- ]ICE\s+(?:riot(?:s|ers|ing)?|protests?)|ICE\s+(?:detention|facility)|"
            r"Antifa|domestic\s+terrorism)\b.{0,180}\bPortland\b"
        ),
    ),
)
PATTERN_MARTYRDOM_WHY = _compile(
    r"\bthis\s+is\s+our\s+why\b"
    r"|\bthey\s+are\s+our\s+why\b"
    r"|\byou\s+are\s+why\s+we\s+fight\b"
)
PATTERN_MARTYRDOM_CONTEXT = _compile(
    r"\bangel\s+(?:famil(?:y|ies)|mom|dad|parent|mother|father|wife|husband|son|daughter|child)\b"
    r"|\b(?:honou?r|remember|commemorate|mourn|never\s+forget|say\s+their\s+names?)\b"
    r".{0,180}\b(?:victims?|fallen|killed|murdered|lives?|life|names?|memory|heroes|families)\b"
    r"|\b(?:victims?|fallen|killed|murdered|lives?|life|names?|memory|heroes|families)\b"
    r".{0,180}\b(?:honou?r|remember|commemorate|mourn|never\s+forget|say\s+their\s+names?)\b"
    r"|\b(?:would\s+still\s+be\s+alive|life\s+was\s+taken|lives?\s+were\s+taken|"
    r"preventable\s+(?:murder|death|tragedy)|killed\s+in\s+the\s+line\s+of\s+duty|"
    r"gave\s+everything)\b"
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
    r"[\s\S]{0,120}\bgo\s+home\b"
    r"|\bgo\s+home\b[\s\S]{0,120}\b"
    r"(?:illegal\s+aliens?|aliens?|migrants?|CBP\s+Home|self[- ]deport|deport)\b"
)
PATTERN_SUBJECT_CELEBRITY = _compile(
    r"\bSydney\s+Sweeney\b"
    r"|\b(?:actress|influencer|celebrity|pop\s+star|movie\s+star|Hollywood\s+star)"
    r"\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b"
    r"|\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+,\s+(?:an?\s+)?"
    r"(?:actress|influencer|celebrity|pop\s+star|movie\s+star)\b"
)
PATTERN_THEME_POP_CULTURE_REFERENCE = _compile(
    r"\bSydney\s+Sweeney\b"
    r"|\bAmerican\s+Eagle\b.{0,100}\b(?:ICE|DHS|CBP|deport|illegal\s+alien|border)\b"
    r"|\b(?:ICE|DHS|CBP|deport|illegal\s+alien|border)\b.{0,100}\bAmerican\s+Eagle\b"
    r"|\bgood\s+genes\b.{0,100}\b(?:jeans|ICE|DHS|CBP|deport|border|illegal\s+alien)\b"
)
# --- legal:birthright-citizenship ----------------------------------------
# Fires on the explicit policy phrase regardless of framing.
PATTERN_LEGAL_BIRTHRIGHT_CITIZENSHIP = _compile(r"\bbirthright\s+citizenship\b")

# Nativist birthright framing: "actual/real/true birthright of (every/all)
# Americans", "American('s) birthright", "our birthright" + harm verb.
# This is SEPARATE from bare "birthright citizenship" (which is just a policy
# term) — we want nativism only when the framing posits Americans' heritage
# entitlement as being stolen/diluted/erased/destroyed.
PATTERN_THEME_NATIVISM_BIRTHRIGHT = _compile(
    r"\b(?:actual|real|true)\s+birthright\s+of\s+(?:every|all)?\s*americans?\b"
    r"|\bamerican(?:'?s?)?\s+birthright\b"
    r"|\b(?:our|the)\s+birthright\b.{0,160}\b(?:steal|steals?|stolen|dilut|eras|destroy|destroyed|replac|betray|undermin)\b"
    r"|\b(?:steal|steals?|stolen|dilut|eras|destroy|destroyed|replac|betray|undermin)\b.{0,160}\b(?:our|the)\s+birthright\b"
    r"|\b(?:destroy(?:ing)?|destroys?|erasing?|replac(?:e|ing)?|betray(?:ing)?|undermin(?:e|ing)?|dilut(?:e|ing)?)"
    r"\b.{0,120}\b(?:our|the|american(?:'?s?)?)\s+birthright\b"
)

# --- slogan:find-and-kill -------------------------------------------------
# Note: OCR text may render "&" instead of "and".
PATTERN_SLOGAN_FIND_AND_KILL = _compile(
    r"\bwe\s+will\s+find\s+you\s+(?:and|&)\s+(?:we\s+will\s+)?kill\s+you\b"
    r"|\bfind\s+you\s+(?:and|&)\s+kill\s+you\b"
)

# --- slogan:import-third-world --------------------------------------------
PATTERN_SLOGAN_IMPORT_THIRD_WORLD = _compile(
    r"\bimport\s+the\s+third\s+world\b"
    r"|\bif\s+you\s+import\s+the\s+third\s+world\b"
)

# --- genre:parody + parody:<franchise> ------------------------------------
# A political/spoof parody of a recognizable media franchise.
# Franchise gazetteer: distinctive phrase -> franchise slug.
# "this is the way" alone is too generic (e.g. Mandalorian is pop-culture but
# appears in non-parody political text too), so we require at least ONE other
# Star Wars cue in the same text, OR the canonical "May the 4th/fourth be with
# you" phrase which is uniquely Star Wars in context.
PARODY_FRANCHISE_GAZETTEER: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        _compile(
            r"\bmay\s+the\s+(?:4th|fourth)\s+be\s+with\s+you\b"
            r"|\bthe\s+force\s+(?:is|be|will\s+be)\s+with\b"
            r"|\b(?:jedi|sith|death\s+star|lightsaber|darth\s+vader|skywalker)\b"
            r"|\b(?:galaxy|republic|empire|rebellion|mandalorian)\b.{0,200}\b(?:jedi|sith|death\s+star|the\s+way)\b"
        ),
        "star-wars",
    ),
    (
        _compile(
            r"\bman\s+of\s+steel\b|\bkrypton(?:ite)?\b|\bfortress\s+of\s+solitude\b"
            r"|\bup,?\s+up,?\s+and\s+away\b|\bsuperman\b.{0,80}\b(?:cape|hero|krypton|steel|villain)\b"
        ),
        "superman",
    ),
    (
        _compile(
            r"\btop\s+gun\b|\bthe\s+need\s+for\s+speed\b|\bi\s+feel\s+the\s+need\b"
            r"|\bdanger\s+zone\b.{0,80}\b(?:maverick|jet|fly|top\s+gun)\b"
        ),
        "top-gun",
    ),
    (
        _compile(
            r"\bwinter\s+is\s+coming\b|\bgame\s+of\s+thrones\b|\bthe\s+iron\s+throne\b"
            r"|\byou\s+know\s+nothing,?\s+jon\s+snow\b"
        ),
        "game-of-thrones",
    ),
    (
        _compile(
            r"\bone\s+ring\s+to\s+rule\s+them\s+all\b|\byou\s+shall\s+not\s+pass\b"
            r"|\bmy\s+precious\b|\b(?:mordor|gandalf|frodo|the\s+shire)\b"
        ),
        "lord-of-the-rings",
    ),
    (
        _compile(
            r"\bhasta\s+la\s+vista,?\s+baby\b|\bskynet\b|\bthe\s+terminator\b"
            r"|\bi'?ll\s+be\s+back\b.{0,80}\b(?:terminator|machine|robot|cyborg)\b"
        ),
        "terminator",
    ),
    (
        _compile(r"\beye\s+of\s+the\s+tiger\b|\brocky\s+balboa\b|\bgonna\s+fly\s+now\b"),
        "rocky",
    ),
    (
        _compile(
            r"\bshaken,?\s+not\s+stirred\b|\blicen[sc]e\s+to\s+kill\b|\bagent\s+007\b|\bjames\s+bond\b"
        ),
        "james-bond",
    ),
    (
        _compile(
            r"\bavengers\s+assemble\b|\bwakanda\s+forever\b|\binfinity\s+(?:gauntlet|stones?)\b"
            r"|\bi\s+am\s+iron\s+man\b|\bthanos\b"
        ),
        "marvel",
    ),
    (
        _compile(r"\bwho\s+(?:you|ya)\s+gonna\s+call\b|\bghostbusters\b"),
        "ghostbusters",
    ),
    (
        _compile(r"\boffer\s+(?:he|you|they)\s+can'?t\s+refuse\b|\bthe\s+godfather\b"),
        "godfather",
    ),
    (
        _compile(r"\bpawn\s+stars\b"),
        "pawn-stars",
    ),
)
# Emit genre:parody ONLY when a franchise match fires; it never fires alone.
# A broader multi-cue Star Wars check: text must contain "this is the way"
# AND at least one other signature Star Wars cue.
PATTERN_PARODY_STAR_WARS_MULTI = _compile(
    r"\bthis\s+is\s+the\s+way\b"
)
PATTERN_PARODY_STAR_WARS_EXTRA_CUE = _compile(
    r"\b(?:may\s+the\s+(?:4th|fourth)\s+be\s+with\s+you|the\s+force|jedi|sith|death\s+star|"
    r"a\s+galaxy|galaxy\s+(?:far|that)|lightsaber|darth|yoda|skywalker|mandalorian)\b"
)

# --- artist:<name> -------------------------------------------------------
# Named cultural figures / musicians and their signature songs. Identifying an
# artist from audio is impossible (the track can't be heard), so these fire only
# when the artist or a signature song is NAMED in tweet text, OCR, or a
# transcript. Coverage therefore grows as the OCR and transcript layers fill in
# — right now artist names are almost entirely in the (un-OCR'd) images / audio,
# not the tweet bodies.
ARTIST_GAZETTEER: tuple[tuple[re.Pattern[str], str], ...] = (
    (_compile(r"\bsydney\s+sweeney\b"), "sydney-sweeney"),
    (
        _compile(
            r"\blee\s+greenwood\b|\bgod\s+bless\s+the\s+u\.?s\.?a\.?\b"
            r"|\bproud\s+to\s+be\s+an\s+american\b"
        ),
        "lee-greenwood",
    ),
    (_compile(r"\bkid\s+rock\b"), "kid-rock"),
    (_compile(r"\bvillage\s+people\b|\bmacho\s+man\b"), "village-people"),
    (_compile(r"\bjason\s+aldean\b|\btry\s+that\s+in\s+a\s+small\s+town\b"), "jason-aldean"),
    (_compile(r"\boliver\s+anthony\b|\brich\s+men\s+north\s+of\s+richmond\b"), "oliver-anthony"),
    (_compile(r"\btaylor\s+swift\b"), "taylor-swift"),
    (_compile(r"\bbeyonc[eé]\b"), "beyonce"),
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
# Explicit music-video phrasing ONLY. Generic / metaphorical music wording
# ("soundtrack", "background music", "musical score", "beat drops", "anthem")
# is intentionally excluded: it over-tagged speeches and incidental mentions
# as music videos. "set to music" / "set to the song" stays as it is strong,
# explicit music-video evidence.
PATTERN_VIDEO_MUSIC_VIDEO = _compile(
    r"\b(?:official\s+)?music\s+video\b"
    r"|\bset\s+to\s+(?:music|the\s+song|the\s+track)\b"
    r"|\bofficial\s+(?:video|audio)\s+for\b"
    r"|\b(?:lyric|lyrics)\s+video\b"
)
PATTERN_PRODUCED_VIDEO_STYLE = _compile(
    r"\b(?:polished|produced|edited|cinematic|trailer[- ]style|multi[- ]shot|rapid[- ]cut|"
    r"b-roll|title[- ]card|end[- ]card|color[- ]graded|montage|screencast|"
    r"music\s+bed|soundtrack|voice[- ]?over|narrat(?:ion|ed|or))\b"
)
PATTERN_AUDIO_MUSIC_CONTEXT = _compile(
    r"\bwhat(?:'s| is)\s+(?:the\s+)?song\b"
    r"|\b(?:name|title)\s+of\s+(?:the\s+)?song\b"
    r"|\bsong\s+(?:name|title|id|identifier)\b"
    r"|\b(?:this|that|the)\s+song\b"
    r"|\bnational\s+anthem\b"
    r"|\banthem\s+(?:plays?|performance)\b"
    r"|\bbackground\s+music\b"
    r"|\bsoundtrack\b"
    r"|\b(?:music|beat|track)\s+(?:goes\s+hard|slaps|is\s+fire)\b"
    r"|\bset\s+to\s+(?:music|the\s+song|the\s+track)\b"
    r"|\bmusic\s+video\b"
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
PATTERN_VIDEO_RECRUITMENT = _compile(
    r"\bjoin\.ice\.gov\b"
    r"|\b(?:ice|cbp|hsi|dhs)\.gov/(?:careers?|join|jobs?|recruit(?:ment|ing)?|homeland-security-careers)\b"
    r"|\b(?:careers?|jobs?)\.(?:ice|cbp|hsi|dhs)\.gov\b"
    r"|\b(?:dhs|ice|cbp|hsi)\s+(?:is\s+)?(?:hiring|recruiting)\b"
    r"|\b(?:recruitment|hiring)\s+(?:ad|spot|video|campaign)\b"
    r"|\bjoin\s+(?:ice|cbp|hsi|dhs|the\s+(?:ice|cbp|hsi|dhs)\s+(?:team|family))\b"
    r"|\bapply\s+(?:today|now)\b"
    r"|\bcareer(?:s)?\s+(?:with|at)\s+(?:ice|cbp|hsi|dhs)\b"
    r"|\banswer\s+the\s+call\b"
    r"|\bserve\s+(?:your|our|their|his|her)\s+(?:country|nation|countrymen|community)\b"
)
PATTERN_VIDEO_AD = _compile(
    r"\b(?:new\s+(?:ad|advert|spot|commercial))\b"
    r"|\b(?:ad|advertisement|commercial|promo|promotional\s+video|campaign\s+spot)\b"
    r"|\b(?:campaign|recruitment)\s+(?:ad|spot|video|campaign)\b"
    r"|\bjoin\s+(?:ice|cbp|hsi|the\s+(?:ice|cbp)\s+(?:team|family))\b"
    r"|\bjoin\.ice\.gov\b"
    r"|\bapply\s+(?:today|now)\b"
    r"|\bpromises?\s+made[,.]?\s+promises?\s+kept\b"
    r"|\bmost\s+secure\s+border\s+in\s+american\s+history\b"
)
PRODUCED_VIDEO_GENRE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("genre:music-video", PATTERN_VIDEO_MUSIC_VIDEO),
    ("genre:psa", PATTERN_VIDEO_PSA),
    ("genre:recruitment", PATTERN_VIDEO_RECRUITMENT),
    ("genre:advertisement", PATTERN_VIDEO_AD),
    (
        "genre:war-movie",
        _compile(
            r"\bwar[- ]movie\b|\bwar[- ]film\b|\baction[- ]movie\b"
            r"|\b(?:cinematic|movie[- ]trailer|trailer[- ]style|dramatic)\b.{0,80}\b(?:war|battle|combat|military|raid|operation)\b"
            r"|\b(?:war|battle|combat|military|raid|operation)\b.{0,80}\b(?:cinematic|movie[- ]trailer|trailer[- ]style|dramatic)\b"
        ),
    ),
    (
        "genre:utopian",
        _compile(
            r"\butopian\b|\bidealized\b|\baspirational\b|\b(?:golden\s+age|bright\s+future|morning\s+in\s+america)\b"
            r"|\b(?:sunlit|heroic|triumphal)\b.{0,80}\b(?:montage|vision|future|aesthetic)\b"
        ),
    ),
    (
        "genre:dystopian",
        _compile(
            r"\bdystopian\b|\bsci[- ]?fi\b|\bscience[- ]fiction\b|\bcyberpunk\b"
            r"|\b(?:dark|bleak|apocalyptic|hellscape|surveillance[- ]state|futuristic)\b.{0,80}\b(?:future|city|vision|scene|aesthetic)\b"
        ),
    ),
)
PRODUCED_VIDEO_STRUCTURE_TAGS = {
    "video:produced",
    "genre:music-video",
    "video:montage",
    "video:text-overlay",
    "video:voiceover",
}
# Explicit textual AI-generation signals (body / OCR / transcript). Kept
# high-precision to avoid firing on bare "AI" mentions; emitted tentative
# because text can describe/accuse rather than declare provenance (the tag is
# only firm with C2PA/watermark evidence, which this layer doesn't have).
PATTERN_MEDIA_AI_GENERATED = _compile(
    r"\bAI[- ]generated\b"
    r"|\b(?:generated|made|created|produced)\s+(?:by|with|using)\s+AI\b"
    r"|\bAI[- ](?:image|video|art|clip|render|footage|animation|slop)\b"
    r"|\bdeepfakes?\b"
    r"|\bsynthetic\s+(?:media|video|imagery|image|footage)\b"
    r"|\bMidjourney\b|\bDALL[- ]?E\b|\bStable\s+Diffusion\b"
    r"|\b(?:Sora|Veo)\s+(?:video|clip|generated|model|AI)\b"
    r"|#AI(?:generated|art|video|slop)\b"
)
SPEAKER_ACTION_CONTEXT = (
    r"(?:deliver(?:s|ed|ing)?|giv(?:e|es|ing)|gave|announce(?:s|d|ment)?|"
    r"say|says|said|speak(?:s|ing)?|spoke|remark(?:s|ed)?|brief(?:s|ed|ing)?|"
    r"join(?:s|ed|ing)?|interview(?:s|ed|ing)?|sat\s+down|quote(?:s|d)?)"
)
SPEAKER_NOUN_CONTEXT = (
    r"(?:remarks?|speech|address|statement|announcement|interview|quote|"
    r"press\s+(?:conference|briefing|gaggle)|briefing)"
)
SPEAKER_ALIASES: tuple[tuple[str, str], ...] = (
    (
        "First Lady Melania Trump",
        r"(?:First\s+Lady\s+)?Melania\s+Trump|FLOTUS",
    ),
    (
        "Secretary Mullin",
        r"Secretary\s+Mullin|Sec\.?\s+Mullin|@?SecMullinDHS",
    ),
    (
        "President Trump",
        r"President\s+(?:Donald\s+J\.?\s+)?Trump|Donald\s+Trump|@?POTUS|@?realDonaldTrump",
    ),
    (
        "Vice President Vance",
        r"Vice\s+President\s+(?:JD\s+|J\.D\.\s+)?Vance|VP\s+Vance|J\.?D\.?\s+Vance|@?VP",
    ),
    (
        "Tom Homan",
        r"Tom\s+Homan|Thomas\s+D\.?\s+Homan|@?RealTomHoman",
    ),
    (
        "Stephen Miller",
        r"Stephen\s+Miller|@?StephenM",
    ),
    (
        "Gregory Bovino",
        r"Gregory\s+Bovino|Gregory\s+K\.?\s+Bovino|@?GregoryKBovino",
    ),
)

PATTERN_STATUS_COPYRIGHT_REMOVAL = _compile(r"\b(copyright|dmca)\b")
PATTERN_SLOGAN_NICE = _compile(r"\b(NICE day|NICE morning|ICE is NICE|NICE city)\b")
PATTERN_SLOGAN_WORST = _compile(r"\bWORST OF THE WORST\b")
PATTERN_SLOGAN_REPORTRECON = _compile(r"\bReport\.\s*Recon\.\s*Raid\.")
PATTERN_SLOGAN_CRIMINAL_ILLEGAL_ALIEN = _compile(r"\bcriminal\s+illegal\s+aliens?\b")
PATTERN_SLOGAN_ILLEGAL_ALIEN = _compile(r"\billegal\s+aliens?\b")
PATTERN_SLOGAN_FREE_TICKET_HOME = _compile(
    r"\bfree\s+(?:ticket|flight|plane\s+ticket)\s+home\b"
    r"|\bcomplimentary\s+plane\s+ticket\s+home\b"
    r"|\bfree\s+flight\s+to\s+(?:your|their|his|her)\s+home\s+country\b"
)
PATTERN_SLOGAN_GO_HOME = _compile(
    r"\b(?:illegal\s+aliens?|aliens?|migrants?|CBP\s+Home|self[- ]deport|deport)\b"
    r"[\s\S]{0,120}\bgo\s+home\b"
    r"|\bgo\s+home\b[\s\S]{0,120}\b"
    r"(?:illegal\s+aliens?|aliens?|migrants?|CBP\s+Home|self[- ]deport|deport)\b"
)
PATTERN_SLOGAN_PROJECT_HOMECOMING = _compile(r"\bProject\s+Homecoming\b")
PATTERN_SLOGAN_MAGA = _compile(r"\bMAGA\b|\bMake\s+America\s+Great\s+Again\b")
PATTERN_SLOGAN_MAHA = _compile(r"\bMAHA\b|\bMake\s+America\s+Healthy\s+Again\b")
PATTERN_SLOGAN_MASA = _compile(r"\bMake\s+America\s+Safe\s+Again\b")
PATTERN_SLOGAN_AMERICA_FIRST = _compile(r"\bAmerica\s+First\b|\bAmericaFirst\b")
PATTERN_SLOGAN_GOLDEN_AGE = _compile(r"\bGolden\s+Age\b|\bWelcome\s+to\s+the\s+Golden\s+Age\b")
PATTERN_SLOGAN_SAVE_AMERICA = _compile(r"\bSave\s+America\b|\bSAVEAMERICA\b")
PATTERN_SLOGAN_LAW_AND_ORDER = _compile(r"\bLaw\s+and\s+Order\b|\bLaw\s*&\s*Order\b")
PATTERN_SLOGAN_PEACE_THROUGH_STRENGTH = _compile(r"\bPeace\s+Through\s+Strength\b")
PATTERN_SLOGAN_PROMISES_KEPT = _compile(
    r"\bPromises?\s+Made[,;:]?\s+Promises?\s+Kept\b|\bPromises?\s+Kept\b"
)
PATTERN_SLOGAN_MASS_DEPORTATION = _compile(r"\bMass\s+Deportations?\b")
PATTERN_SLOGAN_MOST_SECURE_BORDER = _compile(r"\bmost\s+secure(?:d)?\s+border\b")
PATTERN_SLOGAN_CATCH_RELEASE = _compile(r"\bcatch(?:-and-|\s+and\s+)release\b")
PATTERN_PHRASE_MIGRANT = _compile(r"\bmigrants?\b")
PATTERN_PHRASE_IMMIGRANT = _compile(r"\bimmigrants?\b")
PATTERN_LEGAL_CRIMINAL_PROSECUTION = _compile(
    r"\bprosecut(?:e|ed|ing|ion|ions)\b"
    r"|\bindict(?:ed|ment|ments)?\b"
    r"|\bcharged\s+with\b"
    r"|\bcriminal\s+(?:complaint|charges?|case|prosecution)\b"
    r"|\barraign(?:ed|ment)?\b"
    r"|\bple(?:a|d|aded|ads)\s+(?:guilty|not\s+guilty)\b"
    r"|\bconvict(?:ed|ion|ions)?\b"
    r"|\bsentenc(?:ed|ing)\b"
    r"|\bfelony\s+charges?\b"
    r"|\bmisdemeanor\s+charges?\b"
)
PATTERN_LEGAL_CIVIL_LAWSUIT = _compile(
    r"\bcivil\s+(?:lawsuit|suit|action|case|complaint|litigation)\b"
    r"|\blawsuits?\b"
    r"|\bfiled\s+(?:a\s+)?(?:lawsuit|suit|civil\s+action|complaint)\b"
    r"|\bsu(?:e|ed|es|ing)\b"
    r"|\binjunction\b"
    r"|\btemporary\s+restraining\s+order\b"
    r"|\bTRO\b"
    r"|\bsettlement\s+agreement\b"
    r"|\bconsent\s+decree\b"
)
PATTERN_THEME_STATISTICS = _compile(
    r"\b\d[\d,]*\s+(?:arrest|removal|deportat|encounter|alien|illegal|criminal|gang|fentanyl)"
)
# Imperative-mood markers at the start of a sentence (lowercase or
# title-case). Used for `theme:directive`.
PATTERN_THEME_DIRECTIVE = _compile(
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

# Demonyms and major foreign cities -> country. The validators above need a
# preposition + the exact country name, so they miss "Iranian regime" (demonym)
# and "in Tehran" (city) — the most common way countries actually appear. These
# maps recover them; demonyms are distinctive enough to match without a
# preposition. Curated for precision: food/ambiguous demonyms ("French",
# "German", "Indian", "Korean") are intentionally omitted.
DEMONYM_TO_COUNTRY: dict[str, str] = {
    "iranian": "Iran", "mexican": "Mexico", "venezuelan": "Venezuela",
    "chinese": "China", "russian": "Russia", "cuban": "Cuba",
    "colombian": "Colombia", "salvadoran": "El Salvador", "salvadorian": "El Salvador",
    "honduran": "Honduras", "guatemalan": "Guatemala", "haitian": "Haiti",
    "somali": "Somalia", "somalian": "Somalia", "afghan": "Afghanistan",
    "syrian": "Syria", "nigerian": "Nigeria", "pakistani": "Pakistan",
    "ukrainian": "Ukraine", "israeli": "Israel", "iraqi": "Iraq",
    "egyptian": "Egypt", "yemeni": "Yemen", "brazilian": "Brazil",
    "nicaraguan": "Nicaragua", "ecuadorian": "Ecuador", "peruvian": "Peru",
    "jamaican": "Jamaica", "filipino": "Philippines", "vietnamese": "Vietnam",
    "lebanese": "Lebanon", "libyan": "Libya", "sudanese": "Sudan",
    "ethiopian": "Ethiopia", "kenyan": "Kenya", "moroccan": "Morocco",
    "algerian": "Algeria", "bangladeshi": "Bangladesh", "cambodian": "Cambodia",
    "indonesian": "Indonesia", "dominican": "Dominican Republic", "panamanian": "Panama",
    "bolivian": "Bolivia", "chilean": "Chile", "argentine": "Argentina",
    "argentinian": "Argentina", "saudi": "Saudi Arabia", "turkish": "Turkey",
}
FOREIGN_CITY_TO_COUNTRY: dict[str, str] = {
    "tehran": "Iran", "caracas": "Venezuela", "beijing": "China",
    "moscow": "Russia", "havana": "Cuba", "kabul": "Afghanistan",
    "damascus": "Syria", "baghdad": "Iraq", "tripoli": "Libya",
    "mogadishu": "Somalia", "bogota": "Colombia", "managua": "Nicaragua",
    "tegucigalpa": "Honduras", "kyiv": "Ukraine",
}


def _alternation_pattern(keys: list[str]) -> re.Pattern[str]:
    alts = sorted((re.escape(k) for k in keys), key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(alts) + r")\b", re.IGNORECASE)


PATTERN_DEMONYM = _alternation_pattern(list(DEMONYM_TO_COUNTRY))
PATTERN_FOREIGN_CITY = _alternation_pattern(list(FOREIGN_CITY_TO_COUNTRY))
# A demonym that immediately precedes one of these nouns describes a person's
# nationality, so it also earns origin:<country>, not just a contextual country:.
PATTERN_DEMONYM_PERSON = _compile(
    r"^\W*(?:national|nationals|citizen|citizens|immigrant|immigrants|migrant|migrants|"
    r"refugee|refugees|nationals?|man|woman|men|women|descent|origin|national)\b"
)

MEDIA_TAG_PREFIXES_ALLOWED_IN_LEXICAL: tuple[str, ...] = (
    "action:",
    "agency:",
    "audio:",
    "country:",
    "crime:",
    "event:",
    "format:",
    "genre:",
    "legal:",
    "media:",
    "media-status:",
    "military:",
    "parody:",
    "phrase:",
    "policy:",
    "religion:",
    "slogan:",
    "speaker:",
    "state:",
    "status:",
    "subject:",
    "theme:",
    "topic:",
    "video:",
)
LEGACY_MEDIA_TAG_ALIASES: dict[str, tuple[str, ...]] = {
    "media:produced-video": ("video:produced",),
    "shape:lineup": ("genre:lineup",),
    "video:ad": ("genre:advertisement",),
    "video:music-video": ("genre:music-video",),
    "video:psa": ("genre:psa",),
    "branch:army": ("military:army",),
    "branch:navy": ("military:navy",),
    "branch:air-force": ("military:air-force",),
    "branch:space-force": ("military:space-force",),
    "branch:marines": ("military:marines",),
    "branch:coast-guard": ("military:coast-guard",),
    "branch:national-guard": ("military:national-guard",),
    # Namespace-migration aliases (media:* production attrs -> video:/genre:)
    "media:montage": ("video:montage",),
    "media:text-overlay": ("video:text-overlay",),
    "media:voiceover": ("video:voiceover",),
    "media:music-video": ("genre:music-video",),
    "media:short-video": ("video:short",),
    # Namespace-migration aliases (media:* status flags -> media-status:*)
    "media:archived": ("media-status:archived",),
    "media:described": ("media-status:described",),
    "media:has-alt-text": ("media-status:has-alt-text",),
    "media:needs-vision": ("media-status:needs-vision",),
    "media:needs-ocr": ("media-status:needs-ocr",),
    "media:graphic-content": ("media-status:graphic-content",),
    # Namespace-migration aliases (theme:*/frame: -> policy:/format:/theme:)
    "theme:border": ("policy:border",),
    "theme:sanctuary-cities": ("policy:sanctuary-cities",),
    "theme:worksite-enforcement": ("policy:worksite-enforcement",),
    "theme:cbp-home": ("policy:cbp-home",),
    "theme:statistics": ("format:statistics",),
    "theme:directive": ("format:directive",),
    "frame:criminal": ("theme:criminal",),
}


# ---------------------------------------------------------------------------
# Per-tweet tagger.
# ---------------------------------------------------------------------------


def normalized_media_tags(media_tags: list[Any] | None) -> list[dict[str, Any]]:
    """Return vetted tag entries imported from media-recognition sidecars."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not isinstance(media_tags, list):
        return out
    for entry in media_tags:
        if isinstance(entry, str):
            tag = entry.strip()
            source = "media-description"
            tentative = False
        elif isinstance(entry, dict):
            tag = str(entry.get("tag") or "").strip()
            source = str(entry.get("source") or "media-description")
            tentative = bool(entry.get("tentative"))
        else:
            continue
        if not tag or tag in seen:
            continue
        aliased_tags = LEGACY_MEDIA_TAG_ALIASES.get(tag, (tag,))
        for aliased_tag in aliased_tags:
            if aliased_tag in seen:
                continue
            if not any(
                aliased_tag.startswith(prefix) for prefix in MEDIA_TAG_PREFIXES_ALLOWED_IN_LEXICAL
            ):
                continue
            seen.add(aliased_tag)
            out.append({"tag": aliased_tag, "source": source, "tentative": tentative})
    return out


def tag_text(
    text: str,
    *,
    tweet_type: str | None,
    mentions: list[str] | None,
    media_count: int,
    account_category: str,
    ocr_text: str = "",
    transcript_text: str = "",
    media_text: str = "",
    media_tags: list[Any] | None = None,
    reply_context_text: str = "",
    is_unavailable: bool = False,
    unavailable_text: str = "",
    community_note: dict[str, Any] | None = None,
    video_count: int = 0,
    video_max_duration_sec: float | None = None,
    possibly_sensitive: bool = False,
    needs_ocr: bool = False,
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

    Spans on emitted tags index into the **combined** regex buffer, so
    they're stable per-tweet but not directly comparable across the
    original text/OCR/media-description boundaries."""
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()  # dedupe identical (tag, span) pairs

    def add(
        tag: str,
        *,
        span: tuple[int, int] | None = None,
        tentative: bool = False,
        source: str = "auto",
    ) -> None:
        key = f"{tag}@{span[0] if span else ''}"
        if key in seen:
            return
        seen.add(key)
        entries.append(
            {
                "tag": tag,
                "tentative": True if tentative else None,
                "source": source,
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

    # X's own media/content warning. This is not an interpretation of the
    # image; it records that the platform flagged the tweet as sensitive.
    if possibly_sensitive:
        add("media-status:graphic-content", source="platform")

    # agency:<HANDLE> — derived from mentions[]
    for mention in mentions or []:
        mention_key = str(mention).lstrip("@").lower()
        canonical = AGENCY_MENTION_ALIASES.get(mention_key)
        if canonical:
            add(f"agency:{canonical}")
        military = MILITARY_MENTION_ALIASES.get(mention_key)
        if military:
            add(f"military:{military}")

    if is_unavailable:
        add("status:unavailable")
        if PATTERN_STATUS_COPYRIGHT_REMOVAL.search(unavailable_text):
            add("status:copyright-removal")
    if community_note:
        add("status:community-note")

    for media_tag in normalized_media_tags(media_tags):
        add(
            media_tag["tag"],
            tentative=bool(media_tag.get("tentative")),
            source=str(media_tag.get("source") or "media-description"),
        )

    # media:needs-ocr is orthogonal to media:needs-vision. needs-ocr means an
    # attached image's text has not been extracted yet (resolved by the OCR
    # layer); needs-vision means the frame/scene itself has not been analyzed
    # (resolved only by an actual visual description, never by OCR).
    if needs_ocr:
        add("media-status:needs-ocr", tentative=True, source="media-metadata")

    # Concatenate OCR and media-description text so a poster's stamped
    # slogan or manually reviewed image description earns the same tags
    # as if the words had been typed into the tweet body.
    body_parts = [part for part in (text, ocr_text, transcript_text, media_text) if part]
    body = " || ".join(body_parts)

    if not body:
        # Even with no text we still get format: + agency: + sticky
        # default. Skip the regex pass.
        _ensure_intrinsic_parent_topics(entries, "", add)
        _maybe_immigration_default(entries, account_category, "", add)
        return entries
    text = body

    # Single-shot regex tags
    for pat, tag in (
        (PATTERN_FRAME_CRIMINAL, "theme:criminal"),
        (PATTERN_ACTION_DETENTION, "action:detention"),
        (PATTERN_ACTION_SELF_DEPORTATION, "action:self-deportation"),
        (PATTERN_ACTION_DEPORTATION, "action:deportation"),
        (PATTERN_ACTION_REPORT_TO_ICE, "action:report-immigrants"),
        (PATTERN_SUBJECT_CBP_HOME_APP, "subject:cbp-home-app"),
        (PATTERN_SUBJECT_CELEBRITY, "subject:celebrity"),
        (PATTERN_LEGAL_CRIMINAL_PROSECUTION, "legal:criminal-prosecution"),
        (PATTERN_LEGAL_CIVIL_LAWSUIT, "legal:civil-lawsuit"),
        (PATTERN_TOPIC_ECONOMY, "topic:economy"),
        (PATTERN_TOPIC_MILITARY, "topic:military"),
        (PATTERN_TOPIC_LAUDATORY, "topic:laudatory"),
        (PATTERN_EVENT_PALESTINE, "event:palestine"),
        (PATTERN_THEME_BORDER, "policy:border"),
        (PATTERN_THEME_SANCTUARY, "policy:sanctuary-cities"),
        (PATTERN_THEME_WORKSITE, "policy:worksite-enforcement"),
        (PATTERN_THEME_HOMELAND, "theme:homeland"),
        (PATTERN_THEME_NATIVISM, "theme:nativism"),
        (PATTERN_THEME_CHRISTIANITY, "religion:christianity"),
        (PATTERN_THEME_TRANSGENDER, "theme:transgender"),
        (PATTERN_THEME_CIVIL_DISTURBANCE, "theme:civil-disturbance"),
        (PATTERN_THEME_CBP_HOME, "policy:cbp-home"),
        (PATTERN_THEME_POP_CULTURE_REFERENCE, "theme:pop-culture-reference"),
        (PATTERN_LEGAL_BIRTHRIGHT_CITIZENSHIP, "legal:birthright-citizenship"),
        (PATTERN_THEME_NATIVISM_BIRTHRIGHT, "theme:nativism"),
        (PATTERN_SLOGAN_NICE, "slogan:nice"),
        (PATTERN_SLOGAN_WORST, "slogan:worst"),
        (PATTERN_SLOGAN_REPORTRECON, "slogan:reportrecon"),
        (PATTERN_SLOGAN_CRIMINAL_ILLEGAL_ALIEN, "slogan:criminal-illegal-alien"),
        (PATTERN_SLOGAN_ILLEGAL_ALIEN, "slogan:illegal-alien"),
        (PATTERN_SLOGAN_FREE_TICKET_HOME, "slogan:free-ticket-home"),
        (PATTERN_SLOGAN_GO_HOME, "slogan:go-home"),
        (PATTERN_SLOGAN_PROJECT_HOMECOMING, "slogan:project-homecoming"),
        (PATTERN_SLOGAN_MAGA, "slogan:maga"),
        (PATTERN_SLOGAN_MAHA, "slogan:maha"),
        (PATTERN_SLOGAN_MASA, "slogan:masa"),
        (PATTERN_SLOGAN_AMERICA_FIRST, "slogan:america-first"),
        (PATTERN_SLOGAN_GOLDEN_AGE, "slogan:golden-age"),
        (PATTERN_SLOGAN_SAVE_AMERICA, "slogan:save-america"),
        (PATTERN_SLOGAN_LAW_AND_ORDER, "slogan:law-and-order"),
        (PATTERN_SLOGAN_PEACE_THROUGH_STRENGTH, "slogan:peace-through-strength"),
        (PATTERN_SLOGAN_PROMISES_KEPT, "slogan:promises-kept"),
        (PATTERN_SLOGAN_MASS_DEPORTATION, "slogan:mass-deportation"),
        (PATTERN_SLOGAN_MOST_SECURE_BORDER, "slogan:most-secure-border"),
        (PATTERN_SLOGAN_CATCH_RELEASE, "slogan:catch-release"),
        (PATTERN_SLOGAN_FIND_AND_KILL, "slogan:find-and-kill"),
        (PATTERN_SLOGAN_IMPORT_THIRD_WORLD, "slogan:import-third-world"),
        (PATTERN_PHRASE_MIGRANT, "phrase:migrant"),
        (PATTERN_PHRASE_IMMIGRANT, "phrase:immigrant"),
        (PATTERN_THEME_STATISTICS, "format:statistics"),
        (PATTERN_THEME_DIRECTIVE, "format:directive"),
        (PATTERN_ANGEL_FAMILY, "subject:angel-family"),
        (PATTERN_NATIVE_BORN_CITIZEN, "subject:native-born-citizen"),
    ):
        m = pat.search(text)
        if m:
            add(tag, span=m.span())

    for pat, tag in AGENCY_TEXT_PATTERNS:
        for m in pat.finditer(text):
            add(tag, span=m.span())

    for tag, span in speaker_matches(text):
        add(tag, span=span)

    for slug, pat in DISTURBANCE_CITY_PATTERNS:
        for m in pat.finditer(text):
            add(f"event:{slug}", span=m.span())
            add("theme:civil-disturbance", span=m.span())

    if m := _coded_nativism_match(text, entries):
        add("theme:nativism", span=m.span())

    if m := _theme_religion_match(text):
        add("theme:religion", span=m.span())

    # Speech / press-conference clips are oratory, not music videos, even if
    # the text mentions incidental music. Suppress music-video when a speech
    # indicator is present.
    speech_indicator_present = PATTERN_VIDEO_SPEECH.search(text) is not None
    for tag, pat in PRODUCED_VIDEO_GENRE_PATTERNS:
        if tag == "genre:music-video" and video_count <= 0:
            continue
        if tag == "genre:music-video" and speech_indicator_present:
            continue
        if m := pat.search(text):
            add(tag, span=m.span())

    if m := PATTERN_MEDIA_AI_GENERATED.search(text):
        add("media:ai-generated", span=m.span(), tentative=True)

    # military:<BRANCH> subtopics. These are narrower than topic:military.
    for slug, pat in MILITARY_VOCAB:
        for m in pat.finditer(text):
            add(f"military:{slug}", span=m.span())

    if _general_topic_score(text) >= 3:
        add("topic:general")

    # crime:<TYPE> — every distinct match emits one tag entry.
    for slug, pat_str in CRIME_VOCAB:
        for m in re.finditer(pat_str, text, re.I):
            add(f"crime:{slug}", span=m.span())
            for parent in CRIME_PARENT_TAGS.get(f"crime:{slug}", ()):
                add(parent, span=m.span())
    for slug, pat_str in CRIME_SUBTYPE_VOCAB:
        for m in re.finditer(pat_str, text, re.I):
            tag = f"crime:{slug}"
            add(tag, span=m.span())
            for parent in CRIME_PARENT_TAGS.get(tag, ()):
                add(parent, span=m.span())

    if m := _martyrdom_match(text, account_category, entries):
        add("theme:martyrdom", span=m.span())

    # genre:parody + parody:<franchise> — high-precision franchise detection.
    for franchise_pat, franchise_slug in PARODY_FRANCHISE_GAZETTEER:
        if m := franchise_pat.search(text):
            add("genre:parody", span=m.span())
            add(f"parody:{franchise_slug}", span=m.span())
            add("theme:pop-culture-reference")
    # Also catch "this is the way" + one more Star Wars cue (e.g. "a galaxy").
    if (
        PATTERN_PARODY_STAR_WARS_MULTI.search(text)
        and PATTERN_PARODY_STAR_WARS_EXTRA_CUE.search(text)
        and not any(e["tag"] == "parody:star-wars" for e in entries)
    ):
        m_way = PATTERN_PARODY_STAR_WARS_MULTI.search(text)
        if m_way:
            add("genre:parody", span=m_way.span())
            add("parody:star-wars", span=m_way.span())
            add("theme:pop-culture-reference")

    # artist:<name> — named cultural figures / musicians (and signature songs).
    for artist_pat, artist_slug in ARTIST_GAZETTEER:
        if m := artist_pat.search(text):
            add(f"artist:{artist_slug}", span=m.span())

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

    # Demonyms ("Iranian regime") and major foreign cities ("in Tehran") ->
    # country, which the bare-name validators above can't see. A demonym
    # followed by a person noun also earns origin:.
    for m in PATTERN_DEMONYM.finditer(text):
        country = DEMONYM_TO_COUNTRY.get(m.group(1).lower())
        if country and country.lower() in COUNTRY_LOWER:
            add(f"country:{_normalize_country(country)}", span=m.span(1))
            if PATTERN_DEMONYM_PERSON.search(text[m.end() : m.end() + 24]):
                add(f"origin:{_normalize_country(country)}", span=m.span(1))
    for m in PATTERN_FOREIGN_CITY.finditer(text):
        country = FOREIGN_CITY_TO_COUNTRY.get(m.group(1).lower())
        if country and country.lower() in COUNTRY_LOWER:
            add(f"country:{_normalize_country(country)}", span=m.span(1))

    # state:<NAME> — the "<City>, <State>" pattern, validated.
    for m in PATTERN_STATE_CANDIDATE.finditer(text):
        candidate = (m.group(1) or "").strip()
        if candidate.lower() in STATE_LOWER:
            add(f"state:{_normalize_state(candidate)}", span=m.span(1))

    # genre:lineup: composite replies that hit theme:criminal with one photo.
    if (
        tweet_type == "reply"
        and any(e["tag"] == "theme:criminal" for e in entries)
        and media_count == 1
    ):
        add("genre:lineup")

    # subject:enforcement-op heuristic from the deterministic rules above:
    # if both action:detention and theme:criminal fire, that's strong enough
    # to set this without tentative.
    if any(e["tag"] == "action:detention" for e in entries) and any(
        e["tag"] == "theme:criminal" for e in entries
    ):
        add("subject:enforcement-op")

    # video:<kind> + video:duration-bucket — only when a video is attached.
    # Apply all matching source/kind tags; produced forms live under genre:*.
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
            (PATTERN_VIDEO_NEWS_CLIP, "video:news-clip"),
            (PATTERN_VIDEO_SPEECH, "video:speech"),
        ):
            m = pat.search(text)
            if m:
                add(tag, span=m.span())
        if PATTERN_AUDIO_MUSIC_CONTEXT.search(text):
            add("audio:music-likely")
        if PATTERN_AUDIO_MUSIC_CONTEXT.search(reply_context_text):
            add("audio:music-likely", source="reply-context")
        if any(e["tag"] in {"genre:music-video"} for e in entries):
            add("audio:music-likely")
        if m := PATTERN_PRODUCED_VIDEO_STYLE.search(text):
            add("video:produced", span=m.span())
        if video_max_duration_sec is not None and video_max_duration_sec > 0:
            if video_max_duration_sec <= 30:
                add("video:short")
            elif video_max_duration_sec <= 120:
                add("video:medium")
            else:
                add("video:long")
        # Derive genre:music-video ONLY from an explicit genre:music-video
        # signal (set by the conservative describe_media / manual-review rules) —
        # NEVER from audio:music-likely, which is an incidental acoustic heuristic.
        # Also suppress it on speech / press-conference clips.
        if not speech_indicator_present and any(
            e["tag"] == "genre:music-video" for e in entries
        ):
            add("genre:music-video")
        if any(
            e["tag"] in PRODUCED_VIDEO_STRUCTURE_TAGS or str(e["tag"]).startswith("genre:")
            for e in entries
        ):
            add("video:produced")

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


def _coded_nativism_match(text: str, entries: list[dict[str, Any]]) -> re.Match[str] | None:
    """Match coded inheritance/inheritor language only with nearby context.

    Standalone "inheritance" can be about taxes or probate. In the corpus,
    though, inheritance/inheritor language next to nation, citizenship,
    Homeland, birthright, American workers, or immigration terms is a
    nativism signal.
    """
    existing = {str(e["tag"]) for e in entries}
    strong_tag_context = any(
        tag in {"subject:native-born-citizen", "theme:homeland", "theme:nativism"}
        or tag.startswith(("action:", "origin:"))
        or tag
        in {
            "agency:ICEgov",
            "agency:CBP",
            "agency:DHSgov",
            "slogan:criminal-illegal-alien",
            "slogan:illegal-alien",
            "topic:immigration",
        }
        for tag in existing
    )
    for match in PATTERN_NATIVISM_INHERITANCE.finditer(text):
        start, end = match.span()
        window = text[max(0, start - 160) : min(len(text), end + 160)]
        if strong_tag_context or PATTERN_NATIVISM_INHERITANCE_CONTEXT.search(window):
            return match
    return None


def _martyrdom_match(
    text: str, account_category: str, entries: list[dict[str, Any]]
) -> re.Match[str] | None:
    """Match victim-commemoration frames used as a moral justification.

    "This is our why" is highly distinctive in the tracked core-account
    corpus, but noisy in public replies. We therefore accept that phrase
    directly for tracked core/government/official accounts and require
    nearby victim/memorial context elsewhere.
    """
    if m := PATTERN_MARTYRDOM_CONTEXT.search(text):
        return m

    why = PATTERN_MARTYRDOM_WHY.search(text)
    if not why:
        return None
    existing = {str(e["tag"]) for e in entries}
    if account_category in {"core", "government", "officials"}:
        return why
    if existing.intersection({"subject:angel-family", "subject:crime-victim", "crime:homicide"}):
        return why
    start, end = why.span()
    window = text[max(0, start - 220) : min(len(text), end + 220)]
    if PATTERN_MARTYRDOM_CONTEXT.search(window):
        return why
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
    "agency:DeptofWar": ("topic:military",),
    "agency:CENTCOM": ("topic:military",),
    "agency:Southcom": ("topic:military",),
    "agency:USCG": ("topic:military",),
    "agency:USArmyNorth": ("topic:military",),
    "agency:USNationalGuard": ("topic:military",),
    "agency:USNorthernCmd": ("topic:military",),
    "theme:criminal": ("topic:immigration",),
    "genre:lineup": ("topic:immigration",),
    "subject:angel-family": ("theme:martyrdom", "topic:immigration"),
    "subject:crime-victim": ("theme:martyrdom", "topic:immigration"),
    "subject:cbp-home-app": ("topic:immigration",),
    "subject:enforcement-op": ("topic:immigration",),
    "policy:border": ("topic:immigration",),
    "policy:cbp-home": ("topic:immigration",),
    "policy:sanctuary-cities": ("topic:immigration",),
    "policy:worksite-enforcement": ("topic:economy", "topic:immigration"),
    "theme:nativism": ("topic:immigration",),
    "theme:pop-culture-reference": ("topic:immigration",),
    "religion:christianity": ("theme:religion",),
    "slogan:criminal-illegal-alien": ("topic:immigration",),
    "slogan:free-ticket-home": ("topic:immigration",),
    "slogan:go-home": ("topic:immigration",),
    "slogan:illegal-alien": ("topic:immigration",),
    "slogan:mass-deportation": ("topic:immigration",),
    "slogan:masa": ("topic:immigration",),
    "slogan:find-and-kill": ("topic:immigration",),
    "slogan:import-third-world": ("topic:immigration",),
    "legal:birthright-citizenship": ("topic:immigration",),
    "genre:parody": ("theme:pop-culture-reference",),
    "slogan:most-secure-border": ("topic:immigration", "policy:border"),
    "slogan:catch-release": ("topic:immigration",),
    "slogan:project-homecoming": ("topic:immigration",),
    "slogan:maga": ("topic:general",),
    "slogan:maha": ("topic:general",),
    "slogan:america-first": ("topic:general",),
    "slogan:golden-age": ("topic:general",),
    "slogan:save-america": ("topic:general",),
    "slogan:law-and-order": ("topic:general",),
    "slogan:peace-through-strength": ("topic:general",),
    "slogan:promises-kept": ("topic:general",),
    "phrase:immigrant": ("topic:immigration",),
    "phrase:migrant": ("topic:immigration",),
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
        any(tag.startswith("military:") for tag in existing_tags)
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
        any(e["tag"] == "policy:worksite-enforcement" for e in entries)
        and "topic:economy" not in existing_tags
    ):
        add("topic:economy")


# Tag names whose presence on a tweet promotes `topic:immigration` from
# a tentative-by-account default to a confirmed-by-text classification.
# Anything that explicitly references the immigration domain (origin
# country pattern, deportation verb, border keyword, ICE/CBP/DHS handle,
# the criminal-alien frame, etc.) clears the bar.
IMMIGRATION_CONFIRMING_PREFIXES: tuple[str, ...] = (
    "action:",
    "origin:",
    "country:",
    "policy:border",
    "policy:sanctuary",
    "policy:worksite",
    "policy:cbp-home",
    "theme:nativism",
    "theme:criminal",
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
        "slogan:criminal-illegal-alien",
        "slogan:free-ticket-home",
        "slogan:go-home",
        "slogan:illegal-alien",
        "slogan:mass-deportation",
        "slogan:masa",
        "slogan:most-secure-border",
        "slogan:catch-release",
        "slogan:nice",
        "slogan:project-homecoming",
        "slogan:reportrecon",
        "slogan:worst",
        "phrase:immigrant",
        "phrase:migrant",
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
    if any(e["tag"] == "event:palestine" for e in entries):
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
    grouped = df.group_by("tweet_id").agg(pl.col("text").str.join(" | ").alias("text"))
    out: dict[str, str] = {}
    for row in grouped.iter_rows(named=True):
        tid = str(row.get("tweet_id") or "")
        if tid:
            out[tid] = str(row.get("text") or "")
    return out


def load_transcript_map() -> dict[str, str]:
    """Return {tweet_id: speech transcript} from the Layer-3c ASR sidecar.

    ``scripts.transcribe_audio`` writes recognized speech for archived videos.
    Folding it into the regex pass (like OCR) lets a spoken slogan or agency
    name in a video earn the same tags as if it were typed in the tweet body.
    Only successful, non-empty transcripts are used; multiple media per tweet
    are joined into one blob.
    """
    p = TAGS_DIR / "transcripts.parquet"
    if not p.exists():
        return {}
    df = pl.read_parquet(p)
    if df.is_empty() or "tweet_id" not in df.columns or "text" not in df.columns:
        return {}
    if "status" in df.columns:
        df = df.filter(pl.col("status") == "ok")
    df = df.filter(pl.col("text").is_not_null() & (pl.col("text").str.strip_chars() != ""))
    if df.is_empty():
        return {}
    grouped = df.group_by("tweet_id").agg(pl.col("text").str.join(" | ").alias("text"))
    out: dict[str, str] = {}
    for row in grouped.iter_rows(named=True):
        tid = str(row.get("tweet_id") or "")
        if tid:
            out[tid] = str(row.get("text") or "")
    return out


def load_media_context_map() -> dict[str, dict[str, Any]]:
    """Return per-tweet media descriptions and tags from media sidecars.

    ``scripts.describe_media`` is the cheap/local recognizer. Feeding those
    descriptions back through lexical rules lets image-only posts earn the
    same topic / slogan / agency tags as text posts.
    """
    out: dict[str, dict[str, Any]] = {}
    p = TAGS_DIR / "media_vision.parquet"
    if not p.exists():
        return {}
    df = pl.read_parquet(p)
    if df.is_empty() or "tweet_id" not in df.columns:
        return {}
    for row in df.iter_rows(named=True):
        tid = str(row.get("tweet_id") or "")
        if not tid:
            continue
        item = out.setdefault(tid, {"text_parts": [], "tags": []})
        for col in ("summary_text", "description"):
            value = str(row.get(col) or "").strip()
            if value:
                item["text_parts"].append(value)
        tags = row.get("tags")
        if isinstance(tags, list):
            item["tags"].extend(tags)
    for item in out.values():
        seen_parts: set[str] = set()
        parts: list[str] = []
        for part in item["text_parts"]:
            if part not in seen_parts:
                seen_parts.add(part)
                parts.append(part)
        item["text"] = " | ".join(parts)
        del item["text_parts"]
    return out


def speaker_matches(text: str) -> list[tuple[str, tuple[int, int]]]:
    """Return speaker tags only when a named official is tied to speech."""
    out: list[tuple[str, tuple[int, int]]] = []
    for canonical, alias in SPEAKER_ALIASES:
        patterns = (
            rf"(?<!\w)({alias})(?!\w).{{0,100}}\b(?:{SPEAKER_ACTION_CONTEXT}|{SPEAKER_NOUN_CONTEXT})\b",
            rf"\b(?:{SPEAKER_ACTION_CONTEXT}|{SPEAKER_NOUN_CONTEXT})\b"
            rf".{{0,80}}\b(?:by|from|with|of|featuring|:)?\s*({alias})(?!\w)",
        )
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                out.append((f"speaker:{canonical}", match.span(1)))
                break
    return out


def load_audio_context_map() -> dict[str, dict[str, Any]]:
    """Return per-tweet audio tags from the cheap audio sidecar."""
    p = TAGS_DIR / "audio_music.parquet"
    if not p.exists():
        return {}
    df = pl.read_parquet(p)
    if df.is_empty() or "tweet_id" not in df.columns:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in df.iter_rows(named=True):
        tid = str(row.get("tweet_id") or "")
        if not tid:
            continue
        item = out.setdefault(tid, {"tags": []})
        tags = row.get("tags")
        if isinstance(tags, list):
            item["tags"].extend(tags)
    return out


def load_reply_context_map(parquets: list[Path]) -> dict[str, str]:
    """Return direct-reply text keyed by replied-to tweet id.

    This is intentionally small and local. It lets deterministic media
    taggers use replies as weak context, e.g. "what song is this?" under
    a video, without having to inspect or stream the media in the viewer.
    """
    buckets: dict[str, list[str]] = {}
    for path in parquets:
        try:
            df = pl.read_parquet(path)
        except Exception:
            LOG.exception("reply context: could not read parquet", path=str(path))
            continue
        for row in df.iter_rows(named=True):
            if str(row.get("tweet_type") or "") != "reply":
                continue
            parent_id = str(row.get("reply_to_tweet_id") or "")
            if not parent_id:
                continue
            text = str(row.get("text_resolved") or row.get("text") or "").strip()
            if not text:
                continue
            bucket = buckets.setdefault(parent_id, [])
            if len(bucket) < 50:
                bucket.append(text[:280])
    return {tweet_id: " | ".join(parts)[:6000] for tweet_id, parts in buckets.items()}


def load_tag_overrides() -> dict[str, list[str]]:
    """Return manual/editor-confirmed tag overrides keyed by tweet id."""
    if not TAG_OVERRIDES_PATH.exists():
        return {}
    data = yaml.safe_load(TAG_OVERRIDES_PATH.read_text(encoding="utf-8")) or {}
    out: dict[str, list[str]] = {}
    rows = data.get("overrides") or []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        tweet_id = str(row.get("tweet_id") or "").strip()
        raw_tags = row.get("tags") or []
        if not tweet_id or not isinstance(raw_tags, list):
            continue
        tags = [str(tag or "").strip() for tag in raw_tags]
        tags = [tag for tag in tags if tag and ":" in tag]
        if tags:
            out[tweet_id] = tags
    return out


def discover_canonical_parquets() -> list[Path]:
    """Return per-account canonical parquets in `data/`, excluding any
    sidecars under `data/tags/`."""
    if not DATA_DIR.exists():
        return []
    return sorted(
        p
        for p in DATA_DIR.glob("*.parquet")
        if p.parent == DATA_DIR and p.name not in GENERATED_DATA_PARQUETS
    )


def tag_one_parquet(
    path: Path,
    account_categories: dict[str, str],
    tagged_at: str,
    ocr_map: dict[str, str] | None = None,
    transcript_map: dict[str, str] | None = None,
    media_context_map: dict[str, dict[str, Any]] | None = None,
    audio_context_map: dict[str, dict[str, Any]] | None = None,
    reply_context_map: dict[str, str] | None = None,
    tag_overrides: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Tag every row in one canonical parquet. Returns list of rows
    keyed for the lexical-tag schema.

    `ocr_map` is an optional `{tweet_id: ocr_text}` overlay sourced from
    `data/tags/image_ocr.parquet`. When the OCR layer hasn't run yet,
    pass `None` and the tagger silently runs against tweet text alone.
    """
    ocr_map = ocr_map or {}
    transcript_map = transcript_map or {}
    media_context_map = media_context_map or {}
    audio_context_map = audio_context_map or {}
    reply_context_map = reply_context_map or {}
    tag_overrides = tag_overrides or {}
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
        photo_count = 0
        video_max_duration_sec: float | None = None
        if isinstance(media, list):
            for m in media:
                if not isinstance(m, dict):
                    continue
                mt = m.get("media_type")
                if mt == "photo":
                    photo_count += 1
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
        media_context = media_context_map.get(tweet_id, {})
        audio_context = audio_context_map.get(tweet_id, {})
        media_tags: list[Any] = []
        if isinstance(media_context, dict) and isinstance(media_context.get("tags"), list):
            media_tags.extend(media_context["tags"])
        if isinstance(audio_context, dict) and isinstance(audio_context.get("tags"), list):
            media_tags.extend(audio_context["tags"])
        tags = tag_text(
            text,
            tweet_type=r.get("tweet_type"),
            mentions=[str(x) for x in mentions if x],
            media_count=media_count,
            account_category=category,
            ocr_text=ocr_map.get(tweet_id, ""),
            transcript_text=transcript_map.get(tweet_id, ""),
            needs_ocr=photo_count > 0 and not ocr_map.get(tweet_id, "").strip(),
            media_text=str(media_context.get("text") or ""),
            media_tags=media_tags,
            reply_context_text=reply_context_map.get(tweet_id, ""),
            is_unavailable=bool(r.get("unavailable_detected_at")),
            unavailable_text=unavailable_text,
            community_note=r.get("community_note"),
            video_count=video_count,
            video_max_duration_sec=video_max_duration_sec,
            possibly_sensitive=bool(r.get("possibly_sensitive")),
        )
        existing_tags = {str(entry.get("tag") or "") for entry in tags}
        for tag in tag_overrides.get(tweet_id, []):
            if tag in existing_tags:
                continue
            tags.append(
                {
                    "tag": tag,
                    "tentative": None,
                    "source": "manual-override",
                    "span_start": None,
                    "span_end": None,
                }
            )
            existing_tags.add(tag)
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
    # Reuse the prior layer timestamp when nothing but generated_at would change,
    # so an unchanged tag run doesn't churn the manifest (and the viewer cache
    # key derived from it) on every pipeline run.
    prior_lexical = layers.get("lexical")
    if isinstance(prior_lexical, dict) and prior_lexical.get("generated_at"):
        prior_cmp = {k: v for k, v in prior_lexical.items() if k != "generated_at"}
        new_cmp = {k: v for k, v in stats.items() if k != "generated_at"}
        if prior_cmp == new_cmp:
            stats = {**stats, "generated_at": prior_lexical["generated_at"]}
    layers["lexical"] = stats
    manifest = {**stats, "layers": layers}
    tmp = TAGS_DIR / "manifest.tmp.json"
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, manifest_path)


def canonical_manifest_tag(tag: str) -> str:
    """Return a stable display key for manifest-only frequency stats.

    The row-level tag sidecar preserves exact tags. The JSON manifest is also
    consumed by tools with case-insensitive object keys, so merge obvious
    country/state case variants there.
    """
    if ":" not in tag:
        return tag
    namespace, value = tag.split(":", 1)
    if namespace not in {"country", "state"}:
        return tag
    if not value or value != value.upper():
        return tag
    return f"{namespace}:{value.title()}"


def manifest_tag_frequency(freq: dict[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for tag, count in freq.items():
        key = canonical_manifest_tag(tag)
        merged[key] = merged.get(key, 0) + count
    return dict(sorted(merged.items(), key=lambda kv: (-kv[1], kv[0])))


def _lexical_row_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    """Order-independent fingerprint of a row's tag content (sans tagged_at)."""
    tags = row.get("tags") or []
    sig = sorted(
        (
            str(t.get("tag") or ""),
            bool(t.get("tentative")),
            str(t.get("source") or ""),
            -1 if t.get("span_start") is None else int(t.get("span_start")),
            -1 if t.get("span_end") is None else int(t.get("span_end")),
        )
        for t in tags
    )
    return (str(row.get("tagger_version") or ""), tuple(sig))


def load_prior_tagged_at(path: Path) -> dict[str, tuple[tuple[Any, ...], str]]:
    if not path.exists():
        return {}
    try:
        prior = pl.read_parquet(path)
    except Exception:
        return {}
    out: dict[str, tuple[tuple[Any, ...], str]] = {}
    for r in prior.iter_rows(named=True):
        tid = str(r.get("tweet_id") or "")
        if tid:
            out[tid] = (_lexical_row_signature(r), str(r.get("tagged_at") or ""))
    return out


def stabilize_tagged_at(
    rows: list[dict[str, Any]], prior: dict[str, tuple[tuple[Any, ...], str]]
) -> None:
    """Reuse the prior ``tagged_at`` for any row whose tag content is unchanged.

    The tagger reruns on every ingest and every archive-media run. Stamping a
    fresh ``tagged_at`` on all ~16k rows each time rewrote lexical.parquet on
    every run, which forced a data/ commit and a full GitHub Pages redeploy
    even when no tag actually changed. Keying the timestamp to tag content
    keeps the sidecar byte-stable across no-op runs.
    """
    for row in rows:
        tid = str(row.get("tweet_id") or "")
        prev = prior.get(tid)
        if prev and prev[1] and prev[0] == _lexical_row_signature(row):
            row["tagged_at"] = prev[1]


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
    transcript_map = load_transcript_map()
    if transcript_map:
        LOG.info("loaded transcript sidecar overlay", tweets_with_transcript=len(transcript_map))
    media_context_map = load_media_context_map()
    if media_context_map:
        LOG.info(
            "loaded media-recognition sidecar overlay",
            tweets_with_media_context=len(media_context_map),
        )
    audio_context_map = load_audio_context_map()
    if audio_context_map:
        LOG.info(
            "loaded audio-recognition sidecar overlay",
            tweets_with_audio_context=len(audio_context_map),
        )
    reply_context_map = load_reply_context_map(parquets)
    if reply_context_map:
        LOG.info("loaded direct-reply text context", parents_with_replies=len(reply_context_map))
    tag_overrides = load_tag_overrides()
    if tag_overrides:
        LOG.info("loaded manual tag overrides", tweets_with_overrides=len(tag_overrides))
    all_rows: list[dict[str, Any]] = []
    per_file: dict[str, int] = {}
    for path in parquets:
        rows = tag_one_parquet(
            path,
            account_categories,
            tagged_at,
            ocr_map=ocr_map,
            transcript_map=transcript_map,
            media_context_map=media_context_map,
            audio_context_map=audio_context_map,
            reply_context_map=reply_context_map,
            tag_overrides=tag_overrides,
        )
        all_rows.extend(rows)
        per_file[path.name] = len(rows)
        LOG.info("tagged parquet", file=path.name, rows=len(rows))

    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = TAGS_DIR / "lexical.parquet"
    # Deterministic within-row tag order: tags are assembled from sets/dicts and
    # several sidecar overlays, so their order varied run-to-run and rewrote the
    # sidecar (and triggered a Pages redeploy) even when the tag set was
    # identical. Sorting on stable fields makes the parquet byte-stable.
    for row in all_rows:
        row["tags"] = sorted(
            row["tags"] or [],
            key=lambda t: (
                str(t.get("tag") or ""),
                str(t.get("source") or ""),
                -1 if t.get("span_start") is None else int(t.get("span_start")),
                -1 if t.get("span_end") is None else int(t.get("span_end")),
                bool(t.get("tentative")),
            ),
        )
    stabilize_tagged_at(all_rows, load_prior_tagged_at(out_path))
    df = (
        pl.DataFrame(all_rows, schema=LEXICAL_TAG_SCHEMA)
        if all_rows
        else empty_lexical_tag_dataframe()
    )
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
            "tag_frequency": manifest_tag_frequency(freq),
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
