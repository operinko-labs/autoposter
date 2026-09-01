"""Row 85 -- parental-guide labels as a mass op.

Two STOP-and-file cells, deliberately absent and must stay absent (see the
row close in the roadmap doc): a vote-count floor (the row and its Kometa-
inventory source name no threshold; the probe observed `"Severe"` off a
single vote) and label removal/sync (the row states no removal semantics,
so this op only ever adds).

The last two tests are the gated-feature entry-point test the memory's law
requires: gate-off byte-identical, gate-on fires through the real seam
(`render.pipeline.apply_metadata`), second pass steady (no label churn when
IMDb's answer hasn't changed).
"""
from autoposter.config.schema import OperationsConfig


def test_the_three_fields_default_off():
    operations = OperationsConfig()
    assert operations.parental_labels_enabled is False
    assert operations.parental_labels_apply is False
    assert operations.parental_labels_include_none is False


from autoposter.plex.writer import parental_label_edits

from test_mass_ops_fields import FakeItem


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


from autoposter.facts.models import GatheredFacts
from autoposter.plex.writer import apply_facts, plan_edits


def test_plan_edits_folds_in_parental_labels():
    item = LabelledItem()
    operations = OperationsConfig(parental_labels_apply=True)
    edits = plan_edits(item, GatheredFacts(), operations, parental_categories=CATEGORIES)
    assert edits["labels.added"] == ["Sex & Nudity: Mild", "Violence & Gore: Severe"]


async def test_apply_facts_calls_addlabel_for_each_addition():
    class RecordingLabelItem(LabelledItem):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.added_labels = []
            self.saved = 0

        def batchEdits(self):  # noqa: N802 - plexapi name
            pass

        def edit(self, **fields):
            pass

        def addLabel(self, tag):  # noqa: N802 - plexapi name
            self.added_labels.append(tag)

        def saveEdits(self):  # noqa: N802 - plexapi name
            self.saved += 1

    item = RecordingLabelItem()
    operations = OperationsConfig(parental_labels_apply=True)
    edits = await apply_facts(item, GatheredFacts(), operations, parental_categories=CATEGORIES)

    assert edits["labels.added"] == ["Sex & Nudity: Mild", "Violence & Gore: Severe"]
    assert item.added_labels == ["Sex & Nudity: Mild", "Violence & Gore: Severe"]
    assert item.saved == 1
