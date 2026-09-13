"""ISO code -> TMDb display name: the one table home rows 196, 190 and 204 share.

Vendored, not recalled. Both tables were fetched ONCE from TMDb's public
configuration endpoints and emitted here by script from the raw bytes
(docs/research/tmdb-iso-names/README.md carries the whole provenance and the
join measurement, and inlines the fetch script verbatim for regeneration):

- ``COUNTRY_NAMES``: /configuration/countries, fetched 2026-08-30,
  251 entries, source sha256 fb4609a1fb14d5b77aed0b65496c05f4c1d0018782b1c91cbc4c9f6bdf97d9e4.
  ISO-3166-1 alpha-2 -> TMDb's ``english_name``, fetched order.
- ``LANGUAGE_NAMES``: /configuration/languages, fetched 2026-08-30,
  187 entries with a non-empty ``english_name`` (skipped, titled
  as their code wherever they occur: none), source sha256 6e23ef1a9fcfb57aa2ac0838b836c9db21be6ee3b09b5bd9e5b57b32a46b1d60.
  ISO-639-1 -> TMDb's ``english_name``, fetched order.

Three rows, one table home: row 196 keys the ``origin_country`` family (and
the region/continent packs) through ``COUNTRY_NAMES``; row 190 titles the
``original_language`` family through ``LANGUAGE_NAMES``; row 204's open
display-title half names ``LANGUAGE_NAMES`` as the table its filters seam
awaits. No runtime dependency, no request: a plain module of data, and the
digest test (tests/test_collection_iso_names.py) is the drift guard's second
site. An LLM writing 250 country names from memory is exactly what the 10b
transcription discipline forbids -- regenerate from fetched bytes or not at
all.

**One name is not unique to one code.** TMDb gives both ``CD`` and ``CG`` the
``english_name`` ``Congo`` -- the only duplicate in the 251, and the
join measurement's D5. ``country_codes`` folds that name back to BOTH codes, so
the membership query loses nothing; the FORWARD direction is lossy and stays
so: two distinct countries share one family key, and upstream's own separate
``Democratic Republic of the Congo`` / ``Republic of the Congo`` members are
unreachable from any ISO code. Harmless where it lands today -- all four Congo
spellings sit in ONE group in both fetched pack files (``Central Africa`` in
region.yml, ``Africa`` in continent.yml), so no item is mis-grouped and only the
displayed key is lossy. Recorded here so it is met knowingly rather than
rediscovered.

**One LANGUAGE name is not unique to one code either, and that one is not
harmless.** TMDb gives both ``nr`` and ``nd`` the ``english_name`` ``Ndebele``
-- the only duplicate in the 187. ``Congo`` above is survivable because
``origin_country`` keys on the NAME (``names="keys"``), so its two codes
collapse to one key BEFORE the title layer ever sees them.
``original_language`` keys on the CODE (``names="titles"``, row 190), so
nothing collapses: ``family_titles`` would see two keys claiming the one title
``Ndebele`` and raise ``DuplicateFamilyTitle``, which the builder turns into
``return []`` for the WHOLE family -- every language collection, not just the
clashing pair. ``language_title`` closes that by appending the code to a name
more than one code carries (``Ndebele (nr)``, ``Ndebele (nd)``). The suffix is
OURS, but it invents no vocabulary: it is the family key itself, the string
every narrowing knob already speaks. It is a property of the TABLE, not of a
library's enumeration, so a scan holding only one Ndebele titles it the same
way -- stable across passes, and never silently claiming the other one.
``language_name`` still answers TMDb's bytes unchanged.

**``COUNTRY_NAME_ALIASES`` is OURS** -- the one table in this module that is not
TMDb's bytes. 10 of the 251 fetched country names are
spelled differently by Kometa's grouping tables, so a family key carrying TMDb's
spelling would fall into a pack's leftover bucket for a country upstream does
carry (measured per name: docs/research/tmdb-iso-names/README.md section 3).
Every pair is DERIVED from the fetched artifacts, never typed from memory, by
three layers tried in order, each requiring the SAME answer in region.yml AND
continent.yml:

  A. TMDb's own ``native_name`` for that code is an exact member of both
     upstream universes -- the derivation the research doc records for ``FO``.
  B. an accent/punctuation/ampersand fold of the ``english_name`` matches
     exactly one member of each universe.
  C. exactly one member of each universe shares a six-character folded prefix
     with the ``english_name``.

10 pairs rather than the Addendum's "eight post-fold": the eight
presumes an accent fold at match time, and no such fold exists anywhere at
runtime, so the two pure-accent divergences are carried here as well and no
caller has to fold anything. Each entry's comment names its code and its layer;
the generation step prints the upstream member's line number in both files, and
the digest test is the drift guard. APPLYING the table is the packs' job, not
this module's: ``COUNTRY_NAMES`` stays TMDb's bytes verbatim so ``country_codes``
can fold a family key back to the code the database stores.
"""

