"""``smart_filter``: the same query ``plex_search`` takes, owned by Plex.

One builder, one behaviour (9c decision C5): the params model is
``PlexSearchParams`` ITSELF, not a copy and not a subclass, so the vocabulary,
the refusals and the error messages cannot drift between the two builders. Two
deltas live inside that shared model rather than beside it -- the default sort
is ``random`` where ``plex_search``'s is the libtype's ``title.asc``, and the
definition's own ``sort`` is refused because on a smart collection the URI's
sort IS the display order.

A consequence worth knowing before reading a refusal: because the model is
literally ``plex_search``'s, its messages name ``plex_search`` ("a plex_search
has one base", "the plex_search vocabulary is ..."). That is deliberate -- the
vocabulary IS that one -- and the module docstring of
``src/autoposter/collections/builders/smart_filter.py`` says so.
"""
import datetime as dt
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from autoposter.collections import smart
from autoposter.collections.builders import REGISTRY
from autoposter.collections.builders.base import LibraryTypeMismatch, SmartContext
from autoposter.collections.builders.smart_filter import SmartFilterBuilder
from autoposter.config.schema import CollectionDefinition
from autoposter.db.models import ManagedCollection

LABEL = "autoposter"
SECTION_KEY = "2"


class FakeChoice:
    def __init__(self, title, key):
        self.title = title
        self.key = key


class FakeItem:
    def __init__(self, rating_key):
        self.ratingKey = rating_key


class FakeServer:
    """``queries`` holds WRITES. A write always names a ``method``; the only
    read through here is ``smart.count_matches``'s container-size-0 count
    (roadmap row 198), which names none -- answered from the section's own
    ``fetchItems`` so the count and the fetch cannot disagree, and so a
    section that refuses one refuses the other."""

    def __init__(self, machine_identifier="abc123", section=None):
        self.queries = []
        self.reads = []
        self._section = section
        self._machine_identifier = machine_identifier
        self._session = type("Sess", (), {"post": "POST", "put": "PUT"})()

    def _uriRoot(self):
        return "server://%s/com.plexapp.plugins.library" % self._machine_identifier

    def query(self, key, method=None, headers=None, **kwargs):
        if method is None:
            self.reads.append((key, headers))
            return SimpleNamespace(
                attrib={"totalSize": str(len(self._section.fetchItems(key)))}
            )
        self.queries.append((key, method))


class FakeCollection:
    def __init__(self, title, labels=(), rating_key="12345", smart=True):
        self.title = title
        self.ratingKey = rating_key
        self.smart = smart
        self.summary = None
        self.titleSort = None
        self.collectionMode = None
        self._real_labels = [type("L", (), {"tag": t})() for t in labels]
        self._labels = []
        self._real_fields = [type("F", (), {"name": "summary", "locked": False})()]
        self._fields = []
        self.labels_added = []
        self._server = FakeServer()

    @property
    def labels(self):
        return self._labels

    @property
    def fields(self):
        return self._fields

    def reload(self, **kw):
        self._labels = self._real_labels
        self._fields = self._real_fields

    def addLabel(self, label, locked=True):
        self.labels_added.append(label)
        self._real_labels.append(type("L", (), {"tag": label})())
        self._labels = self._real_labels

    def editSortTitle(self, value, locked=True):
        self.titleSort = value

    def query(self, key, method=None, **kwargs):
        self._server.query(key, method)
        self._real_fields[0].locked = True


class FakeSection:
    def __init__(self, matches=3, existing=()):
        self.key = SECTION_KEY
        self._server = FakeServer(section=self)
        self._existing = {c.title: c for c in existing}
        self._matches = matches
        self.choice_calls = []

    def collections(self, **kw):
        return list(self._existing.values())

    def collection(self, title):
        if title not in self._existing:
            self._existing[title] = FakeCollection(title, smart=True)
        return self._existing[title]

    def fetchItems(self, path, **kw):
        return [FakeItem(str(i)) for i in range(self._matches)]

    def listFilterChoices(self, field, libtype=None):
        self.choice_calls.append((field, libtype))
        return [FakeChoice("Horror", "1138"), FakeChoice("Drama", "9")]


def _config(**overrides):
    options = {
        "adopt": False, "adopt_from": [], "adopt_removes_prior_label": False,
        "protect_labels": [], "posters": False, "separators": False,
        "ownership_label": LABEL,
    }
    options.update(overrides)
    return SimpleNamespace(collections=SimpleNamespace(**options))


