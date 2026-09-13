"""What a dynamic family's collections are CALLED.

Membership is ``dynamic_keys``'; identity is here. The distinction matters
because a collection's identity in this service is ``(library, title)`` -- the
``managed_collections`` uniqueness constraint, the ownership check, and the
sweep's enumeration all key on it -- so a title mistake is not a cosmetic one:
two keys that title the same are one collection whose membership flips every
pass.

Upstream's order, unchanged (modules/meta.py, Kometa v2.4.8):

1. ``key_name`` starts as the enumerated VALUE, not the key (:1355).
2. ``key_name_override[key]`` replaces it, and SUPPRESSES ``remove_prefix``/
   ``remove_suffix`` -- the strip is the ``else`` branch (:1352-1361).
3. Otherwise every matching prefix is stripped in sequence, then every matching
   suffix, each result ``.strip()``ed (:1356-1361).
4. ``title_override[key]`` is the finished title, verbatim, and ``title_format``
   is never applied to it (:1382-1383).
5. Otherwise ``title_format`` has its two library-type tokens substituted
   (:1268-1271), then ``<<title>>`` and ``<<key_name>>`` (:1401), then every
   ``og_call`` variable -- ``<<value>>``, ``<<{auto_type}>>``, ``<<key>>`` --
   unconditionally (:1366, :1402-1404).

What upstream resolves that this does NOT: :1406-1410 substitutes a template's
``default:`` values, and :1272-1273 resolves ``<<limit>>`` from a library-level
template variable. Both are the template system, which this service does not
have, so a ``title_format`` naming any ``<<…>>`` token beyond the seven above
has nowhere to resolve it and would ship the literal token into a live
collection name. Refusing such a format belongs to the params model at config
load, where the operator learns at the moment of the edit -- not here, where the
family is already enumerated.

**Three places this REFUSES where upstream logs and continues**, each because
the upstream behaviour is a setting that reads as applied and is not:

- a ``title_format`` naming neither ``<<key_name>>`` nor ``<<title>>`` is
  reverted to the default upstream (:1234-1236), which titles every collection
  in the family identically and then skips all but the first as duplicates;
- two ``key_name_override`` values that are equal pop a key while iterating
  upstream (:1251-1257) -- a ``RuntimeError`` in CPython 3, not a graceful skip;
- a duplicate generated title is warned and skipped upstream (:1417-1418), and
  the ``other`` collection bypasses the check entirely (:1455). The second is
  the collision predicted against ``buckets.py:66`` -- "Not Rated Movies"
  from an ``other_name`` landing on the Common Sense family's own catch-all
  -- so it is refused here rather than reproduced.

The first two refuse at CONFIG LOAD, in the builder's params model, where an
operator learns at the moment of the edit; the third can only be known once the
library has been enumerated, so it refuses the definition's pass and names both
keys.

**And one place this DROPS where upstream would build.** A key that is the
literal string ``"None"`` is Plex's way of saying "the items with no value for
this field", not a value -- the live probe caught the DVR section
answering ``content_rating`` with ``16=16, None=None``. Upstream would title a
collection "Top None Movies" from it. Here it is the absent case and gets no
collection at all, whatever the override tables say: naming the absence is what
``other_name`` is for, and a title an operator did not choose is exactly the
class of surprise this module exists to refuse. A family left with nothing after
the drop is the empty-enumeration case, refused a layer up.

Pure: no Plex, no config, no I/O.
"""
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from autoposter.collections.dynamic_keys import DerivedKeys

__all__ = [
    "ABSENT_KEY",
    "OTHER_KEY",
    "DuplicateFamilyTitle",
    "TitledKey",
    "family_titles",
    "key_name_for",
    "render_title",
    "title_format_names_the_key",
]

# The literal ``key`` upstream gives the ``other`` bucket (meta.py:1435), so
# ``title_override``/``key_name_override`` can never reach it -- their keys are
# library values. Kept as the same string for the same reason.
OTHER_KEY = "other"

# What Plex reports for "these items have no value for this field". A choice,
# not a tag: see the module docstring's fourth divergence and the DVR probe it
# names.
ABSENT_KEY = "None"


