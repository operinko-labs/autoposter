"""Row 85 -- parental-guide labels as a mass op.

One STOP-and-file cell, deliberately absent and must stay absent (see the
row close in the roadmap doc): a vote-count floor (the row and its Kometa-
inventory source name no threshold; the probe observed `"Severe"` off a
single vote). Label removal, filed alongside it at the close, was ruled by
the operator on 2026-09-23: a changed severity swaps the label, never
duplicates it -- and only for categories IMDb reported this time.

The last two tests are the gated-feature entry-point test the memory's law
requires: gate-off byte-identical, gate-on fires through the real seam
(`render.pipeline.apply_metadata`), second pass steady (no label churn when
IMDb's answer hasn't changed).
"""
from pathlib import Path

import httpx as _httpx
import pytest
import pytest_asyncio

from autoposter.config.loader import load_config
from autoposter.config.schema import OperationsConfig
from autoposter.facts.mdblist import NullMDBListClient
from autoposter.facts.models import GatheredFacts
from autoposter.plex.writer import apply_facts, parental_label_edits, plan_edits
from autoposter.render.pipeline import _fetch_parental_categories, apply_metadata

from conftest import seed_media_item
from test_mass_ops_fields import FakeItem, FakeTMDB, _item
from test_mass_ops_verbs import RecordingPlexItem, RecordingServer

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


def test_the_three_fields_default_off():
    operations = OperationsConfig()
    assert operations.parental_labels_enabled is False
    assert operations.parental_labels_apply is False
    assert operations.parental_labels_include_none is False


class LabelledItem(FakeItem):
    """A FakeItem that also carries Plex labels, tag-object shaped."""

    def __init__(self, labels=(), **attrs):
        super().__init__(**attrs)
        self.labels = [_Tag(t) for t in labels]


class _Tag:
    def __init__(self, tag):
        self.tag = tag


CATEGORIES = [
    ("NUDITY", "Sex & Nudity", "Mild"),
    ("VIOLENCE", "Violence & Gore", "Severe"),
    ("PROFANITY", "Profanity", "None"),
]


def test_no_categories_produces_nothing():
    operations = OperationsConfig(parental_labels_apply=True)
    assert parental_label_edits(LabelledItem(), None, operations) == {}
    assert parental_label_edits(LabelledItem(), [], operations) == {}


def test_none_severity_is_excluded_by_default():
    operations = OperationsConfig(parental_labels_apply=True)
    edits = parental_label_edits(LabelledItem(), CATEGORIES, operations)
    assert edits == {"labels.added": ["Sex & Nudity: Mild", "Violence & Gore: Severe"]}


def test_none_severity_is_included_when_configured():
    operations = OperationsConfig(parental_labels_apply=True, parental_labels_include_none=True)
    edits = parental_label_edits(LabelledItem(), CATEGORIES, operations)
    assert edits == {
        "labels.added": [
            "Sex & Nudity: Mild", "Violence & Gore: Severe", "Profanity: None",
        ]
    }


def test_a_label_already_on_the_item_is_not_re_added():
    item = LabelledItem(labels=["Sex & Nudity: Mild"])
    operations = OperationsConfig(parental_labels_apply=True)
    edits = parental_label_edits(item, CATEGORIES, operations)
    assert edits == {"labels.added": ["Violence & Gore: Severe"]}


def test_the_match_is_casefolded_like_every_other_label_comparison():
    """Plex canonicalises label case -- an exact compare would re-add a
    label already present under different casing every single pass."""
    item = LabelledItem(labels=["sex & nudity: mild"])
    operations = OperationsConfig(parental_labels_apply=True)
    edits = parental_label_edits(item, CATEGORIES, operations)
    assert edits == {"labels.added": ["Violence & Gore: Severe"]}


def test_every_label_already_present_produces_no_edit():
    item = LabelledItem(labels=["Sex & Nudity: Mild", "Violence & Gore: Severe"])
    operations = OperationsConfig(parental_labels_apply=True)
    assert parental_label_edits(item, CATEGORIES, operations) == {}


def test_a_changed_severity_swaps_the_label_instead_of_adding_a_second():
    """Operator ruling 2026-09-23: swapped, not duplicated. The old severity
    for a category IMDb still reports goes; the new one comes."""
    item = LabelledItem(labels=["Sex & Nudity: Mild", "Violence & Gore: Moderate"])
    operations = OperationsConfig(parental_labels_apply=True)
    edits = parental_label_edits(item, CATEGORIES, operations)
    assert edits == {
        "labels.added": ["Violence & Gore: Severe"],
        "labels.removed": ["Violence & Gore: Moderate"],
    }


