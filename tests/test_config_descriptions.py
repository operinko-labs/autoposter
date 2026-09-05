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
from typing import get_args

from pydantic import BaseModel

from autoposter.config.descriptions import (
    FIELD_DESCRIPTIONS,
    _item_model_of,
    _mapping_model_of,
    _model_of,
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
    from autoposter.config.descriptions import (
        _item_model_of,
        _mapping_model_of,
        _model_of,
    )

    direct = _model_of(annotation)
    if direct is not None:
        return [(direct, "")]
    item = _item_model_of(annotation)
    if item is not None:
        return [(item, "[]")]
    # Roadmap row 92: a mapping of models is reached under a `{}` wildcard,
    # a whole segment of its own because what it stands in for -- a Plex
    # library name -- is a whole segment, unlike `[]`'s list index.
    mapping = _mapping_model_of(annotation)
    return [] if mapping is None else [(mapping, ".{}")]


def test_every_path_in_the_map_addresses_a_real_schema_field():
    """A path the schema cannot resolve is a description no row can ever show."""
    unresolved = []
    for path in FIELD_DESCRIPTIONS:
        model: type[BaseModel] | None = Config
        for segment in path.split("."):
            if segment == "{}":
                # The mapping wildcard. `_nested_models` already advanced the
                # model to the mapping's VALUE type when it produced this
                # segment, so there is no field here to resolve -- exactly as
                # `[]` carries no field of its own.
                continue
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


def _model_types_reachable(annotation) -> bool:
    """True if this annotation is a shape ``_model_of``/``_item_model_of``/
    ``_mapping_model_of`` cover: a model, ``Model | None``, ``list[Model]``
    or ``dict[str, Model]`` (each optionally wrapped in the other)."""
    return (
        _model_of(annotation) is not None
        or _item_model_of(annotation) is not None
        or _mapping_model_of(annotation) is not None
    )


def _model_types_present(annotation) -> set[type]:
    """Every ``BaseModel`` subclass named anywhere in this annotation's type
    arguments, however deeply wrapped -- a generic scan independent of the two
    helpers above, so it can tell when their vocabulary has missed one."""
    found: set[type] = set()
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        found.add(annotation)
        return found
    for arg in get_args(annotation):
        found |= _model_types_present(arg)
    return found


def _unsupported_container_fields() -> list[str]:
    """Every field, across every model class ``config/schema.py`` defines,
    whose annotation names a ``BaseModel`` type in a container shape neither
    ``_model_of`` nor ``_item_model_of`` can reach -- a ``dict[str, Model]`` or
    a ``tuple[Model, ...]``, say. The walk and the guard both find nested
    models through only those two helpers, so a field shaped like this would
    be served silently as nothing, rather than failing loudly."""
    import autoposter.config.schema as schema_module

    offenders = []
    for obj in vars(schema_module).values():
        if not (isinstance(obj, type) and issubclass(obj, BaseModel)):
            continue
        if obj.__module__ != schema_module.__name__:
            continue
        for name, field in obj.model_fields.items():
            if _model_types_reachable(field.annotation):
                continue
            if _model_types_present(field.annotation):
                offenders.append(f"{obj.__name__}.{name}")
    return offenders


def test_unsupported_container_shape_is_detected():
    """The detector's own correctness, pinned against a throwaway model rather
    than the real schema.

    Repointed by roadmap row 92, not relaxed: ``dict[str, Model]`` USED to be
    the offender here and is now a shape the walk covers (a ``{}`` wildcard
    segment, so ``libraries.{}.operations.enabled`` gets a description like
    any other leaf). The guard still has to be able to see a shape the walk's
    vocabulary misses, so it is pinned against one that is genuinely still
    unreachable -- a ``tuple[Model, ...]``. Deleting this test along with the
    shape it named would have left the completeness guard with nothing
    proving it can fail at all.
    """

    class _Nested(BaseModel):
        name: str = ""

    class _Holder(BaseModel):
        by_key: dict[str, _Nested] = {}
        pairs: tuple[_Nested, ...] = ()
        plain: str = ""
        wrapped: _Nested | None = None
        many: list[_Nested] = []

    offenders = []
    for name, field in _Holder.model_fields.items():
        if _model_types_reachable(field.annotation):
            continue
        if _model_types_present(field.annotation):
            offenders.append(name)
    assert offenders == ["pairs"]


def test_every_model_holding_field_uses_a_shape_the_walk_covers():
    """The completeness guard's own blind spot, closed: a future field whose
    annotation buries a model inside a container the walk's vocabulary does
    not cover (``dict[str, Model]``, ``tuple[Model, ...]``) fails here loudly,
    instead of being served as nothing and passing every other test silently
    (settings-clarity review, Minor 2)."""
    offenders = _unsupported_container_fields()
    assert offenders == [], (
        "fields whose model is buried in an unsupported container shape: "
        f"{offenders}"
    )
