"""The settings that ride along on a definition, beyond its membership.

Everything here is applied to the collection *object* (or to its members)
rather than deciding who is in it: labels (row 29), a summary borrowed from
TMDB (row 30), hub visibility and priority (row 68), labels on the members
themselves (row 69), and the sort title and display mode the create paths never
set (row 104).

Three properties hold the file together:

- **a settings-only edit still applies.** A pass short-circuits on the members
  hash, so a definition whose only change was a new label would be recognised
  as already current and the label would never be written. The settings are in
  the hash for that reason -- and contribute *nothing* to it when they are at
  their defaults, which is what keeps every hash already stored matching.
- **sync stops at the collection.** ``label_sync`` may strip a label off a
  collection this service owns; nothing ever strips one off an *item*. A member
  that leaves the collection is not a member, and an item's labels are not this
  definition's to own.
- **Plex Pass features degrade.** Hub pinning needs one. On a server without
  it every call fails, and that has to cost the definition an action string
  rather than the library its pass.
"""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from autoposter.collections.builders import REGISTRY, BuilderContext, BuilderResult, register
from autoposter.collections.engine import run_definitions
from autoposter.collections.lists import _members_hash
from autoposter.collections.reconcile import _apply_labels
from autoposter.config.schema import CollectionDefinition

LABEL = "autoposter"
PROTECTED = "Collection managed by Maintainerr"


class FakeGuid:
    def __init__(self, guid_id):
        self.id = guid_id


class FakeItem:
    """A library item, with the label surface row 69 writes through."""

    def __init__(self, key, guids=()):
        self.ratingKey = key
        self.title = key
        self.guids = [FakeGuid(g) for g in guids]
        self.added: list[str] = []
        self.removed: list[str] = []

    def addLabel(self, labels, locked=True):
        self.added.append(labels)

    def removeLabel(self, labels, locked=True):
        self.removed.append(labels)


class FakeHub:
    """A ``ManagedHub``: records the visibility it was given and where it moved."""

    def __init__(self, identifier="custom.collection.1.c", promoted=True):
        self.identifier = identifier
        self._promoted = promoted
        self.visibility_calls: list[tuple] = []
        self.moved_after: list[object] = []

    def updateVisibility(self, recommended=None, home=None, shared=None):
        self.visibility_calls.append((recommended, home, shared))
        return self

    def move(self, after=None):
        self.moved_after.append(after)


class FailingMoveHub(FakeHub):
    """A hub whose visibility write succeeds but whose move fails -- the
    fix round's partial-success case (item 2c)."""

    def move(self, after=None):
        raise RuntimeError("https://plex.local/hubs/move?token=SECRET failed")


class FakeCollection:
    """``test_builder_knobs.py``'s fake plus the ride-along surface."""

    def __init__(self, title, items=(), labels=(), title_sort=None, mode=-1, hub=None,
                 summary=None, summary_locked=False):
        self.title = title
        self.ratingKey = "c-" + title
        self._live = list(items)
        self._cache = list(items)
        self._real_labels = [type("L", (), {"tag": t})() for t in labels]
        self._labels = []
        self.summary = summary
        # The lock is what marks a summary as this service's own (every managed
        # write carries ``summary.locked=1``), so it is the gate both the clear
        # and its refusals read.
        self._fields = [type("F", (), {"name": "summary", "locked": summary_locked})()]
        self.summary_writes: list[str] = []
        self.titleSort = title_sort
        self.collectionMode = mode
        self.sort_set = None
        self.mode_set: list[str] = []
        self.removed_labels: list[str] = []
        self.hub = hub or FakeHub()
        self.visibility_calls = 0
        self._server = self
        self._session = type("Sess", (), {"put": "PUT-SENTINEL"})()

    @property
    def labels(self):
        return self._labels

    @property
    def fields(self):
        return self._fields

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
        self._live = [i for i in self._live if i.ratingKey != item.ratingKey]
        if after is None:
            self._live.insert(0, item)
        else:
            position = [i.ratingKey for i in self._live].index(after.ratingKey)
            self._live.insert(position + 1, item)

    def sortUpdate(self, sort=None):
        self.sort_set = sort

    def query(self, key, method=None, **kwargs):
        self.summary_writes.append(key)

    def addLabel(self, labels, locked=True):
        self._real_labels.append(type("L", (), {"tag": labels})())
        self._labels = self._real_labels

    def removeLabel(self, labels, locked=True):
        self.removed_labels.append(labels)
        self._real_labels = [t for t in self._real_labels if t.tag != labels]
        self._labels = self._real_labels

    def editSortTitle(self, sortTitle):
        self.titleSort = sortTitle

    def modeUpdate(self, mode=None):
        self.mode_set.append(mode)

    def visibility(self):
        self.visibility_calls += 1
        return self.hub

    def label_names(self):
        """In the order they were applied, not sorted: which label arrived
        when is what the add-versus-sync tests are actually about."""
        return [tag.tag for tag in self._real_labels]


