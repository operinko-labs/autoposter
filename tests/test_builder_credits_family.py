"""``credits_family``: one smart collection per sufficiently-credited person.

The fourth family shape, and the one that needed a new one: ``cs_bucket``
manages smart collections from a static table, ``dynamic`` smart collections
from a PLEX enumeration, ``facts_family`` list collections from a DATABASE
enumeration -- and this manages smart collections from a database enumeration,
because the question these packs ask ("everyone with at least `depth`
appearances, capped to the `limit` most-credited") needs COUNTS, and
``listFilterChoices`` answers values and never counts (roadmap row 194).

The harness is ``tests/test_builder_dynamic``'s -- the same fake section with
``listFilterChoices``, the same real reconcile against it -- plus Task 4's
credits seeding, because that is exactly what this builder is: the dynamic
family's write path over the credits cache's enumeration.

**Nothing here asserts a complete cast, and nothing may.** ``enumerate_credits``
returns a FLOOR, not a census: the phase-B probe measured a server-side cap of
200 ``Role`` children per item, so a person whose every appearance is in a
>200-role cast can be absent from the ranking entirely. The counts seeded below
are what the cache holds, which is the only thing this builder can be right
about.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from autoposter.collections.builders import REGISTRY
from autoposter.collections.builders.base import SmartContext
from autoposter.collections.builders.credits_family import (
    CreditsFamilyBuilder,
    CreditsFamilyParams,
    family_label,
    generated_titles,
)
from autoposter.config.schema import CollectionDefinition
from autoposter.db.models import ItemCredit, ManagedCollection, MediaItem

LABEL = "autoposter"
SECTION_KEY = "2"


class FakeChoice:
    def __init__(self, key, title):
        self.key = key
        self.title = title


class FakeItem:
    def __init__(self, rating_key):
        self.ratingKey = rating_key


class FakeServer:
    """``queries`` holds WRITES. A write always names a ``method``; the only
    read through here is ``smart.count_matches``'s container-size-0 count
    (roadmap row 198), which names none -- answered from the section's own
    ``fetchItems`` so the count and the fetch cannot disagree, and so a
    person Plex cannot resolve still refuses the same way."""

    def __init__(self, section=None):
        self.queries = []
        self.reads = []
        self._section = section
        self._session = type("Sess", (), {"post": "POST", "put": "PUT"})()

    def _uriRoot(self):
        return "server://abc123/com.plexapp.plugins.library"

    def query(self, key, method=None, headers=None, **kwargs):
        if method is None:
            self.reads.append((key, headers))
            return SimpleNamespace(
                attrib={"totalSize": str(len(self._section.fetchItems(key)))}
            )
        self.queries.append((key, method))


class FakeCollection:
    def __init__(self, title, labels=(), rating_key="1"):
        self.title = title
        self.ratingKey = rating_key
        self.smart = True
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
    """Answers ``listFilterChoices`` with a person vocabulary and every search
    with three items.

    ``matches_nothing`` is the set of tag KEYS whose query comes back empty --
    which is how a per-person write failure is provoked without reaching into
    the reconciler: ``require_matches`` refuses at zero.
    """

    def __init__(self, people=("Ann", "Bob", "Cy"), section_type="movie",
                 matches_nothing=()):
        self.key = SECTION_KEY
        self.type = section_type
        self._server = FakeServer(self)
        self._existing = {}
        # The key is deliberately NOT the name: a resolver that returned the
        # written word unchanged would pass every assertion below if they were
        # equal, and Plex's own keys are numeric.
        self._choices = [
            FakeChoice("%d" % (100 + index), name)
            for index, name in enumerate(people)
        ]
        self._matches_nothing = set(matches_nothing)
        self.choice_calls = []
        self.fetched = []

    def collections(self, **kw):
        return list(self._existing.values())

    def collection(self, title):
        if title not in self._existing:
            self._existing[title] = FakeCollection(title)
        return self._existing[title]

    def fetchItems(self, path, **kw):
        self.fetched.append(path)
        if any("=%s&" % key in path or path.endswith("=%s" % key)
               for key in self._matches_nothing):
            return []
        return [FakeItem("1"), FakeItem("2"), FakeItem("3")]

    def listFilterChoices(self, field, libtype=None):
        self.choice_calls.append((field, libtype))
        return list(self._choices)


def _config(**overrides):
    options = {
        "adopt": False, "adopt_from": [], "adopt_removes_prior_label": False,
        "protect_labels": [], "posters": False, "separators": False,
        "ownership_label": LABEL,
    }
    options.update(overrides)
    from types import SimpleNamespace

    return SimpleNamespace(collections=SimpleNamespace(**options))


def _ctx(session, section, definition, *, dry_run=False, library="Movies",
         library_type="Movie", config=None, listing=None):
    return SmartContext(
        session=session, section=section, library=library,
        library_type=library_type, label=LABEL, config=config or _config(),
        http=None, dry_run=dry_run, definition=definition, run_cache={},
        listing=listing,
    )


def _definition(**overrides):
    options = {
        "title": "Top actors",
        "builder": "credits_family",
        "params": {"type": "actor"},
    }
    options.update(overrides)
    return CollectionDefinition(**options)


async def _seed(session, counts, *, kind="actor", library="Movies",
                item_kind="movie", unvisited=0):
    """``counts`` is ``{person: how many items credit them}``.

    Items are created up to the largest count and each person is credited on
    the first N of them, so the enumeration's order is decided by the counts
    alone. ``unvisited`` adds items the credits scan has NOT stamped -- the
    other half of every coverage number this builder reports.
    """
    import datetime as dt

    total = max(counts.values(), default=0)
    items = []
    for index in range(1, total + 1):
        item = MediaItem(
            rating_key=str(index), library=library, kind=item_kind,
            title="Item %d" % index,
            credits_attempted_at=dt.datetime(2026, 8, 29, 12, 0),
        )
        session.add(item)
        items.append(item)
    for index in range(total + 1, total + 1 + unvisited):
        session.add(MediaItem(
            rating_key=str(index), library=library, kind=item_kind,
            title="Unvisited %d" % index,
        ))
    await session.flush()
    for person, count in counts.items():
        for item in items[:count]:
            session.add(ItemCredit(item_id=item.id, kind=kind, person=person))
    await session.flush()
    return items


# --- registration and the protocol -------------------------------------------


def test_the_builder_is_registered_as_a_smart_builder():
    builder = REGISTRY["credits_family"]
    assert builder.smart is True
    assert isinstance(builder, CreditsFamilyBuilder)
    assert builder.params_model is CreditsFamilyParams
    assert not hasattr(builder, "titles"), (
        "this family's titles are the CREDITS CACHE's -- enumerating them "
        "offline is impossible and enumerating them online would put a "
        "database read inside _titles_must_not_collide, which runs on every "
        "config write"
    )


def test_the_builder_offers_the_sweep_its_generated_record_by_protocol():
    """``family_label`` plus ``generated_titles`` is the whole protocol
    ``builders/dynamic.py`` names, and the engine reads both off the REGISTRY
    entry -- so joining the sweep is two methods and no engine change."""
    builder = REGISTRY["credits_family"]
    assert callable(getattr(builder, "family_label", None))
    assert callable(getattr(builder, "generated_titles", None))
    assert builder.generated_titles({}, _definition()) is None


def test_the_family_label_prefix_is_this_familys_own():
    """Three family sweeps now share one engine, and each enumerates its
    members by LABEL. A shared prefix would let one family's sweep delete
    another's collections."""
    from autoposter.collections.builders import credits_family, dynamic
    from autoposter.collections.builders import facts_family as facts

    prefixes = {
        credits_family.FAMILY_LABEL_PREFIX,
        dynamic.FAMILY_LABEL_PREFIX,
        facts.FAMILY_LABEL_PREFIX,
    }
    assert len(prefixes) == 3
    assert family_label(_definition()) == "autoposter-credits: Top actors"


