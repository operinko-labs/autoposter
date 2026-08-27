"""Plex query strings as member sets: the instrument the golden gate cannot be.

``tests/fixtures/collections/golden_port.json`` records ``updated_filters`` --
the plexapi CALL SHAPE the Common Sense port removes -- so it cannot grade the
port. What has to be graded is whether the OLD query and the NEW one select the
same items, which is a question about SEMANTICS and not about bytes.

This module answers it by decoding both query strings into the same predicate
tree and evaluating the trees over a fixture library. Five premises, each cited,
so a reviewer can reject one rather than re-derive the module:

P1. ``field=a,b`` (a comma-joined multi-value tag) is OR over the values.
    plexapi constructs exactly that -- ``','.join(result)``, plexapi 4.18.2
    ``library.py:1102`` -- and the shipped Common Sense family is built that
    way: its live membership was verified against production for Movies bucket
    17, Shows bucket 14 and the Shows catch-all
    (``collections/buckets.py:9-11``).
    MEASURED, at P2's standard: ``?type=1&sort=titleSort&contentRating=17%2C16``
    returns 527 items, exactly 291 + 236 -- the recorded counts of the two
    ratings taken separately, so the union to the item. An AND would have
    returned 0, since an item carries one content rating and not two
    (``video.py:393``). ``docs/research/plex-dynamic-probe/README.md`` section 5;
    baselines ``docs/research/plex-search-probe/README.md:161-163, :168-169``.
P2. ``push=1 ... pop=1`` groups; ``or=1`` between adjacent terms is OR and
    ``and=1`` is AND. MEASURED: the same five Spanish subtitle variants return
    442 items under an ``any:`` base and 0 under ``all:``
    (``docs/research/plex-search-probe/README.md``, section 4).
P3. ``sort=`` and ``limit=`` decide what is returned and in what order, never
    what matches (Kometa ``builder.py:4287-4289``). Both are dropped here.
    ``limit`` is ``None`` on both sides of this comparison in any case, and
    ``includeGuids`` -- which plexapi adds and this engine does not -- enriches
    each returned item rather than selecting it (plexapi ``library.py:1266``;
    ``search_url.py``'s module docstring makes the same argument about
    ``includeCollections``).
P4. In a query already scoped by ``type=N``, a bare ``contentRating`` and a
    ``<libtype>.contentRating`` name the same field. 9b's oracle configs ``12``
    and ``17-country-on-a-show`` pin the dotted spelling as Kometa's for a show
    library, and 9b's live probe searched it successfully. This module strips a
    leading ``<libtype>.`` from a field name and SAYS SO rather than treating
    the two spellings as different fields -- which would make the proof pass for
    the wrong reason (two queries that select nothing both select nothing).
    Note precisely what is and is not measured here: the proof MEASURES which
    spelling plexapi emits (``plexapi_side.py`` runs the real
    ``_validateFilterField``) and runs the show case under both. Their SEMANTIC
    equivalence is what P4 asserts, and ``_strip_libtype`` assumes it rather
    than testing it. The live evidence that exists for it: 9b's probe searched
    ``?type=2&sort=titleSort&show.network=126689`` successfully
    (``docs/research/plex-search-probe/README.md:217``) and 9b ships the dotted
    spelling for this very field (``filters.py:613``).
P5. An unencoded ``+`` in a value reaches the server's matcher as a SPACE. This
    is a premise about the SERVER's decoder -- a separate fact from plexapi's
    encoder -- and it is what ``_decode_values`` models by using
    ``unquote_plus``. It decides whether the new side's raw
    ``contentRating=12+`` is matched as ``12+`` or as ``12 ``, and therefore
    which of the ten URL-unsafe shipped ratings move the member set.
    MEASURED: ``?type=1&sort=titleSort&studio=Columbia+Pictures`` returns the
    same 43 items as the recorded ``studio=Columbia%20Pictures`` baseline
    (``docs/research/plex-dynamic-probe/README.md`` section 5; baseline
    ``docs/research/plex-search-probe/README.md:129, :142``). ``studio`` is a
    ``str`` row whose bare form is a *contains* match
    (``plex-search-probe/README.md:148``), so a server taking ``+`` literally
    would have answered 0 -- no studio holds the substring ``Columbia+Pictures``.
    **The divergence does not rest on P5.** Under the alternative model (``+``
    literal) the same ten ratings still diverge -- the four space-only ones plus
    ``R+ - Mild Nudity`` and ``R - 17+ (violence & profanity)``, all of which
    carry a space -- and the OLD, shipped side is what breaks instead. There is
    no decoding model under which the two grammars agree on all ten, and
    ``quote()``, the fix, emits ``%2B``/``%20``/``%26``, which decode identically
    under either. P5 settles WHICH six move and WHOSE fault it is, not WHETHER
    they diverge.

Pure. No Plex, no plexapi, no repository imports: it is an oracle, and an oracle
that imported the thing it grades would grade nothing. It also shares no helper
with either driver, so no single bug can reach both sides of the comparison.
"""
from dataclasses import dataclass
from urllib.parse import unquote_plus