class NoPlexPassCollection(FakeCollection):
    """What a server without a Plex Pass does when asked for a managed hub."""

    def visibility(self):
        raise RuntimeError("https://plex.local/hubs?token=SECRET returned 403")


class FakeSection:
    def __init__(self, items=(), existing=(), hubs=(), ratings=()):
        self._items = [FakeItem(key, guids) for key, guids in items]
        self._existing = {c.title: c for c in existing}
        self.type = "movie"
        self._hubs = list(hubs)
        self._ratings = list(ratings)
        self.created: list[str] = []
        # The Common Sense family writes through the raw POST/PUT routes since
        # phase 10a-2, so this fake stands in for ``section._server`` too.
        self.key = "42"
        self._server = self
        self._session = type("Sess", (), {
            "post": "POST-SENTINEL", "put": "PUT-SENTINEL",
        })()

    def all(self):
        return list(self._items)

    def item_for(self, key):
        return next(i for i in self._items if i.ratingKey == key)

    def _uriRoot(self):
        return "server://FAKE-MACHINE-ID/com.plexapp.plugins.library"

    def query(self, key, method=None, headers=None, params=None, timeout=None, **kwargs):
        """The create POST (which carries a ``title``) and the filter-replacing
        PUT (which carries only a ``uri``)."""
        args = parse_qs(urlsplit(key).query)
        if "title" not in args:
            return None
        title = args["title"][0]
        self.created.append(title)
        self._existing[title] = FakeCollection(title)
        return None

    def collection(self, title):
        return self._existing[title]

    def listFilterChoices(self, field, libtype=None):
        """Empty unless a test asked for content ratings: with none present the
        Common Sense family derives no buckets, which is what keeps it out of
        the way of the list-collection tests here.

        ``key`` matches ``title``: Plex answers contentRating's two the same
        way, so the resolver the family builds its query through is the
        identity here."""
        return [SimpleNamespace(title=rating, key=rating) for rating in self._ratings]

    def collections(self, **kw):
        return list(self._existing.values())

    def managedHubs(self):
        return list(self._hubs)

    def createCollection(self, title, items=None, smart=False, **kw):
        self.created.append(title)
        collection = FakeCollection(title, items or [])
        self._existing[title] = collection
        return collection


def _config(**overrides):
    options = {
        "ownership_label": LABEL, "apply_to_plex": True, "adopt": False,
        "adopt_from": ["Kometa"], "adopt_removes_prior_label": False,
        "protect_labels": [PROTECTED], "posters": False, "charts": False,
        "awards": False, "separators": False, "definitions": [],
        "libraries": ["Movies"], "delete_unconfigured": False, "max_deletes": 5,
        "enabled": True,
    }
    options.update(overrides)
    return SimpleNamespace(collections=SimpleNamespace(**options))


@pytest.fixture
def registry_entry():
    registered: list[str] = []

    def add(builder):
        register(builder)
        registered.append(builder.type_name)
        return builder

    yield add

    for type_name in registered:
        del REGISTRY[type_name]


class _Listing:
    def __init__(self, type_name, ids, summary=None):
        self.type_name = type_name
        self.ids = list(ids)
        self._summary = summary

    async def build(self, ctx: BuilderContext) -> BuilderResult:
        return BuilderResult(ids=list(self.ids), summary=self._summary)


async def _run(session, section, definitions, config=None, **kwargs):
    return await run_definitions(
        session, section, "Movies", "Movie", definitions, config or _config(), **kwargs
    )


def _one_item_section(existing=(), **kwargs):
    return FakeSection([("m1", ["imdb://tt1"])], existing=existing, **kwargs)


# --- row 29: labels on the collection object -------------------------------


async def test_a_definitions_labels_are_added_to_the_collection(session, registry_entry):
    registry_entry(_Listing("settings_labels", [("imdb", "tt1")]))
    live = FakeCollection("Labelled", labels=[LABEL])
    section = _one_item_section(existing=[live])

    actions = await _run(session, section, [CollectionDefinition(
        title="Labelled", builder="settings_labels", labels=["Favourites", "Shelf"],
    )])

    assert live.label_names() == [LABEL, "Favourites", "Shelf"]
    assert "labelled 'Labelled': +2 -0" in actions