# --- the params, at config load ----------------------------------------------


def test_an_unknown_type_refuses_at_load_and_lists_the_credit_kinds():
    with pytest.raises(ValueError) as refusal:
        _definition(params={"type": "composer"})
    assert "composer" in str(refusal.value)
    assert "actor" in str(refusal.value)


def test_depth_and_limit_refuse_a_value_below_one():
    """Neither has a no-op sentinel: ``depth: 0`` would build a collection for
    everyone the cache has ever seen, and ``limit: 0`` would build none."""
    for params in ({"type": "actor", "depth": 0}, {"type": "actor", "limit": 0}):
        with pytest.raises(ValueError):
            _definition(params=params)


def test_a_title_format_that_names_no_key_refuses_at_load():
    """The siblings' rule, and this family needs it for their reason: without
    ``<<key_name>>`` every person in the family would be given one name."""
    with pytest.raises(ValueError, match="key_name"):
        _definition(params={"type": "actor", "title_format": "A <<library_type>>"})


def test_an_unknown_sort_refuses_at_load():
    """``DynamicParams``' own gate, and worth having twice: a sort Plex has no
    column for would otherwise refuse ONCE PER PERSON at run time, twenty-five
    identical refusals for one typo."""
    with pytest.raises(ValueError, match="sort_by"):
        _definition(params={"type": "actor", "sort_by": ["popularity.desc"]})