__all__ = [
    "COUNTRY_NAMES",
    "COUNTRY_NAME_ALIASES",
    "LANGUAGE_NAMES",
    "country_codes",
    "country_name",
    "language_name",
    "language_title",
]

# /configuration/countries, verbatim, fetched order.
COUNTRY_NAMES: dict[str, str] = {
    "AD": "Andorra",
    "AE": "United Arab Emirates",
    "AF": "Afghanistan",
    "AG": "Antigua and Barbuda",
    "AI": "Anguilla",
    "AL": "Albania",
    "AM": "Armenia",
    "AN": "Netherlands Antilles",
    "AO": "Angola",
    "AQ": "Antarctica",
    "AR": "Argentina",
    "AS": "American Samoa",
    "AT": "Austria",
    "AU": "Australia",
    "AW": "Aruba",
    "AZ": "Azerbaijan",
    "BA": "Bosnia and Herzegovina",
    "BB": "Barbados",
    "BD": "Bangladesh",
    "BE": "Belgium",
    "BF": "Burkina Faso",
    "BG": "Bulgaria",
    "BH": "Bahrain",
    "BI": "Burundi",
    "BJ": "Benin",
    "BM": "Bermuda",
    "BN": "Brunei Darussalam",
    "BO": "Bolivia",
    "BR": "Brazil",
    "BS": "Bahamas",
    "BT": "Bhutan",
    "BU": "Burma",
    "BV": "Bouvet Island",
    "BW": "Botswana",
    "BY": "Belarus",
    "BZ": "Belize",
    "CA": "Canada",
    "CC": "Cocos  Islands",
    "CD": "Congo",
    "CF": "Central African Republic",
    "CG": "Congo",
    "CH": "Switzerland",
    "CI": "Cote D'Ivoire",
    "CK": "Cook Islands",
    "CL": "Chile",
    "CM": "Cameroon",
    "CN": "China",
    "CO": "Colombia",
    "CR": "Costa Rica",
    "CS": "Serbia and Montenegro",
    "CU": "Cuba",
    "CV": "Cape Verde",
    "CX": "Christmas Island",
    "CY": "Cyprus",
    "CZ": "Czech Republic",
    "DE": "Germany",
    "DJ": "Djibouti",
    "DK": "Denmark",
    "DM": "Dominica",
    "DO": "Dominican Republic",
    "DZ": "Algeria",
    "EC": "Ecuador",
    "EE": "Estonia",
    "EG": "Egypt",
    "EH": "Western Sahara",
    "ER": "Eritrea",
    "ES": "Spain",
    "ET": "Ethiopia",
    "FI": "Finland",
    "FJ": "Fiji",
    "FK": "Falkland Islands",
    "FM": "Micronesia",
    "FO": "Faeroe Islands",
    "FR": "France",
    "GA": "Gabon",
    "GB": "United Kingdom",
    "GD": "Grenada",
    "GE": "Georgia",
    "GF": "French Guiana",
    "GH": "Ghana",
    "GI": "Gibraltar",
    "GL": "Greenland",
    "GM": "Gambia",
    "GN": "Guinea",
    "GP": "Guadaloupe",
    "GQ": "Equatorial Guinea",
    "GR": "Greece",
    "GS": "South Georgia and the South Sandwich Islands",
    "GT": "Guatemala",
    "GU": "Guam",
    "GW": "Guinea-Bissau",
    "GY": "Guyana",
    "HK": "Hong Kong",
    "HM": "Heard and McDonald Islands",
    "HN": "Honduras",
    "HR": "Croatia",
    "HT": "Haiti",
    "HU": "Hungary",
    "ID": "Indonesia",
    "IE": "Ireland",
    "IL": "Israel",
    "IN": "India",
    "IO": "British Indian Ocean Territory",
    "IQ": "Iraq",
    "IR": "Iran",
    "IS": "Iceland",
    "IT": "Italy",
    "JM": "Jamaica",
    "JO": "Jordan",
    "JP": "Japan",
    "KE": "Kenya",
    "KG": "Kyrgyz Republic",
    "KH": "Cambodia",
    "KI": "Kiribati",
    "KM": "Comoros",
    "KN": "St. Kitts and Nevis",
    "KP": "North Korea",
    "KR": "South Korea",
    "KW": "Kuwait",
    "KY": "Cayman Islands",
    "KZ": "Kazakhstan",
    "LA": "Lao People's Democratic Republic",
    "LB": "Lebanon",
    "LC": "St. Lucia",
    "LI": "Liechtenstein",
    "LK": "Sri Lanka",
    "LR": "Liberia",
    "LS": "Lesotho",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "LV": "Latvia",
    "LY": "Libyan Arab Jamahiriya",
    "MA": "Morocco",
    "MC": "Monaco",
    "MD": "Moldova",
    "ME": "Montenegro",
    "MG": "Madagascar",
    "MH": "Marshall Islands",
    "MK": "Macedonia",
    "ML": "Mali",
    "MM": "Myanmar",
    "MN": "Mongolia",
    "MO": "Macao",
    "MP": "Northern Mariana Islands",
    "MQ": "Martinique",
    "MR": "Mauritania",
    "MS": "Montserrat",
    "MT": "Malta",
    "MU": "Mauritius",
    "MV": "Maldives",
    "MW": "Malawi",
    "MX": "Mexico",
    "MY": "Malaysia",
    "MZ": "Mozambique",
    "NA": "Namibia",
    "NC": "New Caledonia",
    "NE": "Niger",
    "NF": "Norfolk Island",
    "NG": "Nigeria",
    "NI": "Nicaragua",
    "NL": "Netherlands",
    "NO": "Norway",
    "NP": "Nepal",
    "NR": "Nauru",
    "NU": "Niue",
    "NZ": "New Zealand",
    "OM": "Oman",
    "PA": "Panama",
    "PE": "Peru",
    "PF": "French Polynesia",
    "PG": "Papua New Guinea",
    "PH": "Philippines",
    "PK": "Pakistan",
    "PL": "Poland",
    "PM": "St. Pierre and Miquelon",
    "PN": "Pitcairn Island",
    "PR": "Puerto Rico",
    "PS": "Palestinian Territory",
    "PT": "Portugal",
    "PW": "Palau",
    "PY": "Paraguay",
    "QA": "Qatar",
    "RE": "Reunion",
    "RO": "Romania",
    "RS": "Serbia",
    "RU": "Russia",
    "RW": "Rwanda",
    "SA": "Saudi Arabia",
    "SB": "Solomon Islands",
    "SC": "Seychelles",
    "SD": "Sudan",
    "SE": "Sweden",
    "SG": "Singapore",
    "SH": "St. Helena",
    "SI": "Slovenia",
    "SJ": "Svalbard & Jan Mayen Islands",
    "SK": "Slovakia",
    "SL": "Sierra Leone",
    "SM": "San Marino",
    "SN": "Senegal",
    "SO": "Somalia",
    "SR": "Suriname",
    "SS": "South Sudan",
    "ST": "Sao Tome and Principe",
    "SU": "Soviet Union",
    "SV": "El Salvador",
    "SY": "Syrian Arab Republic",
    "SZ": "Swaziland",
    "TC": "Turks and Caicos Islands",
    "TD": "Chad",
    "TF": "French Southern Territories",
    "TG": "Togo",
    "TH": "Thailand",
    "TJ": "Tajikistan",
    "TK": "Tokelau",
    "TL": "Timor-Leste",
    "TM": "Turkmenistan",
    "TN": "Tunisia",
    "TO": "Tonga",
    "TP": "East Timor",
    "TR": "Turkey",
    "TT": "Trinidad and Tobago",
    "TV": "Tuvalu",
    "TW": "Taiwan",
    "TZ": "Tanzania",
    "UA": "Ukraine",
    "UG": "Uganda",
    "UM": "United States Minor Outlying Islands",
    "US": "United States of America",
    "UY": "Uruguay",
    "UZ": "Uzbekistan",
    "VA": "Holy See",
    "VC": "St. Vincent and the Grenadines",
    "VE": "Venezuela",
    "VG": "British Virgin Islands",
    "VI": "US Virgin Islands",
    "VN": "Vietnam",
    "VU": "Vanuatu",
    "WF": "Wallis and Futuna Islands",
    "WS": "Samoa",
    "XC": "Czechoslovakia",
    "XG": "East Germany",
    "XI": "Northern Ireland",
    "XK": "Kosovo",
    "YE": "Yemen",
    "YT": "Mayotte",
    "YU": "Yugoslavia",
    "ZA": "South Africa",
    "ZM": "Zambia",
    "ZR": "Zaire",
    "ZW": "Zimbabwe",
}