async def test_a_label_already_present_is_not_written_again(session, registry_entry):
    """The module's rule: an unchanged pass issues no requests. A collection
    that already carries the label must not be told about it again."""
    registry_entry(_Listing("settings_labels_present", [("imdb", "tt1")]))
    live = FakeCollection("Labelled", labels=[LABEL, "Favourites"])
    section = _one_item_section(existing=[live])

    actions = await _run(session, section, [CollectionDefinition(
        title="Labelled", builder="settings_labels_present", labels=["Favourites"],
    )])

    assert live.label_names() == [LABEL, "Favourites"]
    assert not any("labelled" in action for action in actions)


async def test_label_sync_removes_a_label_the_definition_does_not_name(
    session, registry_entry
):
    registry_entry(_Listing("settings_sync", [("imdb", "tt1")]))
    live = FakeCollection("Labelled", labels=[LABEL, "Favourites", "Stale"])
    section = _one_item_section(existing=[live])

    actions = await _run(session, section, [CollectionDefinition(
        title="Labelled", builder="settings_sync",
        labels=["Favourites"], label_sync=True,
    )])

    assert live.removed_labels == ["Stale"]
    assert live.label_names() == [LABEL, "Favourites"]
    assert "labelled 'Labelled': +0 -1" in actions


async def test_label_sync_never_strips_the_ownership_label(session, registry_entry):
    """Stripping it would orphan the collection from the service that built
    it -- the next pass would read it as somebody else's and refuse to touch
    it, and the sweep would never see it either."""
    registry_entry(_Listing("settings_sync_own", [("imdb", "tt1")]))
    live = FakeCollection("Labelled", labels=[LABEL])
    section = _one_item_section(existing=[live])

    await _run(session, section, [CollectionDefinition(
        title="Labelled", builder="settings_sync_own", label_sync=True,
    )])

    assert live.removed_labels == []
    assert live.label_names() == [LABEL]


async def test_label_sync_never_strips_the_ownership_label_plex_recased(
    session, registry_entry
):
    """The live production pair, on the write path: the config says
    ``autoposter`` and the server stores ``Autoposter`` (Plex canonicalises
    label case -- measured 2026-08-30, one tag, tagID 214239). Compared
    exactly, the keep-set held the config's spelling and the stored set the
    server's, so this pass called ``removeLabel('Autoposter')`` and took off the
    label that says the collection is ours: the next pass reads it as a
    stranger's, refuses it, writes no sort title, and the sweep never sees it
    either.

    The definition's own ``Favourites`` is stored as ``favourites`` for the same
    reason, and must not be added a second time -- Plex holds one tag per
    case-folded name, so the add would be a no-op request every pass.
    """
    registry_entry(_Listing("settings_sync_recased", [("imdb", "tt1")]))
    live = FakeCollection("Labelled", labels=["Autoposter", "favourites"])
    section = _one_item_section(existing=[live])

    actions = await _run(session, section, [CollectionDefinition(
        title="Labelled", builder="settings_sync_recased",
        labels=["Favourites"], label_sync=True,
    )])

    assert live.removed_labels == []
    assert live.label_names() == ["Autoposter", "favourites"]
    assert not any("labelled" in action for action in actions)


def test_label_sync_keeps_a_protect_labels_entry_whose_case_differs():
    """The Maintainerr direction, on the write path.

    Called directly, because end-to-end a protected collection never reaches
    the settings at all (``resolve_collision`` refuses it first, pinned above).
    The keep-set entry is the second lock, and an exact compare left it open:
    a ``protect_labels`` of ``collection managed by maintainerr`` against the
    stored ``Collection managed by Maintainerr`` subtracted to a removal of
    another tool's claim.
    """
    live = FakeCollection("Labelled", labels=[LABEL, PROTECTED])

    actions = _apply_labels(
        live,
        CollectionDefinition(title="Labelled", builder="plex_all", label_sync=True),
        LABEL,
        _config(protect_labels=[PROTECTED.casefold()]),
    )

    assert live.removed_labels == []
    assert live.label_names() == [LABEL, PROTECTED]
    assert actions == []


