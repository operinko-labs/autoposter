"""The dynamic type table -- transcription checksums.

The table is Kometa's ``auto`` type map (meta.py:15-22), its
``auto_type_translation`` (meta.py:24-34), its per-type ``default_template``
(meta.py:947-959) and its per-type ``title_format`` defaults (meta.py:868,
:898, :948-959), reduced to the types phase 10a ships (adjudication C3). These
tests are what a reviewer checks the transcription against.
"""
import dataclasses
import pathlib
import re

import pytest

from autoposter.collections.dynamic_types import DYNAMIC_TYPES, DynamicType
from autoposter.collections.filters import BY_NAME, parse_filters
from autoposter.collections.search_sorts import KNOWN_SORT_NAMES

PROBE = (
    pathlib.Path(__file__).parent.parent
    / "docs" / "research" / "plex-dynamic-probe" / "README.md"
)

# ``=== section Movies (movie)`` and, under it,
# ``  country               63 value(s)  137628=Argentina, ...``. The two
# non-numeric forms the script can print -- ``n/a for this library type`` and
# ``REFUSED (...)`` -- deliberately do not match, because neither is a verdict
# that a row may ship on.
_SECTION = re.compile(r"^=== section .+ \((movie|show)\)$")
_ANSWER = re.compile(r"^ {2}(\w+) +(\d+) value\(s\)")

# A value every row's attribute type accepts, so the parse check below tests
# the KEY and not the value: ``_as_text`` takes it for the tag and str rows,
# ``_as_int`` takes it for ``year`` and ``decade``. The plan's block used
# ``"x"``, which the two int rows refuse for a reason that has nothing to do
# with what the test is asserting.
SOME_VALUE = "1980"


def test_the_table_holds_exactly_the_types_c3_scoped():
    """C3's IN list, and nothing else. Every absence is a decision with a
    reason: ``edition`` waits on roadmap row 170; ``original_language`` and
    ``origin_country`` are PLAIN collections upstream, built from a
    full-library TMDb walk (meta.py:36-37), which is the metadata-prefetch
    budget family and not this phase; the people types are 10c; show-decade
    needs the full scan Kometa falls back to (meta.py:881-898); and
    ``number``/``custom``/``tmdb_collection``/the list types are not library
    enumerations at all."""
    assert list(DYNAMIC_TYPES) == [
        "year",
        "decade",
        "content_rating",
        "studio",
        "genre",
        "country",
        "resolution",
        "audio_language",
        "subtitle_language",
        "network",
    ]


def test_every_row_is_frozen_and_a_caller_cannot_mutate_it():
    """The table is data, and data that can be mutated at import time by any
    caller is not a transcription any more: a caller holding a row cannot
    reassign one of its fields."""
    for name, row in DYNAMIC_TYPES.items():
        assert isinstance(row, DynamicType), name
        with pytest.raises(dataclasses.FrozenInstanceError):
            row.name = "x"


def test_every_row_names_a_searchable_attribute_it_can_enumerate():
    """The table's two columns that can silently disagree: the attribute a row
    enumerates and the key it writes into the query are the same row of
    ``FILTER_ATTRIBUTES``, and that row must be searchable on every library
    type the dynamic type serves -- otherwise ``field_for`` raises mid-pass on
    a library the operator was told this type supports."""
    for name, row in DYNAMIC_TYPES.items():
        attribute = BY_NAME[row.attribute]
        assert attribute.searchable, name
        assert row.search_key.split(".")[0] == row.attribute, name
        for library_type in row.kinds:
            assert library_type in ("Movie", "Show"), name
            assert library_type.lower() in attribute.search_kinds, name
            attribute.field_for(library_type.lower())


def test_every_rows_query_key_is_one_this_vocabulary_accepts():
    """The emitted key goes through ``parse_filters`` with ``searching=True``,
    so an operator never writes it and nothing else would catch a row whose
    modifier the attribute's type does not take in a search. ``studio.is`` is
    the row this exists for: upstream singles it out (meta.py:950) because a
    bare ``studio:`` is a CONTAINS match and the bucket wants the exact
    value."""
    for name, row in DYNAMIC_TYPES.items():
        parse_filters(
            {row.search_key: [SOME_VALUE]},
            field="params",
            searching=True,
            base="any",
        )
        assert isinstance(row.key_from, str) and row.key_from in ("key", "title"), name


