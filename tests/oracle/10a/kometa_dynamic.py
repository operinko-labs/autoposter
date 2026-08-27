"""THE DYNAMIC-COLLECTIONS ORACLE -- Kometa's own key and title derivation.

Provenance: Kometa v2.4.8. Every function below is transcribed from the line
ranges quoted verbatim in ``.superpowers/sdd/p10a-upstream-dynamic.md`` §5 and
§6, which were read out of:

  modules/meta.py:825-867      the exclude/include/addons reads and the
                               addon-members-become-exclusions extension
  modules/meta.py:1217-1228    the addons -> keys merge (``custom_keys``)
  modules/meta.py:1348-1365    the include whitelist and each key's query values
  modules/meta.py:1268-1271    the two library-type substitutions, done ONCE
                               before the key loop
  modules/meta.py:1352-1361    the key name: ``key_name_override`` and, in its
                               ``else`` branch only, the prefix/suffix strip
  modules/meta.py:1366         ``og_call`` -- the four per-key variables
  modules/meta.py:1382-1404    the title: ``title_override`` outright, else
                               ``title_format`` with ``<<title>>``/``<<key_name>>``
                               and then every ``og_call`` variable
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

ALSO REMOVED from the title half: the ``name_format`` template variable and the
GitHub translation lookup with its unresolvable-``<<…>>`` fall-back
(meta.py:1385-1400 and :1411-1416) -- both are the template system, which this
service does not have, so ``_base`` at :1400 is always ``title_format``.

NOT removed, and the correction the Task 4 review forced on this docstring: the
substitution pass at :1402-1404 is **not** a no-op. It runs unconditionally,
over the ``og_call`` dict built at :1366 -- ``{"value": key_value, auto_type:
key_value, "key_name": key_name, "key": key}`` -- which exists with or without a
template. So a ``title_format`` carrying ``<<value>>``, ``<<{auto_type}>>`` or
``<<key>>`` resolves it there rather than shipping the literal token into a
collection name, and ``key_name_and_title`` below transcribes that.

The one half genuinely without a counterpart here is :1406-1410, the template
``default:`` values. Upstream really does run it -- :1288 installs
``default_templates[auto_type]`` when no ``template:`` is given -- but its
``default:`` map belongs to the template system and this service has nothing to
read it from. That is also why :1272-1273's ``<<limit>>`` is deliberately
untranscribed: its only two resolution paths are ``self.temp_vars["limit"]``
(the removed library-level variable layer) and that same template ``default:``
map, so there is no value this driver could substitute for it.

Run:  python kometa_dynamic.py
      -> prints one ``K<n> <json>`` line per key case, then one ``T<n> <json>``
         line per title case
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


def strdict(value):
    """modules/util.py:961 -- ``strdict``: keys AND values both ``str()``."""
    if not value:
        return {}
    return {str(k): str(v) for k, v in value.items()}


def commalist(value):
    """modules/util.py -- ``commalist``: comma-split, and NOT str()-coerced.

    The one option of the six that is not coerced, which is upstream's own
    asymmetry and is why a numeric prefix does not strip.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [part.strip() for part in str(value).split(",")]


def key_name_and_title(key, value, *, library_type, title_format,
                       key_name_override=None, title_override=None,
                       remove_prefix=None, remove_suffix=None,
                       auto_type=None, values=None):
    """meta.py:1268-1271 (the library-type substitution), :1352-1361 (the key
    name), :1366 (``og_call``) and :1382-1404 (the title).

    ``library_type`` is Kometa's ``library.type``: ``<<library_type>>`` is its
    lowercase form and ``<<library_typeU>>`` is it unchanged (meta.py:1268-1271).

    ``values`` is upstream's ``key_value`` (:1362-1365) -- the query values this
    key asks for. It defaults to ``[key]``, which is what :1362 produces for a
    key the library really carries and what the oracle test's
    ``DynamicKey(values=(key,))`` passes. ``auto_type`` is the dynamic type's
    own name (``content_rating``, ``genre``, ...), which :1366 makes a token of.
    """
    key_name_override = strdict(key_name_override)
    title_override = strdict(title_override)

    # :1268-1271, in upstream's own order -- ``<<library_type>>`` first, then
    # ``<<library_typeU>>``. The order is NOT load-bearing and that is worth
    # stating, because it reads as if it were: ``<<library_type>>`` is not a
    # substring of ``<<library_typeU>>`` (the closing ``>>`` is separated by the
    # ``U``), so neither replacement can eat the other's token and the two
    # orders are provably identical. Transcribed in upstream's order anyway --
    # this file is a transcription, not a rewrite.
    title_format = title_format.replace("<<library_type>>", library_type.lower())
    title_format = title_format.replace("<<library_typeU>>", library_type)

    if key in key_name_override:                                   # :1352-1353
        key_name = key_name_override[key]
    else:
        key_name = value
        for prefix in commalist(remove_prefix):                    # :1356-1358
            if key_name.startswith(prefix):
                key_name = key_name[len(prefix):].strip()
        for suffix in commalist(remove_suffix):                    # :1359-1361
            if key_name.endswith(suffix):
                key_name = key_name[:-len(suffix)].strip()

    if key in title_override:                                      # :1382-1383
        # Verbatim, and the whole :1384-1416 else branch -- the og_call pass
        # below included -- is skipped with it.
        return {"key_name": key_name, "title": title_override[key]}

    # :1401. With no template system there is no ``name_format`` and no
    # translation name, so ``_base`` is ``title_format``.
    title = title_format.replace("<<title>>", key_name).replace("<<key_name>>", key_name)

    # :1366 then :1402-1404, in that order and unconditionally -- ``og_call``
    # is built whether or not a template was named. ``value`` and the
    # auto_type's own name are THE SAME list object at :1366, and the
    # substitution is ``str()`` of it, so both render as a Python list repr
    # (``['16']``) rather than the members joined. ``key_name`` is in the dict
    # too and is already gone by :1401; it is kept for the dict's real shape.
    key_value = [key] if values is None else list(values)
    og_call = {"value": key_value}                                 # :1366
    if auto_type is not None:
        og_call[auto_type] = key_value
    og_call["key_name"] = key_name
    og_call["key"] = key
    for var_key, var_val in og_call.items():                       # :1402-1404
        if "<<%s>>" % var_key in title:
            title = title.replace("<<%s>>" % var_key, str(var_val))

    # :1406-1410 (the template ``default:`` values) is NOT transcribed: this
    # service has no template system to read a ``default:`` map from. The
    # module docstring says what that leaves unresolvable.
    return {"key_name": key_name, "title": title}


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