def test_the_membership_knobs_refuse_on_this_definition():
    """``refused_definition_fields``, exercised rather than read: this
    definition names a FAMILY and Plex owns each member's membership, so a
    ``summary:`` or a ``filters:`` block here could only be silently ignored."""
    for field, value in (
        ("summary", "One person"), ("sort", "title.asc"), ("limit", 10),
        ("sync_mode", "append"), ("item_label", ["Seen"]),
        ("tmdb_summary", True), ("filters", {"year.gte": 2000}),
    ):
        with pytest.raises(ValueError, match=field):
            _definition(**{field: value})


# --- the enumeration: depth, limit, and what the family reports ---------------


async def test_depth_gates_and_limit_caps_the_most_credited(session):
    """The two knobs are upstream's own ``data:`` block for these packs, and
    they compose in this order: ``depth`` is the floor (how many appearances
    make a person worth a collection) and ``limit`` caps how many of the people
    who cleared it get one -- most-credited first, which is
    ``enumerate_credits``' own order.

    The narrowing is REPORTED rather than silent: an operator who asked for a
    family and got one collection must be able to see that two people cleared
    the floor and the cap took one.
    """
    await _seed(session, {"Ann": 6, "Bob": 5, "Cy": 4})
    section = FakeSection()
    definition = _definition(params={"type": "actor", "depth": 5, "limit": 1})

    actions = await REGISTRY["credits_family"].apply(
        _ctx(session, section, definition)
    )

    assert list(section._existing) == ["Ann"]
    assert any(
        "2 actor(s) meet depth 5" in one and "built the 1 most-credited" in one
        for one in actions
    ), actions
    assert any("created 'Ann'" in one for one in actions), actions


async def test_the_membership_is_the_persons_own_plex_tag_search(session):
    """Roadmap row 194's open question, answered in the emitted bytes.

    Each collection is a Plex search on the person's TAG -- the library's own
    credit data, upstream's semantic -- and NOT a TMDb filmography, which
    credits people this library's files do not name. The written name resolves
    through the same ``listFilterChoices`` vocabulary every tag search uses, so
    ``Ann`` reaches Plex as the key Plex knows her by; the base is ``any``,
    the libtype is the library's, and there is no ``limit=`` because a person's
    collection is all of their films, not the first fifty.
    """
    await _seed(session, {"Ann": 2, "Bob": 1})
    section = FakeSection()

    await REGISTRY["credits_family"].apply(_ctx(
        session, section, _definition(params={"type": "actor", "depth": 1}),
    ))

    assert section.choice_calls == [("actor", "movie")]
    assert section.fetched == [
        "/library/sections/2/all?type=1&sort=titleSort&push=1&actor=100&pop=1",
        "/library/sections/2/all?type=1&sort=titleSort&push=1&actor=101&pop=1",
    ]


