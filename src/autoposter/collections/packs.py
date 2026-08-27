"""Kometa's dynamic packs, transcribed.

``catalog.py`` says what a preset IS; this module is what four of them are made
of. A dynamic pack is one ``builder: dynamic`` definition whose ``params``
carry Kometa's own answer to "which values get a collection, and what is each
one called" -- the include list, the addon merges, the name overrides and the
title format that live in a ``defaults/`` YAML file upstream. The engine that
consumes them shipped in phase 10a and is not touched by this module.

Separate from ``catalog.py`` for one reason: these are TABLES, several hundred
rows of somebody else's data, and ``catalog.py`` is already 1900 lines of
prose-heavy rows. Nothing here is logic. Every constant below is either a
transcription of a file named in its own comment or is marked ``NOT KOMETA:``
with the decision and the reason.

**Provenance.** Every file cited here was fetched from
github.com/Kometa-Team/Kometa at tag ``v2.4.8`` and read while these tables
were written; the fetch record, with byte counts and the full extracted tables,
is the phase's transcription document (``.superpowers/sdd/
p10b-upstream-packs.md``, whose section and line numbers each table below
cites). The ids, keys, names and filter values below are transcriptions of
those files, not recollections of them -- the discipline ``catalog.py``'s
provenance section states for the whole catalog.

**Shape.** Each pack exports one ``*_PARAMS`` tuple of pairs, which is exactly
what a ``PresetCollection.params`` field takes and what
``PresetCollection.definition`` hands to ``DynamicParams``. Three of the params
are MAPPINGS (``addons``, ``key_name_override``, ``title_override``) and are
real dicts: pydantic will not build a ``dict[str, list[str]]`` from a tuple of
pairs (measured -- ``Input should be a valid dictionary``), so the pairs
convention stops at the value.

**Where a pack's sort and limit really come from, recorded once here rather
than seven times below.** The record's §1.8 measured it: all seven packs pass
``template: [smart_filter, shared]``, whose defaults are ``sort_by:
release.desc`` and NO limit at all (``defaults/templates.yml:238-255``), and
only ``decade.yml`` overrides either. Both halves transcribe. ``sort_by``
always could -- both names are in ``search_sorts`` -- and "no limit" can now
too: ``DynamicParams.limit`` takes ``0`` as a documented no-limit sentinel
(``builders/dynamic.py``'s field comment), which is a different statement from
leaving it unset, since unset means "the type row's default" and that is 50.
So every pack below pins what its own file says -- ``limit: 0`` on the six
upstream leaves unlimited, ``limit: 100`` on ``decade`` -- and no row here
ships the engine's default wearing a transcription's name.
"""

__all__ = [
    "AUDIO_LANGUAGE_PARAMS",
    "COUNTRY_PARAMS",
    "DECADE_PARAMS",
    "GENRE_PARAMS",
]

# The `smart_filter` template's own default, which is what every pack but
# `decade` runs on (record §1.8, `defaults/templates.yml:238-255`). Named once
# because three packs share it and a second literal is a second thing to keep
# right.
_TEMPLATE_SORT: tuple[str, ...] = ("release.desc",)

# The same template's `limit`, which is an OPTIONAL variable it does not supply
# -- so the emitted search carries no limit at all (record §1.8). Six of the
# seven packs are in that position; `0` is `DynamicParams.limit`'s no-limit
# sentinel and is named here so the six read as one transcribed fact rather
# than as six magic zeroes.
_TEMPLATE_NO_LIMIT = 0


# --- genre: defaults/both/genre.yml -------------------------------------------
#
# The file pins no include list and no exclusions -- every genre the library
# holds gets a collection -- and its whole narrowing is the `addons:` table
# below, which folds Plex's and the agents' spelling variants of a genre into
# one bucket ("Action & Adventure" into both Action and Adventure, "Biography"
# into Biopic, "Film-Noir" into Film Noir).