def test_every_default_sort_is_a_real_sort_and_every_title_format_is_renderable():
    """Two transcription errors that would only surface against a live library:
    a sort name Plex does not have refuses the whole family at build time, and
    a ``title_format`` with neither ``<<key_name>>`` nor ``<<title>>`` would
    title every collection in the family identically."""
    for name, row in DYNAMIC_TYPES.items():
        for sort_name in row.sort_by:
            assert sort_name in KNOWN_SORT_NAMES, name
        assert "<<key_name>>" in row.title_format or "<<title>>" in row.title_format, name
        assert row.note.strip(), name


def test_the_general_default_is_upstreams_and_resolution_is_the_exception():
    """meta.py:950 gives every tag type ``limit: 50`` and
    ``critic_rating.desc``; meta.py:947 gives ``resolution`` ``title.asc`` and
    NO limit. Pinned because they are the two numbers an operator inherits
    without writing anything."""
    assert DYNAMIC_TYPES["genre"].sort_by == ("critic_rating.desc",)
    assert DYNAMIC_TYPES["genre"].limit == 50
    assert DYNAMIC_TYPES["resolution"].sort_by == ("title.asc",)
    assert DYNAMIC_TYPES["resolution"].limit is None


@pytest.mark.parametrize("name", ["year", "content_rating", "studio", "network"])
def test_every_type_row_carries_a_sort_and_a_limit_that_exist(name):
    """T2 review, deferred minor: ``limit``/``sort_by`` were pinned only for
    genre and resolution and only transitively. Every row, directly."""
    row = DYNAMIC_TYPES[name]
    assert row.sort_by, name
    for sort in row.sort_by:
        assert sort in KNOWN_SORT_NAMES, (name, sort)
    assert row.limit is None or row.limit >= 1, name


def test_the_title_formats_are_upstreams_four_shapes():
    """meta.py:868 (the base), :955 (year), :957 (movie decade), :948
    (resolution), :959 (every other tag type)."""
    assert DYNAMIC_TYPES["year"].title_format == "Best <<library_type>>s of <<key_name>>"
    assert DYNAMIC_TYPES["decade"].title_format == "Best <<library_type>>s of the <<key_name>>"
    assert DYNAMIC_TYPES["resolution"].title_format == "<<key_name>> <<library_type>>s"
    assert DYNAMIC_TYPES["genre"].title_format == "Top <<key_name>> <<library_type>>s"
    assert DYNAMIC_TYPES["content_rating"].title_format == "Top <<key_name>> <<library_type>>s"


def test_the_two_key_from_key_types_are_the_ones_upstream_keys_on_choice_key():
    """meta.py:933 vs :936 -- ``resolution`` and movie ``decade`` key on
    ``choice.key`` and title on ``choice.title``; every other tag type uses the
    title as BOTH. The languages key on ``choice.key`` too (:928-929). Getting
    this backwards builds ``decade=1980s``, which Plex answers with nothing.

    The phase's own probe measured every one of these against the production
    libraries (``docs/research/plex-dynamic-probe/README.md`` §3), and two of
    the ``title`` rows are the reason the column is not guessable from the
    name: ``genre``'s key is the opaque tag id ``482`` and ``studio``'s is the
    title percent-encoded TWICE (``20th%2520Century%2520Fox``)."""
    keyed = [name for name, row in DYNAMIC_TYPES.items() if row.key_from == "key"]
    assert keyed == ["decade", "resolution", "audio_language", "subtitle_language"]


def test_studio_is_the_one_row_whose_query_key_carries_a_modifier():
    """meta.py:950's ``f'{auto_type}.is' if auto_type == 'studio' else
    auto_type`` -- a bare ``studio:`` is a CONTAINS match, so dropping the
    ``.is`` would put every A24-distributed label into the A24 bucket."""
    assert DYNAMIC_TYPES["studio"].search_key == "studio.is"
    assert [n for n, r in DYNAMIC_TYPES.items() if "." in r.search_key] == ["studio"]


def test_decade_emits_the_bare_form_and_says_so():
    """T1 carry, binding. ``decade``'s operator subtraction is held by ONE
    table test and no oracle config can cover it, because Kometa refuses
    ``decade.gte`` outright (``no_not_mods``, plex.py:593, :597-599). So every
    downstream emitter -- this table, and eventually the dynamic builder --
    must emit the BARE form only, and the row has to say so where the next
    reader of the table will see it rather than leaving it to the filters
    table's own note."""
    row = DYNAMIC_TYPES["decade"]
    assert row.search_key == "decade"
    assert BY_NAME["decade"].search_operators == ("eq",)
    assert "bare" in row.note.lower()