def test_duplicates_left_by_earlier_passes_are_cleared():
    item = LabelledItem(labels=[
        "Sex & Nudity: Mild", "Violence & Gore: Severe",
        "violence & gore: mild", "Sex & Nudity: Severe",
    ])
    operations = OperationsConfig(parental_labels_apply=True)
    edits = parental_label_edits(item, CATEGORIES, operations)
    assert edits == {"labels.removed": ["violence & gore: mild", "Sex & Nudity: Severe"]}


def test_a_severity_that_dropped_to_none_loses_its_label():
    """Profanity is ``None`` in CATEGORIES: with include_none off there is no
    label to swap to, and the stale one would still claim Mild."""
    item = LabelledItem(labels=["Sex & Nudity: Mild", "Violence & Gore: Severe", "Profanity: Mild"])
    operations = OperationsConfig(parental_labels_apply=True)
    edits = parental_label_edits(item, CATEGORIES, operations)
    assert edits == {"labels.removed": ["Profanity: Mild"]}


def test_a_none_label_is_swapped_like_any_other_when_configured():
    item = LabelledItem(labels=["Sex & Nudity: Mild", "Violence & Gore: Severe", "Profanity: Mild"])
    operations = OperationsConfig(parental_labels_apply=True, parental_labels_include_none=True)
    edits = parental_label_edits(item, CATEGORIES, operations)
    assert edits == {
        "labels.added": ["Profanity: None"],
        "labels.removed": ["Profanity: Mild"],
    }


def test_labels_that_are_not_a_reported_category_severity_are_never_removed():
    """Only ``<category IMDb reported>: <one of the four severities>`` is
    ours. A category missing from this answer, a near-miss spelling and any
    other label are left alone -- missing data never strips anything."""
    item = LabelledItem(labels=[
        "Overlay", "Sex & Nudity: Mild", "Violence & Gore: Severe",
        "Frightening & Intense Scenes: Mild", "Violence & Gore: Extreme",
        "Violence & Gore", "Profanity:Mild",
    ])
    operations = OperationsConfig(parental_labels_apply=True)
    assert parental_label_edits(item, CATEGORIES, operations) == {}


def test_no_categories_never_removes_anything():
    item = LabelledItem(labels=["Violence & Gore: Mild"])
    operations = OperationsConfig(parental_labels_apply=True)
    assert parental_label_edits(item, None, operations) == {}
    assert parental_label_edits(item, [], operations) == {}


def test_apply_off_reports_a_swap_and_writes_nothing(caplog):
    item = LabelledItem(labels=["Sex & Nudity: Mild", "Violence & Gore: Moderate"])
    with caplog.at_level("INFO"):
        edits = parental_label_edits(item, CATEGORIES, OperationsConfig())
    assert edits == {}
    assert "would add parental-guide label(s) Violence & Gore: Severe" in caplog.text
    assert "would remove parental-guide label(s) Violence & Gore: Moderate" in caplog.text


def test_apply_off_reports_and_writes_nothing():
    operations = OperationsConfig()  # parental_labels_apply defaults False
    edits = parental_label_edits(LabelledItem(), CATEGORIES, operations)
    assert edits == {}


def test_severity_id_never_reaches_the_label_text():
    """Belt and braces: even a caller that (wrongly) hands this function
    IMDb's internal severity.id-shaped string must not see it echoed back --
    the label text is built from the tuple's own fields, category text and
    severity TEXT only."""
    operations = OperationsConfig(parental_labels_apply=True)
    edits = parental_label_edits(
        LabelledItem(), [("VIOLENCE", "Violence & Gore", "Severe")], operations
    )
    assert "Votes" not in edits["labels.added"][0]


def test_plan_edits_folds_in_parental_labels():
    item = LabelledItem()
    operations = OperationsConfig(parental_labels_apply=True)
    edits = plan_edits(item, GatheredFacts(), operations, parental_categories=CATEGORIES)
    assert edits["labels.added"] == ["Sex & Nudity: Mild", "Violence & Gore: Severe"]


