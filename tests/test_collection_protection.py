"""Protecting collections that belong to a tool we do not replace.

Maintainerr labels its own collections -- and, in one observed case on the
live server, does not label them at all. Where it does label them, that
label must win unconditionally: over ownership, over adoption, even over a
collection that also happens to carry an ``adopt_from`` label. That last
case is the point of this guard -- if the previous tool had clobbered a
Maintainerr collection with its own label, adoption would otherwise sweep
it up.
"""
from autoposter.collections.lists import reconcile_list_collection
from autoposter.collections.reconcile import reconcile_content_ratings
from autoposter.config.schema import CollectionsConfig
from test_collection_lists import FakeCollection as ListCollection
from test_collection_lists import FakeItem
from test_collection_lists import FakeSection as ListSection
from test_collection_reconcile import FakeCollection as SmartCollection
from test_collection_reconcile import FakeSection as SmartSection

LABEL = "autoposter"
MAINTAINERR = "Collection managed by Maintainerr"


# --- smart (Common Sense) reconciler ---


async def test_smart_a_protected_collision_is_not_modified_and_reported_distinctly(session):
    theirs = SmartCollection("Age 17+ Movies", labels=[MAINTAINERR])
    section = SmartSection({"R", "17"}, existing=[theirs])
    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
        protect_labels=[MAINTAINERR],
    )
    assert theirs.updated_filters is None
    assert theirs.labels_added == []
    assert any("protected" in a.lower() for a in actions)
    assert not any("conflict" in a.lower() for a in actions)


async def test_smart_a_protected_and_adopt_from_label_together_is_still_not_adopted(session):
    """The case the guard exists for: a previous tool may have clobbered a
    Maintainerr collection with its own label."""
    theirs = SmartCollection("Age 17+ Movies", labels=[MAINTAINERR, "Kometa"])
    section = SmartSection({"R", "17"}, existing=[theirs])
    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
        adopt=True, adopt_from=["Kometa"], protect_labels=[MAINTAINERR],
    )
    assert theirs.labels_added == []
    assert theirs.updated_filters is None
    assert any("protected" in a.lower() for a in actions)


async def test_smart_protection_applies_even_with_adopt_disabled(session):
    """Not merely an adoption rule -- it also blocks the ordinary conflict path."""
    theirs = SmartCollection("Age 17+ Movies", labels=[MAINTAINERR])
    section = SmartSection({"R", "17"}, existing=[theirs])
    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
        adopt=False, protect_labels=[MAINTAINERR],
    )
    assert theirs.labels_added == []
    assert any("protected" in a.lower() for a in actions)


async def test_smart_a_collision_without_any_protected_label_is_unaffected(session):
    theirs = SmartCollection("Age 17+ Movies", labels=["Kometa"])
    section = SmartSection({"R", "17"}, existing=[theirs])
    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
        adopt=True, adopt_from=["Kometa"], protect_labels=[MAINTAINERR],
    )
    assert theirs.labels_added == [LABEL]
    assert any("claimed" in a.lower() for a in actions)


async def test_smart_an_empty_protect_list_disables_the_guard(session):
    theirs = SmartCollection("Age 17+ Movies", labels=[MAINTAINERR])
    section = SmartSection({"R", "17"}, existing=[theirs])
    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
        protect_labels=[],
    )
    assert any("conflict" in a.lower() for a in actions)
    assert not any("protected" in a.lower() for a in actions)


async def test_smart_matching_is_exact_not_a_substring(session):
    theirs = SmartCollection(
        "Age 17+ Movies", labels=["XCollection managed by MaintainerrX"],
    )
    section = SmartSection({"R", "17"}, existing=[theirs])
    actions = await reconcile_content_ratings(
        session, section, "Movies", "Movie", LABEL, dry_run=False,
        protect_labels=[MAINTAINERR],
    )
    assert any("conflict" in a.lower() for a in actions)
    assert not any("protected" in a.lower() for a in actions)


# --- list reconciler ---