async def test_label_sync_never_strips_a_prior_tools_label(session, registry_entry):
    """The prior tool's label is kept by ``claim_ownership`` on purpose --
    taking it off is what ``adopt_removes_prior_label`` decides, once and
    deliberately, so a sync that quietly undid that decision would be the same
    bug backwards."""
    registry_entry(_Listing("settings_sync_prior", [("imdb", "tt1")]))
    live = FakeCollection("Labelled", labels=[LABEL, "Kometa", "Stale"])
    section = _one_item_section(existing=[live])

    await _run(session, section, [CollectionDefinition(
        title="Labelled", builder="settings_sync_prior", label_sync=True,
    )])

    assert live.removed_labels == ["Stale"]
    assert live.label_names() == [LABEL, "Kometa"]


async def test_a_protected_collection_never_reaches_the_settings_at_all(
    session, registry_entry
):
    """The stronger guarantee, and the reason a protected label is not in
    ``label_sync``'s keep-set: ``resolve_collision`` refuses the collection
    before any of this runs, so nothing here can be the code that touches a
    Maintainerr collection."""
    registry_entry(_Listing("settings_sync_protected", [("imdb", "tt1")]))
    live = FakeCollection("Labelled", labels=[LABEL, PROTECTED, "Stale"])
    section = _one_item_section(existing=[live])

    actions = await _run(session, section, [CollectionDefinition(
        title="Labelled", builder="settings_sync_protected",
        label_sync=True, labels=["Favourites"], sort_title="!010_x",
        collection_mode="hide", visible_home=True,
    )])

    assert live.removed_labels == []
    assert live.label_names() == [LABEL, PROTECTED, "Stale"]
    assert live.titleSort is None and live.mode_set == []
    assert live.visibility_calls == 0
    assert actions == [
        "protected: 'Labelled' carries 'Collection managed by Maintainerr'; "
        "leaving it untouched"
    ]


async def test_labels_are_applied_to_a_freshly_created_collection(
    session, registry_entry
):
    registry_entry(_Listing("settings_labels_new", [("imdb", "tt1")]))
    section = _one_item_section()

    await _run(session, section, [CollectionDefinition(
        title="Fresh", builder="settings_labels_new", labels=["Favourites"],
    )])

    assert section._existing["Fresh"].label_names() == [LABEL, "Favourites"]


# --- row 104: sort title and display mode ----------------------------------


async def test_the_sort_title_is_set_on_create(session, registry_entry):
    registry_entry(_Listing("settings_sort_new", [("imdb", "tt1")]))
    section = _one_item_section()

    actions = await _run(session, section, [CollectionDefinition(
        title="Fresh", builder="settings_sort_new", sort_title="!010_Fresh",
    )])

    assert section._existing["Fresh"].titleSort == "!010_Fresh"
    assert "set the sort title of 'Fresh' to '!010_Fresh'" in actions


async def test_the_sort_title_is_kept_in_sync_on_an_existing_collection(
    session, registry_entry
):
    """Row 104 is a *create*-path gap, but a sort title only written on create
    would mean an edited one never took effect on the collections that already
    exist -- which is every collection an operator would be editing."""
    registry_entry(_Listing("settings_sort_edit", [("imdb", "tt1")]))
    live = FakeCollection("Sorted", labels=[LABEL], title_sort="!010_old")
    section = _one_item_section(existing=[live])

    await _run(session, section, [CollectionDefinition(
        title="Sorted", builder="settings_sort_edit", sort_title="!020_new",
    )])

    assert live.titleSort == "!020_new"


async def test_a_matching_sort_title_is_not_rewritten(session, registry_entry):
    registry_entry(_Listing("settings_sort_same", [("imdb", "tt1")]))
    live = FakeCollection("Sorted", labels=[LABEL], title_sort="!010_Sorted")
    section = _one_item_section(existing=[live])

    actions = await _run(session, section, [CollectionDefinition(
        title="Sorted", builder="settings_sort_same", sort_title="!010_Sorted",
    )])

    assert not any("sort title" in action for action in actions)


async def test_the_display_mode_is_set(session, registry_entry):
    registry_entry(_Listing("settings_mode", [("imdb", "tt1")]))
    live = FakeCollection("Moded", labels=[LABEL], mode=-1)
    section = _one_item_section(existing=[live])

    actions = await _run(session, section, [CollectionDefinition(
        title="Moded", builder="settings_mode", collection_mode="hideItems",
    )])

    assert live.mode_set == ["hideItems"]
    assert "set the display mode of 'Moded' to 'hideItems'" in actions


async def test_a_display_mode_already_set_is_not_rewritten(session, registry_entry):
    """``collectionMode`` is the int plexapi casts it to, so the comparison
    has to be against 1 rather than against 'hideItems'."""
    registry_entry(_Listing("settings_mode_same", [("imdb", "tt1")]))
    live = FakeCollection("Moded", labels=[LABEL], mode=1)
    section = _one_item_section(existing=[live])

    await _run(session, section, [CollectionDefinition(
        title="Moded", builder="settings_mode_same", collection_mode="hideItems",
    )])

    assert live.mode_set == []