async def test_apply_facts_adds_every_label_in_one_addlabel_call():
    class RecordingLabelItem(LabelledItem):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.addlabel_calls = []
            self.saved = 0

        def batchEdits(self):  # noqa: N802 - plexapi name
            pass

        def edit(self, **fields):
            pass

        def addLabel(self, tags):  # noqa: N802 - plexapi name
            self.addlabel_calls.append(tags)

        def saveEdits(self):  # noqa: N802 - plexapi name
            self.saved += 1

    item = RecordingLabelItem()
    operations = OperationsConfig(parental_labels_apply=True)
    edits = await apply_facts(item, GatheredFacts(), operations, parental_categories=CATEGORIES)

    assert edits["labels.added"] == ["Sex & Nudity: Mild", "Violence & Gore: Severe"]
    assert item.addlabel_calls == [["Sex & Nudity: Mild", "Violence & Gore: Severe"]]
    assert item.saved == 1


def test_every_missing_label_reaches_the_payload_against_real_plexapi():
    """Against plexapi's *real* ``Movie`` -- ``batchEdits()`` accumulates into
    ``item._edits`` with no network I/O, and ``saveEdits()`` is never called.

    One ``addLabel`` per tag inside a batch wrote every tag to the same
    ``label[N]`` slot, so only the last one landed and the item was written
    again on every pass until it had them all.
    """
    import xml.etree.ElementTree as ET

    from plexapi.video import Movie

    from autoposter.plex.writer import _apply_label_edits

    xml = (
        '<Video ratingKey="1" key="/library/metadata/1" type="movie" title="T">'
        '<Label tag="Overlay" /><Label tag="Profanity: Mild" /></Video>'
    )
    item = Movie(server=None, data=ET.fromstring(xml))
    item.batchEdits()
    _apply_label_edits(item, ["Sex & Nudity: Mild", "Violence & Gore: Severe"])

    # The held labels ride along as plexapi ``Label`` objects, which the
    # request's query string renders with ``str()`` -- their tag.
    sent = sorted(
        str(value) for key, value in item._edits.items()
        if key.startswith("label[") and key.endswith("].tag.tag")
    )
    assert sent == sorted(
        ["Overlay", "Profanity: Mild", "Sex & Nudity: Mild", "Violence & Gore: Severe"]
    )
    assert isinstance(item._edits, dict)


def test_a_swap_sends_no_conflicting_directives_against_real_plexapi():
    """The genre path's gotcha, for labels: ``addLabel`` re-lists every held
    label, so without care the stale tag would be listed as kept in the very
    request that removes it. The payload removes it once, re-lists only the
    labels that stay, and leaves the caller's object as it found it."""
    import xml.etree.ElementTree as ET

    from plexapi.video import Movie

    from autoposter.plex.writer import _apply_label_edits

    xml = (
        '<Video ratingKey="1" key="/library/metadata/1" type="movie" title="T">'
        '<Label tag="Overlay" /><Label tag="Profanity: Severe" />'
        '<Label tag="Profanity: Moderate" /></Video>'
    )
    item = Movie(server=None, data=ET.fromstring(xml))
    item.batchEdits()
    _apply_label_edits(item, ["Violence & Gore: Mild"], ["Profanity: Severe"])

    edits = item._edits
    assert edits["label[].tag.tag-"] == "Profanity%3A%20Severe"
    sent = sorted(
        str(value) for key, value in edits.items()
        if key.startswith("label[") and key.endswith("].tag.tag")
    )
    assert sent == ["Overlay", "Profanity: Moderate", "Violence & Gore: Mild"]
    assert edits["label.locked"] == 1
    assert [label.tag for label in item.labels] == [
        "Overlay", "Profanity: Severe", "Profanity: Moderate",
    ]


def test_a_removal_alone_sends_only_the_removal_against_real_plexapi():
    import xml.etree.ElementTree as ET

    from plexapi.video import Movie

    from autoposter.plex.writer import _apply_label_edits

    xml = (
        '<Video ratingKey="1" key="/library/metadata/1" type="movie" title="T">'
        '<Label tag="Overlay" /><Label tag="Profanity: Mild" /></Video>'
    )
    item = Movie(server=None, data=ET.fromstring(xml))
    item.batchEdits()
    _apply_label_edits(item, [], ["Profanity: Mild"])

    assert item._edits == {"label.locked": 1, "label[].tag.tag-": "Profanity%3A%20Mild"}


@pytest.fixture
def config():
    return load_config(EXAMPLE)


@pytest_asyncio.fixture
async def media_item_id(session):
    media = await seed_media_item(session, "1", library="Movies", kind="movie", title="Heat")
    return media.id


class FakeImdbParental:
    def __init__(self, result):
        self._result = result
        self.calls = []

    async def categories(self, imdb_id):
        self.calls.append(imdb_id)
        return self._result


