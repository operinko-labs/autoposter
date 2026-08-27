"""THE DYNAMIC-COLLECTIONS ORACLE -- Kometa's own key and title derivation.

Provenance: Kometa v2.4.8. Every function below is transcribed from the line
ranges quoted verbatim in ``.superpowers/sdd/p10a-upstream-dynamic.md`` §5 and
§6, which were read out of:

  modules/meta.py:825-867      the exclude/include/addons reads and the
                               addon-members-become-exclusions extension
  modules/meta.py:1217-1228    the addons -> keys merge (``custom_keys``)
  modules/meta.py:1348-1365    the include whitelist and each key's query values
  modules/util.py:917-961      the ``strlist`` / ``dictliststr`` / ``strdict``
                               coercions the three options are read through

NOTHING from the autoposter repository is imported, and nothing outside the
standard library is either: this half of Kometa reads a dict of enumerated
values and three option lists and returns keys -- there is no Plex object, no
TMDb client and no template engine anywhere in it.

REMOVED throughout: every ``logger.*`` call (they warn, they do not decide),
the ``self.temp_vars`` library-level override layer and its
``append_*``/``remove_*`` variants (meta.py:826-861 -- this service has no
library-level template variables, so the ``elif dynamic[...]`` branch is the
only reachable one), and the per-type enumeration itself (meta.py:869-1211),
whose output IS this driver's ``all_pairs`` argument.

Run:  python kometa_dynamic.py
      -> prints one ``N <json>`` line per case
"""
import json


