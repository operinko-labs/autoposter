"""Roadmap row 158: a ``filters:`` tag value, against the library's own words.

Before this, a misspelled tag value produced an EMPTY COLLECTION rather than a
message -- which is indistinguishable from a correct filter the library happens
not to match, and is the failure mode the row was filed for. The search half
(9b) has resolved values since it shipped, because a Plex search is sent a KEY
and never a written word; the client-side half compares strings at evaluation
time and had no vocabulary to compare against.

**The semantics here are Kometa's ``validate: false`` degradation applied
unconditionally, and that is a decision, not an inheritance.** Kometa RAISES
(``Plex Error: {attribute}: {value} not found``, builder.py:4433-4437) because
its own regional-rating defaults ENUMERATE the library's vocabulary
(``both_content_rating_uk.yml:20-22`` is ``dynamic_collections``) and therefore
cannot name a value the library lacks. This catalog's equivalents NAME values:
50 of the 106 shipped preset collections carry a ``filters:`` block, and seven
of the eight presets are regional content-rating lists that exist precisely to
be switched on one region at a time. A faithful refusal would turn all of them
red on the normal case. So: warn and drop per value, keep the survivors, and
fail closed only when a predicate loses everything. ``test_the_shipped_content_
rating_and_resolution_presets_still_run`` is what makes that claim structural.

The stage runs at the ENGINE, never inside ``parse_filters``: overlay
``condition:`` blocks share that parser and have no library behind them. That
half is pinned in ``tests/test_overlay_selection.py``.
"""
from types import SimpleNamespace

from plexapi.exceptions import BadRequest

from autoposter.collections.builders.base import BuilderContext, SourceClients
from autoposter.collections.builders.plex_search import LibraryTagResolver
from autoposter.collections.builders.sources_bundle import PlexSectionAccess
from autoposter.collections.catalog import preset_definitions
from autoposter.collections.engine import run_library
from autoposter.collections.filters import (
    BY_NAME,
    LANGUAGE_FOLD_ATTRIBUTES,
    parse_filters,
    predicates,
    tag_predicates,
    without_values,
)
from autoposter.config.schema import CollectionDefinition

LABEL = "autoposter"

_PRESET_KEYS = (
    "content_ratings_uk", "content_ratings_de", "content_ratings_au",
    "content_ratings_nz", "content_ratings_mal", "content_ratings_us",
    "content_ratings_us_show", "media_resolution",
)


class FakeChoice:
    def __init__(self, title, key=None):
        self.title = title
        self.key = title if key is None else key


class FakeItem:
    """A listing item, as ``PlexItemView`` reads one: partial, and answering
    only what the library listing actually carried."""

    def __init__(self, rating_key, content_rating=None, resolutions=("1080",)):
        self.ratingKey = rating_key
        self.title = "m%s" % rating_key
        self.guids = []
        self.contentRating = content_rating
        self.media = [SimpleNamespace(videoResolution=r) for r in resolutions]


class FakeStream:
    def __init__(self, stream_type, language_tag):
        self.streamType = stream_type
        self.languageTag = language_tag


class FakeMetadataItem:
    """What ``section.fetchItems`` hands back for the batched tier-2 read --
    the only place ``audio_language``'s stream tags live (``plex.client.
    _stream_languages`` reads ``<Media><Part><Stream streamType=2>``)."""

    def __init__(self, rating_key, audio_languages=()):
        self.ratingKey = rating_key
        self.media = [SimpleNamespace(parts=[SimpleNamespace(
            streams=[FakeStream(2, tag) for tag in audio_languages]
        )])]


class FakeCollection:
    def __init__(self, title, items=()):
        self.title = title
        self.ratingKey = "c-" + title
        self._live = list(items)
        self._cache = list(items)
        self._real_labels = []
        self._labels = []
        self.summary = None
        self.titleSort = None
        self._server = self
        self._session = type("Sess", (), {"put": "PUT"})()

    @property
    def labels(self):
        return self._labels

    @property
    def fields(self):
        return []

    def reload(self, **kw):
        self._cache = list(self._live)
        self._labels = self._real_labels

    def items(self):
        return list(self._cache)

    def addItems(self, items):
        self._live.extend(items)

    def removeItems(self, items):
        removed = {i.ratingKey for i in items}
        self._live = [i for i in self._live if i.ratingKey not in removed]

    def moveItem(self, item, after=None):
        pass

    def sortUpdate(self, sort=None):
        pass

    def editSortTitle(self, sortTitle, locked=True):
        self.titleSort = sortTitle

    def addLabel(self, label, locked=True):
        self._real_labels.append(type("L", (), {"tag": label})())
        self._labels = self._real_labels

    def query(self, key, method=None, **kwargs):
        pass


