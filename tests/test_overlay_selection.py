"""The overlay-side selection seam: the item view, the vocabulary, the refusals.

The view is deliberately NOT `collections/filter_values.py::PlexItemView`.
That one is bound by "reading a filter value never costs a Plex request",
because the collections engine walks the library once with `section.all()`
and holds PARTIAL objects. The badge pipeline holds a richer context: by the
time `apply_badges` builds this view, `media_info_from_plex` has already
called `reload()` and walked `part.streams`, and `facts` and `plex_item` are
handed in directly. So the four attributes the collections table defers or
scopes for that reason (`audio_language`, `resolution`'s movie-only kind,
`duplicate`, `network`) do not bind here -- the recon's own table, adjudication
A3b answered by construction rather than by relaxing a column. That is a
`kinds`-column fact only, not an accessor one (T1 review finding L-2):
`duplicate` and `network` still have no accessor on this view and are
refused the same way they always were. `versions` (sub-phase C2a) was the
first attribute this view genuinely gained since C1; sub-phase C2b added
three more -- `aspect` (shared verbatim with `PlexItemView`) and the two
stream-language rows, which this view answers from the `MediaInfo` the badge
pass already built rather than from the collections engine's batched
enrichment. `audio_language` is therefore no longer an example of an
attribute this view cannot supply; it is an example of the two views
answering the same question from two different reads, which is what the
verdict-level agreement pins below exist for.

What DOES bind, and is pinned below: wherever the two views both answer, they
must answer identically. A disagreement would be the same-name-different-filter
class `collections/filters.py`'s module docstring rules out.
"""
import pytest

from autoposter.badges.values import media_info_from_plex
from autoposter.collections.filter_values import PlexItemView
from autoposter.collections.filters import (
    base_language_code,
    evaluate,
    predicates as predicates_of,
)
from autoposter.overlays.selection import (
    OVERLAY_ATTRIBUTES,
    AttributeNotOnItem,
    OverlayItemView,
    compiled_condition,
    parse_condition,
    select,
)
from autoposter.overlays.schema import OverlayDefinition
from autoposter.plex.client import ItemTags

# `compiled_condition`, `parse_condition` and `select` are imported below, in
# Step 9 -- Step 6 (next) writes only the view half of this module, so
# importing all six names here would make Step 7's "7 passed" an
# `ImportError` at collection instead. Splitting the import keeps each RED
# step failing for the one reason it names.


class _Media:
    def __init__(self, resolution, aspect=None, audio=(), subtitles=()):
        self.videoResolution = resolution
        self.aspectRatio = aspect
        self.audioCodec = "eac3"
        self.audioChannels = 6
        streams = [
            type("S", (), {"streamType": 2, "languageCode": code})() for code in audio
        ] + [
            type("S", (), {"streamType": 3, "languageCode": code})() for code in subtitles
        ]
        self.parts = [type("P", (), {"file": "/movies/X/X.mkv", "streams": streams})()]


class _Item:
    """A plexapi-shaped stand-in. Plain attributes, so
    `object.__getattribute__` (which PlexItemView uses) reaches them.

    `aspects`, `audio` and `subtitles` are per-VERSION lists so a
    multi-version item can be shaped: `_Item(resolutions=("1080", "4k"),
    aspects=(None, 2.35))` is the unanalysed-first-version case adjudication
    A-2's rule answers."""

    def __init__(
        self, resolutions=("1080",), content_rating="PG-13",
        aspects=(), audio=("eng",), subtitles=(),
    ):
        padded = tuple(aspects) + (None,) * (len(resolutions) - len(aspects))
        self.media = [
            _Media(r, padded[i], audio if i == 0 else (), subtitles if i == 0 else ())
            for i, r in enumerate(resolutions)
        ]
        self.contentRating = content_rating
        self.duration = 4845912
        self.seasonNumber = None
        self.episodeNumber = None

    def reload(self):
        """No-op. `badges/values.py::media_info_from_plex` calls this
        unconditionally when `.media` is empty -- matching the real function,
        which does not gate the call on the attribute's presence -- so a fake
        with no `.media` needs somewhere for that call to land."""