async def test_a_show_library_asks_plex_at_the_show_scope(session):
    """``actor`` is the one credit kind a show library answers (probe D7), and
    ``show_translation`` re-scopes it to ``show.actor`` (plex.py:168-193). So
    the choices call is scoped to ``show`` and the query field is the show's --
    a bare ``actor`` here would be a query Plex answers with the wrong set."""
    await _seed(session, {"Ann": 2}, library="TV", item_kind="show")
    section = FakeSection(people=("Ann",), section_type="show")

    await REGISTRY["credits_family"].apply(_ctx(
        session, section, _definition(params={"type": "actor", "depth": 1}),
        library="TV", library_type="Show",
    ))

    assert section.choice_calls == [("actor", "show")]
    assert section.fetched == [
        "/library/sections/2/all?type=2&sort=titleSort&push=1&show.actor=100&pop=1"
    ]


async def test_the_three_crew_types_refuse_a_show_library_before_any_read(session):
    """``director``/``writer``/``producer`` are movie-only end to end: their
    SEARCH is in Kometa's ``movie_only_searches`` and their FILTER scope is the
    movie/episode key. The probe measured the third reason (D7): a show section
    enumerates them EMPTY rather than refusing, so a family that got as far as
    querying would report "nobody qualifies" for a library that can never
    answer.

    Refused above every read -- the session is ``None`` here, so the refusal
    that arrives proves the library-type gate ran BEFORE the database was
    touched, which is ``facts_family``'s own ordering.
    """
    from autoposter.collections.builders.base import LibraryTypeMismatch

    for kind in ("director", "writer", "producer"):
        section = FakeSection(section_type="show")
        with pytest.raises(LibraryTypeMismatch, match=kind):
            await REGISTRY["credits_family"].apply(_ctx(
                None, section, _definition(params={"type": kind}),
                library="TV", library_type="Show",
            ))
        assert section.choice_calls == []


async def test_a_movie_library_without_a_session_says_so(session):
    """The guard below the library-type gate: this builder reads this service's
    own database, and a caller that handed it no session gets that sentence
    rather than an ``AttributeError`` from inside a savepoint."""
    with pytest.raises(ValueError, match="database"):
        await REGISTRY["credits_family"].apply(
            _ctx(None, FakeSection(), _definition())
        )


async def test_an_empty_enumeration_refuses_with_the_coverage_numbers(session):
    """A family with nobody in it is not an empty family -- it is a scan that
    has not got there yet, and the difference is the whole reason
    ``credits_coverage`` exists. The refusal carries both numbers and names the
    setting that closes the gap, so an operator can tell "nobody qualifies"
    from "we have not looked".
    """
    await _seed(session, {"Ann": 2}, unvisited=3)
    section = FakeSection()

    actions = await REGISTRY["credits_family"].apply(_ctx(
        session, section, _definition(params={"type": "actor", "depth": 5}),
    ))

    assert section._existing == {}
    assert section.choice_calls == []
    assert len(actions) == 1
    assert "visited 2 of 5 item(s)" in actions[0], actions
    assert "credits_scan_days" in actions[0], actions
    assert "depth" in actions[0], actions


async def test_a_fanout_past_max_collections_refuses_with_both_numbers(session):
    """Refusal, not truncation -- facts C5 and roadmap row 194's own law. A cap
    that silently built the first N would be the same surprise the operator set
    the cap to avoid, so nothing is created at all and the message carries the
    number asked for AND the number allowed."""
    await _seed(session, {"Ann": 3, "Bob": 3, "Cy": 3})
    section = FakeSection()

    actions = await REGISTRY["credits_family"].apply(_ctx(
        session, section,
        _definition(params={"type": "actor", "depth": 1, "max_collections": 2}),
    ))

    assert section._existing == {}
    assert len(actions) == 1
    assert "3 collections" in actions[0] and "`max_collections` is 2" in actions[0]


async def test_an_all_excluded_family_says_so_rather_than_building_nothing(session):
    """``exclude`` emptying the family is an operator's own doing and reads
    identically to a broken enumeration unless it is reported."""
    await _seed(session, {"Ann": 3, "Bob": 3})
    section = FakeSection()

    actions = await REGISTRY["credits_family"].apply(_ctx(
        session, section, _definition(params={
            "type": "actor", "depth": 1, "exclude": ["Ann", "Bob"],
        }),
    ))

    assert section._existing == {}
    assert any("every eligible actor was excluded" in one for one in actions)