def _ctx(session, section, definition, *, dry_run=False, library_type="Movie", config=None):
    return SmartContext(
        session=session, section=section, library="Movies",
        library_type=library_type, label=LABEL, config=config or _config(),
        http=None, dry_run=dry_run, definition=definition, run_cache={},
    )


def _definition(**overrides):
    options = {
        "title": "Recent Horror",
        "builder": "smart_filter",
        "params": {"all": {"genre": "Horror"}},
    }
    options.update(overrides)
    return CollectionDefinition(**options)


# --- registration and the protocol ------------------------------------------


def test_it_is_registered_as_a_smart_builder():
    builder = REGISTRY["smart_filter"]
    assert builder.smart is True
    assert builder.params_model.__name__ == "PlexSearchParams"


def test_it_declares_no_titles_method():
    """C6's degenerate case. A ``cs_bucket`` definition names a FAMILY and has
    to enumerate it; a ``smart_filter`` definition names exactly one collection,
    its own title, so it declares no ``titles`` and the engine falls through --
    which is a smaller diff than a method that restates the definition's title
    back to the engine that already has it."""
    assert not hasattr(REGISTRY["smart_filter"], "titles")


# --- the sort delta (C5) ----------------------------------------------------


def test_a_definition_with_no_sort_by_sorts_random(session):
    """Kometa's smart_filter passes ``default_sort="random"``
    (modules/builder.py:1478) where a plex_search takes the libtype's
    ``title.asc``. Pinned through the oracle as config 16."""
    url = SmartFilterBuilder().search_url(
        _ctx(session, FakeSection(), _definition())
    )
    assert url == "?type=1&sort=random&genre=1138"


def test_a_written_sort_by_still_wins(session):
    url = SmartFilterBuilder().search_url(
        _ctx(session, FakeSection(), _definition(
            params={"all": {"genre": "Horror"}, "sort_by": "year.desc"}
        ))
    )
    assert url == "?type=1&sort=year%3Adesc&genre=1138"


def test_the_tag_vocabulary_is_read_once_per_pass(session):
    """The resolver is ``plex_search``'s own, so the per-pass memo is too: two
    definitions in one pass share ``run_cache`` and cost ONE
    ``listFilterChoices``."""
    section = FakeSection()
    ctx = _ctx(session, section, _definition())
    SmartFilterBuilder().search_url(ctx)
    SmartFilterBuilder().search_url(
        SmartContext(
            session=ctx.session, section=section, library=ctx.library,
            library_type=ctx.library_type, label=ctx.label, config=ctx.config,
            http=None, dry_run=False,
            definition=_definition(title="More Horror"), run_cache=ctx.run_cache,
        )
    )
    assert section.choice_calls == [("genre", "movie")]


# --- the sentinel resolve (C1, roadmap row 171 amendment) -------------------


def test_current_year_in_a_smart_filter_resolves_to_the_real_year(session):
    """The exact gap ``e869a1a`` closed for ``plex_search`` and left open here:
    ``smart_filter.search_url`` calls ``build_search_url`` with the raw
    ``params.group``, no ``resolve_search_values`` in between. Before this fix
    an unresolved ``_CurrentYear`` reached ``search_url``'s plain ``str(value)``
    fallback and rendered its own ``repr()`` -- and because this URI is not a
    transient query but the string Plex STORES as the smart collection's
    filter, that wrong value would be written into the operator's library and
    keep mis-selecting until noticed."""
    url = SmartFilterBuilder().search_url(
        _ctx(session, FakeSection(), _definition(
            params={"all": {"genre": "Horror", "year": "current_year"}}
        ))
    )
    year = dt.datetime.now().year
    assert f"year={year}" in url
    assert "_CurrentYear" not in url


def test_today_in_a_smart_filter_resolves_to_a_bare_date(session):
    """The same gap's other shape: an unresolved ``_Today`` has no
    ``search_url.py`` branch of its own and previously raised
    ``AttributeError`` (``_Today`` has no ``isoformat``) rather than silently
    persisting a wrong value -- still a bug, since a smart_filter definition
    that used ``today`` in a date predicate could never build at all."""
    today = dt.date.today()
    url = SmartFilterBuilder().search_url(
        _ctx(session, FakeSection(), _definition(
            params={"all": {"genre": "Horror", "release.after": "today"}}
        ))
    )
    assert (
        f"originallyAvailableAt%3E%3E={today.isoformat()}" in url
        or f"originallyAvailableAt%3E%3E={(today - dt.timedelta(days=1)).isoformat()}" in url
    )