class _Facts:
    """`item_facts` -- whose `content_rating` is MDBList's COMMON SENSE age
    rating, a different value space from Plex's certification (A5)."""

    critic_rating = 4.9
    audience_rating = 6.3
    content_rating = "3"


def _view(item, facts=None):
    return OverlayItemView(media_info_from_plex(item), facts=facts, plex_item=item)


def test_the_vocabulary_is_exactly_what_this_slice_supplies():
    assert OVERLAY_ATTRIBUTES == (
        "content_rating", "resolution", "versions",
        "aspect", "audio_language", "subtitle_language",
    )


def test_content_rating_comes_from_plex_not_from_item_facts():
    """Adjudication A5. The item carries BOTH values and they differ; the
    view must answer Plex's certification, not Common Sense's age."""
    item = _Item(content_rating="PG-13")
    assert _view(item, _Facts()).get("content_rating") == "PG-13"


def test_resolution_answers_every_version_not_just_the_first():
    """`MediaInfo` keeps `media[0]` only, so the view reads the item's own
    `media` list -- which `media_info_from_plex` has already reloaded, so
    this costs no request. Reading MediaInfo instead would answer ("4k",)
    for an item PlexItemView answers ("4k", "1080") for."""
    item = _Item(resolutions=("4k", "1080"))
    assert _view(item).get("resolution") == ("4k", "1080")


def test_an_item_with_no_media_has_no_resolution():
    """A show or a season carries no `media`. That is an ANSWER -- None,
    which the tag missing-value rule turns into "a positive filter excludes
    it" -- not an error."""
    item = _Item()
    item.media = []
    assert _view(item).get("resolution") is None


def test_an_attribute_outside_the_vocabulary_raises_rather_than_answering_none():
    """None means "this item has no value", which the table turns into a
    defined match result. Answering None for something we simply cannot read
    would be indistinguishable from a real answer -- the same discipline
    `filter_values.AttributeNotInListing` holds. `genre` is still outside
    after C2b; `audio_language` no longer is."""
    with pytest.raises(AttributeNotOnItem) as caught:
        _view(_Item()).get("genre")
    assert "content_rating" in str(caught.value)


def test_the_two_views_agree_on_content_rating():
    """Global Constraint 9."""
    item = _Item(content_rating="TV-MA")
    assert _view(item).get("content_rating") == PlexItemView(item).get("content_rating")


def test_the_two_views_agree_on_resolution_including_a_multi_version_item():
    """Global Constraint 9, on the attribute where a naive MediaInfo-backed
    accessor would have diverged."""
    for resolutions in (("1080",), ("4k", "1080"), ()):
        item = _Item(resolutions=resolutions)
        assert _view(item).get("resolution") == PlexItemView(item).get("resolution")


def test_versions_answers_the_media_count():
    item = _Item(resolutions=("4k", "1080"))
    assert _view(item).get("versions") == 2


def test_an_item_with_no_media_has_no_versions():
    item = _Item()
    item.media = []
    assert _view(item).get("versions") is None


def test_the_two_views_agree_on_versions():
    """Global Constraint 9 -- structural here, not just tested: both views
    import the SAME `_versions` function object from `filter_values`, the
    same way `resolution` already does."""
    for resolutions in (("1080",), ("4k", "1080"), ()):
        item = _Item(resolutions=resolutions)
        assert _view(item).get("versions") == PlexItemView(item).get("versions")


def test_a_condition_can_now_name_versions():
    group = parse_condition({"versions.gt": 1})
    [written] = predicates_of(group)
    assert written.attribute.name == "versions"
    assert written.operator == "gt"


def test_a_search_only_attribute_is_refused_with_an_overlay_appropriate_message():
    """L-4, met here: filters.py's own message for a search-only attribute
    (`duplicate` -- still search-only after this phase; only `versions` is
    new) tells a COLLECTION operator to 'move it into the plex_search
    builder's params'. An overlay `condition:` has no plex_search builder to
    move it into, so `parse_condition` re-words the remedy rather than
    repeating Kometa's advice verbatim; the attribute is refused either way."""
    with pytest.raises(ValueError) as caught:
        parse_condition({"duplicate": True})
    message = str(caught.value)
    assert "duplicate" in message
    assert "plex_search builder" not in message
    assert "condition" in message


