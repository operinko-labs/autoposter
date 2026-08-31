"""The example config must actually mean what it says.

Pydantic ignores unknown keys, so a setting written under the wrong section is
silently dropped and the default applies instead. That is worse than an error:
the file documents a choice the application never makes. This happened -- the
`charts` and `awards` toggles sat under `cleanup:` rather than `collections:`,
so setting `charts: false` there would have had no effect at all.
"""
from pathlib import Path

import yaml

from autoposter.config.schema import Config

# Resolved from this file, not the working directory, so the suite does not
# depend on where pytest was invoked from -- every other test file does the
# same.
EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


def _model_for(field):
    annotation = field.annotation
    return annotation if hasattr(annotation, "model_fields") else None


def test_every_key_in_the_example_exists_in_the_schema():
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    unknown: list[str] = []

    for key, value in data.items():
        field = Config.model_fields.get(key)
        if field is None:
            unknown.append(key)
            continue
        model = _model_for(field)
        if model is None or not isinstance(value, dict):
            continue
        for subkey in value:
            if subkey not in model.model_fields:
                unknown.append("%s.%s" % (key, subkey))

    assert not unknown, (
        "these keys are in the example config but not in the schema, so they "
        "are silently ignored: %s" % ", ".join(sorted(unknown))
    )


def test_the_collections_toggles_are_under_collections():
    """Regression: these were under `cleanup:`, where they did nothing."""
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    assert "charts" in data["collections"]
    assert "awards" in data["collections"]
    assert "charts" not in data.get("cleanup", {})


def test_the_artwork_modes_section_is_recognized_by_the_schema():
    """The Phase 7b section is a real Config field, so its keys are applied
    rather than silently dropped (the failure this whole file exists to catch).
    """
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    model = _model_for(Config.model_fields["artwork_modes"])
    assert model is not None
    for subkey in data["artwork_modes"]:
        assert subkey in model.model_fields


def test_every_schema_section_appears_in_the_example():
    """Roadmap row 167 -- the REVERSE walk. The three tests above check
    example -> schema, so a brand-new top-level Config section can ship with
    no line in the example and the suite stays green (the near-miss on
    record: deleting the whole ``tracearr:`` block reddened exactly one test
    in the entire suite, and only because it names ``tracearr`` by hand).
    Section level only, deliberately: a subkey-level reverse would red
    immediately on the many optional subkeys the example rightly omits,
    which is a different and larger decision.

    The ONE exemption: ``version`` is the derived render-version hash
    (config/loader.py's render_version), never operator-set, so the example
    deliberately omits it.
    """
    data = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    missing = set(Config.model_fields) - set(data)

    assert missing == {"version"}, (
        "these schema sections have no line in the example config, so an "
        "operator cannot discover them: %s" % ", ".join(sorted(missing - {"version"}))
    )