# /configuration/languages, verbatim, fetched order; empty english_name entries skipped (module docstring).
LANGUAGE_NAMES: dict[str, str] = {
    "xx": "No Language",
    "aa": "Afar",
    "af": "Afrikaans",
    "ak": "Akan",
    "an": "Aragonese",
    "as": "Assamese",
    "av": "Avaric",
    "ae": "Avestan",
    "ay": "Aymara",
    "az": "Azerbaijani",
    "ba": "Bashkir",
    "bm": "Bambara",
    "bn": "Bengali",
    "bi": "Bislama",
    "bo": "Tibetan",
    "bs": "Bosnian",
    "br": "Breton",
    "ca": "Catalan",
    "cs": "Czech",
    "ch": "Chamorro",
    "ce": "Chechen",
    "cu": "Slavic",
    "cv": "Chuvash",
    "kw": "Cornish",
    "co": "Corsican",
    "cr": "Cree",
    "cy": "Welsh",
    "da": "Danish",
    "de": "German",
    "dv": "Divehi",
    "dz": "Dzongkha",
    "en": "English",
    "eo": "Esperanto",
    "et": "Estonian",
    "eu": "Basque",
    "fo": "Faroese",
    "fj": "Fijian",
    "fi": "Finnish",
    "fr": "French",
    "fy": "Frisian",
    "ff": "Fulah",
    "gd": "Gaelic",
    "ga": "Irish",
    "gl": "Galician",
    "gv": "Manx",
    "gn": "Guarani",
    "gu": "Gujarati",
    "ht": "Haitian; Haitian Creole",
    "ha": "Hausa",
    "sh": "Serbo-Croatian",
    "hz": "Herero",
    "ho": "Hiri Motu",
    "hr": "Croatian",
    "hu": "Hungarian",
    "ig": "Igbo",
    "io": "Ido",
    "ii": "Yi",
    "iu": "Inuktitut",
    "ie": "Interlingue",
    "ia": "Interlingua",
    "id": "Indonesian",
    "ik": "Inupiaq",
    "is": "Icelandic",
    "it": "Italian",
    "jv": "Javanese",
    "ja": "Japanese",
    "kl": "Kalaallisut",
    "kn": "Kannada",
    "ks": "Kashmiri",
    "ka": "Georgian",
    "kr": "Kanuri",
    "kk": "Kazakh",
    "km": "Khmer",
    "ki": "Kikuyu",
    "rw": "Kinyarwanda",
    "ky": "Kirghiz",
    "kv": "Komi",
    "kg": "Kongo",
    "ko": "Korean",
    "kj": "Kuanyama",
    "ku": "Kurdish",
    "lo": "Lao",
    "la": "Latin",
    "lv": "Latvian",
    "li": "Limburgish",
    "ln": "Lingala",
    "lt": "Lithuanian",
    "lb": "Letzeburgesch",
    "lu": "Luba-Katanga",
    "lg": "Ganda",
    "mh": "Marshall",
    "ml": "Malayalam",
    "mr": "Marathi",
    "mg": "Malagasy",
    "mt": "Maltese",
    "mo": "Moldavian",
    "mn": "Mongolian",
    "mi": "Maori",
    "ms": "Malay",
    "my": "Burmese",
    "na": "Nauru",
    "nv": "Navajo",
    "nr": "Ndebele",
    "nd": "Ndebele",
    "ng": "Ndonga",
    "ne": "Nepali",
    "nl": "Dutch",
    "nn": "Norwegian Nynorsk",
    "nb": "Norwegian Bokmål",
    "no": "Norwegian",
    "ny": "Chichewa; Nyanja",
    "oc": "Occitan",
    "oj": "Ojibwa",
    "or": "Oriya",
    "om": "Oromo",
    "os": "Ossetian; Ossetic",
    "pa": "Punjabi",
    "pi": "Pali",
    "pl": "Polish",
    "pt": "Portuguese",
    "qu": "Quechua",
    "rm": "Raeto-Romance",
    "ro": "Romanian",
    "rn": "Rundi",
    "ru": "Russian",
    "sg": "Sango",
    "sa": "Sanskrit",
    "si": "Sinhalese",
    "sk": "Slovak",
    "sl": "Slovenian",
    "se": "Northern Sami",
    "sm": "Samoan",
    "sn": "Shona",
    "sd": "Sindhi",
    "so": "Somali",
    "st": "Sotho",
    "es": "Spanish",
    "sq": "Albanian",
    "sc": "Sardinian",
    "sr": "Serbian",
    "ss": "Swati",
    "su": "Sundanese",
    "sw": "Swahili",
    "sv": "Swedish",
    "ty": "Tahitian",
    "ta": "Tamil",
    "tt": "Tatar",
    "te": "Telugu",
    "tg": "Tajik",
    "tl": "Tagalog",
    "th": "Thai",
    "ti": "Tigrinya",
    "to": "Tonga",
    "tn": "Tswana",
    "ts": "Tsonga",
    "tk": "Turkmen",
    "tr": "Turkish",
    "tw": "Twi",
    "ug": "Uighur",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "uz": "Uzbek",
    "ve": "Venda",
    "vi": "Vietnamese",
    "vo": "Volapük",
    "wa": "Walloon",
    "wo": "Wolof",
    "xh": "Xhosa",
    "yi": "Yiddish",
    "za": "Zhuang",
    "zu": "Zulu",
    "ab": "Abkhazian",
    "zh": "Mandarin",
    "ps": "Pushto",
    "am": "Amharic",
    "ar": "Arabic",
    "be": "Belarusian",
    "bg": "Bulgarian",
    "cn": "Cantonese",
    "mk": "Macedonian",
    "ee": "Ewe",
    "el": "Greek",
    "fa": "Persian",
    "he": "Hebrew",
    "hi": "Hindi",
    "hy": "Armenian",
    "yo": "Yoruba",
}