# --- the load-time refusals (C7, and roadmap row 140) -----------------------


def test_sort_is_refused_on_a_smart_filter_definition():
    """C7. On a smart collection the URI's sort IS the display order, so a
    definition-level ``sort`` and ``params.sort_by`` would be two knobs steering
    one behaviour. The message has to point at the one that works."""
    with pytest.raises(ValueError) as caught:
        _definition(sort="release")
    assert "sort_by" in str(caught.value)


def test_summary_is_accepted_on_a_smart_filter_definition():
    """The other half of C7: a smart_filter definition names ONE collection, and
    that collection's summary is written by the reconciler exactly as the
    Common Sense family's is. Refusing it would be refusing something that
    works."""
    assert _definition(summary="Everything that scared us lately").summary


def test_tmdb_summary_is_accepted_on_a_smart_filter_definition():
    """Roadmap row 186. The original refusal reasoned about a FAMILY of
    collections with no single summary -- true of cs_bucket, false of
    smart_filter, which names exactly one collection whose summary the
    reconciler writes. Mechanically nothing was ever in the way; it was
    refused only because 9c's C7 had not adjudicated it."""
    assert _definition(tmdb_summary=603).tmdb_summary == 603


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("limit", 10),
        ("sync_mode", "append"),
        ("item_label", ["Scary"]),
        ("filters", {"year.gte": 2000}),
    ],
)
def test_the_membership_knobs_are_refused(field, value):
    with pytest.raises(ValueError) as caught:
        _definition(**{field: value})
    assert field in str(caught.value)
    assert "smart_filter" in str(caught.value)


def test_cs_bucket_now_refuses_summary_and_sort_too():
    """Roadmap row 140, closed by the per-builder split. Neither was ever read
    for a ``cs_bucket`` definition -- the reconciler applies only the
    builder-derived per-bucket summary, and a family of smart collections has no
    single membership to order -- so both silently no-opped. This is load-time
    breaking for a config that sets either today, which is what the row says."""
    with pytest.raises(ValueError) as summary_refusal:
        CollectionDefinition(title="Ages", builder="cs_bucket", summary="hi")
    with pytest.raises(ValueError) as sort_refusal:
        CollectionDefinition(title="Ages", builder="cs_bucket", sort="release")
    # The messages, not only the classes: this is the test that closes the row,
    # and a refusal that stopped naming the field or the builder would still
    # raise while telling the operator nothing about which knob to move.
    for refusal, field in ((summary_refusal, "summary"), (sort_refusal, "sort")):
        assert field in str(refusal.value)
        assert "cs_bucket" in str(refusal.value)


def test_a_smart_builder_declaring_no_refusals_is_refused_outright(monkeypatch):
    """The validator's own load-bearing guarantee, which no registered builder
    exercises because both declare a table.

    Defaulting a missing table to "refuse nothing" is exactly how a
    silently-ignored setting ships: the smart builder that forgot the attribute
    is the one whose definitions most need it, since nobody has yet thought
    about which of the membership knobs it cannot apply.
    """
    class Forgetful:
        type_name = "forgetful_smart"
        smart = True

    monkeypatch.setitem(REGISTRY, "forgetful_smart", Forgetful())
    with pytest.raises(ValueError) as caught:
        CollectionDefinition(title="Anything", builder="forgetful_smart")

    assert "refused_definition_fields" in str(caught.value)
    assert "forgetful_smart" in str(caught.value)


def test_the_shipped_default_definition_still_loads():
    """The refusal above is only safe because nothing shipped sets either field:
    ``sources.default_definitions`` builds the family with title and builder and
    nothing else (``src/autoposter/collections/sources.py:87``)."""
    assert CollectionDefinition(
        title="Common Sense age ratings", builder="cs_bucket"
    ).sort == "custom"


# --- the stored uri is interpolated, not pinned -----------------------------


def test_the_stored_uri_carries_each_servers_own_identifier_and_section():
    """Controller amendment 1, closing the T1 oracle's known blind spot.

    Every oracle string and every Task 2 assertion reuses ONE
    ``machineIdentifier`` (``abc123``) and ONE section key (``"2"``), so an
    implementation that hard-coded either would still be byte-identical to
    Kometa's answer on all sixteen configs. Varying both at once is what
    proves the two are read from the server and the section at run time.
    """
    query = "?type=1&sort=random&genre=1138"
    one = smart.smart_filter_uri(FakeServer("abc123"), "2", query)
    two = smart.smart_filter_uri(FakeServer("zzz999"), "7", query)

    assert one == (
        "server://abc123/com.plexapp.plugins.library/library/sections/2/all" + query
    )
    assert two == (
        "server://zzz999/com.plexapp.plugins.library/library/sections/7/all" + query
    )
    assert "zzz999" not in one
    assert "/sections/7/" not in one
    assert "abc123" not in two
    assert "/sections/2/" not in two