# Transcribed from `defaults/both/genre.yml`, `addons:`, in file order --
# record §1.1 (`.superpowers/sdd/p10b-upstream-packs.md:333-375`).
#
# One shape note: upstream writes `Film Noir:` with a bare scalar `Film-Noir`
# beneath it rather than a list item, and Kometa's own `_dictliststr` coerces a
# scalar to a one-item list. `DynamicParams.addons` is typed `dict[str,
# list[str]]` (that coercion is what the type makes unreachable from YAML), so
# it is written here as the one-item list the coercion produces.
_GENRE_ADDONS: dict[str, list[str]] = {
    "Action": [
        "Action/Adventure",
        "Action/adventure",
        "Action & Adventure",
        "Action & adventure",
        "Action and Adventure",
        "Action and adventure",
    ],
    "Adventure": [
        "Action/Adventure",
        "Action/adventure",
        "Action & Adventure",
        "Action & adventure",
        "Action and Adventure",
        "Action and adventure",
    ],
    "Biopic": ["Biography"],
    "Family": ["Kids & Family"],
    "Fantasy": [
        "SciFi & Fantasy",
        "Science Fiction & Fantasy",
        "Science-Fiction & Fantasy",
        "Sci-Fi & Fantasy",
    ],
    "Film Noir": ["Film-Noir"],
    "Politics": ["War & Politics"],
    "Science Fiction": [
        "SciFi",
        "Sci-Fi",
        "Science-Fiction",
        "SciFi & Fantasy",
        "Science Fiction & Fantasy",
        "Sci-Fi & Fantasy",
    ],
    "Talk Show": ["Talk"],
    "War": ["War & Politics"],
}

GENRE_PARAMS: tuple[tuple[str, object], ...] = (
    ("type", "genre"),
    ("title_format", "<<key_name>> <<library_typeU>>s"),
    ("addons", _GENRE_ADDONS),
    ("sort_by", _TEMPLATE_SORT),
    ("limit", _TEMPLATE_NO_LIMIT),
    # No `max_collections`: 21 genres on the production movie library and 14 on
    # the shows (`docs/research/plex-dynamic-probe/README.md` §2), and at most
    # ten more synthetic buckets from the addon keys above, so the builder's
    # own default of 50 never fires.
)


# --- decade: defaults/movie/decade.yml ----------------------------------------
#
# The one pack of the seven that pins its OWN sort and limit, and it pins both
# away from the `smart_filter` template's defaults (record §1.8): "Best of the
# 1980s" is the hundred highest-rated films of the decade, not the hundred most
# recent. No include list, no exclusions, no addons -- a decade is whatever the
# library's own `decade` filter answers.

DECADE_PARAMS: tuple[tuple[str, object], ...] = (
    ("type", "decade"),
    # Record §1.2 (`.superpowers/sdd/p10b-upstream-packs.md:377-423`).
    ("title_format", "Best of <<key_name>>"),
    ("sort_by", ("critic_rating.desc",)),
    ("limit", 100),
    # No `max_collections`: 12 decades on the production movie library, inside
    # the builder's default of 50.
)


# --- languages: defaults/both/audio_language.yml ------------------------------
#
# The include list and the key-name override below are shared with
# `defaults/both/subtitle_language.yml`, whose blocks are BYTE-IDENTICAL to
# this file's -- verified by comparing the extracted blocks, record §1.4. One
# table serves both packs for that reason: two copies could differ, and a
# difference between them would be a transcription error with nothing to catch
# it.