# OURS, not TMDb's: the fetched country names upstream's grouping tables spell
# differently, each pair derived from the fetched artifacts by the layered rule
# the module docstring states. TMDb's spelling -> upstream's.
COUNTRY_NAME_ALIASES: dict[str, str] = {
    "Cocos  Islands": "Cocos (Keeling) Islands",  # CC, layer A native_name
    "Cote D'Ivoire": "Côte d’Ivoire",  # CI, layer A native_name
    "Faeroe Islands": "Faroe Islands",  # FO, layer A native_name
    "Guadaloupe": "Guadeloupe",  # GP, layer A native_name
    "Kyrgyz Republic": "Kyrgyzstan",  # KG, layer A native_name
    "Libyan Arab Jamahiriya": "Libya",  # LY, layer A native_name
    "Pitcairn Island": "Pitcairn Islands",  # PN, layer A native_name
    "Palestinian Territory": "Palestine",  # PS, layer C prefix6
    "Reunion": "Réunion",  # RE, layer A native_name
    "Svalbard & Jan Mayen Islands": "Svalbard and Jan Mayen Islands",  # SJ, layer B fold
}

_CODES_BY_NAME: dict[str, tuple[str, ...]] = {}
for _code, _name in COUNTRY_NAMES.items():
    _CODES_BY_NAME[_name] = _CODES_BY_NAME.get(_name, ()) + (_code,)
