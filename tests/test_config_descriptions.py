"""Every config field says what it does, and the map can address every one.

Roadmap row 217. The settings page has no place to put documentation that
lives in a ``#`` comment, so the operator-facing half of each comment moves
onto the field as ``Field(description=...)`` and is served as a dotted-path
map. These are the guards that keep that true for fields nobody has written
yet: a new setting added in a year with no description fails here, at the
cheapest possible moment, instead of shipping as a silent row on a page whose
whole point is that nothing on it is silent.

Three rules, three tests:

1. every path in the map addresses a real field, and every leaf the endpoint
   serves has an entry in the map (the endpoint half lives in
   ``test_api_config_editor.py``, where the client fixture is);
2. every field's description is non-empty -- every model, no exemptions (the
   list of classes awaiting their batch is gone, and a new one must never be
   added: a permanent exemption list is how a field stays undescribed
   forever);
3. no description says WHEN a setting takes effect -- ``frozen_paths`` owns
   that fact and the editor already renders it on the restart pill, so a
   second copy here would be a copy that drifts.
"""
from pydantic import BaseModel

from autoposter.config.descriptions import (
    FIELD_DESCRIPTIONS,
    build_field_descriptions,
)
from autoposter.config.schema import Config, Secrets


def _described_models() -> list[tuple[str, type[BaseModel], str]]:
    """Every model the served config reaches, with the path prefix it sits at.

    ``Secrets`` is not a field of ``Config`` -- the endpoint injects a redacted
    block for it -- so it is named here for the same reason the walk names it:
    the map mirrors what is served, not what the model holds.
    """
    found: dict[str, tuple[str, type[BaseModel], str]] = {}

    def visit(model: type[BaseModel], prefix: str) -> None:
        if model.__name__ in found:
            return
        found[model.__name__] = (model.__name__, model, prefix)
        for name, field in model.model_fields.items():
            for nested, segment in _nested_models(field.annotation):
                visit(nested, f"{prefix}{name}{segment}.")

    visit(Config, "")
    visit(Secrets, "secrets.")
    return list(found.values())


def _nested_models(annotation):
    """The models an annotation reaches, each with its path segment."""
    from autoposter.config.descriptions import _item_model_of, _model_of

    direct = _model_of(annotation)
    if direct is not None:
        return [(direct, "")]
    item = _item_model_of(annotation)
    return [] if item is None else [(item, "[]")]


def test_every_path_in_the_map_addresses_a_real_schema_field():
    """A path the schema cannot resolve is a description no row can ever show."""
    unresolved = []
    for path in FIELD_DESCRIPTIONS:
        model: type[BaseModel] | None = Config
        for segment in path.split("."):
            name = segment[:-2] if segment.endswith("[]") else segment
            if path.startswith("secrets.") and model is Config:
                model = Secrets
                if name == "secrets":
                    continue
            if model is None or name not in model.model_fields:
                unresolved.append(path)
                break
            field = model.model_fields[name]
            nested = _nested_models(field.annotation)
            model = nested[0][0] if nested else None
    assert unresolved == [], f"paths that address no field: {unresolved}"


def test_every_config_field_carries_a_description():
    """The completeness guard: a field with no description is a silent row."""
    missing = []
    for class_name, model, prefix in _described_models():
        for name, field in model.model_fields.items():
            if not (field.description or "").strip():
                missing.append(f"{class_name}.{name} (serves {prefix}{name})")
    assert missing == [], (
        "every setting the page renders has to say what it does; these do not: "
        f"{missing}"
    )


def test_a_description_never_says_when_a_setting_takes_effect():
    """WHAT, not WHEN. ``frozen_paths`` owns the restart requirement and the
    editor already renders it as the restart pill's own tooltip; a second copy
    inside a description is a copy that drifts out of date silently."""
    timing = ("restart", "takes effect", "at the next pass", "until the process")
    offenders = [
        (path, word)
        for path, description in build_field_descriptions().items()
        for word in timing
        if word in description.lower()
    ]
    assert offenders == [], f"descriptions that describe timing: {offenders}"
