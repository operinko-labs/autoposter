"""A ``smart_filter`` definition, through the engine and the delete sweep.

Three seams, each of which a builder unit test cannot see:

1. the engine's smart dispatch reaches this builder with the pass's
   ``run_cache`` and reports one ``DefinitionResult`` for it;
2. ``definition_titles`` enumerates the definition's own title, so the
   leftovers report never invites an operator to delete a collection this
   service is actively maintaining;
3. the delete sweep treats the collection as an ordinary managed collection --
   never a candidate while a definition builds it, and deletable under
   ``delete_unconfigured`` once none does.

Seam 3 is the one worth stating out loud, because a smart collection LOOKS
fire-and-forget: Plex owns its membership and no pass touches it. It is still a
collection this service created, labelled and holds a row for, so when its
definition is deleted from the config it becomes an orphan exactly like a
retired chart, and the same four guards decide its fate -- ownership label,
managed row, ``delete_unconfigured``, ``max_deletes``. A ``cs_bucket``
definition's collections behave differently only because that builder ENUMERATES
its family, so the sweep never sees them while the definition exists.
"""
from types import SimpleNamespace

from sqlalchemy import select

from autoposter.collections.engine import (
    definition_titles,
    definition_titles_for,
    run_library,
)
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
    ``fetchItems`` so the count and the fetch cannot disagree."""

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
        self.deleted = False
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
        self._real_labels.append(type("L", (), {"tag": label})())
        self._labels = self._real_labels

    def editSortTitle(self, value, locked=True):
        self.titleSort = value

    def delete(self):
        self.deleted = True

    def query(self, key, method=None, **kwargs):
        self._server.query(key, method)
        self._real_fields[0].locked = True


class FakeSection:
    def __init__(self, matches=3, existing=()):
        self.key = SECTION_KEY
        self._server = FakeServer(self)
        self._existing = {c.title: c for c in existing}
        self._matches = matches
        self.listings = 0

    def collections(self, **kw):
        self.listings += 1
        return list(self._existing.values())

    def collection(self, title):
        if title not in self._existing:
            self._existing[title] = FakeCollection(title, smart=True)
        return self._existing[title]

    def fetchItems(self, path, **kw):
        return [FakeItem(str(i)) for i in range(self._matches)]

    def listFilterChoices(self, field, libtype=None):
        return [FakeChoice("Horror", "1138"), FakeChoice("Drama", "9")]


class FakeSummaries:
    """The TMDB facts client's one method the engine's pull uses.

    ``raises`` is the outage: ``_summary_for`` contains whatever the client
    throws and reports the miss, so the failure reaches the reconciler as an
    unresolved summary rather than as a failed definition."""

    def __init__(self, text="Borrowed from TMDb.", raises=None):
        self.text = text
        self.raises = raises
        self.asked = []

    async def collection_summary(self, collection_id):
        self.asked.append(collection_id)
        if self.raises is not None:
            raise self.raises
        return self.text


def _config(**overrides):
    options = {
        "ownership_label": LABEL, "apply_to_plex": True, "adopt": False,
        "adopt_from": ["Kometa"], "adopt_removes_prior_label": False,
        "protect_labels": [], "posters": False, "charts": False, "awards": False,
        "separators": False, "definitions": [], "presets": [], "libraries": ["Movies"],
        "delete_unconfigured": False, "max_deletes": 5, "enabled": True,
    }
    options.update(overrides)
    return SimpleNamespace(collections=SimpleNamespace(**options))


def _definition(**overrides):
    options = {
        "title": "Recent Horror",
        "builder": "smart_filter",
        "params": {"all": {"genre": "Horror"}},
    }
    options.update(overrides)
    return CollectionDefinition(**options)


async def _managed_row(session, library, title, kind="smart"):
    """The row the T2 reconciler writes, stamps and all -- which is to say
    neither stamp.

    ``member_count`` and ``last_reconciled_at`` are spelled out as None rather
    than left to the column defaults, because they are the point: a smart row
    carries both NULL permanently (Plex evaluates the filter live, so there is
    no membership this service could count or stamp a time against), and every
    sweep test below therefore drives its surfaces against a row that has them.
    """
    row = ManagedCollection(
        library=library, title=title, kind=kind, plex_rating_key="12345",
        definition_hash="stale", member_count=None, last_reconciled_at=None,
    )
    session.add(row)
    await session.flush()
    return row


# --- seam 1: the engine's smart dispatch ------------------------------------


async def test_a_smart_filter_definition_runs_through_the_engine(session):
    section = FakeSection(matches=3)
    run = await run_library(
        session, section, "Movies", "Movie", [_definition()], _config(),
    )
    assert any("created" in action for action in run.actions)
    assert [result.title for result in run.definitions] == ["Recent Horror"]
    key, method = section._server.queries[0]
    assert key.startswith("/library/collections?sectionId=2&smart=1")
    assert method == "POST"


async def test_the_row_it_writes_is_a_smart_row(session):
    section = FakeSection(matches=3)
    await run_library(
        session, section, "Movies", "Movie", [_definition()], _config(),
    )
    row = (
        await session.execute(
            select(ManagedCollection).where(ManagedCollection.title == "Recent Horror")
        )
    ).scalar_one_or_none()
    # Captured into locals before anything can expire them.
    kind, member_count, last_reconciled = row.kind, row.member_count, row.last_reconciled_at
    assert kind == "smart"
    # Permanently NULL for a smart row, which is what ``ManagedCollection``'s
    # own comment says: Plex evaluates the filter live, so there is no
    # membership this service could count or stamp a time against.
    assert member_count is None
    assert last_reconciled is None


async def test_a_smart_filter_definition_borrows_its_summary_from_tmdb(session):
    """Row 186's acceptance, end to end: the engine resolves the pull where
    the ``summaries`` client lives and hands it down as the definition's
    effective summary; the reconciler writes it through the item-level PUT."""
    section = FakeSection(matches=3)
    summaries = FakeSummaries()
    definition = _definition(tmdb_summary=603)

    await run_library(
        session, section, "Movies", "Movie", [definition], _config(),
        summaries=summaries,
    )

    assert summaries.asked == [603]
    created = section._existing[definition.title]
    assert any(
        "summary.value=Borrowed" in key for key, _ in created._server.queries
    )


async def test_a_written_summary_still_wins_over_tmdb_summary(session):
    """``_summary_for``'s standing rule, inherited: a summary written in the
    config is an explicit choice, and a pull that silently overrode it would
    be a setting that reads as applied and is not."""
    section = FakeSection(matches=3)
    summaries = FakeSummaries()
    definition = _definition(summary="Written out.", tmdb_summary=603)

    await run_library(
        session, section, "Movies", "Movie", [definition], _config(),
        summaries=summaries,
    )

    assert summaries.asked == []
    created = section._existing[definition.title]
    assert any(
        "summary.value=Written" in key for key, _ in created._server.queries
    )


async def test_a_failed_tmdb_pull_leaves_the_locked_summary_alone(session):
    """Row 186's pull and row 187's clear, composed. ``_summary_for`` contains
    a failed pull by returning the BUILDER's summary -- None for a smart
    definition, whose ``BuilderResult`` is empty -- alongside a note. That None
    is not the definition asserting "no summary"; it is the effective summary
    being unresolvable this pass. Clearing on it would let a transient TMDB
    outage wipe and unlock the summary the last healthy pass wrote, while the
    same actions list reported "the summary is unchanged" -- and the next
    healthy pass would write it back, churning the hash both ways."""
    existing = FakeCollection("Recent Horror", labels=[LABEL], rating_key="12345")
    # The healthy state this pass must not destroy: the pulled text, and the
    # lock every managed summary write leaves behind.
    existing.summary = "Borrowed from TMDb."
    existing._real_fields[0].locked = True
    section = FakeSection(matches=3, existing=[existing])
    await _managed_row(session, "Movies", "Recent Horror")
    summaries = FakeSummaries(
        raises=RuntimeError("https://api.themoviedb.org/3?key=SECRET")
    )

    run = await run_library(
        session, section, "Movies", "Movie", [_definition(tmdb_summary=603)],
        _config(), summaries=summaries,
    )

    assert existing._server.queries == [], "no summary write of any kind"
    assert existing.summary == "Borrowed from TMDb."
    assert existing._real_fields[0].locked is True
    assert any("could not read the TMDB summary" in one for one in run.actions)
    assert not any("cleared the summary" in one for one in run.actions), run.actions


async def test_a_second_pass_writes_nothing(session):
    """The hash short-circuit, end to end. A smart definition that costs a Plex
    write on every pass would be the 'fire-and-forget' promise broken in the
    most expensive direction."""
    section = FakeSection(matches=3)
    config = _config()
    await run_library(session, section, "Movies", "Movie", [_definition()], config)
    before = len(section._server.queries)
    run = await run_library(session, section, "Movies", "Movie", [_definition()], config)
    assert len(section._server.queries) == before
    assert run.actions == []


async def test_the_pass_run_cache_reaches_the_builder(session):
    """C6's plumbing, observed rather than asserted on the dataclass: two
    smart_filter definitions naming the same genre in one pass cost ONE
    ``listFilterChoices``."""
    calls = []

    class Counting(FakeSection):
        def listFilterChoices(self, field, libtype=None):
            calls.append((field, libtype))
            return super().listFilterChoices(field, libtype)

    await run_library(
        session, Counting(matches=3), "Movies", "Movie",
        [_definition(), _definition(title="More Horror")], _config(),
    )
    assert calls == [("genre", "movie")]


async def test_a_pass_lists_the_sections_collections_once(session):
    """The engine memoises ONE ``section.collections()`` per pass and threads it
    to every list reconciler. Before this it did not reach the smart dispatch,
    so each smart definition listed the library for itself -- 305 collections on
    the production Movies section, per definition, on every pass, an unchanged
    definition included, because the listing is read before the hash
    short-circuit can return. Three definitions, one listing.
    """
    section = FakeSection(matches=3)
    config = _config()
    definitions = [
        _definition(), _definition(title="More Horror"), _definition(title="Even More"),
    ]
    await run_library(session, section, "Movies", "Movie", definitions, config)
    assert section.listings == 1

    # And the second pass -- every definition hash-current -- lists it once
    # more and no more: the fallback is gone, not merely amortised on a create.
    await run_library(session, section, "Movies", "Movie", definitions, config)
    assert section.listings == 2


async def test_the_shape_conflict_still_fires_for_a_hash_current_definition(session):
    """The listing is read BEFORE the hash short-circuit, and that ordering is
    load-bearing rather than incidental: a collection whose shape changed in
    Plex has to be re-detected on a pass where the definition itself did not
    change. Sharing the listing must not turn into hoisting the short-circuit
    above it.
    """
    section = FakeSection(matches=3)
    config = _config()
    await run_library(session, section, "Movies", "Movie", [_definition()], config)
    writes = len(section._server.queries)

    # Plex-side: the collection is no longer smart. The definition's hash is
    # untouched, so only a check that runs ahead of the short-circuit can see it.
    section._existing["Recent Horror"].smart = False
    run = await run_library(session, section, "Movies", "Movie", [_definition()], config)

    assert any("shape conflict" in action for action in run.actions)
    assert len(section._server.queries) == writes


# --- seam 2: what the definition is understood to manage --------------------


def test_a_smart_filter_definition_enumerates_its_own_title():
    """C6's degenerate case. The builder declares no ``titles``, so the engine
    falls through to the definition's title -- and this is the assertion that
    goes red if that fallthrough is ever reverted to ``builder.titles(...)``."""
    assert definition_titles(
        [_definition()], [], "Movie", _config()
    ) == {"Recent Horror"}


def test_a_definition_aimed_elsewhere_does_not_claim_this_librarys_titles():
    """``definition_titles_for`` is the sweep's narrower enumeration: a
    definition targeting another library must not make this library's collection
    of the same name look managed here."""
    elsewhere = _definition(libraries=["Kids Movies"])
    assert definition_titles_for([elsewhere], [], "Movies", "Movie", _config()) == set()
    assert definition_titles([elsewhere], [], "Movie", _config()) == {"Recent Horror"}


def test_a_gated_off_definition_still_counts_as_managing_its_title():
    """Deliberate, and inherited: a collection skipped this pass is still
    managed, and reporting it as a leftover would invite an operator to delete
    it."""
    gated = _definition(schedule={"every_n_runs": 12})
    assert definition_titles([gated], [], "Movie", _config()) == {"Recent Horror"}


# --- seam 3: the delete sweep -----------------------------------------------


async def test_the_sweep_never_touches_a_collection_its_definition_builds(session):
    """Deletion armed, so the sweep genuinely reaches its delete logic: a
    collection its own definition builds must never become a candidate, even
    while an orphan alongside it -- built by nothing -- is deleted as usual."""
    built = FakeCollection("Recent Horror", labels=[LABEL], smart=True)
    orphan = FakeCollection("Old Awards", labels=[LABEL], smart=True)
    section = FakeSection(matches=3, existing=[built, orphan])
    await _managed_row(session, "Movies", "Recent Horror")
    await _managed_row(session, "Movies", "Old Awards")

    run = await run_library(
        session, section, "Movies", "Movie", [_definition()],
        _config(delete_unconfigured=True),
        sweep=True,
    )
    assert built.deleted is False
    assert orphan.deleted is True
    assert not any("deleted 'Recent Horror'" in action for action in run.actions)


async def test_an_orphaned_smart_filter_collection_is_only_reported_by_default(session):
    """The definition was deleted from the config. The collection is ours -- our
    label, our row -- so it is a sweep candidate like any other, and
    ``delete_unconfigured`` off means REPORTED."""
    orphan = FakeCollection("Recent Horror", labels=[LABEL], smart=True)
    section = FakeSection(matches=3, existing=[orphan])
    await _managed_row(session, "Movies", "Recent Horror")

    run = await run_library(
        session, section, "Movies", "Movie", [], _config(), sweep=True,
    )
    assert orphan.deleted is False
    assert any("delete_unconfigured" in action for action in run.actions)
    assert (
        await session.execute(
            select(ManagedCollection).where(ManagedCollection.title == "Recent Horror")
        )
    ).scalar_one_or_none() is not None


async def test_the_report_of_an_orphan_renders_its_null_stamps_as_null(session):
    """The T2 carry, at the surface that reads the row.

    A smart row reaches the sweep with ``member_count`` and
    ``last_reconciled_at`` both NULL, and the report has to survive that without
    coercing either to 0 -- "no pass has ever stamped this row" and "a pass ran
    and counted nothing" are different facts, and the sweep is deciding whether
    to invite an operator to delete the collection. This is the sweep's half of
    the rule ``api/routes.py::list_collections`` keeps on the listing side by
    passing both straight through.
    """
    orphan = FakeCollection("Recent Horror", labels=[LABEL], smart=True)
    section = FakeSection(matches=3, existing=[orphan])
    row = await _managed_row(session, "Movies", "Recent Horror")
    assert (row.member_count, row.last_reconciled_at) == (None, None)

    run = await run_library(
        session, section, "Movies", "Movie", [], _config(), sweep=True,
    )
    reported = [result for result in run.definitions if result.title == "Recent Horror"]
    assert len(reported) == 1
    # Nothing about the row's stamps reached the report -- the sweep reports a
    # PENDING delete, and ``deleting`` is that count, not a membership.
    assert (reported[0].deleting, reported[0].skipped) == (0, True)
    await session.refresh(row)
    assert (row.member_count, row.last_reconciled_at) == (None, None)


async def test_an_orphaned_smart_filter_collection_is_deleted_when_opted_in(session):
    orphan = FakeCollection("Recent Horror", labels=[LABEL], smart=True)
    section = FakeSection(matches=3, existing=[orphan])
    await _managed_row(session, "Movies", "Recent Horror")

    run = await run_library(
        session, section, "Movies", "Movie", [], _config(delete_unconfigured=True),
        sweep=True,
    )
    assert orphan.deleted is True
    assert any("deleted 'Recent Horror'" in action for action in run.actions)
    assert (
        await session.execute(
            select(ManagedCollection).where(ManagedCollection.title == "Recent Horror")
        )
    ).scalar_one_or_none() is None


async def test_a_protected_label_still_wins_over_the_sweep(session):
    """Checked before ownership everywhere else, and here too. A smart
    collection carrying a protected label is reported and left alone even though
    it also carries ours."""
    orphan = FakeCollection("Recent Horror", labels=[LABEL, "Maintainerr"], smart=True)
    section = FakeSection(matches=3, existing=[orphan])
    await _managed_row(session, "Movies", "Recent Horror")

    run = await run_library(
        session, section, "Movies", "Movie", [],
        _config(delete_unconfigured=True, protect_labels=["Maintainerr"]),
        sweep=True,
    )
    assert orphan.deleted is False
    assert any("protected" in action for action in run.actions)


# --- the shape refusal, end to end ------------------------------------------


async def test_a_list_collection_under_a_smart_definition_costs_one_definition(session):
    """C11 through the engine: the refusal is one definition's action string,
    and the pass carries on. An exception here would reach
    ``reconcile_libraries``' per-library rollback and undo the rest of the
    library's work over one config edit."""
    dumb = FakeCollection("Recent Horror", labels=[LABEL], smart=False)
    section = FakeSection(matches=3, existing=[dumb])

    run = await run_library(
        session, section, "Movies", "Movie", [_definition()], _config(),
    )
    assert any("shape conflict" in action for action in run.actions)
    assert section._server.queries == []
    assert (
        await session.execute(
            select(ManagedCollection).where(ManagedCollection.title == "Recent Horror")
        )
    ).scalar_one_or_none() is None