async def test_an_unknown_display_mode_is_refused_when_the_config_loads():
    """plexapi answers a bad mode with a BadRequest mid-pass against a live
    server, which is a much worse place to learn about a typo."""
    with pytest.raises(ValueError):
        CollectionDefinition(
            title="Moded", builder="plex_id", params={"ids": ["1"]},
            collection_mode="hide_items",
        )


async def test_the_smart_create_path_gets_the_sort_title_and_mode_too(session):
    """Row 104 names *both* create paths, and this is the other one: the
    Common Sense buckets. One definition names the whole family, so they share
    the settings -- which is what a section sort-title prefix is for, the
    family sorting as one block. The separator is excluded: its own sort title
    is the constant that makes it a divider."""
    section = FakeSection(ratings=["R", "PG"])

    actions = await _run(
        session, section,
        [CollectionDefinition(
            title="Common Sense age ratings", builder="cs_bucket",
            sort_title="!110_Ages", collection_mode="hideItems",
            labels=["Ratings"],
        )],
        _config(separators=False),
    )

    created = list(section._existing.values())
    assert created, "the family was built"
    assert {c.titleSort for c in created} == {"!110_Ages"}
    assert {tuple(c.mode_set) for c in created} == {("hideItems",)}
    assert all("Ratings" in c.label_names() for c in created)
    assert any("set the sort title of" in action for action in actions)


# --- row 68: hub visibility and priority -----------------------------------


async def test_hub_visibility_is_pinned(session, registry_entry):
    registry_entry(_Listing("settings_hub", [("imdb", "tt1")]))
    live = FakeCollection("Pinned", labels=[LABEL])
    section = _one_item_section(existing=[live])

    actions = await _run(session, section, [CollectionDefinition(
        title="Pinned", builder="settings_hub",
        visible_library=True, visible_home=True, visible_shared=False,
    )])

    assert live.hub.visibility_calls == [(True, True, False)]
    assert (
        "set the hub visibility of 'Pinned' (library=True, home=True, shared=False)"
        in actions
    )


async def test_a_definition_that_pins_nothing_never_asks_for_the_hub(
    session, registry_entry
):
    """None means "not managed by this definition", not "off": reading the hub
    is a request per collection per pass, and writing one would take a
    collection an operator pinned by hand back off their home page."""
    registry_entry(_Listing("settings_hub_none", [("imdb", "tt1")]))
    live = FakeCollection("Unpinned", labels=[LABEL])
    section = _one_item_section(existing=[live])

    await _run(session, section, [CollectionDefinition(
        title="Unpinned", builder="settings_hub_none",
    )])

    assert live.visibility_calls == 0
    assert live.hub.visibility_calls == []


async def test_hub_priority_moves_the_hub_after_the_one_at_that_index(
    session, registry_entry
):
    """plexapi moves a hub *after* another rather than to an index, and the
    hub being moved is taken out of the listing first -- otherwise a hub
    already in the list would be asked to move after itself."""
    registry_entry(_Listing("settings_hub_priority", [("imdb", "tt1")]))
    first, second = FakeHub("hub.a"), FakeHub("hub.b")
    mine = FakeHub("hub.mine")
    live = FakeCollection("Pinned", labels=[LABEL], hub=mine)
    section = _one_item_section(existing=[live], hubs=[first, mine, second])

    actions = await _run(session, section, [CollectionDefinition(
        title="Pinned", builder="settings_hub_priority",
        visible_home=True, hub_priority=1,
    )])

    assert mine.moved_after == [first]
    assert "moved 'Pinned' to position 1 in the managed recommendations" in actions


async def test_hub_priority_zero_moves_the_hub_to_the_front(session, registry_entry):
    registry_entry(_Listing("settings_hub_first", [("imdb", "tt1")]))
    other = FakeHub("hub.a")
    mine = FakeHub("hub.mine")
    live = FakeCollection("Pinned", labels=[LABEL], hub=mine)
    section = _one_item_section(existing=[live], hubs=[other, mine])

    await _run(session, section, [CollectionDefinition(
        title="Pinned", builder="settings_hub_first",
        visible_home=True, hub_priority=0,
    )])

    assert mine.moved_after == [None]