class FakeSection:
    """Records every ``listFilterChoices`` call, because "how many vocabulary
    reads, for which fields" is what half of these tests assert.

    ``vocabulary`` is ``{field: [titles]}``; a field it does not name answers
    with an empty list, which is a library that uses none of those values.
    ``raises`` makes the call fail, which is the outage case.
    """

    def __init__(self, items=(), vocabulary=None, raises=None, metadata=None):
        self._items = list(items)
        self._existing = {}
        self._vocabulary = dict(vocabulary or {})
        self._raises = raises
        self.choice_calls: list[tuple[str, str]] = []
        # ``{rating_key: FakeMetadataItem}`` for the tier-2 enrichment a
        # language-attribute filter needs before it ever reaches the
        # vocabulary check.
        self._metadata = dict(metadata or {})

    def all(self):
        return list(self._items)

    def fetchItems(self, ekey):
        return [self._metadata[str(k)] for k in ekey if str(k) in self._metadata]

    def collections(self, **kw):
        return list(self._existing.values())

    def createCollection(self, title, items=None, smart=False, **kw):
        collection = FakeCollection(title, items or [])
        self._existing[title] = collection
        return collection

    def listFilterChoices(self, field, libtype=None):
        self.choice_calls.append((field, libtype))
        if self._raises is not None:
            raise self._raises
        return [FakeChoice(v) for v in self._vocabulary.get(field, [])]


def _config(**overrides):
    options = {
        "ownership_label": LABEL, "apply_to_plex": True, "adopt": False,
        "adopt_from": ["Kometa"], "adopt_removes_prior_label": False,
        "protect_labels": [], "posters": False, "charts": False, "awards": False,
        "separators": False, "definitions": [], "presets": [],
        "libraries": ["Movies"], "delete_unconfigured": False, "max_deletes": 5,
        "enabled": True,
    }
    options.update(overrides)
    return SimpleNamespace(collections=SimpleNamespace(**options))


def _rated(*pairs):
    return [FakeItem(key, content_rating=rating) for key, rating in pairs]


async def test_an_unknown_genre_is_dropped_and_the_definition_still_runs(session):
    """The row's own case, in the shape the presets forced: `PG-133` is not a
    rating this library uses, so it is dropped -- and `R`, which is, still
    selects. Before row 158 the whole definition answered "no members" with no
    message at all."""
    section = FakeSection(
        _rated(("101", "R"), ("102", "PG-13")),
        vocabulary={"contentRating": ["R", "PG-13"]},
    )
    definition = CollectionDefinition(
        title="Grown Up", builder="plex_all",
        filters={"content_rating": ["R", "PG-133"]},
    )

    run = await run_library(
        session, section, "Movies", "Movie", [definition], _config(),
    )

    [result] = run.definitions
    assert result.failed is False
    assert [i.ratingKey for i in section._existing["Grown Up"]._live] == ["101"]
    [warning] = [a for a in result.actions if "PG-133" in a]
    assert "content_rating" in warning
    assert "dropped" in warning


async def test_a_value_written_in_another_case_is_not_dropped(session):
    """The four-spelling match ``LibraryTagResolver`` already does (title, key
    and both lowercased -- Kometa's ``get_search_choices``,
    plex.py:1308-1315). A check that dropped `r` because the library spells it
    `R` would break working configs to catch typos."""
    section = FakeSection(
        _rated(("101", "R")), vocabulary={"contentRating": ["R"]},
    )
    definition = CollectionDefinition(
        title="Grown Up", builder="plex_all", filters={"content_rating": "r"},
    )

    run = await run_library(
        session, section, "Movies", "Movie", [definition], _config(),
    )

    [result] = run.definitions
    assert not any("dropped" in action for action in result.actions)
    assert [i.ratingKey for i in section._existing["Grown Up"]._live] == ["101"]