# --- apply ------------------------------------------------------------------


async def test_apply_creates_the_collection_and_writes_a_row(session):
    section = FakeSection(matches=3)
    definition = _definition()
    actions = await SmartFilterBuilder().apply(_ctx(session, section, definition))

    assert any("created" in action for action in actions)
    key, method = section._server.queries[0]
    assert key.startswith("/library/collections?sectionId=2&smart=1")
    assert "genre%3D1138" in key
    assert method == "POST"
    row = (
        await session.execute(
            select(ManagedCollection).where(ManagedCollection.title == "Recent Horror")
        )
    ).scalar_one_or_none()
    kind = row.kind
    assert kind == "smart"


async def test_a_filter_matching_nothing_costs_this_definition_and_no_other(session):
    """C8's refusal reaches the operator as this definition's action string, not
    as an exception. The engine does NOT wrap a smart builder (engine.py:341-344,
    on the grounds that anything it raises is a Plex write failing), so an
    escaping refusal would reach ``reconcile_libraries``' per-library rollback
    and undo the OTHER definitions' work in the same library. A too-narrow
    filter is this definition's problem alone."""
    section = FakeSection(matches=0)
    actions = await SmartFilterBuilder().apply(_ctx(session, section, _definition()))
    assert len(actions) == 1
    assert actions[0].startswith("refused 'Recent Horror'")
    assert "widen" in actions[0].lower()
    assert section._server.queries == []


async def test_an_unknown_tag_value_costs_this_definition_and_no_other(session):
    """The same containment for the other refusal an operator can cause: a
    genre this library does not have. 9b refuses rather than building a query
    that matches nothing (roadmap row 158)."""
    section = FakeSection()
    actions = await SmartFilterBuilder().apply(
        _ctx(session, section, _definition(params={"all": {"genre": "Polka"}}))
    )
    assert len(actions) == 1
    assert actions[0].startswith("refused 'Recent Horror'")
    assert "Polka" in actions[0]


async def test_a_show_only_sort_against_a_movie_library_is_contained(session):
    actions = await SmartFilterBuilder().apply(
        _ctx(session, FakeSection(), _definition(
            params={"all": {"genre": "Horror"}, "sort_by": "episode_added.desc"}
        ))
    )
    assert len(actions) == 1
    assert actions[0].startswith("refused 'Recent Horror'")
    assert "libraries:" in actions[0]


async def test_no_refusal_string_can_carry_a_plex_token(session):
    """Every refusal this builder reports is one it constructed, or one wrapped
    class-name-only. The action strings go to the run report, the logs page and
    the notifier, so a tokenised URL reaching one of them is a leak with three
    audiences."""
    class Exploding(FakeSection):
        def fetchItems(self, path, **kw):
            raise RuntimeError("https://plex.example:32400/x?X-Plex-Token=SECRET")

    actions = await SmartFilterBuilder().apply(
        _ctx(session, Exploding(), _definition())
    )
    assert len(actions) == 1
    assert "SECRET" not in actions[0]
    assert "X-Plex-Token" not in actions[0]
    assert "RuntimeError" in actions[0]


# --- the reconciler's own refusals, caught here (controller amendment 2) ----


async def test_the_reconcilers_empty_refusal_is_returned_not_raised(session, monkeypatch):
    """Controller amendment 2, half one. ``SmartFilterMatchedNothing`` can be
    raised from either of the reconciler's two paths -- create and update -- and
    the update one is reached long after this builder's own probe would have
    been satisfied. Whichever path raises it, the engine's smart dispatch
    (``engine.py:341-345``) is deliberately unwrapped and an escaping exception
    reaches ``service.py:392-395``, which rolls back the WHOLE library's pass.
    One operator's over-narrow filter must not cost every other definition in
    that library its run."""
    async def refuse(*args, **kwargs):
        raise smart.SmartFilterMatchedNothing(
            "this search matches nothing in this library right now. Widen the "
            "filter, or narrow the definition with `libraries:`"
        )

    monkeypatch.setattr(
        "autoposter.collections.builders.smart_filter.reconcile_smart_collection",
        refuse,
    )

    actions = await SmartFilterBuilder().apply(
        _ctx(session, FakeSection(), _definition())
    )
    assert len(actions) == 1
    assert actions[0].startswith("refused 'Recent Horror'")
    assert "widen" in actions[0].lower()