async def test_a_priority_past_the_end_lands_the_hub_last(session, registry_entry):
    registry_entry(_Listing("settings_hub_last", [("imdb", "tt1")]))
    other = FakeHub("hub.a")
    mine = FakeHub("hub.mine")
    live = FakeCollection("Pinned", labels=[LABEL], hub=mine)
    section = _one_item_section(existing=[live], hubs=[other, mine])

    await _run(session, section, [CollectionDefinition(
        title="Pinned", builder="settings_hub_last",
        visible_home=True, hub_priority=99,
    )])

    assert mine.moved_after == [other]


async def test_a_server_without_a_plex_pass_reports_and_carries_on(
    session, registry_entry
):
    """Hub pinning is a Plex Pass feature. Without one every call fails, and
    that must cost this definition an action string rather than the library
    its pass -- and the failure's text stays in the log, because a Plex error
    carries the URL it failed on and that URL carries a token."""
    registry_entry(_Listing("settings_hub_nopass", [("imdb", "tt1")]))
    live = NoPlexPassCollection("Pinned", labels=[LABEL])
    section = _one_item_section(existing=[live])

    actions = await _run(session, section, [CollectionDefinition(
        title="Pinned", builder="settings_hub_nopass", visible_home=True,
    )])

    assert any("could not set the hub visibility of 'Pinned'" in a for a in actions)
    assert not any("token" in a or "http" in a for a in actions)
    assert [i.ratingKey for i in live._live] == ["m1"], (
        "the membership still reconciled"
    )


async def test_a_failed_move_does_not_discard_a_successful_visibility_write(
    session, registry_entry
):
    """Fix round item 2c: a successful ``updateVisibility`` before a failed
    ``move`` must not be reported as total failure -- the operator would
    otherwise be told the hub pinning failed when half of it actually
    worked."""
    registry_entry(_Listing("settings_hub_move_fails", [("imdb", "tt1")]))
    mine = FailingMoveHub("hub.mine")
    live = FakeCollection("Pinned", labels=[LABEL], hub=mine)
    section = _one_item_section(existing=[live], hubs=[mine])

    actions = await _run(session, section, [CollectionDefinition(
        title="Pinned", builder="settings_hub_move_fails",
        visible_home=True, hub_priority=0,
    )])

    assert mine.visibility_calls == [(None, True, None)], "the visibility write ran"
    assert any(
        "set the hub visibility of 'Pinned'" in a for a in actions
    ), "the successful write must still be reported"
    assert any("could not" in a for a in actions), "and the failed move too"
    assert not any("token" in a or "http" in a for a in actions)


def test_hub_priority_alone_is_refused_when_the_config_loads():
    """Fix round item 2b: ``hub_priority`` without any ``visible_*`` flag
    would hit plexapi's ``ManagedHub.move`` on a hub ``visibility()`` never
    promoted -- a guaranteed ``BadRequest`` on the first pass, which
    ``_apply_hub`` used to misreport as a missing Plex Pass. Caught here,
    at config load, with the real cause."""
    with pytest.raises(ValueError, match="hub_priority"):
        CollectionDefinition(
            title="Pinned", builder="plex_id", params={"ids": ["1"]}, hub_priority=0,
        )


# --- row 69: labels on the members -----------------------------------------


async def test_item_labels_are_applied_to_every_resolved_member(
    session, registry_entry
):
    registry_entry(_Listing("settings_items", [("imdb", "tt1"), ("imdb", "tt2")]))
    section = FakeSection([("m1", ["imdb://tt1"]), ("m2", ["imdb://tt2"])])

    actions = await _run(session, section, [CollectionDefinition(
        title="Fresh", builder="settings_items", item_label=["Hand Picked"],
    )])

    assert [i.added for i in section._items] == [["Hand Picked"], ["Hand Picked"]]
    assert "labelled 2 member(s) of 'Fresh': Hand Picked" in actions


async def test_a_member_that_leaves_the_collection_keeps_its_item_label(
    session, registry_entry
):
    """The mutation proof of row 69's one rule. A departing member is not a
    member, so taking the label off it would be a write against an item this
    definition no longer describes -- and the label may be one the operator
    applies from elsewhere too."""
    registry_entry(_Listing("settings_items_leave", [("imdb", "tt1")]))
    section = FakeSection([("m1", ["imdb://tt1"]), ("m2", ["imdb://tt2"])])
    departing = section.item_for("m2")
    live = FakeCollection("Shrinking", [departing], labels=[LABEL])
    section._existing[live.title] = live

    await _run(session, section, [CollectionDefinition(
        title="Shrinking", builder="settings_items_leave", item_label=["Hand Picked"],
    )])

    assert [i.ratingKey for i in live._live] == ["m1"], "it did leave"
    assert departing.removed == [], (
        "a member that left must never have a label taken off it"
    )
    assert section.item_for("m1").added == ["Hand Picked"]