async def test_a_hyphenated_rating_written_in_another_case_is_known(session):
    """M2 (branch review): ``LibraryTagResolver.known``'s non-language branch
    now compares ``.casefold()`` against the raw choices rather than
    delegating to ``__call__``'s ``.lower()``. A ``content_rating`` value is
    the realistic case where that distinction matters -- ratings carry
    hyphens and mixed case (`TV-MA`) -- so pin it directly rather than only
    through the generic single-letter case in the test above."""
    section = FakeSection(
        _rated(("101", "TV-MA")), vocabulary={"contentRating": ["TV-MA"]},
    )
    definition = CollectionDefinition(
        title="Mature", builder="plex_all", filters={"content_rating": "tv-ma"},
    )

    run = await run_library(
        session, section, "Movies", "Movie", [definition], _config(),
    )

    [result] = run.definitions
    assert not any("dropped" in action for action in result.actions)
    assert [i.ratingKey for i in section._existing["Mature"]._live] == ["101"]


async def test_a_predicate_that_loses_every_value_matches_nothing_and_says_so(
    session,
):
    """Fail CLOSED, and warned once beyond the per-value lines. Two properties
    are being pinned: the collection is not written (no items survive, which
    ``lists.py`` reads as "make no changes"), and the definition is not marked
    FAILED -- a library that simply does not use a region's ratings is the
    normal case for the seven regional presets, not an error."""
    section = FakeSection(
        _rated(("101", "R"), ("102", "PG-13")),
        vocabulary={"contentRating": ["R", "PG-13"]},
    )
    definition = CollectionDefinition(
        title="Aussie", builder="plex_all",
        filters={"content_rating": ["AU MA15+", "AU R18+"]},
    )

    run = await run_library(
        session, section, "Movies", "Movie", [definition], _config(),
    )

    [result] = run.definitions
    assert result.failed is False
    assert "Aussie" not in section._existing
    dropped = [a for a in result.actions if "dropped" in a]
    emptied = [a for a in result.actions if "matches nothing" in a]
    assert len(dropped) == 2, dropped
    assert len(emptied) == 1, emptied
    assert "content_rating" in emptied[0]


async def test_two_definitions_naming_one_attribute_read_the_vocabulary_once(
    session,
):
    """THE WIRING CHECK. The memo is the PASS's ``run_cache`` -- the same dict
    every builder's scratch lives on -- so two definitions naming
    ``content_rating`` cost ONE ``listFilterChoices``. Handing each definition
    its own cache would leave every other test here passing and turn a
    forty-definition library into forty round trips, which is exactly the cost
    9a declined to add speculatively."""
    section = FakeSection(
        _rated(("101", "R"), ("102", "PG-13")),
        vocabulary={"contentRating": ["R", "PG-13"]},
    )
    definitions = [
        CollectionDefinition(
            title="A", builder="plex_all", filters={"content_rating": "R"},
        ),
        CollectionDefinition(
            title="B", builder="plex_all", filters={"content_rating": "PG-13"},
        ),
    ]

    await run_library(session, section, "Movies", "Movie", definitions, _config())

    assert section.choice_calls == [("contentRating", "movie")]


async def test_a_filter_naming_no_tag_attribute_reads_no_vocabulary_at_all(session):
    """A filter of ints and dates pays nothing. The stage is scoped by the
    TABLE's own ``type`` column, never by config."""
    section = FakeSection(
        [FakeItem("101"), FakeItem("102")], vocabulary={"contentRating": ["R"]},
    )
    definition = CollectionDefinition(
        title="Modern", builder="plex_all", filters={"year.gte": 1990},
    )

    await run_library(session, section, "Movies", "Movie", [definition], _config())

    assert section.choice_calls == []


async def test_one_warning_per_definition_attribute_and_value_per_pass(session):
    """Facts C2's rate: one per (definition, attribute, value). The filter
    below names `PG-133` TWICE, in two predicates of one definition, and gets
    one line -- an operator reading a run summary is looking for which
    definition is wrong, not for how many places they wrote one word."""
    section = FakeSection(
        _rated(("101", "R")), vocabulary={"contentRating": ["R"]},
    )
    definition = CollectionDefinition(
        title="Twice", builder="plex_all",
        filters={"any": [{"content_rating": "PG-133"}, {"content_rating": "PG-133"}]},
    )

    run = await run_library(
        session, section, "Movies", "Movie", [definition], _config(),
    )

    [result] = run.definitions
    assert len([a for a in result.actions if "PG-133" in a and "dropped" in a]) == 1
    assert section.choice_calls == [("contentRating", "movie")]