TITLE_CASES = [
    # 1. The default shape every tag type gets (meta.py:959).
    ("general-default", "Horror", "Horror", "Movie",
     "Top <<key_name>> <<library_type>>s", {}),
    # 2. The show wording of the same, which is the only thing the two
    #    library-type tokens can get wrong.
    ("general-default-show", "Drama", "Drama", "Show",
     "Top <<key_name>> <<library_type>>s", {}),
    # 3. Movie decade: the key and the display value differ (meta.py:933).
    ("decade", "1980", "1980s", "Movie",
     "Best <<library_type>>s of the <<key_name>>", {}),
    # 4. Both library-type tokens in one format -- the CS certification shape
    #    (``<<key_name>> <<library_typeU>>s``) beside the lowercase one.
    ("both-library-type-tokens", "5", "5", "Movie",
     "<<key_name>> <<library_typeU>>s for a <<library_type>> library", {}),
    # 5. ``key_name_override`` rewrites the name BEFORE the format, and
    #    SUPPRESSES the prefix/suffix strip -- the strip is the else branch
    #    (meta.py:1354-1361). "the " is the load-bearing prefix: it is what the
    #    OVERRIDE would lose if the strip were not suppressed, and without it an
    #    implementation that strips the override's own text answers identically
    #    and the case proves nothing but "the override replaces the value".
    #    "BBC " is narrative rather than discriminating -- it shows what the
    #    VALUE would have lost had there been no override, and the case stays
    #    RED under that mutation with "the " alone.
    ("key-name-override-suppresses-the-strip", "BBC One", "BBC One", "Show",
     "Top <<key_name>> <<library_type>>s",
     {"key_name_override": {"BBC One": "the BBC"},
      "remove_prefix": ["BBC ", "the "]}),
    # 6. Prefixes then suffixes, each stripped in sequence and each ``.strip()``ed.
    ("prefix-and-suffix", "The Studio Ltd", "The Studio Ltd", "Movie",
     "Top <<key_name>> <<library_type>>s",
     {"remove_prefix": ["The "], "remove_suffix": [" Ltd"]}),
    # 7. ``title_override`` replaces the finished title outright, and the
    #    format is never applied to it.
    ("title-override", "R", "R", "Movie", "Top <<key_name>> <<library_type>>s",
     {"title_override": {"R": "Grown-Up Movies"}}),
    # 8. ``<<title>>`` is the other accepted token for the same value
    #    (meta.py:1401).
    ("title-token", "1990", "1990s", "Movie", "<<title>> Cinema", {}),
    # 9. ``<<key>>`` -- an ``og_call`` variable (:1366) that :1402-1404
    #    substitutes AFTER :1401 and unconditionally. This docstring called
    #    that range a no-op until the Task 4 review proved otherwise. The
    #    decade shape is where it shows, because the key ("1980") and the key
    #    name ("1980s") differ, so a driver that skipped the pass would ship
    #    the literal "<<key>>" into the title instead.
    ("key-token", "1980", "1980s", "Movie",
     "Best <<library_type>>s of the <<key_name>> (<<key>>)", {}),
    # 10. ``<<value>>`` and ``<<{auto_type}>>`` -- the same list object at
    #     :1366, so both resolve to a Python LIST REPR rather than the members
    #     joined, and og_call's insertion order between them is invisible
    #     precisely because they are one object. That identity is the claim.
    ("type-token", "16", "16", "Movie",
     "<<key_name>> <<library_type>>s tagged <<value>> and <<content_rating>>",
     {"auto_type": "content_rating"}),
]


def main():
    for index, (name, pairs, options) in enumerate(KEY_CASES, start=1):
        print("K%d %s %s" % (index, name, json.dumps(derive(pairs, **options))))
    for index, case in enumerate(TITLE_CASES, start=1):
        name, key, value, library_type, title_format, options = case
        print("T%d %s %s" % (index, name, json.dumps(key_name_and_title(
            key, value, library_type=library_type,
            title_format=title_format, **options,
        ))))


if __name__ == "__main__":
    main()