def test_country_and_decade_are_movie_only_and_network_is_show_only():
    """Upstream's ``auto`` table (meta.py:15-22): ``country`` is Movie/Artist/
    Video, ``network`` and ``origin_country`` are Show, and movie ``decade`` is
    the only decade this phase ships."""
    assert DYNAMIC_TYPES["country"].kinds == ("Movie",)
    assert DYNAMIC_TYPES["decade"].kinds == ("Movie",)
    assert DYNAMIC_TYPES["network"].kinds == ("Show",)
    assert DYNAMIC_TYPES["genre"].kinds == ("Movie", "Show")


def _probe_verdicts() -> dict[str, dict[str, int]]:
    """The recorded probe output, read as data: ``{attribute: {libtype: n}}``.

    Parsed out of ``docs/research/plex-dynamic-probe/README.md`` §2 -- the
    script's own stdout, quoted verbatim -- rather than restated here, for the
    same reason ``_roadmap_rows`` reads the roadmap instead of copying it: a
    hand-kept second copy of a measurement is a measurement nobody can check.
    Where a library type has several probed sections the LARGEST count wins;
    that is the honest reduction, because a section answering zero (the ``DVR``
    section's ``country``) is a fact about that section, not about the field.
    """
    found: dict[str, dict[str, int]] = {}
    libtype = None
    for line in PROBE.read_text(encoding="utf-8").splitlines():
        section = _SECTION.match(line)
        if section:
            libtype = section.group(1)
            continue
        answer = _ANSWER.match(line)
        if answer and libtype is not None:
            counts = found.setdefault(answer.group(1), {})
            counts[libtype] = max(counts.get(libtype, 0), int(answer.group(2)))
    return found


def test_the_probe_parse_finds_the_recorded_run_and_not_an_empty_file():
    """The guard on the guard below. A regex that stopped matching -- the file
    reformatted, the fenced block moved -- would turn the enforcement into a
    loop over nothing that passes for free, which is the one way a test like
    this fails silently. So the parse is pinned against three numbers a reader
    can find in §2 by eye, including the zero the reduction discards."""
    verdicts = _probe_verdicts()
    assert verdicts["network"] == {"show": 91}
    assert verdicts["country"] == {"movie": 63, "show": 18}
    assert verdicts["studio"]["movie"] == 824


def test_no_row_ships_on_a_library_type_the_probe_did_not_measure():
    """T2 ⚠️3, the authoring rule this wrap owes as enforcement rather than as
    prose: C3's scope is "the types ``listFilterChoices`` can enumerate TODAY"
    and it is fail-closed, but until now nothing stopped a future
    ``DYNAMIC_TYPES`` row shipping on an inference. Every row, on every library
    type its ``kinds`` claims, must have a recorded non-zero verdict in the
    probe's own captured output.

    What this catches that the note-substring check below does not: a row added
    with no probe line at all, and a row whose ``kinds`` grows a library type
    the probe never asked about. What it deliberately does NOT do is compare
    counts -- the library changes, and a test that pinned live totals would
    fail for the wrong reason. To ship a new row, re-run the probe and append
    its output to §2; what this forecloses is shipping on an inference --
    §2 itself stays hand-editable."""
    verdicts = _probe_verdicts()
    for name, row in DYNAMIC_TYPES.items():
        assert row.kinds, name
        measured = verdicts.get(name, {})
        for library_type in row.kinds:
            libtype = library_type.lower()
            assert measured.get(libtype, 0) > 0, (
                f"{name!r} ships on {library_type} with no probe verdict for it "
                f"-- measured {measured}"
            )


def test_the_two_probed_rows_ship_because_the_probe_answered():
    """C3's fail-closed rule, pinned as a test rather than left as prose:
    ``network`` and ``country`` ship ONLY because the phase's read-only probe
    measured their ``listFilterChoices`` enumerability (91 and 63 values, see
    ``docs/research/plex-dynamic-probe/README.md`` §0). If a future edit drops
    the citation from the note, the row it justifies is a row nobody checked."""
    for name in ("network", "country"):
        assert "plex-dynamic-probe" in DYNAMIC_TYPES[name].note, name