async def test_fetch_returns_none_without_an_imdb_id():
    client = FakeImdbParental(CATEGORIES)
    result = await _fetch_parental_categories(client, _item(imdb_id=None))
    assert result is None
    assert client.calls == []


async def test_fetch_returns_none_for_a_season():
    client = FakeImdbParental(CATEGORIES)
    result = await _fetch_parental_categories(client, _item(kind="season"))
    assert result is None
    assert client.calls == []


async def test_fetch_delegates_to_the_client_for_a_movie():
    client = FakeImdbParental(CATEGORIES)
    result = await _fetch_parental_categories(client, _item())
    assert result == CATEGORIES
    assert client.calls == ["tt0113277"]


async def test_fetch_returns_none_on_a_transport_error(caplog):
    class FailingClient:
        async def categories(self, imdb_id):
            raise _httpx.HTTPError("boom")

    with caplog.at_level("WARNING"):
        result = await _fetch_parental_categories(FailingClient(), _item())
    assert result is None
    assert "IMDb parental-guide request failed" in caplog.text


# --- the gated-feature entry-point test (Global Constraint) -----------------

async def test_entry_point_gate_off_is_byte_identical(session, media_item_id, config):
    # (a) GATE OFF. parental_labels_enabled unset -- apply_metadata must
    # produce exactly the edits it produced before this row existed.
    plex_item = RecordingPlexItem(studio="Warner", locks=[])
    plex_item.labels = []
    imdb_parental = FakeImdbParental(CATEGORIES)

    await apply_metadata(
        session, config, media_item_id, _item(), RecordingServer(plex_item),
        FakeTMDB(GatheredFacts()), NullMDBListClient(), imdb_parental=imdb_parental,
    )
    assert plex_item.edits == []
    assert imdb_parental.calls == []  # not even fetched -- enabled is off


async def test_entry_point_gate_on_fires_and_the_second_pass_is_steady(
    session, media_item_id, config
):
    # (b) GATE ON: the fetch and the write both fire through the real seam.
    config.operations.parental_labels_enabled = True
    config.operations.parental_labels_apply = True
    plex_item = RecordingPlexItem(studio="Warner", locks=[])
    plex_item.labels = []
    imdb_parental = FakeImdbParental(CATEGORIES)

    await apply_metadata(
        session, config, media_item_id, _item(), RecordingServer(plex_item),
        FakeTMDB(GatheredFacts()), NullMDBListClient(), imdb_parental=imdb_parental,
    )
    assert plex_item.edits[-1] == {
        "labels.added": ["Sex & Nudity: Mild", "Violence & Gore: Severe"]
    }
    assert imdb_parental.calls == ["tt0113277"]

    # (c) SECOND PASS: steady state. The client is asked again (this test
    # double has no cache of its own -- ProviderCache is what makes
    # a real second pass free), but the labels are already on the item, so
    # no second label write happens.
    plex_item.labels = [_Tag("Sex & Nudity: Mild"), _Tag("Violence & Gore: Severe")]
    edits_before = len(plex_item.edits)
    await apply_metadata(
        session, config, media_item_id, _item(), RecordingServer(plex_item),
        FakeTMDB(GatheredFacts()), NullMDBListClient(), imdb_parental=imdb_parental,
    )
    assert len(plex_item.edits) == edits_before


async def test_entry_point_swaps_a_changed_severity_and_the_next_pass_is_steady(
    session, media_item_id, config
):
    config.operations.parental_labels_enabled = True
    config.operations.parental_labels_apply = True
    plex_item = RecordingPlexItem(studio="Warner", locks=[])
    plex_item.labels = [_Tag("Overlay"), _Tag("Sex & Nudity: Mild"), _Tag("Violence & Gore: Mild")]
    imdb_parental = FakeImdbParental(CATEGORIES)

    await apply_metadata(
        session, config, media_item_id, _item(), RecordingServer(plex_item),
        FakeTMDB(GatheredFacts()), NullMDBListClient(), imdb_parental=imdb_parental,
    )
    assert plex_item.edits[-1] == {
        "labels.added": ["Violence & Gore: Severe"],
        "labels.removed": ["Violence & Gore: Mild"],
    }

    plex_item.labels = [_Tag("Overlay"), _Tag("Sex & Nudity: Mild"), _Tag("Violence & Gore: Severe")]
    edits_before = len(plex_item.edits)
    await apply_metadata(
        session, config, media_item_id, _item(), RecordingServer(plex_item),
        FakeTMDB(GatheredFacts()), NullMDBListClient(), imdb_parental=imdb_parental,
    )
    assert len(plex_item.edits) == edits_before