# Transcribed from `defaults/both/audio_language.yml`, `include:`, in file
# order -- record §1.3 (`.superpowers/sdd/p10b-upstream-packs.md:484-675`).
#
# Norwegian is written `"no"`, quoted, and that is load-bearing: upstream's
# line is an unquoted `- no`, which a YAML 1.1 loader (PyYAML) parses as the
# boolean `False`. A transcription round-tripped through such a loader silently
# drops the one code of the four the production movie library actually holds
# (record §2's intersection). A test pins the string's presence.
#
# `include` is matched against the KEY, and this type keys on `choice.key`
# (`dynamic_types.py`'s `key_from`), so these are ISO codes and not display
# names. Four of the 187 are not ISO-639-1 two-letter codes -- `fil`, `myn`,
# `rom`, `tai` -- which is why the one-entry override below exists.
_LANGUAGE_INCLUDE: tuple[str, ...] = (
    "ab", "aa", "af", "ak", "sq", "am", "ar", "an", "hy", "as",
    "av", "ae", "ay", "az", "bm", "ba", "eu", "be", "bn", "bi",
    "bs", "br", "bg", "my", "ca", "km", "ch", "ce", "ny", "zh",
    "cu", "cv", "kw", "co", "cr", "hr", "cs", "da", "dv", "nl",
    "dz", "en", "eo", "et", "ee", "fo", "fj", "fil", "fi", "fr",
    "ff", "gd", "gl", "lg", "ka", "de", "el", "gn", "gu", "ht",
    "ha", "he", "hz", "hi", "ho", "hu", "is", "io", "ig", "id",
    "ia", "ie", "iu", "ik", "ga", "it", "ja", "jv", "kl", "kn",
    "kr", "ks", "kk", "ki", "rw", "ky", "kv", "kg", "ko", "kj",
    "ku", "lo", "la", "lv", "li", "ln", "lt", "lu", "lb", "mk",
    "mg", "ms", "ml", "mt", "gv", "mi", "mr", "mh", "myn", "mn",
    "na", "nv", "ng", "ne", "nd", "se", "no", "nb", "nn", "oc",
    "oj", "or", "om", "os", "pi", "ps", "fa", "pl", "pt", "pa",
    "qu", "ro", "rm", "rom", "rn", "ru", "sm", "sg", "sa", "sc",
    "sr", "sn", "ii", "sd", "si", "sk", "sl", "so", "nr", "st",
    "es", "su", "sw", "ss", "sv", "tl", "ty", "tai", "tg", "ta",
    "tt", "te", "th", "bo", "ti", "to", "ts", "tn", "tr", "tk",
    "tw", "ug", "uk", "ur", "uz", "ve", "vi", "vo", "wa", "cy",
    "fy", "wo", "xh", "yi", "yo", "za", "zu",
)

# Transcribed from `defaults/both/audio_language.yml`, `key_name_override:` --
# record §1.3 (`.superpowers/sdd/p10b-upstream-packs.md:477-482`) and §3.
#
# The whole table, and it is one entry. Upstream names a language bucket from
# TMDb's `_iso_639_1` lookup at run time (`meta.py:928`), so there is no
# code->name table in the file to transcribe; `myn` is the single repair,
# because it is the one non-ISO-639-1 code of the four whose TMDb lookup
# misses. It is a `key_name_override` and NOT a `title_override`: upstream uses
# it as the `key_name` that `title_format` then wraps ("Mayan Audio"), where a
# `title_override` would be the finished title verbatim.
_LANGUAGE_KEY_NAME_OVERRIDE: dict[str, str] = {"myn": "Mayan"}

AUDIO_LANGUAGE_PARAMS: tuple[tuple[str, object], ...] = (
    ("type", "audio_language"),
    ("title_format", "<<key_name>> Audio"),
    ("include", _LANGUAGE_INCLUDE),
    ("key_name_override", _LANGUAGE_KEY_NAME_OVERRIDE),
    ("other_name", "Other Audio"),
    ("sort_by", _TEMPLATE_SORT),
    ("limit", _TEMPLATE_NO_LIMIT),
    # NOT KOMETA: upstream has no cap concept at all. The number is the
    # STRUCTURAL ceiling -- `include` is applied last and the cap is measured
    # against the derived titles, which include the leftovers bucket
    # (`builders/dynamic.py:599`, `:610`) -- so the family cannot exceed it
    # whatever the library holds, and no library holding more of Kometa's own
    # codes than the dev one did can be refused by a number nobody set. Written
    # as an expression so it cannot drift from the list. Record §2, §5 row 3.
    ("max_collections", len(_LANGUAGE_INCLUDE) + 1),
    # No `title_override`: §3 found no code->name table in the file to
    # transcribe, so buckets are named from Plex's own `choice.title`, which is
    # the fallback branch upstream itself ships. An empty override is left OUT
    # rather than written as `{}`.
)