class DuplicateFamilyTitle(Exception):
    """Two keys in one family title the same collection.

    Its own class so the builder can turn it into one definition's refusal
    rather than a dead pass, and so the message can name both keys: an operator
    with a twelve-key family needs to know which two, not that there was a
    clash.
    """


@dataclass(frozen=True)
class TitledKey:
    """One collection-to-be, named.

    ``values`` rides along from ``DynamicKey`` unchanged so the builder has one
    object per collection rather than two lists it has to keep in step.
    """

    key: str
    key_name: str
    title: str
    values: tuple[str, ...]


def title_format_names_the_key(title_format: str) -> bool:
    """meta.py:1234-1236's condition, as a predicate the config layer can ask.

    Upstream reverts a format that fails this to the type's default and logs;
    the builder's params model refuses it instead, naming the rule -- because
    the reverted family builds fine, under names the operator did not write.
    """
    return "<<key_name>>" in title_format or "<<title>>" in title_format


def _strdict(value: object) -> dict[str, str]:
    """Kometa's ``strdict`` (modules/util.py:961): keys AND values ``str()``."""
    if not value:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("expected a mapping of key -> replacement, not %r" % (value,))
    return {str(k): str(v) for k, v in value.items()}


def _affixes(value: object) -> list[str]:
    """Kometa's ``commalist`` (modules/util.py), and deliberately NOT
    ``str()``-coerced: that asymmetry is upstream's own, and coercing here
    would make ``remove_prefix: 20`` strip the digits out of a year's name."""
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",")]
    if not isinstance(value, Iterable):
        raise TypeError("expected a list of affixes, not %r" % (value,))
    return list(value)


def key_name_for(
    key: str,
    value: str,
    *,
    key_name_override: object = None,
    remove_prefix: object = (),
    remove_suffix: object = (),
) -> str:
    """meta.py:1352-1361. The override wins outright and suppresses the strip."""
    overrides = _strdict(key_name_override)
    if key in overrides:
        return overrides[key]
    name = value
    for prefix in _affixes(remove_prefix):
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
    for suffix in _affixes(remove_suffix):
        if name.endswith(suffix):
            name = name[:-len(suffix)].strip()
    return name


def _substitute_library_type(text: str, library_type: str) -> str:
    """meta.py:1268-1271, in upstream's own order.

    That order is NOT load-bearing, which is worth saying because it reads as
    if it were: ``<<library_type>>`` is not a substring of ``<<library_typeU>>``
    -- the ``U`` sits between ``type`` and the closing ``>>`` -- so neither
    replacement can eat the other's token and the two orders are provably
    identical. What IS load-bearing is which one lowercases.
    """
    text = text.replace("<<library_type>>", library_type.lower())
    return text.replace("<<library_typeU>>", library_type)


def _substitute_call_vars(
    title: str,
    *,
    key: str,
    key_name: str,
    values: tuple[str, ...],
    auto_type: str | None,
) -> str:
    """meta.py:1366 and :1402-1404 -- the pass the oracle driver's docstring
    once wrongly called a no-op.

    ``og_call`` is built at :1366 with or without a template, and the loop at
    :1402-1404 is unconditional, so a ``title_format`` carrying ``<<value>>``,
    ``<<{auto_type}>>`` or ``<<key>>`` resolves it here instead of shipping the
    literal token into a collection name. ``<<key_name>>`` is in the dict too
    and is already gone by :1401; it is kept for the dict's real shape.

    ``value`` and ``<<{auto_type}>>`` are THE SAME list object at :1366 and the
    substitution is ``str()`` of it, so both render as a Python list repr
    (``['16']``), not as the members joined. ``values`` is a tuple here and is
    listed back before the ``str()`` so the repr is upstream's, ugly and all.

    ``auto_type`` is ``None`` when the caller names no dynamic type, which drops
    that one entry -- upstream always has it, and a caller who wants
    ``<<{auto_type}>>`` resolved must say which type.
    """
    key_value = list(values)
    og_call: dict[str, object] = {"value": key_value}
    if auto_type is not None:
        og_call[auto_type] = key_value
    og_call["key_name"] = key_name
    og_call["key"] = key
    for var_key, var_val in og_call.items():
        token = "<<%s>>" % var_key
        if token in title:
            title = title.replace(token, str(var_val))
    return title


