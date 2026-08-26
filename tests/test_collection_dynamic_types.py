"""The dynamic type table -- transcription checksums.

The table is Kometa's ``auto`` type map (meta.py:15-22), its
``auto_type_translation`` (meta.py:24-34), its per-type ``default_template``
(meta.py:947-959) and its per-type ``title_format`` defaults (meta.py:868,
:898, :948-959), reduced to the types phase 10a ships (adjudication C3). These
tests are what a reviewer checks the transcription against.
"""
from autoposter.collections.dynamic_types import DYNAMIC_TYPES, DynamicType
from autoposter.collections.filters import BY_NAME, parse_filters
from autoposter.collections.search_sorts import KNOWN_SORT_NAMES

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


def test_every_row_is_a_frozen_row_of_the_declared_shape():
    """The table is data, and data that can be mutated at import time by any
    caller is not a transcription any more."""
    for name, row in DYNAMIC_TYPES.items():
        assert isinstance(row, DynamicType), name
        assert row.name == name


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


def test_the_two_probed_rows_ship_because_the_probe_answered():
    """C3's fail-closed rule, pinned as a test rather than left as prose:
    ``network`` and ``country`` ship ONLY because the phase's read-only probe
    measured their ``listFilterChoices`` enumerability (91 and 63 values, see
    ``docs/research/plex-dynamic-probe/README.md`` §0). If a future edit drops
    the citation from the note, the row it justifies is a row nobody checked."""
    for name in ("network", "country"):
        assert "plex-dynamic-probe" in DYNAMIC_TYPES[name].note, name