async def test_a_definition_with_no_item_label_touches_no_item(
    session, registry_entry
):
    registry_entry(_Listing("settings_items_none", [("imdb", "tt1")]))
    section = _one_item_section()

    await _run(session, section, [CollectionDefinition(
        title="Fresh", builder="settings_items_none",
    )])

    assert section._items[0].added == []


async def test_item_labels_are_refused_on_a_smart_definition():
    """A smart collection's members are Plex's to decide, so there is no
    resolved list to label -- a setting that read as applied and never was."""
    with pytest.raises(ValueError):
        CollectionDefinition(
            title="Common Sense age ratings", builder="cs_bucket",
            item_label=["Hand Picked"],
        )


# --- row 30: the summary from TMDB -----------------------------------------


class _Summaries:
    """The TMDB facts client, reduced to the one call the engine makes."""

    def __init__(self, answer=None, raises=None):
        self.answer = answer
        self.raises = raises
        self.asked: list[int] = []

    async def collection_summary(self, tmdb_id: int):
        self.asked.append(tmdb_id)
        if self.raises is not None:
            raise self.raises
        return self.answer


async def test_the_summary_is_pulled_from_tmdb(session, registry_entry):
    registry_entry(_Listing("settings_tmdb", [("imdb", "tt1")]))
    section = _one_item_section()
    summaries = _Summaries("The films of a decade.")

    await _run(
        session, section,
        [CollectionDefinition(title="Fresh", builder="settings_tmdb", tmdb_summary=10)],
        summaries=summaries,
    )

    assert summaries.asked == [10]
    assert section._existing["Fresh"].summary_writes, "the summary was written to Plex"


async def test_a_static_summary_beats_the_tmdb_pull(session, registry_entry):
    """``summary:`` written in the config is an explicit choice; a pull that
    silently overrode it would be a setting that reads as applied and is not.
    And the pull is not even made -- an override is not a fallback."""
    registry_entry(_Listing("settings_tmdb_static", [("imdb", "tt1")]))
    section = _one_item_section()
    summaries = _Summaries("From TMDB.")

    await _run(
        session, section,
        [CollectionDefinition(
            title="Fresh", builder="settings_tmdb_static",
            summary="Mine.", tmdb_summary=10,
        )],
        summaries=summaries,
    )

    assert summaries.asked == []


async def test_a_static_summary_beats_a_person_builders_biography(session, registry_entry):
    """Phase 10c-lite gave the five person builders a summary of their own --
    the person's TMDb biography (``builders/tmdb_person.py::_profile``, pinned
    by ``test_a_person_builder_takes_its_summary_and_poster_from_the_person``).
    ``_summary_for`` is unchanged and already prefers the definition's own
    ``summary:`` over whatever the builder derived, but no shipped person
    builder derived one, so that preference had never been pinned for the
    case amendment 4 asked about.

    The double stands in for the builder deliberately: ``_summary_for`` reads
    ``result.summary`` and never learns which builder produced it, so driving a
    real ``tmdb_actor`` through HTTP doubles here would test the same branch
    with more machinery. What it carries is exactly what a person builder now
    puts there."""
    registry_entry(_Listing("settings_person_bio", [("imdb", "tt1")], summary="Theirs"))
    section = _one_item_section()

    await _run(session, section, [CollectionDefinition(
        title="Fresh", builder="settings_person_bio", summary="Mine",
    )])

    writes = section._existing["Fresh"].summary_writes
    assert writes, "the summary was written to Plex"
    assert all("Mine" in write for write in writes)
    assert not any("Theirs" in write for write in writes)


async def test_a_failed_tmdb_pull_is_contained(session, registry_entry):
    """A summary is cosmetic. A TMDB outage must leave the membership
    reconciled and report the miss, not fail the definition -- and whatever
    the client raised stays in the log."""
    registry_entry(_Listing("settings_tmdb_dead", [("imdb", "tt1")]))
    section = _one_item_section()
    summaries = _Summaries(raises=RuntimeError("https://api.themoviedb.org/3?key=SECRET"))

    actions = await _run(
        session, section,
        [CollectionDefinition(
            title="Fresh", builder="settings_tmdb_dead", tmdb_summary=10,
        )],
        summaries=summaries,
    )

    assert "could not read the TMDB summary for 'Fresh'; the summary is unchanged" in actions
    assert not any("SECRET" in action for action in actions)
    assert section.created == ["Fresh"], "the collection was still built"