async def test_an_unreadable_vocabulary_leaves_every_value_as_written(session):
    """The outage case, and it is deliberately NOT a refusal. The check is
    advisory: it changes no membership a correct filter would have produced,
    so a Plex hiccup must not turn 50 preset collections red. Evaluation falls
    back to exactly the pre-158 behaviour, and the pass says why -- ONCE for
    the attribute, which is a property of the RESOLVER rather than of this
    stage: ``BadRequest`` is one of the classes
    ``LibraryTagResolver._raw_choices`` wraps as ``PlexSearchUnavailable`` and
    stores in the pass's ``run_cache``, so the failure is memoised the way a
    success is. It is also what plexapi's own ``listFilterChoices`` documents
    raising for an invalid field -- which is why the fake raises it rather than
    a bare ``RuntimeError``, a class the resolver does NOT wrap and which would
    have left this test passing for the wrong reason."""
    section = FakeSection(
        _rated(("101", "R"), ("102", "PG-13")),
        vocabulary={"contentRating": ["R"]},
        raises=BadRequest("https://plex.example/library?X-Plex-Token=SECRET01"),
    )
    definition = CollectionDefinition(
        title="Grown Up", builder="plex_all",
        filters={"content_rating": ["R", "PG-13"]},
    )

    run = await run_library(
        session, section, "Movies", "Movie", [definition], _config(),
    )

    # First, because it is the assertion this test goes RED on: the stage ran,
    # exactly once for the attribute.
    assert section.choice_calls == [("contentRating", "movie")]
    [result] = run.definitions
    assert result.failed is False
    # Both values survived, so both items are in.
    assert [i.ratingKey for i in section._existing["Grown Up"]._live] == ["101", "102"]
    [note] = [a for a in result.actions if "could not be read" in a]
    assert "content_rating" in note
    # Global Constraint 7: a wrapped third-party exception is class-name-only,
    # and the wrap happens inside the resolver, which this inherits for free.
    assert "SECRET01" not in note and "plex.example" not in note


async def test_the_shipped_content_rating_and_resolution_presets_still_run(session):
    """The blast-radius gate, through the real library-pass entry point.

    All eight presets that carry a ``filters:`` block are switched on at once
    (50 collections across the catalog; on a Movie library the seven that apply
    expand to 45 definitions, since ``content_ratings_us_show`` is Show-only).
    The library uses exactly TWO of the values any of them names. Under a
    Kometa-faithful refusal every one of these would be red; under warn-and-drop
    none is, the two matching buckets are built, and the whole family costs two
    vocabulary reads."""
    section = FakeSection(
        [
            FakeItem("101", content_rating="R", resolutions=("1080",)),
            FakeItem("102", content_rating="PG-13", resolutions=("1080",)),
        ],
        vocabulary={"contentRating": ["R", "PG-13"], "resolution": ["1080"]},
    )
    config = _config(presets=list(_PRESET_KEYS))
    definitions = preset_definitions(config, "Movie")

    run = await run_library(
        session, section, "Movies", "Movie", definitions, config,
    )

    assert definitions, "the eight preset keys expanded to nothing"
    assert not any(result.failed for result in run.definitions), [
        r.title for r in run.definitions if r.failed
    ]
    # One read per (library, libtype-scope, field), for the whole family.
    assert section.choice_calls == [
        ("contentRating", "movie"), ("resolution", "movie"),
    ]
    # The buckets whose values the library DOES use were built.
    assert "R Movies" in section._existing
    assert "1080 Movies" in section._existing


# --- Fix round 1, Important 1: language rows fold to the base code ----------