async def test_the_reconcilers_transport_refusal_is_returned_not_raised(
    session, monkeypatch
):
    """Controller amendment 2, half two. ``SmartCollectionUnavailable`` is
    already class-name-only by construction (``smart.py``: it never carries the
    Plex exception's own message, which can hold a tokenised URL), so containing
    it here is the whole of this builder's job -- reporting what it says, adding
    nothing, and letting the rest of the library's definitions run."""
    async def refuse(*args, **kwargs):
        raise smart.SmartCollectionUnavailable(
            "Plex would not answer this smart filter: ConnectionError"
        )

    monkeypatch.setattr(
        "autoposter.collections.builders.smart_filter.reconcile_smart_collection",
        refuse,
    )

    actions = await SmartFilterBuilder().apply(
        _ctx(session, FakeSection(), _definition())
    )
    assert len(actions) == 1
    assert actions[0].startswith("refused 'Recent Horror'")
    assert "ConnectionError" in actions[0]


# --- search-tail E-2: builder_level types the stored query (roadmap rows 173/179)


def test_an_episode_builder_level_types_the_smart_query_at_four(session):
    """The same field, the same split, on the builder whose query Plex STORES.
    ``smart_filter``'s default sort is ``random`` (Kometa's own call site,
    modules/builder.py:1478), which is in every one of the four matrices, so
    the only thing that moves here is the ``type=`` byte and the scoping."""
    definition = _definition(
        params={"all": {"episode_title.begins": "Pilot"}}, builder_level="episode",
    )
    url = SmartFilterBuilder().search_url(
        _ctx(session, FakeSection(), definition, library_type="Show")
    )
    assert url.startswith("?type=4&")
    assert "episode.title%3C=Pilot" in url


def test_a_non_item_level_on_a_movie_library_refuses_in_search_url(session):
    definition = _definition(
        params={"all": {"genre": "Horror"}}, builder_level="episode",
    )
    with pytest.raises(LibraryTypeMismatch) as error:
        SmartFilterBuilder().search_url(
            _ctx(session, FakeSection(), definition, library_type="Movie")
        )
    assert "episode" in str(error.value) and "Movie library" in str(error.value)


async def test_the_level_reaches_the_reconciler(session, monkeypatch):
    """``apply`` is what turns the level into a Plex ``type=`` on the POST, and
    the reconciler is where that byte is chosen. Pinned as the argument rather
    than as the byte -- the byte itself is
    ``tests/test_collection_smart.py``'s."""
    seen = {}

    async def fake_reconcile(*args, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(
        "autoposter.collections.builders.smart_filter.reconcile_smart_collection",
        fake_reconcile,
    )
    definition = _definition(
        params={"all": {"episode_title.begins": "Pilot"}}, builder_level="episode",
    )
    await SmartFilterBuilder().apply(
        _ctx(session, FakeSection(), definition, library_type="Show")
    )
    assert seen["level"] == "episode"


def test_smart_filter_refuses_folder_location_by_name(session):
    """Roadmap row 176, ruling C5. ``smart_definition_hash``
    (collections/smart.py:225) hashes the BUILT URL, and for every other
    attribute that URL is a pure function of the config. ``folder_location``'s
    field is discovered from the server, so a smart collection naming it would
    have a stored hash that is a function of the SERVER too -- and a Plex-side
    rename of the filter would silently re-PUT every definition naming it
    (``update_smart_collection``: replacing the stored filter is the only edit a
    smart collection has, there is no partial one).

    Refused rather than shipped-and-disclosed, so no definition hash in this
    service is ever a function of anything but the config. The refusal is
    contained to this definition like every other ``REFUSALS`` member, and it
    names the builder that CAN answer the question."""
    from autoposter.collections.search_url import SearchAttributeNotAvailable

    ctx = _ctx(session, FakeSection(), _definition(
        params={"all": {"folder_location": "/mnt/media/Movies"}}
    ))

    with pytest.raises(SearchAttributeNotAvailable) as error:
        SmartFilterBuilder().search_url(ctx)

    message = str(error.value)
    assert "folder_location" in message
    assert "plex_search" in message
    assert "discovered" in message