# P3: parameters that decide what is RETURNED and in what order, never what
# matches. Dropped before the tree is built, so neither side's sort spelling
# (plexapi's ``movie.originallyAvailableAt%3Adesc`` against ours,
# ``originallyAvailableAt%3Adesc``) can make two equivalent queries compare
# unequal or two different ones compare equal.
IGNORED = ("type", "sort", "limit", "includeGuids", "title")


@dataclass(frozen=True)
class Term:
    field: str
    values: tuple[str, ...]      # comma-joined values, already split (P1)


@dataclass(frozen=True)
class Group:
    op: str                      # "or" | "and"
    children: tuple


def _decode_values(raw: str) -> tuple[str, ...]:
    """One parameter's raw value, as the values it names (P1).

    ``unquote_plus`` and not ``unquote`` is premise P5, and it is worth being
    exact about which of two different facts it rests on. The first is about
    plexapi's ENCODER: ``urlencode`` (``library.py:1103``) is ``quote_plus``, so
    a rating with a space goes out as ``G+-+All+Ages``. That fact is certain but
    it decides nothing on its own. What this line actually asserts is the
    second fact, about the SERVER's DECODER -- that a ``+`` arriving in a value
    is matched as a space -- because that is what says whether the new side's
    raw ``contentRating=12+`` looks for ``12+`` or for ``12 ``. P5 in the module
    docstring cites the live measurement that settles it (43 items for
    ``studio=Columbia+Pictures``, the ``%20`` baseline exactly) and states why
    the divergence stands under the other model too.

    Using the same decoder on both sides is the point: it is what lets one
    instrument read a value plexapi encoded and a value this engine sent raw,
    and report the difference honestly instead of hiding it.

    Decode first, then split on the comma: plexapi joins and then encodes
    (``','.join(result)`` at :1102, ``urlencode`` at :1103), so a comma inside
    a value is already indistinguishable from a separator on the wire. Splitting
    after decoding reproduces that ambiguity exactly rather than inventing a
    precision the query string does not have. No value in the shipped table
    contains a comma.
    """
    return tuple(unquote_plus(raw).split(","))


def _strip_libtype(field: str, libtype: str) -> str:
    """P4. ``show.contentRating`` and ``contentRating`` under ``type=2`` are one
    field. Only the query's OWN libtype prefix is stripped: a genuinely
    different scope (``episode.contentRating`` in a show query) is a different
    field and stays one."""
    prefix = libtype + "."
    return field[len(prefix):] if field.startswith(prefix) else field


def decode(query: str, libtype: str) -> Group:
    """One query string from ``?`` onward, as a predicate tree.

    ``push``/``pop`` open and close a group; ``or=1``/``and=1`` set the
    conjunction of the group they appear in; everything else is a term. A query
    with no explicit group is one implicit AND group, which is what an ``all:``
    base and plexapi's own flat parameter list both are.

    The split on ``&`` is the query string's own rule and is applied before
    anything is decoded, so a value carrying an unencoded ``&`` splits into two
    parameters here exactly as it would at the server. That is not a limitation
    of this decoder; it is the behaviour under test.
    """
    stack: list[list] = [[]]
    ops: list[str] = ["and"]
    for pair in query.lstrip("?").split("&"):
        if not pair:
            continue
        name, _, raw = pair.partition("=")
        if name == "push":
            stack.append([])
            ops.append("and")
            continue
        if name == "pop":
            children = tuple(stack.pop())
            op = ops.pop()
            stack[-1].append(Group(op, children))
            continue
        if name in ("or", "and"):
            ops[-1] = name
            continue
        if name in IGNORED:      # P3
            continue
        stack[-1].append(Term(_strip_libtype(name, libtype), _decode_values(raw)))
    return Group(ops[0], tuple(stack[0]))


def _matches(node, item: dict) -> bool:
    if isinstance(node, Term):
        held = item.get(node.field)
        return held is not None and held in node.values
    if not node.children:
        # No terms at all is THE WHOLE LIBRARY, which is the failure mode
        # ``SearchProducedNothing`` exists to refuse. Never silently True here:
        # a proof where both sides select everything proves nothing.
        raise ValueError("a query with no predicate matches the whole library")
    results = [_matches(child, item) for child in node.children]
    return any(results) if node.op == "or" else all(results)


def members(query: str, library: list[dict], libtype: str) -> set[str]:
    """The rating keys ``query`` selects out of ``library``.

    ``library`` is ``[{"ratingKey": "m1", "contentRating": "G"}, ...]`` -- one
    dict per item, whose keys are field names in the query's own vocabulary,
    undotted. ``libtype`` is the query's own scope and is used for exactly one
    thing: stripping its prefix off a field name (P4).
    """
    tree = decode(query, libtype)
    return {item["ratingKey"] for item in library if _matches(tree, item)}