async def test_a_regional_audio_language_value_matches_its_base_and_is_not_dropped(
    session,
):
    """The known-value check must agree with the evaluator's own fold
    (``filters.language_fold_key``, ``filters.py`` ~2483-2487): a REGIONAL
    written value (``pt-BR``) matches a library whose stream tag is only the
    BASE code (``pt``), exactly as ``_matches_one`` already matches it.
    Before this fix ``_known_tag_values`` asked ``LibraryTagResolver.__call__``'s
    SEARCH semantics instead -- which accepts a regional value only under its
    own exact spelling -- and dropped this one, narrowing a collection a
    correctly-spelled filter would have built (Task 2 review, Important 1)."""
    section = FakeSection(
        [FakeItem("101")],
        vocabulary={"audioLanguage": ["pt"]},
        metadata={"101": FakeMetadataItem("101", audio_languages=["pt"])},
    )
    definition = CollectionDefinition(
        title="Portuguese Audio", builder="plex_all",
        filters={"audio_language": "pt-BR"},
    )

    run = await run_library(
        session, section, "Movies", "Movie", [definition], _config(),
    )

    [result] = run.definitions
    assert result.failed is False
    assert not any("dropped" in action for action in result.actions)
    # The pre-158 membership: the item's `pt` stream is exactly what
    # `audio_language: pt-BR` would have matched with no vocabulary check at
    # all.
    assert [i.ratingKey for i in section._existing["Portuguese Audio"]._live] == ["101"]


async def test_an_audio_language_value_with_no_matching_base_is_still_dropped(session):
    """The inverse boundary: a written value whose base (``xx``) is not any
    library stream's base is still dropped -- the fold agrees with the
    evaluator in both directions, it does not turn the check into a no-op."""
    section = FakeSection(
        [FakeItem("101")],
        vocabulary={"audioLanguage": ["en"]},
        metadata={"101": FakeMetadataItem("101", audio_languages=["en"])},
    )
    definition = CollectionDefinition(
        title="Nonsense Audio", builder="plex_all",
        filters={"audio_language": "xx-YY"},
    )

    run = await run_library(
        session, section, "Movies", "Movie", [definition], _config(),
    )

    [result] = run.definitions
    assert result.failed is False
    assert "Nonsense Audio" not in section._existing
    dropped = [a for a in result.actions if "dropped" in a]
    assert len(dropped) == 1 and "xx-YY" in dropped[0]


# --- Fix round 1, Important 2: one predicate for "is this row checkable" ----


def test_tag_predicates_and_without_values_agree_on_which_rows_are_checkable():
    """The rule -- a tag-typed row under ``eq``/``not`` -- lives in one place
    (``filters._vocabulary_checked``), so ``tag_predicates`` (which rows
    ``_known_tag_values`` walks) and ``without_values`` (which rows it may
    prune) cannot answer the question differently. Proven without restating
    the rule itself: for every predicate in a filter with one eligible row
    (``content_rating``, tag/eq) and one ineligible row (``year.gte``,
    int/gte), asking ``without_values`` to drop everything changes exactly
    the predicates ``tag_predicates`` names, and none of the others
    (Task 2 review, Important 2)."""
    parsed = parse_filters({"content_rating": "R", "year.gte": 1990})
    every_predicate = tuple(predicates(parsed))
    assert len(every_predicate) == 2
    checkable = set(tag_predicates(parsed))
    assert len(checkable) == 1

    for predicate in every_predicate:
        pruned = without_values(predicate, lambda p, v: True)
        assert (pruned is not predicate) == (predicate in checkable)


# --- one shared set: the evaluator's fold and the resolver's fold agree -----


def test_known_folds_every_shared_language_attribute_to_its_evaluator_base():
    """``filters.LANGUAGE_FOLD_ATTRIBUTES`` is now the ONE definition of
    "which attributes fold to the base ISO 639-1 code" -- before this fix
    ``filters._matches_one`` (the evaluator) and ``plex_search.
    LibraryTagResolver`` (the resolver) each spelled ``{"audio_language",
    "subtitle_language"}`` a second time. Iterating the shared set itself
    here, rather than naming the two attributes, is what pins the fix: a
    third language attribute added to it later is covered by this test with
    no edit."""
    assert LANGUAGE_FOLD_ATTRIBUTES  # never silently empty

    section = FakeSection(vocabulary={
        BY_NAME[attribute].field_for("movie"): ["pt"]
        for attribute in LANGUAGE_FOLD_ATTRIBUTES
    })
    ctx = BuilderContext(
        library="Movies", library_type="Movie", config={}, run_cache={},
        sources=SourceClients(plex=PlexSectionAccess(section, lambda: {})),
    )
    resolver = LibraryTagResolver(ctx, section, "movie")

    for attribute in LANGUAGE_FOLD_ATTRIBUTES:
        # A REGIONAL written value folds to the library's own BASE stream
        # tag -- the same reduction ``_matches_one`` applies at evaluation.
        assert resolver.known(attribute, "pt-BR") is True, attribute