# --- the parse half: one narrowing in front of collections/filters.py -------


def test_a_condition_parses_through_the_shared_grammar():
    group = parse_condition({"resolution.regex": "(?i)2160|4k"})
    written = list(predicates_of(group))
    assert len(written) == 1
    assert written[0].attribute.name == "resolution"
    assert written[0].operator == "regex"


def test_an_attribute_outside_the_overlay_vocabulary_is_refused_at_parse():
    """Global Constraint 10. `genre` is a perfectly good COLLECTIONS filter
    attribute; this view cannot supply it, so an operator gets told, at load,
    rather than silently getting a family that matches nothing."""
    with pytest.raises(ValueError) as caught:
        parse_condition({"genre": "Horror"})
    message = str(caught.value)
    assert "genre" in message
    assert "content_rating" in message and "resolution" in message


def test_an_attribute_no_filter_vocabulary_has_is_refused_by_the_shared_parser():
    """The refusal comes from `collections/filters.py` unchanged -- proof the
    narrowing sits IN FRONT of the shared parser rather than replacing it."""
    with pytest.raises(ValueError) as caught:
        parse_condition({"nonsense": "x"})
    assert "unknown filter attribute" in str(caught.value)


def test_an_operator_the_type_does_not_carry_is_refused():
    """`content_rating` is a `tag`; `tag` takes eq/not/regex and, since
    sub-phase C2b, the four `count_*` modifiers -- and nothing else.
    `.contains` is a `str` operator and stays refused."""
    with pytest.raises(ValueError) as caught:
        parse_condition({"content_rating.contains": "PG"})
    assert "content_rating" in str(caught.value)


def test_an_empty_condition_is_refused_rather_than_matching_everything():
    with pytest.raises(ValueError):
        parse_condition({})


def test_compiled_conditions_are_cached_by_value_not_by_definition_identity():
    """Two definitions writing the same condition compile once. The cache key
    is the condition's canonical JSON, so it cannot be fooled by key order."""
    a = OverlayDefinition(name="a", condition={"content_rating": "PG", "resolution": "4k"})
    b = OverlayDefinition(name="b", condition={"resolution": "4k", "content_rating": "PG"})
    assert compiled_condition(a) is compiled_condition(b)


def test_a_definition_with_no_condition_compiles_to_none():
    assert compiled_condition(OverlayDefinition(name="a")) is None


# --- select(): the matched subset AND the outcomes the fingerprint folds ----


def test_an_unconditioned_definition_matches_every_item_and_reports_no_outcome():
    """The pre-seam behaviour, preserved exactly: a definition with no
    condition draws on every badged item. It contributes NO outcome, which is
    what keeps an existing row-97 config's fingerprint from moving."""
    plain = OverlayDefinition(name="plain")
    matched, outcomes = select([plain], _view(_Item()))
    assert matched == [plain]
    assert outcomes == []


def test_a_matching_condition_selects_the_definition_and_records_a_true():
    fires = OverlayDefinition(name="dp", condition={"resolution.regex": "(?i)2160|4k"})
    matched, outcomes = select([fires], _view(_Item(resolutions=("4k",))))
    assert matched == [fires]
    assert outcomes == [("dp", True)]


def test_a_failing_condition_drops_the_definition_and_records_a_false():
    fires = OverlayDefinition(name="dp", condition={"resolution.regex": "(?i)2160|4k"})
    matched, outcomes = select([fires], _view(_Item(resolutions=("1080",))))
    assert matched == []
    assert outcomes == [("dp", False)]


def test_outcomes_keep_the_configured_order_not_a_sorted_one():
    """Definition ORDER is already significant to `_definitions_digest`
    (it decides which member of a group wins a tie), so the outcomes list
    carries the same order rather than a sorted one -- and two definitions
    sharing a name cannot collide the way a dict keyed on name would."""
    first = OverlayDefinition(name="z", condition={"resolution": "4k"})
    second = OverlayDefinition(name="a", condition={"resolution": "1080"})
    _, outcomes = select([first, second], _view(_Item(resolutions=("1080",))))
    assert outcomes == [("z", False), ("a", True)]