# --- country: defaults/movie/country.yml --------------------------------------
#
# Movie-only, as upstream has it, and Kometa-parity in its value set: upstream's
# `type: country` enumerates the same Plex `country` tag this service does,
# keyed on the same `choice.title` (`meta.py:899-936`; record §1.6, which
# corrects the earlier assumption that this pack read TMDb's origin-country
# field -- that is `origin_country`, a show-only type nothing here builds).
#
# The `addons:` table is what makes the include list usable: Plex's country tag
# titles are not canonical, and the addons fold them into Kometa's names. The
# production movie library's own probe sample proves it -- its fifth country
# value is "Bolivarian Republic of Venezuela", which this table merges into
# "Venezuela". Without the addons that title would fall into "Other Countries".

# Transcribed from `defaults/movie/country.yml`, `include:`, in file order --
# record §1.6 (`.superpowers/sdd/p10b-upstream-packs.md:1769-2051`). Upstream
# groups the list under commented region headings (Northern Africa, Eastern
# Africa, ...); the headings are comments and not data, so the order is kept
# and the groupings are not.
_COUNTRY_INCLUDE: tuple[str, ...] = (
    # Northern Africa
    "Algeria", "Egypt", "Libya", "Morocco", "Sudan", "Tunisia",
    "Western Sahara",
    # Eastern Africa
    "British Indian Ocean Territory", "Burundi", "Comoros", "Djibouti",
    "Eritrea", "Ethiopia", "French Southern Territories", "Kenya",
    "Madagascar", "Malawi", "Mauritius", "Mayotte", "Mozambique", "Réunion",
    "Rwanda", "Seychelles", "Somalia", "South Sudan", "Uganda", "Tanzania",
    "Zambia", "Zimbabwe",
    # Central Africa
    "Angola", "Cameroon", "Central African Republic", "Chad",
    "Republic of the Congo", "Democratic Republic of the Congo",
    "Equatorial Guinea", "Gabon", "São Tomé and Príncipe",
    # Southern Africa
    "Botswana", "Eswatini", "Lesotho", "Namibia", "South Africa",
    # Western Africa
    "Benin", "Burkina Faso", "Cape Verde", "Côte d'Ivoire", "Gambia", "Ghana",
    "Guinea", "Guinea-Bissau", "Liberia", "Mali", "Mauritania", "Niger",
    "Nigeria", "Saint Helena, Ascension and Tristan da Cunha", "Senegal",
    "Sierra Leone", "Togo",
    # Caribbean
    "Anguilla", "Antigua and Barbuda", "Aruba", "Bahamas", "Barbados",
    "Bonaire, Sint Eustatius and Saba", "Netherlands Antilles",
    "British Virgin Islands", "Cayman Islands", "Cuba", "Curaçao", "Dominica",
    "Dominican Republic", "Grenada", "Guadeloupe", "Haiti", "Jamaica",
    "Martinique", "Montserrat", "Puerto Rico", "Saint Barthélemy",
    "Saint Kitts and Nevis", "Saint Lucia", "Saint Martin",
    "Saint Vincent and the Grenadines", "Sint Maarten", "Trinidad and Tobago",
    "Turks and Caicos Islands", "US Virgin Islands",
    # Central America
    "Belize", "Costa Rica", "El Salvador", "Guatemala", "Honduras", "Mexico",
    "Nicaragua", "Panama",
    # South America
    "Argentina", "Bolivia", "Bouvet Island", "Brazil", "Chile", "Colombia",
    "Ecuador", "Falkland Islands", "French Guiana", "Guyana", "Paraguay",
    "Peru", "South Georgia and the South Sandwich Islands", "Suriname",
    "Uruguay", "Venezuela",
    # North America
    "Bermuda", "Canada", "Greenland", "Saint Pierre and Miquelon",
    "United States",
    # Antarctica
    "Antarctica",
    # Central Asia
    "Kazakhstan", "Kyrgyzstan", "Tajikistan", "Turkmenistan", "Uzbekistan",
    # Eastern Asia
    "China", "Hong Kong", "Macao", "North Korea", "Japan", "Mongolia",
    "South Korea", "Taiwan",
    # South-Eastern Asia
    "Brunei", "Cambodia", "Indonesia", "Laos", "Malaysia", "Myanmar",
    "Philippines", "Singapore", "Thailand", "East Timor", "Vietnam",
    # Southern Asia
    "Afghanistan", "Bangladesh", "Bhutan", "India", "Iran", "Maldives",
    "Nepal", "Pakistan", "Sri Lanka",
    # Western Asia
    "Armenia", "Azerbaijan", "Bahrain", "Cyprus", "Georgia", "Iraq", "Israel",
    "Jordan", "Kuwait", "Lebanon", "Oman", "Qatar", "Saudi Arabia",
    "Palestine", "Syria", "Turkey", "United Arab Emirates", "Yemen",
    # Eastern Europe
    "Belarus", "Bulgaria", "Czech Republic", "Hungary", "Poland", "Moldova",
    "Romania", "Russia", "Slovakia", "Ukraine",
    # Northern Europe
    "Åland Islands", "Guernsey", "Jersey", "Sark", "Denmark", "Estonia",
    "Faroe Islands", "Finland", "Iceland", "Ireland", "Northern Ireland",
    "Isle of Man", "Latvia", "Lithuania", "Norway",
    "Svalbard and Jan Mayen Islands", "Sweden", "United Kingdom",
    # Southern Europe
    "Albania", "Andorra", "Bosnia and Herzegovina", "Croatia", "Gibraltar",
    "Greece", "Kosovo", "Vatican City", "Italy", "Malta", "Montenegro",
    "North Macedonia", "Portugal", "San Marino", "Serbia",
    "Serbia and Montenegro", "Slovenia", "Spain", "Yugoslavia",
    # Western Europe
    "Austria", "Belgium", "France", "Germany", "Liechtenstein", "Luxembourg",
    "Monaco", "Netherlands", "Switzerland",
    # Australia and New Zealand
    "Australia", "Christmas Island", "Cocos (Keeling) Islands",
    "Heard Island and McDonald Islands", "New Zealand", "Norfolk Island",
    # Melanesia
    "Fiji", "New Caledonia", "Papua New Guinea", "Solomon Islands", "Vanuatu",
    # Micronesia
    "Guam", "Kiribati", "Marshall Islands", "Micronesia", "Nauru",
    "Northern Mariana Islands", "Palau", "US Minor Outlying Islands",
    # Polynesia
    "American Samoa", "Cook Islands", "French Polynesia", "Niue",
    "Pitcairn Islands", "Samoa", "Tokelau", "Tonga", "Tuvalu",
    "Wallis and Futuna Islands",
)