def strlist(value):
    """modules/util.py:933 -- ``strlist``: every element ``str()``.

    util.py:931 (``if v or v == 0``): a falsy element is dropped before the
    ``str()``. ``0`` survives the ``or`` because upstream tests it by
    equality, not truthiness -- a naive ``if v`` would wrongly drop it too.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    return [str(v) for v in value if v or v == 0]


def dictliststr(value):
    """modules/util.py:959 -- ``dictliststr``: keys AND members both ``str()``."""
    if not value:
        return {}
    out = {}
    for key, members in value.items():
        if not isinstance(members, list):
            members = [members]
        out[str(key)] = [str(m) for m in members]
    return out


def derive(all_pairs, *, include=None, exclude=None, addons=None, custom_keys=True):
    """meta.py:825-867, :1217-1228 and :1348-1365, in upstream's own order.

    ``all_pairs`` is the enumeration: an ordered sequence of ``(key, value)``,
    which is what each type's ``all_keys``/``auto_list`` comprehension produces
    (e.g. :933 keys on ``choice.key``, :936 keys on ``choice.title``).
    """
    # :826-829 / :835-839 / :845-849, with the temp_vars layer removed.
    og_exclude = strlist(exclude)
    include = [i for i in strlist(include) if i not in og_exclude]
    addons = dictliststr(addons)

    # :863-867. EVERY addon member is pushed into ``exclude`` -- that is how an
    # addon member stops getting a collection of its own. Note the asymmetry
    # upstream keeps and this keeps with it: ``exclude`` is str()-coerced and
    # grows, while ``og_exclude`` stays as written and is what the custom-key
    # check below consults.
    exclude = [str(e) for e in og_exclude]
    for k, v in addons.items():
        if k in v:
            pass  # logger.warning(f"{k} cannot be an addon for itself")
        exclude.extend([y for y in v if y != k and y not in exclude])

    # The enumeration comprehension, meta.py:932-937: ``all_keys`` is
    # everything the library reported; ``auto_list`` is what survived
    # ``exclude``. A key is dropped when the KEY or the VALUE is excluded.
    # (:928-931's three-value language variant is not this driver's shape:
    # language names here come from Plex's ``choice.title`` rather than
    # TMDb's ISO name, which collapses upstream's ``final_title`` and
    # ``str(i.title)`` into one value and makes the two-value rule above
    # exact.)
    all_keys = {}
    auto_list = {}
    for key, value in all_pairs:
        all_keys[key] = value
        if key not in exclude and value not in exclude:
            auto_list[key] = value

    # :1217-1228 -- the addons->keys merge, which is ``custom_keys``' whole job.
    # The guard means this fires ONLY for an addon key the library does not
    # itself carry: a synthetic bucket. An addon key that IS a real library
    # value keeps its own enumerated entry untouched.
    for add_key, combined_keys in addons.items():
        if add_key not in all_keys and add_key not in og_exclude:
            final_keys = [ck for ck in combined_keys if ck in all_keys]
            if custom_keys and final_keys:
                if add_key not in auto_list:
                    auto_list[add_key] = add_key
                addons[add_key] = final_keys
            elif custom_keys:
                pass  # logger.trace(f"{add_key} Custom Key must have at least one Key")
            else:
                for final_key in final_keys:
                    auto_list[final_key] = all_keys[final_key]

    keys = []
    other_keys = []
    for key, value in auto_list.items():
        # :1348-1351 -- ``include`` is a whitelist applied LAST. An excluded key
        # is neither built nor swept into ``other``; an unincluded-but-not-
        # excluded key becomes an ``other`` member.
        if include and key not in include:
            if key not in exclude:
                other_keys.append(key)
            continue
        # :1362-1365 -- the bucket's query value: the key itself if the library
        # really has it, plus every addon member the library really has, self
        # excluded. (Upstream's ``or auto_type == "custom"`` disjunct is dropped
        # with the ``custom`` type itself, which this service does not ship.)
        key_value = [key] if key in all_keys else []
        if key in addons:
            key_value.extend([a for a in addons[key] if a in all_keys and a != key])
        keys.append({"key": key, "value": value, "values": key_value})
    return {"keys": keys, "other_keys": other_keys}


# The enumerations the cases run against. Shaped like a real
# ``listFilterChoices`` answer for the type named, and shared BY VALUE with
# ``tests/test_collection_dynamic_oracle.py`` -- never by import, in either
# direction.
RATINGS = [
    ("G", "G"), ("PG", "PG"), ("PG-13", "PG-13"), ("R", "R"),
    ("NC-17", "NC-17"), ("Unrated", "Unrated"),
]
DECADES = [("1980", "1980s"), ("1990", "1990s"), ("2000", "2000s")]

KEY_CASES = [
    # 1. The plainest family: every value becomes a key, each asking for itself.
    ("plain", RATINGS, {}),
    # 2. The Common Sense shape -- synthetic buckets over addon members, with
    #    ``include`` naming exactly the bucket keys (the production block's own
    #    invariant). Every member is excluded from having its own collection by
    #    :863-867, and the leftovers land in ``other_keys``.
    ("cs-shaped", RATINGS, {
        "include": ["Kids", "Teens"],
        "addons": {"Kids": ["G", "PG"], "Teens": ["PG-13", "R"]},
    }),
    # 3. The same, with ``custom_keys: false``: no synthetic bucket at all, and
    #    each present member is promoted back to a collection of its own
    #    (:1226-1228) -- which is what re-adds the keys :863-867 excluded.
    ("custom-keys-false", RATINGS, {
        "addons": {"Kids": ["G", "PG"]}, "custom_keys": False,
    }),
    # 4. An addon key that IS a real library value: the merge's guard means the
    #    branch never fires, so the key keeps its own enumerated entry and picks
    #    its members up as extra query values.
    ("addon-key-the-library-has", RATINGS, {
        "addons": {"PG": ["G"]},
    }),
    # 5. An addon list containing its own key -- the AU/NZ shape. ``a != key``
    #    is where the de-dupe happens (:1365), not in a prepend.
    ("addon-list-holds-its-own-key", RATINGS, {
        "addons": {"Teens": ["Teens", "PG-13", "R"]},
    }),
    # 6. ``exclude`` matched against the VALUE rather than the key, which is the
    #    only way to exclude a decade by the name an operator can see.
    ("exclude-by-value", DECADES, {"exclude": ["1990s"]}),
    # 7. ``include`` naming a key the library does not have: no collection, and
    #    nothing in ``other`` either.
    ("include-names-a-missing-key", DECADES, {"include": ["1980", "1970"]}),
    # 8. YAML integer keys and integer members -- the DE/UK certification shape.
    #    ``dictliststr`` and ``strlist`` are what make them match.
    ("integer-keys", [("12", "12"), ("16", "16"), ("18", "18")], {
        "include": [12, 16], "addons": {12: [16]},
    }),
    # 9. The :1218 guard where it BITES. Case 4's addon key is real AND
    #    surviving, so :1221's "if add_key not in auto_list" absorbs the whole
    #    difference and the guard is invisible there. Here the addon key is
    #    real but EXCLUDED -- by its display value, which never reaches
    #    ``og_exclude``'s membership test at :1218. Upstream's guard is the only
    #    thing keeping the decade an operator excluded from coming back as a
    #    synthetic bucket keyed on "1990" instead of "1990s".
    ("addon-key-excluded-by-value", DECADES, {
        "exclude": ["1990s"], "addons": {"1990": ["1980"]},
    }),
    # 10. The guard's other leg: ``custom_keys: false`` does NOT promote the
    #     members of an addon key the library carries, because :1218 skips that
    #     key before :1226 can be reached. So this answers identically to case
    #     4 -- which is the claim, not a coincidence.
    ("custom-keys-false-with-a-key-the-library-has", RATINGS, {
        "addons": {"PG": ["G"]}, "custom_keys": False,
    }),
    # 11. util.py:931's falsy skip (I1): an ``include`` list holding an empty
    #     string, an integer zero, and a real key. "" is falsy and gets
    #     dropped, so the "" key -- never whitelisted -- lands in
    #     ``other_keys`` rather than getting a spurious collection of its own.
    #     ``0`` survives the ``or v == 0`` clause and still matches the real
    #     "0" key: a naive ``if v`` filter would wrongly drop it too, since
    #     ``0`` is itself falsy in Python.
    ("falsy-include-elements", [("", ""), ("0", "0"), ("PG", "PG")], {
        "include": ["PG", "", 0],
    }),
]


def main():
    for index, (name, pairs, options) in enumerate(KEY_CASES, start=1):
        print("%d %s %s" % (index, name, json.dumps(derive(pairs, **options))))


if __name__ == "__main__":
    main()
