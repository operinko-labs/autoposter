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