def render_title(
    title_format: str,
    key_name: str,
    library_type: str,
    *,
    key: str,
    values: tuple[str, ...],
    auto_type: str | None = None,
) -> str:
    """meta.py:1268-1271, :1401, and :1402-1404.

    Upstream substitutes the library type once for the whole family and the key
    name once per key; done together here because the result is the same string
    and one function is easier to prove than two halves that must be called in
    order.

    ``key`` and ``values`` are keyword-only and REQUIRED even though most
    formats name neither: they are what the :1402-1404 pass resolves, and a
    default would mean a format naming ``<<key>>`` silently renders an empty
    string -- the class of surprise this module exists to refuse.
    """
    rendered = _substitute_library_type(title_format, library_type)
    rendered = rendered.replace("<<title>>", key_name).replace("<<key_name>>", key_name)
    return _substitute_call_vars(
        rendered, key=key, key_name=key_name, values=values, auto_type=auto_type,
    )


def _other_title(other_name: str, library_type: str) -> str:
    """meta.py:1306-1310: the ``other`` name gets the same library-type
    substitution ``title_format`` gets, and no key-name substitution at all --
    its key is the literal ``"other"``, which is not a name anybody wants in a
    title."""
    return _substitute_library_type(other_name, library_type)


def family_titles(
    derived: DerivedKeys,
    *,
    library_type: str,
    title_format: str,
    key_name_override: object = None,
    title_override: object = None,
    remove_prefix: object = (),
    remove_suffix: object = (),
    other_name: str | None = None,
    auto_type: str | None = None,
) -> tuple[TitledKey, ...]:
    """Every collection this family builds, named, in the family's own order.

    ``other_name`` is the leftovers bucket and the caller passes it only when
    an ``include`` list exists -- upstream gates it the same way
    (meta.py:1301-1305), and without an ``include`` there are no leftovers for
    it to hold. Its key is the literal ``"other"``, so neither override table
    can reach it, and its VALUES are the leftover keys themselves.

    ``auto_type`` is the dynamic type's own name (``content_rating``,
    ``genre``, ...), which upstream makes a title token of at :1366. Optional
    because most formats never name it; a format that does and is not given one
    keeps its literal token, which is the params model's to refuse.

    A key of ``ABSENT_KEY`` builds nothing (the module docstring says why).

    Raises ``DuplicateFamilyTitle`` if two keys name one collection, the
    ``other`` bucket included -- upstream warns and skips, and exempts ``other``
    from even that (the module docstring says why).
    """
    overrides = _strdict(title_override)
    titled: list[TitledKey] = []
    seen: dict[str, str] = {}

    def claim(key: str, key_name: str, title: str, values: tuple[str, ...]) -> None:
        if title in seen:
            raise DuplicateFamilyTitle(
                "%r and %r both name a collection %r. One collection cannot be "
                "two keys' -- its membership would flip every pass -- so this "
                "definition builds nothing until one of them is renamed with "
                "`title_override`, or one is dropped with `exclude`. (Kometa "
                "warns and skips the second here, modules/meta.py:1417-1418, "
                "and does not check the `other` collection at all, :1455.)"
                % (seen[title], key, title)
            )
        seen[title] = key
        titled.append(
            TitledKey(key=key, key_name=key_name, title=title, values=values)
        )

    for unit in derived.keys:
        if unit.key == ABSENT_KEY:
            continue
        key_name = key_name_for(
            unit.key, unit.value,
            key_name_override=key_name_override,
            remove_prefix=remove_prefix,
            remove_suffix=remove_suffix,
        )
        # :1382-1383 -- membership, not truthiness: an override written as the
        # empty string is still the operator's answer, and falling back to the
        # format there would be the silent-ignore this module refuses.
        title = (
            overrides[unit.key] if unit.key in overrides
            else render_title(
                title_format, key_name, library_type,
                key=unit.key, values=unit.values, auto_type=auto_type,
            )
        )
        claim(unit.key, key_name, title, unit.values)

    # :1432-1434 -- an ``other_name`` with nothing left over creates nothing.
    if other_name and derived.other_keys:
        claim(
            OTHER_KEY, OTHER_KEY,
            _other_title(other_name, library_type), derived.other_keys,
        )
    return tuple(titled)