# Transcribed from `defaults/movie/country.yml`, `addons:`, in file order --
# record §1.6 (`.superpowers/sdd/p10b-upstream-packs.md:2053-2175`).
#
# Both spellings of Côte d'Ivoire are upstream's: the first uses U+2019 (the
# curly apostrophe) and the key uses U+0027, and a transcription that
# normalised them would merge one real Plex tag title less.
_COUNTRY_ADDONS: dict[str, list[str]] = {
    "Tanzania": ["United Republic of Tanzania"],
    "Republic of the Congo": ["Congo"],
    "Democratic Republic of the Congo": ["Zaire"],
    "São Tomé and Príncipe": ["Sao Tome and Principe"],
    "Eswatini": ["Swaziland"],
    "Cape Verde": ["Cabo Verde"],
    "Côte d'Ivoire": ["Côte d’Ivoire", "Ivory Coast"],
    "Saint Helena, Ascension and Tristan da Cunha": [
        "Saint Helena", "St. Helena", "Ascension", "Tristan da Cunha",
    ],
    "Antigua and Barbuda": ["Antigua", "Barbuda"],
    "Bonaire, Sint Eustatius and Saba": [
        "Bonaire", "Sint Eustatius", "Saba",
    ],
    "Saint Kitts and Nevis": ["St. Kitts and Nevis"],
    "Saint Lucia": ["St. Lucia"],
    "Saint Vincent and the Grenadines": [
        "Saint Vincent and Grenadines",
        "St. Vincent and the Grenadines",
        "St. Vincent and Grenadines",
    ],
    "US Virgin Islands": [
        "U.S. Virgin Islands", "United States Virgin Islands",
    ],
    "Bolivia": ["Plurinational State of Bolivia"],
    "Falkland Islands": ["Malvinas"],
    "South Georgia and the South Sandwich Islands": [
        "South Georgia and South Sandwich Islands",
        "South Georgia",
        "South Sandwich Islands",
    ],
    "Venezuela": ["Bolivarian Republic of Venezuela"],
    "Saint Pierre and Miquelon": ["St. Pierre and Miquelon"],
    "United States": ["United States of America"],
    "Hong Kong": ["Hong Kong SAR China"],
    "Macao": ["Macau", "Macau SAR China"],
    "North Korea": ["Democratic People's Republic of Korea"],
    "South Korea": ["Republic of Korea", "Korea"],
    "Taiwan": ["Taiwan, Province of China"],
    "Brunei": ["Brunei Darussalam"],
    "Laos": ["Lao People's Democratic Republic", "Lao"],
    "Myanmar": ["Burma"],
    "East Timor": ["Timor-Leste"],
    "Vietnam": ["Viet Nam"],
    "Iran": ["Islamic Republic of Iran"],
    "Palestine": ["State of Palestine"],
    "Syria": ["Syrian Arab Republic"],
    "Turkey": ["Türkiye"],
    "Czech Republic": ["Czechia", "Czechoslovakia"],
    "Moldova": ["Republic of Moldova"],
    "Russia": ["Russian Federation", "Soviet Union"],
    "Svalbard and Jan Mayen Islands": ["Svalbard and Jan Mayen"],
    "Vatican City": ["Holy See"],
    "North Macedonia": ["Macedonia", "Republic of North Macedonia"],
    "France": ["French Republic"],
    "Germany": ["East Germany"],
    "Heard Island and McDonald Islands": ["Heard and McDonald Islands"],
    "Papua New Guinea": ["New Guinea"],
    "Micronesia": ["Federated States of Micronesia"],
    "US Minor Outlying Islands": [
        "United States Minor Outlying Islands",
        "United States Outlying Islands",
        "U.S. Minor Outlying Islands",
        "U.S. Outlying Islands",
        "US Outlying Islands",
    ],
    "Pitcairn Islands": ["Pitcairn"],
    "Wallis and Futuna Islands": ["Wallis and Futuna"],
}

COUNTRY_PARAMS: tuple[tuple[str, object], ...] = (
    ("type", "country"),
    ("title_format", "<<key_name>>"),
    ("include", _COUNTRY_INCLUDE),
    ("addons", _COUNTRY_ADDONS),
    ("other_name", "Other Countries"),
    ("sort_by", _TEMPLATE_SORT),
    ("limit", _TEMPLATE_NO_LIMIT),
    # NOT KOMETA: the same structural ceiling the audio pack pins, for the same
    # reason -- `len(include)` plus the leftovers bucket. Record §2, §5 row 5.
    ("max_collections", len(_COUNTRY_INCLUDE) + 1),
)