async def test_lists_a_protected_collision_is_not_modified_and_reported_distinctly(session):
    theirs = ListCollection("IMDb Top 250", items=[FakeItem("a")], labels=[MAINTAINERR])
    section = ListSection([theirs])
    actions = await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=False,
        protect_labels=[MAINTAINERR],
    )
    assert theirs.added == []
    assert LABEL not in {label.tag for label in theirs.labels}
    assert any("protected" in a.lower() for a in actions)
    assert not any("conflict" in a.lower() for a in actions)


async def test_lists_a_protected_and_adopt_from_label_together_is_still_not_adopted(session):
    theirs = ListCollection(
        "IMDb Top 250", items=[FakeItem("a")], labels=[MAINTAINERR, "Kometa"],
    )
    section = ListSection([theirs])
    actions = await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=False,
        adopt=True, adopt_from=["Kometa"], protect_labels=[MAINTAINERR],
    )
    assert theirs.added == []
    assert LABEL not in {label.tag for label in theirs.labels}
    assert any("protected" in a.lower() for a in actions)


async def test_lists_protection_applies_even_with_adopt_disabled(session):
    theirs = ListCollection("IMDb Top 250", items=[FakeItem("a")], labels=[MAINTAINERR])
    section = ListSection([theirs])
    actions = await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=False,
        adopt=False, protect_labels=[MAINTAINERR],
    )
    assert theirs.added == []
    assert any("protected" in a.lower() for a in actions)


async def test_lists_a_collision_without_any_protected_label_is_unaffected(session):
    theirs = ListCollection("IMDb Top 250", items=[FakeItem("a")], labels=["Kometa"])
    section = ListSection([theirs])
    actions = await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=False,
        adopt=True, adopt_from=["Kometa"], protect_labels=[MAINTAINERR],
    )
    assert LABEL in {label.tag for label in theirs.labels}
    assert any("claimed" in a.lower() for a in actions)


async def test_lists_an_empty_protect_list_disables_the_guard(session):
    theirs = ListCollection("IMDb Top 250", items=[FakeItem("a")], labels=[MAINTAINERR])
    section = ListSection([theirs])
    actions = await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=False,
        protect_labels=[],
    )
    assert any("conflict" in a.lower() for a in actions)
    assert not any("protected" in a.lower() for a in actions)


async def test_lists_matching_is_exact_not_a_substring(session):
    theirs = ListCollection(
        "IMDb Top 250", items=[FakeItem("a")],
        labels=["Collection managed by Maintainerr, sort of"],
    )
    section = ListSection([theirs])
    actions = await reconcile_list_collection(
        session, section, "Movies", "IMDb Top 250",
        [FakeItem("a"), FakeItem("b")], LABEL, dry_run=False,
        protect_labels=[MAINTAINERR],
    )
    assert any("conflict" in a.lower() for a in actions)
    assert not any("protected" in a.lower() for a in actions)


# --- pinned against the real server data ---


def test_the_default_protect_labels_cover_the_observed_maintainerr_label():
    """Pinned against the live server audit: Movies 'Deleted Soon' carries
    exactly this label. If a future edit narrows the default, this must
    fail loudly rather than silently stop protecting it."""
    assert CollectionsConfig().protect_labels == [MAINTAINERR]


async def test_real_maintainerr_collection_is_never_adopted_under_the_default_config(session):
    """The exact scenario the guard exists for, using the real strings
    observed on the live server: if the previous tool had clobbered
    Maintainerr's 'Deleted Soon' with its own 'Kometa' label, the default
    configuration must still refuse to adopt it."""
    config = CollectionsConfig(adopt=True)
    theirs = ListCollection(
        "Deleted Soon", items=[FakeItem("a")], labels=[MAINTAINERR, "Kometa"],
    )
    section = ListSection([theirs])
    actions = await reconcile_list_collection(
        session, section, "Movies", "Deleted Soon",
        [FakeItem("a"), FakeItem("b")], config.ownership_label,
        dry_run=False, adopt=config.adopt, adopt_from=config.adopt_from,
        protect_labels=config.protect_labels,
    )
    assert theirs.added == []
    assert config.ownership_label not in {label.tag for label in theirs.labels}
    assert any("protected" in a.lower() for a in actions)
