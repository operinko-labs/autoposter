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
    "NETWORK_PARAMS",
    "STUDIO_PARAMS",
    "SUBTITLE_LANGUAGE_PARAMS",
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
SUBTITLE_LANGUAGE_PARAMS: tuple[tuple[str, object], ...] = (
    ("type", "subtitle_language"),
    # Record §1.4 (`.superpowers/sdd/p10b-upstream-packs.md:677-729`). The whole
    # of the difference between this pack and the audio one above: a different
    # type, a different format, a different leftovers name. Everything that
    # NARROWS the family is the shared table, because upstream's two files carry
    # the same one byte for byte.
    ("title_format", "<<key_name>> Subtitles"),
    ("include", _LANGUAGE_INCLUDE),
    ("key_name_override", _LANGUAGE_KEY_NAME_OVERRIDE),
    ("other_name", "Other Subtitles"),
    ("sort_by", _TEMPLATE_SORT),
    ("limit", _TEMPLATE_NO_LIMIT),
    # NOT KOMETA: the audio pack's structural ceiling, unchanged and for the
    # unchanged reason -- `include` is applied last and the cap is measured
    # against the derived titles, the leftovers bucket among them
    # (`builders/dynamic.py:599`, `:610`). It matters more here than there: 115
    # subtitle languages on the production movie library and 104 on the shows is
    # the widest enumeration of any pack that ships, and the audience for this
    # pack is exactly the large multilingual library that a
    # measured-plus-headroom number would refuse. Record §2, §5 row 4.
    ("max_collections", len(_LANGUAGE_INCLUDE) + 1),
    # No `title_override`, for §3's reason: there is no code->name table in
    # either file to transcribe, so buckets are named from Plex's own
    # `choice.title` -- the fallback branch upstream itself ships.
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


# --- studio: defaults/both/studio.yml -----------------------------------------
#
# The pack whose fan-out is the reason the engine has a cap at all: phase 10a's
# probe measured 824 studio values on the production movie library. Upstream's
# answer is the 485-name include list below and NO `other_name`, so a studio
# outside the list builds nothing rather than falling into a leftovers bucket
# (`builders/dynamic.py:599`, `meta.py:1301-1311`) -- which is what turns 824
# into at most 485.
#
# `include` is matched against the KEY, and this type keys on `choice.title`
# (`dynamic_types.py`'s `key_from`), so the list below is display names and has
# to be: Plex answers the studio filter's key double percent-encoded
# (`20th%2520Century%2520Fox`, measured), so a list of keys would match
# nothing at all.
#
# One upstream behaviour this service cannot express, recorded rather than
# worked around (record §1.5, §5 row 14): `search_term2`/`search_value2` add a
# per-key CONTAINS term for the single key `20th Century Studios` (`studio: 20th
# Century`) beside the exact `studio.is` terms. This engine emits one `any:`
# block on the row's single search key, so that bucket matches the exact names
# in its addon list and not the substring -- a silent narrowing relative to
# upstream, and the only one.

# Transcribed from `defaults/both/studio.yml`, `include:`, in file order --
# record §1.5 (`.superpowers/sdd/p10b-upstream-packs.md:992-1485`). Upstream
# groups the list under two commented headings; the headings are comments and
# not data, so the order is kept and the grouping is kept as comments.
_STUDIO_INCLUDE: tuple[str, ...] = (
    # ANIMES
    "8bit", "A-1 Pictures", "A.C.G.T.", "Acca effe", "Actas", "AIC", "Ajia-Do",
    "Akatsuki", "Animation Do", "Ankama", "APPP", "Arms", "Artland", "Artmic",
    "Arvo Animation", "Asahi Production", "Ashi Productions", "asread.",
    "AtelierPontdarc", "B.CMAY PICTURES", "Bandai Namco Pictures", "Bee Train",
    "Berlanti Productions", "Bibury Animation Studios", "bilibili", "Bones",
    "Brain's Base", "Bridge", "BUG FILMS", "C-Station", "C2C",
    "Children's Playground Entertainment", "Cloud Hearts", "CloverWorks",
    "Colored Pencil Animation", "CoMix Wave Films", "Connect", "Craftar Studios",
    "Creators in Pack", "CygamesPictures", "David Production", "Diomedéa", "DLE",
    "Doga Kobo", "domerica", "Drive", "EMT Squared", "Encourage Films", "ENGI",
    "feel.", "Felix Film", "Fenz", "GAINAX", "Gallop", "Geek Toys", "Gekkou", "Gemba",
    "GENCO", "Geno Studio", "GoHands", "Gonzo", "Graphinica", "Group Tac",
    "Hal Film Maker", "Haoliners Animation League", "Hoods Entertainment", "Hotline",
    "J.C.Staff", "Jumondou", "Kadokawa", "Khara", "Kinema Citrus", "Kyoto Animation",
    "Lan Studio", "LandQ Studio", "Lay-duce", "Lerche", "LIDENFILMS", "M.S.C",
    "Madhouse", "Magic Bus", "Maho Film", "Manglobe", "MAPPA", "Millepensee",
    "Namu Animation", "NAZ", "Nexus", "Nippon Animation", "Nomad", "Nut",
    "Okuruto Noboru", "OLM", "Orange", "Ordet", "OZ", "P.A. Works", "P.I.C.S.",
    "Passione", "Pb Animation Co. Ltd", "Pierrot", "Pine Jam", "Platinum Vision",
    "Polygon Pictures", "Pony Canyon", "Production +h.", "Production I.G",
    "Production IMS", "Production Reed", "Project No.9", "Quad", "Radix", "Revoroot",
    "Saetta", "SANZIGEN", "Satelight", "Science SARU", "Sentai Filmworks",
    "Seven Arcs", "Shaft", "Shin-Ei Animation", "Shogakukan", "Shuka", "Signal.MD",
    "Silver", "SILVER LINK.", "Square Enix", "Staple Entertainment", "Studio 3Hz",
    "Studio A-CAT", "Studio Bind", "Studio Blanc.", "Studio Chizu", "Studio Comet",
    "Studio Deen", "Studio Elle", "Studio Ghibli", "Studio Flad", "Studio Gokumi",
    "Studio Guts", "Studio Hibari", "Studio Kafka", "Studio Kai", "Studio Mir",
    "studio MOTHER", "Studio Palette", "Studio Rikka", "Studio Signpost",
    "Studio VOLN", "STUDIO4°C", "Sunrise Beyond", "Sunrise", "SynergySP",
    "Tatsunoko Production", "Telecom Animation Film", "Tezuka Productions",
    "TMS Entertainment", "TNK", "Toei Animation", "Topcraft", "Triangle Staff",
    "Trigger", "TROYCA", "TYO Animations", "Typhoon Graphics", "ufotable", "V1 Studio",
    "W-Toon Studio", "Wawayu Animation", "White Fox", "Wit Studio", "Wolfsbane",
    "Xebec", "Yokohama Animation Lab", "Yostar Pictures", "Yumeta Company", "Zero-G",
    "Zexcs",
    # MOVIES & TV SHOWS
    "3 Arts Entertainment", "6th & Idaho", "20th Century Animation",
    "20th Century Studios", "20th Century Fox Television", "21 Laps Entertainment",
    "87Eleven", "87North Productions", "101 Studios", "1492 Pictures", "A Bigger Boat",
    "A+E Studios", "A24", "Aardman", "Aamir Khan Productions", "ABC Signature",
    "ABC Studios", "Ace Entertainment", "AGBO", "Amazon Studios",
    "Amblin Entertainment", "AMC Studios", "Anima Sola Productions",
    "Annapurna Pictures", "Ardustry Entertainment", "Artisan Entertainment",
    "Artists First", "Atlas Entertainment", "Atresmedia", "Bad Hat Harry Productions",
    "Bad Robot", "Bad Wolf", "Barunson E&A", "Bakken Record", "Bardel Entertainment",
    "BBC Studios", "Bill Melendez Productions", "Blade", "Bleecker Street",
    "Blown Deadline Productions", "Blue Ice Pictures", "Blue Sky Studios",
    "Bluegrass Films", "Blueprint Pictures", "Blumhouse Productions", "Blur Studio",
    "Bold Films", "Bona Film Group", "Bonanza Productions", "Boo Pictures",
    "Bosque Ranch Productions", "Box to Box Films", "Brandywine Productions",
    "Broken Lizard Industries", "Broken Road Productions", "Calt Production", "Canal+",
    "Carnival Films", "Carolco", "Cartoon Saloon", "Carsey-Werner Company",
    "Castle Rock Entertainment", "CBS Productions", "CBS Studios",
    "CBS Television Studios", "Centropolis Entertainment", "Chernin Entertainment",
    "Chimp Television", "Chris Morgan Productions", "Cinergi Pictures Entertainment",
    "Codeblack Entertainment", "Columbia Pictures", "Constantin Film", "Cowboy Films",
    "Cross Creek Pictures", "Dark Horse Entertainment", "Davis Entertainment",
    "DC Comics", "Dimension Films", "Dino De Laurentiis Company",
    "Disney Television Animation", "DisneyToon Studios",
    "Don Simpson Jerry Bruckheimer Films", "Doozer",
    "Dreams Salon Entertainment Culture", "DreamWorks Studios", "DreamWorks Pictures",
    "Dropout", "Dynamic Planning", "Eleventh Hour Films", "EMJAG Productions",
    "Endeavor Content", "Entertainment 360", "Entertainment One", "Eon Productions",
    "Everest Entertainment", "Expectation Entertainment", "Exposure Labs", "Fandango",
    "Fields Entertainment", "Film4 Productions", "FilmDistrict",
    "FilmNation Entertainment", "Flynn Picture Company", "Focus Features",
    "Food Network", "Fortiche Production", "Fox Television Studios", "Freckle Films",
    "Frederator Studios", "FremantleMedia", "Fuqua Films", "Gallagher Films Ltd",
    "Gary Sanchez Productions", "Gaumont", "Generator Entertainment", "Golden Harvest",
    "Gracie Films", "Green Hat Films", "Grindstone Entertainment Group", "Hallmark",
    "HandMade Films", "Happy Madison Productions", "HartBeat Productions",
    "Hartswood Films", "Hasbro", "HBO", "Heyday Films", "Hughes Entertainment",
    "Hungry Man", "Hurwitz & Schlossberg Productions", "Hyperobject Industries",
    "Icon Entertainment International", "IFC Films", "Illumination Entertainment",
    "Imagin", "Imperative Entertainment", "Impossible Factual", "Ingenious Media",
    "Irwin Entertainment", "Jerry Bruckheimer Films", "Jessie Films",
    "Jinks-Cohen Company", "Kazak Productions", "Kennedy Miller Productions",
    "Kilter Films", "Kjam Media", "Kudos", "Kurtzman Orci", "Laika Entertainment",
    "Landscape Entertainment", "Laura Ziskin Productions", "Leftfield Pictures",
    "Legendary Pictures", "Let's Not Turn This Into a Whole Big Production",
    "Lifetime", "Levity Entertainment Group", "Lightstorm Entertainment",
    "Likely Story", "Lionsgate", "Live Entertainment", "Lord Miller Productions",
    "Lucasfilm Ltd", "Magic Light Pictures", "Magnolia Pictures", "Malevolent Films",
    "Mandalay Entertainment", "Mandarin", "Mandarin Motion Pictures Limited",
    "Marv Films", "Marvel Animation", "Marvel Studios", "Matt Tolmach Productions",
    "Maximum Effort", "Media Res", "Metro-Goldwyn-Mayer",
    "Michael Patrick King Productions", "Millennium Films", "Miramax", "NEON",
    "Netflix", "New Line Cinema", "Nickelodeon Animation Studio",
    "NorthSouth Productions", "Nu Boyana Film Studios", "O2 Filmes", "Open Road Films",
    "Original Film", "Orion Pictures", "Palomar", "Paramount Animation",
    "Paramount Pictures", "Paramount Television Studios", "Participant",
    "Phoenix Pictures", "Piki Films", "Pixar", "Plan B Entertainment",
    "PlayStation Productions", "Playtone", "Plum Pictures",
    "Powerhouse Animation Studios", "PRA", "Prescience", "Prospect Park",
    "Pulse Films", "Radar Pictures", "RadicalMedia", "Railsplitter Pictures",
    "Rankin Bass Productions", "RatPac Entertainment", "Red Dog Culture House",
    "Regency Pictures", "Reveille Productions", "Rip Cord Productions",
    "RocketScience", "Savoy Pictures", "Scenic Labs", "Scion Films",
    "Scott Free Productions", "Sculptor Media", "Screen Gems", "Sean Daniel Company",
    "Searchlight Pictures", "Secret Hideout", "See-Saw Films", "Serendipity Pictures",
    "Shaw Brothers", "Show East", "Showtime Networks", "Sil-Metropole Organisation",
    "Silverback Films", "Siren Pictures", "SISTER", "Sixteen String Jack Productions",
    "SKA Films", "Sky studios", "Skydance", "Sony Pictures Animation", "Sony Pictures",
    "Sphère Média Plus", "Spyglass Entertainment", "Stöð 2",
    "Star Thrower Entertainment", "Stark Raving Black Productions", "StudioCanal",
    "Studio 8", "Studio Babelsberg", "Studio Dragon", "Studio Live",
    "STX Entertainment", "Summit Entertainment", "Syfy", "Syncopy",
    "T-Street Productions", "Tall Ship Productions", "Team Downey",
    "Temple Street Productions", "The Cat in the Hat Productions",
    "The Donners' Company", "The Jim Henson Company", "The Kennedy-Marshall Company",
    "The Linson Company", "The Littlefield Company", "The Mark Gordon Company",
    "The Sea Change Project", "The Stone Quarry", "The Weinstein Company",
    "Tim Burton Productions", "TOHO", "Thunder Road", "Titmouse", "Tomorrow Studios",
    "Touchstone Pictures", "Touchstone Television", "Trademark Films",
    "Triage Entertainment", "Tribeca Productions", "TriStar Pictures",
    "TSG Entertainment", "Twisted Pictures", "UCP", "United Artists",
    "Universal Animation Studios", "Universal Pictures", "Universal Television",
    "Vancouver Media", "Vertigo Entertainment", "Village Roadshow Pictures",
    "W. Chump and Sons", "Walden Media", "Walt Disney Animation Studios",
    "Walt Disney Pictures", "Walt Disney Productions", "Warner Animation Group",
    "Warner Bros. Pictures", "Warner Bros. Television", "Warner Premiere", "warparty",
    "Waverly Films", "Wayfare Entertainment", "Williams Street",
    "Whitaker Entertainment", "Wiedemann & Berg Television", "Winkler Films",
    "Wolf Entertainment", "Working Title Films",
)

# Transcribed from `defaults/both/studio.yml`, `addons:`, in file order --
# record §1.5 (`.superpowers/sdd/p10b-upstream-packs.md:1487-1701`). Every one
# of the 85 merge keys is itself an `include` name, which is what makes them
# merges rather than additions: `Toei` folds into `Toei Animation`, `MGM` into
# `Metro-Goldwyn-Mayer`, the three 20th Century spellings into one bucket.
_STUDIO_ADDONS: dict[str, list[str]] = {
    "8bit": ["8-bit"],
    "20th Century Studios": [
        "20th Century", "20th Century Animation", "20th Century Fox",
    ],
    "AIC": [
        "AIC ASTA", "AIC A.S.T.A", "AIC Build", "AAIC PLUS+", "AIC RIGHTS",
        "AIC Spirits",
    ],
    "Ajia-Do": ["Ajiado"],
    "Amazon Studios": ["Amazon"],
    "Amblin Entertainment": ["Amblin Television"],
    "APPP": ["A.P.P.P."],
    "asread.": ["Asread"],
    "AtelierPontdarc": ["Atelier Pontdarc"],
    "B.CMAY PICTURES": ["G.CMay Animation & Film"],
    "Bandai Namco Pictures": ["Bandai Visual", "Bandai Visual Company"],
    "BBC Studios": ["BBC", "BBC Studios Natural History Unit"],
    "Bibury Animation Studios": ["Bibury Animation CG"],
    "Blue Sky Studios": ["Blue Sky Films"],
    "Bones": ["BONES", "Bones Film", "BONES FILM"],
    "Canal+": ["Canal+ Polska"],
    "Cloud Hearts": ["CLOUDHEARTS"],
    "Columbia Pictures": ["Columbia TriStar"],
    "CoMix Wave Films": ["CoMix Wave"],
    "Craftar Studios": ["Craftar"],
    "CygamesPictures": ["Cygames Pictures"],
    "DC Comics": ["DC Films", "DC Entertainment"],
    "DreamWorks Studios": [
        "DreamWorks", "DreamWorks Animation", "DreamWorks Animation Television",
        "DreamWorks Classics",
    ],
    "Dropout": ["CollegeHumor", "CollegeHumor Media"],
    "EMT Squared": ["EMT²0"],
    "feel.": ["Feel"],
    "Gallop": ["Studio Gallop"],
    "Gaumont": ["Gaumont International Television"],
    "Geek Toys": ["GEEKTOYS"],
    "Gekkou": ["GEKKOU Production"],
    "GoHands": ["Go Hands"],
    "Gonzo": ["GONZO", "Gonzo Digimation"],
    "Hallmark": [
        "Hallmark+", "Hallmark Channel", "Hallmark Entertainment", "Hallmark Media",
        "Hallmark Movies & Mysteries", "The Hallmark Channel",
    ],
    "Haoliners Animation League": [
        "Haoliners Huimeng Animation", "Haoliners Animation",
    ],
    "Illumination Entertainment": ["Illumination", "Illumination Films"],
    "J.C.Staff": ["J.C. Staff"],
    "Khara": ["Studio Khara"],
    "Lan Studio": ["Studio LAN"],
    "Legendary Pictures": ["Legendary Television"],
    "LIDENFILMS": ["Liden Films"],
    "Lucasfilm Ltd": ["Lucasfilm", "Lucasfilm Animation"],
    "Mandarin": ["Mandarin Films", "Mandarin Television"],
    "Marvel Studios": ["Marvel Enterprises", "Marvel Entertainment", "Marvel"],
    "Metro-Goldwyn-Mayer": ["MGM"],
    "New Line Cinema": ["New Line"],
    "Nexus": ["Nexus Factory"],
    "Nut": ["NUT"],
    "P.A. Works": ["P.A.WORKS"],
    "Paramount Pictures": ["Paramount"],
    "Pierrot": ["Pierrot Plus", "Studio Pierrot"],
    "Pixar": ["Pixar Animation Studios"],
    "Plan B Entertainment": ["PlanB Entertainment"],
    "Platinum Vision": ["PlatinumVision"],
    "Production +h.": ["Production +h"],
    "Rankin Bass Productions": ["Rankin/Bass Productions", "Videocraft International"],
    "RatPac Entertainment": ["Dune Entertainment"],
    "Regency Pictures": [
        "Regency Enterprises", "New Regency Pictures", "Monarchy Enterprises S.a.r.l.",
    ],
    "Science SARU": ["Science Saru"],
    "Searchlight Pictures": ["Fox Searchlight Pictures"],
    "Seven Arcs": ["Seven", "Seven Arcs Pictures"],
    "Shaft": ["SHAFT"],
    "Shogakukan": ["Shogakukan Production"],
    "Signal.MD": ["Signal MD"],
    "Silver": ["Studio Silver"],
    "SILVER LINK.": ["Silver Link"],
    "Sky studios": ["British Sky Broadcasting", "British Sky Broadcasting(BSkyB)"],
    "Skydance": ["Skydance Media"],
    "Sony Pictures": [
        "Sony", "Sony Pictures Animation", "Sony Pictures Television Studios",
    ],
    "Studio Blanc.": ["Studio Blanc"],
    "Studio Deen": ["Studio DEEN"],
    "STX Entertainment": ["STX Films"],
    "The Kennedy-Marshall Company": ["The Kennedy/Marshall Company"],
    "The Mark Gordon Company": ["Tiger Aspect Productions"],
    "Sunrise": ["SUNRISE"],
    "TOHO": ["Toho Pictures", "Toho Pictures, Inc."],
    "TMS Entertainment": ["Tokyo Movie Shinsha"],
    "Toei Animation": ["Toei"],
    "Trigger": ["TRIGGER"],
    "TriStar Pictures": ["TriStar"],
    "Universal Pictures": ["Universal", "Universal Animation Studios"],
    "Walt Disney Pictures": ["Disney"],
    "Warner Animation Group": ["Warner Bros. Cartoon Studios", "Warner Animation"],
    "Warner Bros. Pictures": ["Warner", "Warner Animation Group"],
    "Wit Studio": ["WIT STUDIO"],
    "Yokohama Animation Lab": ["Yokohama Animation Laboratory"],
}

STUDIO_PARAMS: tuple[tuple[str, object], ...] = (
    ("type", "studio"),
    # NOT KOMETA: upstream's format is the bare `<<key_name>>` -- the studio's
    # own name and nothing else -- which it also gives `country` and `network`.
    # Three families rendering one format is a collision an operator creates by
    # ticking two boxes (Movie: studio x country; Show: studio x network), and
    # this catalog refuses that offline (`test_no_two_families_an_operator_can_
    # co_enable_share_a_title_format`). Studio is the one row in BOTH pairs, so
    # qualifying it alone clears both and leaves country and network
    # Kometa-exact (adjudication Addendum item 4). Two shapes were weighed: the
    # record's own starting point `<<key_name>> Studio`, which reads "Marvel
    # Studios Studio" on the many include names already ending in Studios; and
    # this one, the plan's decision table, which stays distinct from
    # `content_genres`' `<<key_name>> <<library_typeU>>s` on both library types.
    # The row states the divergence in words. Record §4, §5 row 1.
    #
    # A THIRD shape is on file as a deferred rename candidate, from the T4
    # review and deliberately not taken here: `<<library_typeU>>s from
    # <<key_name>>` ("Movies from Studio Ghibli"). It clears the same two
    # collisions and is the more accurate of the two, because "Top" promises a
    # ranking nothing backs -- this pack transcribes upstream's `release.desc`
    # with no limit, so the collection holds every title the studio has and is
    # ordered newest-first, not best-first. Deferred rather than adopted
    # because a rename here renames every collection a shipped family has
    # already created, which is a migration and not a wording fix, and because
    # it is a two-site edit by design: the literal is pinned again in
    # `tests/test_collection_catalog.py`'s `_PINNED_FORMATS`, and both sites
    # plus the preset row's own prose move together or not at all.
    ("title_format", "Top <<key_name>> <<library_typeU>>s"),
    ("include", _STUDIO_INCLUDE),
    # `include` is a whitelist applied LAST (`dynamic_keys`' module docstring,
    # `meta.py:1348-1351`) and the engine's cap is checked against the DERIVED
    # titles (`builders/dynamic.py:610`), so this list is a hard upper bound on
    # the family whatever the library holds -- which is why the pin is computed
    # from it rather than written as a number that could drift from it. No
    # `+ 1`: upstream ships no `other_name` for this pack, so there is no
    # leftovers title in the set the cap counts. The raw enumeration (824 values
    # on the production movie library) never reaches it. NOT KOMETA, like every
    # cap here -- upstream has no such concept. Record §2, §5 row 7.
    ("max_collections", len(_STUDIO_INCLUDE)),
    ("addons", _STUDIO_ADDONS),
    ("sort_by", _TEMPLATE_SORT),
    ("limit", _TEMPLATE_NO_LIMIT),
    # No `key_name_override` and no `title_override`: record §1.5 found neither
    # in the file. An absent key is written as nothing at all rather than as an
    # empty mapping, so the tables here are the file's tables and no more.
)


# --- network: defaults/show/network.yml ---------------------------------------
#
# Show-only, as upstream has it, and the same shape as `studio`: a long include
# list, an addons table that folds a broadcaster's regional and sibling channels
# into the parent, and no `other_name` -- so a network the library holds that is
# outside the 272 names builds nothing (record §1.7).
#
# Two entries are not strings when the file is parsed: `- 5` is the integer 5
# and `"#0"` is quoted upstream because `#` would open a comment. Both are
# written here as the strings they are matched against, which is what
# `DynamicParams`' `coerce_numbers_to_str` and `_strlist` would produce anyway.
#
# The one key upstream carries that nothing here can consume (record §5 row 15):
# `delete_collections_named`, a seven-entry map from a network's current name to
# the collection its PREVIOUS name built (`Apple TV: Apple TV+`, `HBO Max: Max`)
# -- a rename cleanup for operators migrating off an older Kometa. This
# service's sweep is keyed on the family label rather than on a rename table, so
# there is nothing to port it into. Not ported.

# Transcribed from `defaults/show/network.yml`, `include:`, in file order --
# record §1.7 (`.superpowers/sdd/p10b-upstream-packs.md:2255-2531`).
_NETWORK_INCLUDE: tuple[str, ...] = (
    "#0", "5", "7mate", "ABC", "ABC Family", "ABC Kids", "ABC TV", "ABS-CBN",
    "Acorn TV", "Adult Swim", "AHC", "ALTBalaji", "Amazon Kids+", "AMC", "AMC+",
    "Animal Planet", "ANIMAX", "Angel Studios", "Antena 3", "Apple TV", "ARD", "Arte",
    "Atresplayer Premium", "Atres Player", "AT-X", "Audience", "AXN", "Azteca Uno",
    "A&E", "BBC America", "BBC Four", "BBC iPlayer", "BBC One", "BBC Scotland",
    "BBC Three", "BBC Two", "BET", "BET+", "bilibili", "Binge", "BluTV", "Boomerang",
    "Bravo", "BritBox", "C More", "Canale 5", "Canal+", "Cartoon Network",
    "Cartoonito", "CBC", "CBC Television", "Cbeebies", "CBS", "Channel 3", "Channel 4",
    "CHCH-DT", "Cinemax", "Citytv", "CNN", "Comedy Central", "Cooking Channel",
    "Crackle", "Crave", "Criterion Channel", "Crunchyroll", "CTV", "Cuatro",
    "Curiosity Stream", "DC Universe", "Discovery", "Discovery Kids", "discovery+",
    "Disney Channel", "Disney Junior", "Disney XD", "Disney+", "DR1", "Dropout",
    "Elisa Viihde", "Elisa Viihde Viaplay", "ENA", "Epix", "ESPN", "EXXEN", "E!", "E4",
    "Facebook Watch", "Family Channel", "Ficción Producciones", "Flooxer",
    "Food Network", "FOX", "Fox Kids", "France 2", "Freeform", "Freevee", "Fuji TV",
    "funnyordie.com", "FX", "FXX", "GAİN", "Game Show Network", "Global TV",
    "Globoplay", "GMA Network", "Hallmark", "HBO", "HBO Max", "HGTV", "History",
    "HOT3", "Hulu", "ICTV", "IFC", "IMDb TV", "Investigation Discovery",
    "ION Television", "iQiyi", "ITV", "ITV Encore", "ITV1", "ITV2", "ITV3", "ITV4",
    "ITVBe", "ITVX", "JioCinema", "joyn", "JTBC", "Kan 11", "Kanal 5", "KBS2",
    "Kids WB", "La 1", "La Une", "Las Estrellas", "Lifetime", "Lionsgate+", "Logo",
    "Magnolia Network", "MasterClass", "MBC", "MBN", "MGM+", "mitele",
    "Movistar Plus+", "MTV", "M-Net", "National Geographic", "NBC", "Netflix",
    "Network 10", "NFL Network", "NHK", "Nick", "Nick Jr", "Nickelodeon", "Nicktoons",
    "Nine Network", "Nippon TV", "NRK1", "OCS City", "OCS Max", "ORF", "Oxygen",
    "Pantaya", "Paramount Network", "Paramount+", "PBS", "PBS Kids", "Peacock",
    "Planète+ A&E", "Prime Video", "Quibi", "Rai 1", "Reelz", "RTÉ One", "RTL",
    "RTL Télé", "RTP1", "RÚV", "S4C", "SAT.1", "SBS", "Science", "Seeso",
    "Seven Network", "Shahid", "Showcase", "Showmax", "Showtime", "Shudder", "Sky",
    "Smithsonian", "Space", "Spectrum", "Spike", "Stöð 2", "Stan", "Starz", "STAR+",
    "Sundance TV", "SVT", "SVT Play", "SVT1", "Syfy", "Syndication", "TBS",
    "Telecinco", "Telefe", "Telemundo", "Televisión de Galicia",
    "Televisión Pública Argentina", "Tencent Video", "TF1", "The CW", "The Daily Wire",
    "The Roku Channel", "The WB", "TLC", "TNT", "Tokyo MX", "Travel Channel", "truTV",
    "tubi", "Turner Classic Movies", "TV 2", "tv asahi", "TV Globo", "TV Land",
    "TV Tokyo", "TV3", "TV4", "TV4 Play", "TVB Jade", "tving", "tvN", "TVNZ 1",
    "TVNZ 2", "TVP1", "U", "U&Alibi", "U&Dave", "U&Drama", "U&Eden", "U&Gold", "U&W",
    "U&Yesterday", "UniMás", "Universal Kids", "Universal TV", "Univision", "UPN",
    "USA Network", "U+ Mobile TV", "VH1", "Viaplay", "Vice", "Virgin Media One",
    "ViuTV", "ViX+", "VRT 1", "VRT Max", "VTM", "W", "WE tv", "Xbox Live", "YLE",
    "Youku", "YouTube", "ZDF", "ZEE5",
)

# Transcribed from `defaults/show/network.yml`, `addons:`, in file order --
# record §1.7 (`.superpowers/sdd/p10b-upstream-packs.md:2533-2748`).
#
# Unlike `studio`'s 85 merge keys, two of these 46 are NOT themselves
# `include` names: `Network Ten` (member `Network 10`) and `ReelzChannel`
# (member `Reelz`). Following `dynamic_keys`'s own rules, an addon member is
# excluded from having its own collection and the unheld parent becomes a
# synthetic bucket that `include` (applied last) then drops -- so with no
# `other_name` to catch it, a library holding "Network 10" or "Reelz" (both
# ARE on the 272-name list below) gets no collection for either. A faithful
# transcription of upstream's own quirk, not a bug here: the record's blocks
# are identical and the oracle chain confirms Kometa does the same, so the
# table is not "fixed". The effective ceiling this leaves is 270, not 272.
_NETWORK_ADDONS: dict[str, list[str]] = {
    "ABC": ["ABC.com"],
    "ABC TV": ["ABC (AU)", "ABC Comedy", "ABC Me", "ABC News", "ABC iview"],
    "AMC": ["AMC.com"],
    "Animal Planet": ["Animal Planet Brasil", "Animal Planet Deutschland"],
    "Angel Studios": ["VidAngel"],
    "Apple TV": ["Apple TV+"],
    "BET": ["BET Her"],
    "Canal+": ["Canal+ Poland", "Canal+ Family", "Canal+ Discovery", "Canal+ Afrique"],
    "Cartoon Network": ["Cartoon Network Latin America", "Cartoon Network Anything"],
    "CBC": ["CBCDrama"],
    "CBC Television": ["CBC Gem", "CBC News Network", "CBC Comedy"],
    "CBS": ["CBS.com", "CBS All Access"],
    "CTV": [
        "CTV Two", "CTV News Channel", "CTV Sci-Fi Channel", "CTV Comedy Channel",
        "CTV Life Channel", "ctv.ca",
    ],
    "Discovery": [
        "Discovery Health Channel", "Discovery Channel",
        "Discovery Home & Health Brasil", "Discovery Family", "Discovery Real Time",
        "Discovery Asia", "Discovery Home & Health", "Discovery Life",
        "Discovery World", "Discovery Science", "Discovery Channel (UK)",
    ],
    "Disney Channel": [
        "Toon Disney", "Playhouse Disney", "Disney Channel Asia", "disney.com",
        "Disney Channel Middle East",
    ],
    "Disney Junior": ["Disney Junior Latin America", "Disney Junior Brasil"],
    "Disney+": ["Disney+ Hotstar"],
    "ESPN": [
        "ESPN2", "ESPN+", "ESPN Classic", "ESPNU", "ESPNews", "ESPN Australia",
        "Sony ESPN", "ESPN.com", "ESPN Deportes",
    ],
    "FOX": [
        "Fox", "Fox News Channel", "Fox Sports", "Fox Reality Channel",
        "Fox Sports Networks", "Fox Latin America", "Fox Brasil", "Fox Soccer",
        "Fox Sports 2", "Fox Nation", "Fox Sports 1", "fox.com", "Fox Sports Detroit",
    ],
    "Freevee": ["Amazon Freevee"],
    "Hallmark": [
        "Hallmark+", "Hallmark Channel", "Hallmark Drama",
        "Hallmark Movie & Mysteries", "Hallmark Movies Now",
    ],
    "HBO": [
        "HBO Brasil", "HBO Europe", "HBO Asia", "HBO Latin America", "HBO España",
        "HBO Nordic", "HBO Canada", "HBO Family", "HBO Mundi",
    ],
    "HBO Max": ["HBO Go", "Max"],
    "HGTV": ["HGTV Canada"],
    "History": ["History Channel Italia", "H2"],
    "Lifetime": ["Lifetime Movies"],
    "MTV": [
        "MTV2", "MTV3", "MTV Lebanon", "MTV Latin America", "MTV Italia",
        "MTV Australia", "MTV Canada", "MTV Global", "MTV Nederland",
    ],
    "National Geographic": [
        "National Geographic Channel", "National Geographic Brasil",
        "National Geographic Latinoamerica", "National Geographic India",
        "Nat Geo Wild",
    ],
    "NBC": [
        "CNBC", "MSNBC", "NBCSN", "CNBC Europe", "CNBC Asia", "WNBC", "Nikkei CNBC",
        "KNBC", "CNBC World", "CNBC TV18", "NBC Weather Plus", "NBC Radio Network",
    ],
    "Network Ten": ["Network 10"],
    "Nickelodeon": ["Nick at Nite"],
    "Paramount+": ["Paramount+ with Showtime"],
    "ReelzChannel": ["Reelz"],
    "Showtime": ["Paramount+ with Showtime"],
    "Sky": [
        "Sky One", "Sky Atlantic", "Sky Atlantic (IT)", "Sky Arts", "Sky History",
        "Sky Living", "Sky Crime", "Sky Uno", "Sky Max", "Sky Sports",
        "Sky Documentaries", "Sky Nature", "Sky News", "Sky Cinema",
        "Sky News Australia", "Sky Italia", "Sky Comedy", "Sky Sports F1", "Sky Two",
        "Sky Witness", "sky Travel", "Sky Vision", "Sky News Weather Channel",
    ],
    "Smithsonian": ["Smithsonian Channel", "Smithsonian Earth"],
    "Spike": ["Spike TV"],
    "Starz": ["Starz Encore"],
    "Sundance TV": ["SundanceTV"],
    "TBS": ["TBS.com", "TBS Brasil"],
    "The CW": ["CW seed"],
    "TNT": ["TNT Comedy", "TNT Latin America", "TNT España", "TNT Serie", "TNT Glitz"],
    "Travel Channel": ["Travel Channel United Kingdom"],
    "VH1": ["VH1 Classic"],
    "Vice": ["Viceland", "Vice TV", "Vice.com"],
    "YouTube": ["YouTube Premium"],
}

NETWORK_PARAMS: tuple[tuple[str, object], ...] = (
    ("type", "network"),
    # Kometa's own format, unchanged: the network's name and nothing else. It
    # renders `KEY` on Show, and nothing else this catalog can build renders
    # `KEY` on Show now that `studio` is qualified -- which is the whole reason
    # `studio` is the row that moved and this one is not.
    ("title_format", "<<key_name>>"),
    ("include", _NETWORK_INCLUDE),
    ("addons", _NETWORK_ADDONS),
    ("sort_by", _TEMPLATE_SORT),
    ("limit", _TEMPLATE_NO_LIMIT),
    # NOT KOMETA: the same structural ceiling `studio` pins, and the same
    # absence of an `other_name`, so the same `len(include)` with no `+ 1`. 91
    # networks measured on the production show library, so the pin costs
    # nothing today; what it buys is that a wider library is not refused by a
    # number nobody set. Record §2, §5 row 6.
    ("max_collections", len(_NETWORK_INCLUDE)),
)