async def test_a_collection_tmdb_has_no_summary_for_is_reported(
    session, registry_entry
):
    registry_entry(_Listing("settings_tmdb_empty", [("imdb", "tt1")]))
    section = _one_item_section()

    actions = await _run(
        session, section,
        [CollectionDefinition(
            title="Fresh", builder="settings_tmdb_empty", tmdb_summary=10,
        )],
        summaries=_Summaries(None),
    )

    assert "TMDB has no summary for the collection 'Fresh' names" in actions


async def test_a_failed_tmdb_pull_does_not_clear_the_locked_summary(
    session, registry_entry
):
    """The other half of the containment above, since row 187 gave the update
    path a clear. A failed pull falls back to the builder's summary (None here)
    and reports the miss -- and that None means "could not be resolved", never
    "the definition asserts no summary". Clearing on it would wipe and unlock
    the last healthy pull's text while the same actions list said the summary
    was unchanged."""
    registry_entry(_Listing("settings_tmdb_keeps", [("imdb", "tt1")]))
    live = FakeCollection(
        "Fresh", labels=[LABEL],
        summary="The last healthy pull.", summary_locked=True,
    )
    section = _one_item_section(existing=[live])
    summaries = _Summaries(
        raises=RuntimeError("https://api.themoviedb.org/3?key=SECRET")
    )

    actions = await _run(
        session, section,
        [CollectionDefinition(
            title="Fresh", builder="settings_tmdb_keeps", tmdb_summary=10,
        )],
        summaries=summaries,
    )

    assert live.summary_writes == [], "no summary write of any kind"
    assert live.summary == "The last healthy pull."
    assert (
        "could not read the TMDB summary for 'Fresh'; the summary is unchanged"
        in actions
    )
    assert not any("cleared the summary" in action for action in actions), actions


async def test_an_instance_without_a_tmdb_client_says_so(session, registry_entry):
    registry_entry(_Listing("settings_tmdb_noclient", [("imdb", "tt1")]))
    section = _one_item_section()

    actions = await _run(session, section, [CollectionDefinition(
        title="Fresh", builder="settings_tmdb_noclient", tmdb_summary=10,
    )])

    assert any("no TMDB client" in action for action in actions)


async def test_tmdb_summary_is_refused_on_a_smart_definition():
    with pytest.raises(ValueError):
        CollectionDefinition(
            title="Common Sense age ratings", builder="cs_bucket", tmdb_summary=10
        )


# --- the members hash carries the settings ---------------------------------


def test_a_definition_with_no_settings_hashes_exactly_as_before():
    """The upgrade must not re-reconcile every managed collection in the
    library to write nothing, so a definition at its defaults has to produce
    the hash the shipped code produced -- byte for byte."""
    items = [SimpleNamespace(ratingKey="m1"), SimpleNamespace(ratingKey="m2")]
    plain = CollectionDefinition(title="X", builder="plex_id", params={"ids": ["1"]})

    assert _members_hash(items, "a summary") == _members_hash(
        items, "a summary", "sync", plain
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("labels", ["Favourites"]),
        ("label_sync", True),
        ("item_label", ["Hand Picked"]),
        ("sort_title", "!010_X"),
        ("collection_mode", "hide"),
        ("visible_library", True),
        ("visible_home", False),
        ("visible_shared", True),
        ("hub_priority", 0),
    ],
)
def test_every_ride_along_setting_changes_the_hash(field, value):
    """The hash is what a pass short-circuits on. A setting missing from it is
    a setting whose edit is recognised as "already current" and never
    applied."""
    items = [SimpleNamespace(ratingKey="m1")]
    plain = CollectionDefinition(title="X", builder="plex_id", params={"ids": ["1"]})
    edited = plain.model_copy(update={field: value})

    assert _members_hash(items, None, "sync", plain) != _members_hash(
        items, None, "sync", edited
    )


async def test_a_settings_only_edit_is_applied_to_an_unchanged_membership(
    session, registry_entry
):
    """The property those hash tests exist for, end to end: two passes over
    the same membership, the second with a label the first did not have."""
    registry_entry(_Listing("settings_reapply", [("imdb", "tt1")]))
    live = FakeCollection("Steady", labels=[LABEL])
    section = _one_item_section(existing=[live])
    plain = CollectionDefinition(title="Steady", builder="settings_reapply")

    await _run(session, section, [plain])
    await _run(session, section, [plain.model_copy(update={"labels": ["Added Later"]})])

    assert live.label_names() == [LABEL, "Added Later"]