# --- the sweep's record -------------------------------------------------------


async def test_the_record_is_seeded_before_any_write_and_survives_one_failing(session):
    """``builders/dynamic.py``'s law, and the reason it is a law: the record is
    what the delete sweep reads as "these are the collections this family
    builds". Seeded with every DERIVED title before a single reconcile, so a
    person whose Plex write refuses keeps their collection -- a failed write is
    not an operator narrowing their family.

    ``Bob``'s query matches nothing here, which the reconciler refuses; the
    refusal is contained to Bob, ``Ann`` and ``Cy`` are still managed, and all
    three titles are still in the record.
    """
    await _seed(session, {"Ann": 3, "Bob": 3, "Cy": 3})
    section = FakeSection(matches_nothing=("101",))
    definition = _definition(params={"type": "actor", "depth": 1})
    ctx = _ctx(session, section, definition)

    actions = await REGISTRY["credits_family"].apply(ctx)

    assert sorted(section._existing) == ["Ann", "Cy"]
    assert any(one.startswith("refused 'Bob'") for one in actions), actions
    assert generated_titles(ctx.run_cache, definition) == {"Ann", "Bob", "Cy"}


async def test_a_person_plex_cannot_resolve_is_contained_to_that_one_key(session):
    """The credits cache and the library's tag vocabulary are two sources, and
    they can disagree: a person the scan recorded before a Plex rescan renamed
    the tag resolves to nothing. That is one person's problem -- the other
    collections are still managed -- and the refusal names them."""
    await _seed(session, {"Ann": 3, "Zed": 3})
    section = FakeSection(people=("Ann",))
    definition = _definition(params={"type": "actor", "depth": 1})
    ctx = _ctx(session, section, definition)

    actions = await REGISTRY["credits_family"].apply(ctx)

    assert list(section._existing) == ["Ann"]
    assert any(one.startswith("refused 'Zed'") for one in actions), actions
    assert any("Zed" in one for one in actions)
    assert generated_titles(ctx.run_cache, definition) == {"Ann", "Zed"}


async def test_generated_titles_is_none_when_the_family_refused_at_family_level(session):
    """Fail-closed, ``builders/dynamic.py``'s contract verbatim: ``None`` means
    "this family never got as far as deciding", and the engine must not read a
    pass that refused as an operator who narrowed -- one transient refusal
    would otherwise take every collection in the family."""
    await _seed(session, {"Ann": 2}, unvisited=3)
    definition = _definition(params={"type": "actor", "depth": 5})
    ctx = _ctx(session, FakeSection(), definition)

    await REGISTRY["credits_family"].apply(ctx)

    assert generated_titles(ctx.run_cache, definition) is None


# --- the ordinary write path --------------------------------------------------


async def test_a_family_labels_every_collection_it_creates(session):
    """Family membership is a LABEL, which is what the sweep enumerates. Never
    an offline title list -- this family's titles are the cache's."""
    await _seed(session, {"Ann": 3, "Bob": 3})
    section = FakeSection()
    definition = _definition(params={"type": "actor", "depth": 1})

    await REGISTRY["credits_family"].apply(_ctx(session, section, definition))

    for collection in section._existing.values():
        assert family_label(definition) in collection.labels_added
        assert LABEL in collection.labels_added


async def test_the_family_writes_one_managed_row_per_collection(session):
    await _seed(session, {"Ann": 3, "Bob": 3})
    section = FakeSection()

    await REGISTRY["credits_family"].apply(
        _ctx(session, section, _definition(params={"type": "actor", "depth": 1}))
    )

    rows = (await session.execute(select(ManagedCollection))).scalars().all()
    assert sorted(row.title for row in rows) == ["Ann", "Bob"]
    assert {row.kind for row in rows} == {"smart"}


async def test_a_crew_family_carries_the_role_suffix_upstream_titles_with(session):
    """``defaults/movie/director.yml``'s own title shape, which the 8c starter
    set already borrowed: a director's collection is "<name> (Director)", while
    an actor's is the bare name (``defaults/both/actor.yml``)."""
    await _seed(session, {"Ann": 3}, kind="director")
    section = FakeSection(people=("Ann",))

    await REGISTRY["credits_family"].apply(_ctx(
        session, section,
        _definition(title="Top directors", params={"type": "director", "depth": 1}),
    ))

    assert list(section._existing) == ["Ann (Director)"]