# --- Concern E: aspect and the stream languages, and their agreement pins --


def test_aspect_answers_the_media_aspect_ratio():
    assert _view(_Item(aspects=(1.78,))).get("aspect") == 1.78


def test_aspect_walks_the_versions_the_same_way_resolution_does():
    """Adjudication A-2 on the overlay side: the FIRST version carrying the
    attrib, because a `float` cannot answer the tuple `resolution` does."""
    item = _Item(resolutions=("1080", "4k"), aspects=(None, 2.35))
    assert _view(item).get("aspect") == 2.35


def test_an_unanalysed_item_has_no_aspect():
    """Plex omits `aspectRatio` until it has analysed the file. Missing, not
    0.0 -- the float missing-value rule then excludes it under every
    operator, so the item draws no aspect badge rather than the wrong one."""
    assert _view(_Item()).get("aspect") is None


def test_the_two_views_agree_on_aspect_raw_and_by_verdict():
    """`aspect` is the ONE C2b attribute whose agreement is structural: both
    views call the same `_aspect` function object, imported rather than
    copied, the way `resolution` and `versions` already are. So this is the
    one place a RAW pin is legitimate (L-1's rule is that a raw pin is a
    stronger claim than the engine needs -- not that it is always false)."""
    band = parse_condition({"aspect.gt": 1.77, "aspect.lt": 1.79})
    for aspects, resolutions in (
        ((1.78,), ("1080",)),
        ((None, 2.35), ("1080", "4k")),
        ((), ("1080",)),
    ):
        item = _Item(resolutions=resolutions, aspects=aspects)
        assert _view(item).get("aspect") == PlexItemView(item).get("aspect")
        assert evaluate(band, _view(item)) == evaluate(band, PlexItemView(item))


def test_the_stream_languages_come_from_the_media_info_the_badge_pass_built():
    """NOT from the collections view's batched enrichment: the badge pass
    already walked `part.streams` in `media_info_from_plex`, so the values
    are in hand and cost nothing.

    The value handed on is Kometa's own -- EVERY stream, across every
    `<Media>`, NOT deduplicated (`modules/plex.py:2915-2922`) -- which is
    what `MediaInfo.audio_stream_languages` /
    `MediaInfo.subtitle_stream_languages` carry. The distinct-language field
    beside them (`MediaInfo.audio_languages`, the flag badge's input) is
    deliberately NOT what this view answers, and that is pinned here rather
    than left to whichever field a reader's eye lands on first: the third
    item below has two English tracks and must answer two."""
    item = _Item(audio=("eng", "fin"), subtitles=("eng", "swe", "fin"))
    assert _view(item).get("audio_language") == ("en", "fi")
    assert _view(item).get("subtitle_language") == ("en", "sv", "fi")

    repeats = _Item(audio=("eng", "eng"), subtitles=("eng", "eng", "dan"))
    assert _view(repeats).get("audio_language") == ("en", "en")
    assert _view(repeats).get("subtitle_language") == ("en", "en", "da")
    assert media_info_from_plex(repeats).audio_languages == ("en",), (
        "the distinct field is unchanged; this view just does not read it"
    )


def test_an_item_with_no_streams_answers_empty_tuples_not_none():
    """`()` is what `MediaInfo` carries and what this view hands on
    unchanged. `filters._is_missing` reads an empty sequence as missing for a
    `tag`, and the `.count_*` operators reduce `()` and `None` alike to zero
    above that rule (Kometa's own `plex.py:2931-2932`), so the two are
    verdict-identical either way -- which is exactly why the next test pins
    VERDICTS rather than raw values."""
    item = _Item(audio=(), subtitles=())
    assert _view(item).get("audio_language") == ()
    assert _view(item).get("subtitle_language") == ()


