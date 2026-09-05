"""Every config field's operator-facing description, as dotted paths.

The schema documents its fields in ``#`` comments and class docstrings, which
no served surface can read: the settings page renders a label and a widget and
has nowhere to put the sentence the comment carries (roadmap row 217). So the
operator-facing half of each comment moves onto the field itself, as
``Field(description=...)``, and this module is the single walk that turns those
into the dotted-path map ``GET /config`` serves beside ``frozen_paths``.

Four rules the walk encodes:

* A description says WHAT a setting does, never WHEN it takes effect. The
  restart requirement is ``config/live.FROZEN_SECTIONS``' job, is already
  served as ``frozen_paths`` and is already rendered on the restart pill. One
  fact, one owner.
* ``Secrets`` is walked even though it is no field of ``Config``: the endpoint
  injects a redacted ``secrets`` block into the response, so the map has to
  mirror what is *served* rather than what the model holds.
* A field whose annotation is a model gets both an entry of its own -- an
  unset optional submodel renders as one leaf row, and a set one renders as a
  section heading -- and a recursion beneath it. A ``list`` of models gets an
  entry plus a recursion under a ``[]`` segment. The generic Settings editor
  still renders a list of objects read-only, so the paths it addresses are the
  scalar ones; the ``[]`` paths are read by the Custom collections panel's
  edit form (roadmap row 138), for the eleven ``CollectionDefinition`` fields
  it edits -- which is what those entries were served for before there was a
  row to hang them on. ``[]`` is a marker in this map, never a path the API
  accepts.
* A field whose annotation is a ``dict`` of models gets an entry plus a
  recursion under a ``{}`` segment -- ``libraries.{}.operations.enabled``
  (roadmap row 92). The wildcard stands in for a key that is DATA rather than
  schema: a Plex library name, which this map cannot enumerate and must not
  try to. It is a whole segment rather than a suffix because what it replaces
  is a whole segment, unlike ``[]``'s list index. The per-library matrix panel
  substitutes the library name into it, the way the Custom collections panel
  reads the ``[]`` paths. ``{}``, like ``[]``, is a marker in this map and
  never a path the API accepts.
"""
from types import UnionType
from typing import Union, get_args, get_origin

from pydantic import BaseModel

from autoposter.config.schema import Config, Secrets


def _model_of(annotation) -> type[BaseModel] | None:
    """The model an annotation holds, unwrapping ``X | None``."""
    if (
        get_origin(annotation) is None
        and isinstance(annotation, type)
        and issubclass(annotation, BaseModel)
    ):
        return annotation
    if get_origin(annotation) in (Union, UnionType):
        for arg in get_args(annotation):
            found = _model_of(arg)
            if found is not None:
                return found
    return None


def _item_model_of(annotation) -> type[BaseModel] | None:
    """The model a ``list[Model]`` annotation holds, unwrapping ``X | None``."""
    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        return _model_of(args[0]) if args else None
    if origin in (Union, UnionType):
        for arg in get_args(annotation):
            found = _item_model_of(arg)
            if found is not None:
                return found
    return None


def _mapping_model_of(annotation) -> type[BaseModel] | None:
    """The model a ``dict[str, Model]`` annotation holds, unwrapping ``X | None``.

    The third container this walk knows. It exists for ``Config.libraries``
    (roadmap row 92), whose keys are Plex library NAMES rather than field
    names -- so the shape is described ONCE, under a ``{}`` segment, and the
    page substitutes the name in. Without it a model buried in a mapping is
    served as nothing at all, which is precisely what
    ``tests/test_config_descriptions.py``'s completeness guard exists to make
    loud rather than let ship.

    Only the VALUE type is inspected. The key type is always ``str`` here and
    could not carry a description anyway: there is no field to hang one on.
    """
    origin = get_origin(annotation)
    if origin is dict:
        args = get_args(annotation)
        return _model_of(args[1]) if len(args) == 2 else None
    if origin in (Union, UnionType):
        for arg in get_args(annotation):
            found = _mapping_model_of(arg)
            if found is not None:
                return found
    return None


def _walk(model: type[BaseModel], prefix: str = "") -> dict[str, str]:
    described: dict[str, str] = {}
    for name, field in model.model_fields.items():
        path = f"{prefix}{name}"
        described[path] = field.description or ""
        nested = _model_of(field.annotation)
        if nested is not None:
            described.update(_walk(nested, f"{path}."))
            continue
        item = _item_model_of(field.annotation)
        if item is not None:
            described.update(_walk(item, f"{path}[]."))
            continue
        mapping = _mapping_model_of(field.annotation)
        if mapping is not None:
            described.update(_walk(mapping, f"{path}.{{}}."))
    return described


def build_field_descriptions() -> dict[str, str]:
    """The whole map, freshly walked. ``FIELD_DESCRIPTIONS`` is the cached one."""
    described = _walk(Config)
    described.update(_walk(Secrets, "secrets."))
    return described


# Computed once: the schema is a set of classes, not a runtime value, so this
# cannot change between requests -- the same reasoning that makes
# ``FROZEN_SECTIONS`` a module-level dict.
FIELD_DESCRIPTIONS: dict[str, str] = build_field_descriptions()