async def test_a_dry_run_reports_the_whole_family_and_writes_nothing(session):
    await _seed(session, {"Ann": 3, "Bob": 3})
    section = FakeSection()

    actions = await REGISTRY["credits_family"].apply(_ctx(
        session, section, _definition(params={"type": "actor", "depth": 1}),
        dry_run=True,
    ))

    assert len(actions) == 2
    assert all(one.startswith("would create") for one in actions)
    assert section._existing == {}


async def test_an_unchanged_second_pass_writes_nothing(session):
    await _seed(session, {"Ann": 3, "Bob": 3})
    section = FakeSection()
    definition = _definition(params={"type": "actor", "depth": 1})
    await REGISTRY["credits_family"].apply(_ctx(session, section, definition))
    before = len(section._server.queries)

    actions = await REGISTRY["credits_family"].apply(
        _ctx(session, section, definition)
    )

    assert len(section._server.queries) == before
    assert actions == []


async def test_a_locked_summary_on_a_family_member_survives_a_definition_change(session):
    """The same stance ``builders/dynamic.py`` takes, for the same reason: this
    builder passes ``summary=None`` unconditionally and its definitions refuse
    ``summary:`` and ``tmdb_summary:`` at load, so that None cannot mean "the
    definition asserts no summary". A person's collection carries only the
    summary Plex or an operator put there, and a definition change (here: a new
    label) must not delete it."""
    await _seed(session, {"Ann": 3})
    section = FakeSection(people=("Ann",))
    definition = _definition(params={"type": "actor", "depth": 1})
    await REGISTRY["credits_family"].apply(_ctx(session, section, definition))
    member = section._existing["Ann"]
    # A hand-edit through Plex's UI: the text, and the lock Plex sets on every
    # field it writes -- the marker the managed clear reads as "ours".
    member.summary = "An operator's own text."
    member._real_fields[0].locked = True
    before = len(member._server.queries)

    actions = await REGISTRY["credits_family"].apply(_ctx(
        session, section,
        _definition(params={"type": "actor", "depth": 1}, labels=["Curated"]),
    ))

    assert member.summary == "An operator's own text."
    assert member._real_fields[0].locked is True
    assert len(member._server.queries) == before, "no summary write"
    assert not any("cleared the summary" in one for one in actions), actions


async def test_the_enumeration_is_this_librarys_and_this_kinds(session):
    """Two containments in one: a person credited only in ANOTHER library is
    not in this family, and a DIRECTOR is not in an actor family. Both are
    ``enumerate_credits``' own scoping -- asserted here because this builder is
    where getting either wrong would build a plausible, wrong collection."""
    await _seed(session, {"Ann": 2})
    other = MediaItem(rating_key="99", library="Other", kind="movie", title="Elsewhere")
    session.add(other)
    await session.flush()
    session.add_all([
        ItemCredit(item_id=other.id, kind="actor", person="Elsewhere Person"),
        ItemCredit(item_id=other.id, kind="director", person="Ann"),
    ])
    await session.flush()
    section = FakeSection(people=("Ann", "Elsewhere Person"))

    await REGISTRY["credits_family"].apply(_ctx(
        session, section, _definition(params={"type": "actor", "depth": 1}),
    ))

    assert list(section._existing) == ["Ann"]


async def test_the_count_is_never_described_as_a_complete_cast(session):
    """The 200-``Role`` cap disclosure has to be readable where the counts are
    CONSUMED, not only where they are produced. This family is the first thing
    that turns them into an operator-facing "top N", which is exactly the
    phrase the cap makes untrue -- so the module says so, and the consequence
    is spelled out at the enumeration itself: a person whose every appearance
    is in a >200-role cast can be ABSENT from the ranking entirely, not merely
    ranked low."""
    from autoposter.collections import credits as credits_module
    from autoposter.collections.builders import credits_family as module

    assert "200" in module.__doc__
    assert "ABSENT" in credits_module.enumerate_credits.__doc__
