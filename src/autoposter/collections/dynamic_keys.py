"""Which keys a dynamic family builds, and what each one asks Plex for.

The generalisation of ``collections/buckets.py``. That module is one hardcoded
instance of this: a fixed include list, a fixed addon table, "the bucket's own
key when the library carries it plus every addon candidate the library carries",
and the leftovers as a set complement. Here the table is the operator's and the
enumeration is the library's, and the order of operations is Kometa's own
(modules/meta.py:825-867, :1217-1228, :1348-1365) because the CS family is one
input to it and must come out unchanged.

Four rules that look like details and are not:

- **Every addon member is excluded from having its own collection**
  (meta.py:863-867). That is what makes a bucket a bucket rather than a
  duplicate of its members.
- **The merge only fires for an addon key the library does not carry**
  (meta.py:1217-1218). An addon key that IS a real value keeps its own
  enumerated entry and simply gains extra query values -- there is no synthetic
  bucket shadowing a real one.
- **``custom_keys: false`` is not "no addons"**, it is "no synthetic buckets":
  each present member is promoted back to a collection of its own
  (meta.py:1226-1228), which un-does the exclusion above for exactly those
  members.
- **``include`` is a whitelist applied LAST** (meta.py:1348-1351), and an
  unincluded-but-not-excluded key falls into ``other_keys``. With no
  ``include`` the ``other`` bucket is dead, which is why ``other_name`` is
  gated on it upstream and here.

Pure. No Plex, no config, no I/O: it takes the enumeration as data, which is
what lets the whole of it be proven against Kometa's own loop in
``tests/test_collection_dynamic_oracle.py``.
"""
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

__all__ = ["DerivedKeys", "DynamicKey", "derive_keys"]


@dataclass(frozen=True)
class DynamicKey:
    """One collection-to-be.

    ``key`` is what ``include``/``exclude``/``addons`` and the overrides are
    matched against; ``value`` is the display string the title is built from
    (the two are the same for every type that keys on ``choice.title``);
    ``values`` is what the query asks Plex for, which is the key itself plus
    every addon member the library actually carries, self excluded.
    """

    key: str
    value: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class DerivedKeys:
    """The whole family: its keys, its leftovers, and what it consumed.

    ``other_keys`` is the ``other`` bucket's membership -- keys the library has
    that no ``include`` entry named. ``used_keys`` is every value the built
    keys consumed, which is upstream's ``used_keys`` (meta.py:1365): the
    complement-shaped reading of the same leftovers, kept because it is the one
    an ``other`` bucket written as "everything not already claimed" needs, and
    because ``buckets.derive_buckets`` computes exactly it today.
    """

    keys: tuple[DynamicKey, ...]
    other_keys: tuple[str, ...]
    used_keys: tuple[str, ...]


def _strlist(value: object) -> list[str]:
    """Kometa's ``strlist`` (modules/util.py:933): every element ``str()``.

    Not decoration: an operator's ``include: [12, 16]`` is YAML integers and
    the enumeration's keys are strings, so without this the two never meet and
    the family silently builds nothing. The DE and UK certification tables are
    written with integer keys upstream.
    """
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        value = [value]
    return [str(one) for one in value]


def _dictliststr(value: object) -> dict[str, list[str]]:
    """Kometa's ``dictliststr`` (modules/util.py:959): keys AND members ``str()``."""
    if not value:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError("addons is a mapping of key -> members, not %r" % (value,))
    out: dict[str, list[str]] = {}
    for key, members in value.items():
        if isinstance(members, (str, bytes)) or not isinstance(members, Iterable):
            members = [members]
        out[str(key)] = [str(one) for one in members]
    return out


def derive_keys(
    enumerated: Sequence[tuple[str, str]],
    *,
    include: object = (),
    exclude: object = (),
    addons: object = None,
    custom_keys: bool = True,
) -> DerivedKeys:
    """Kometa's key derivation, whole.

    ``enumerated`` is ``(key, value)`` in the library's own order -- what
    ``LibraryTagResolver.choices`` returns, mapped through the type's
    ``key_from`` rule. Order is preserved end to end, so the family's
    collections are created in the order Plex reported its values, and a
    synthetic bucket appears after every real key.
    """
    written_exclude = _strlist(exclude)
    included = [one for one in _strlist(include) if one not in written_exclude]
    addon_table = _dictliststr(addons)

    # meta.py:863-867. ``excluded`` grows with every addon member; the WRITTEN
    # list stays as it was, because the merge below consults it rather than the
    # grown one -- an addon key an operator explicitly excluded must not come
    # back as a synthetic bucket.
    excluded = list(written_exclude)
    for key, members in addon_table.items():
        excluded.extend([m for m in members if m != key and m not in excluded])

    # meta.py:928-937. ``present`` is everything the library reported;
    # ``surviving`` is what ``exclude`` left, matched against the key OR the
    # display value -- an operator excluding "1990s" means the decade whose key
    # is "1990".
    present: dict[str, str] = {}
    surviving: dict[str, str] = {}
    for key, value in enumerated:
        present[key] = value
        if key not in excluded and value not in excluded:
            surviving[key] = value

    # meta.py:1217-1228.
    for add_key, members in addon_table.items():
        if add_key in present or add_key in written_exclude:
            continue
        real = [m for m in members if m in present]
        if custom_keys and real:
            surviving.setdefault(add_key, add_key)
            addon_table[add_key] = real
        elif not custom_keys:
            for member in real:
                surviving[member] = present[member]

    keys: list[DynamicKey] = []
    other_keys: list[str] = []
    used_keys: list[str] = []
    for key, value in surviving.items():
        # meta.py:1348-1351.
        if included and key not in included:
            if key not in excluded:
                other_keys.append(key)
            continue
        # meta.py:1362-1365.
        values = [key] if key in present else []
        values.extend(
            [m for m in addon_table.get(key, ()) if m in present and m != key]
        )
        used_keys.extend(values)
        keys.append(DynamicKey(key=key, value=value, values=tuple(values)))

    return DerivedKeys(
        keys=tuple(keys),
        other_keys=tuple(other_keys),
        used_keys=tuple(used_keys),
    )