def test_the_two_views_agree_by_verdict_on_distinct_languages_and_diverge_on_repeats():
    """**L-1, met here** (roadmap row 100's C2b sentence, reworded per the C1
    branch review). The two views source these from genuinely different
    reads -- this one from the `MediaInfo` the badge pass built off
    `part.streams`, the collections one from the batched metadata endpoint's
    `ItemTags` -- so they cannot share a function object the way `aspect`
    does. And their RAW answers differ by construction on the empty case:
    `PlexItemView` appends `or None`, this view does not. On an item whose
    stream languages are DISTINCT they are nonetheless verdict-equivalent
    under every operator, which is the claim the engine actually needs.

    **AND THE DIVERGENCE IS RECORDED, not papered over.** On an item with
    REPEATED languages the two genuinely disagree under `.count_*`, because
    `plex/client.py::_stream_languages` ends in `_uniq` while this view
    carries Kometa's undeduplicated list. Kometa's own answer is this view's
    (`plex.py:2915-2922`); the collections side's dedupe is pre-existing,
    predates C2b by two phases, is out of this sub-phase's Files block, and
    affects only the four operators C2b introduces -- so it ships as a NAMED
    divergence with a failing-if-it-changes pin rather than as a silent
    inconsistency. Closing it means widening `plex/client.py`, which is a
    separate adjudication.
    """
    for audio in (("eng", "fin"), ("eng",), ("swe",), ()):
        item = _Item(audio=audio)
        # `base_language_code`, not a `[:2]` slice: `plex/client.py`'s real
        # `_stream_languages` reads `languageTag` (already ISO 639-1), and
        # `base_language_code` is the same `langcodes`-backed reduction
        # `audio_stream_languages` uses on this view's side -- a `[:2]` slice
        # here would agree with the badge-side conversion by accident on
        # `eng`/`fin` and diverge on a code like `swe` (`sw` vs `sv`),
        # mis-deriving the fixture rather than the code under test.
        codes = tuple(base_language_code(code) for code in audio)
        tags = ItemTags(
            genres=(), labels=(), collections=(),
            audio_languages=codes, subtitle_languages=(),
        )
        for condition in (
            {"audio_language.count_gte": 2},
            {"audio_language.count_gte": 2, "audio_language.count_lt": 3},
            {"audio_language": "en"},
        ):
            group = parse_condition(condition)
            assert evaluate(group, _view(item)) == evaluate(
                group, PlexItemView(item, tags=tags)
            ), (audio, condition)

    repeats = _Item(audio=("eng", "eng"))
    deduped = ItemTags(
        genres=(), labels=(), collections=(),
        audio_languages=("en",), subtitle_languages=(),
    )
    dual = parse_condition({"audio_language.count_gte": 2})
    assert evaluate(dual, _view(repeats)) is True, "Kometa counts both tracks"
    assert evaluate(dual, PlexItemView(repeats, tags=deduped)) is False, (
        "the collections view's ItemTags are deduped upstream in "
        "plex/client.py::_stream_languages -- a recorded, pre-existing "
        "divergence, not something sub-phase C2b introduced or may fix here"
    )


def test_a_condition_can_now_name_aspect_and_the_count_modifiers():
    for condition, attribute, operator in (
        ({"aspect.gt": 1.77}, "aspect", "gt"),
        ({"audio_language.count_gte": 2}, "audio_language", "count_gte"),
        ({"subtitle_language.count_lt": 3}, "subtitle_language", "count_lt"),
    ):
        [written] = predicates_of(parse_condition(condition))
        assert (written.attribute.name, written.operator) == (attribute, operator)


def test_an_attribute_still_outside_the_vocabulary_is_refused_naming_the_six():
    """The narrowing did not become a free-for-all: `genre` is a perfectly
    good collections filter this view still cannot supply, and the refusal
    now names all six attributes it CAN."""
    with pytest.raises(ValueError) as caught:
        parse_condition({"genre": "Horror"})
    message = str(caught.value)
    assert "genre" in message
    for available in ("content_rating", "resolution", "versions",
                      "aspect", "audio_language", "subtitle_language"):
        assert available in message