del _code, _name

# Measured from the table, never typed: the language names more than one code
# carries. ``Ndebele`` (``nr``/``nd``) is the only one today, and a
# regeneration that adds or drops a clash needs no edit here.
_LANGUAGE_NAME_COUNTS: dict[str, int] = {}
for _name in LANGUAGE_NAMES.values():
    _LANGUAGE_NAME_COUNTS[_name] = _LANGUAGE_NAME_COUNTS.get(_name, 0) + 1
_SHARED_LANGUAGE_NAMES: frozenset[str] = frozenset(
    name for name, count in _LANGUAGE_NAME_COUNTS.items() if count > 1
)
del _name, _LANGUAGE_NAME_COUNTS


def country_name(code: str) -> str | None:
    """TMDb's English name for an ISO-3166-1 alpha-2 code, or None."""
    return COUNTRY_NAMES.get(str(code).upper())


def language_name(code: str) -> str | None:
    """TMDb's English name for an ISO-639-1 code, or None."""
    return LANGUAGE_NAMES.get(str(code).lower())


def language_title(code: str) -> str | None:
    """What a language code may safely TITLE a collection, or None.

    ``language_name`` answers TMDb's bytes and only those. This answers the
    same string EXCEPT where the vendored table gives one name to more than one
    code, where it appends the code -- ``Ndebele (nr)``, ``Ndebele (nd)``.
    The module docstring says why that matters on this seam and not on the
    countries one: ``names="titles"`` keys on the CODE, so two codes sharing a
    name reach ``family_titles`` as two keys claiming one title and refuse the
    whole family rather than collapsing into one collection.

    Deliberately a property of the TABLE and not of any one enumeration: a
    library holding only ``nr`` still gets ``Ndebele (nr)``. A collision-time
    rule would read a set the include/exclude narrowing has not been applied to
    yet, so the same code could title two ways in two scans. ``title_override:
    {nr: …}`` renames it by hand, as it renames any key.
    """
    name = language_name(code)
    if name is None:
        return None
    if name in _SHARED_LANGUAGE_NAMES:
        return "%s (%s)" % (name, str(code).lower())
    return name


def country_codes(name: str) -> tuple[str, ...]:
    """Every code the vendored table maps to this exact TMDb name.

    A tuple rather than one code so a duplicate ``english_name`` -- ``Congo``,
    the one the join record measured -- folds a family key back to ALL its
    codes rather than silently dropping one. Empty for a name the table does
    not carry, which the family layer passes through unchanged.
    """
    return _CODES_BY_NAME.get(str(name), ())
