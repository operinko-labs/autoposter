"""Every config field's operator-facing description, as dotted paths.

The schema documents its fields in ``#`` comments and class docstrings, which
no served surface can read: the settings page renders a label and a widget and
has nowhere to put the sentence the comment carries (roadmap row 217). So the
operator-facing half of each comment moves onto the field itself, as
``Field(description=...)``, and this module is the single walk that turns those
into the dotted-path map ``GET /config`` serves beside ``frozen_paths``.

Three rules the walk encodes:

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
  entry plus a recursion under a ``[]`` segment: those paths address no row
  today, because the editor renders a list of objects read-only (roadmap row
  138), and they are served so that they do the day that row lands. ``[]`` is
  a marker in this map, never a path the API accepts.
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
