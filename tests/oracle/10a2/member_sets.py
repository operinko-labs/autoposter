"""Plex query strings as member sets: the instrument the golden gate cannot be.

``tests/fixtures/collections/golden_port.json`` records ``updated_filters`` --
the plexapi CALL SHAPE the Common Sense port removes -- so it cannot grade the
port. What has to be graded is whether the OLD query and the NEW one select the
same items, which is a question about SEMANTICS and not about bytes.

This module answers it by decoding both query strings into the same predicate
tree and evaluating the trees over a fixture library. Four premises, each cited,
so a reviewer can reject one rather than re-derive the module:

P1. ``field=a,b`` (a comma-joined multi-value tag) is OR over the values.
    plexapi constructs exactly that -- ``','.join(result)``, plexapi 4.18.2
    ``library.py:1102`` -- and the shipped Common Sense family is built that
    way: its live membership was verified against production for Movies bucket
    17, Shows bucket 14 and the Shows catch-all
    (``collections/buckets.py:9-11``).
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

    ``unquote_plus`` and not ``unquote``: plexapi builds its side with
    ``urlencode`` (``library.py:1103``), which is ``quote_plus``, so a rating
    with a space arrives as ``G+-+All+Ages`` and a server that did not turn
    ``+`` back into a space would be reading a different value than the one
    plexapi sent. Decoding the way the sender encoded is what makes this a
    model of the wire rather than of one library's habits -- and it is what
    lets the same decoder read a value this engine sent UNencoded and report
    the difference honestly instead of hiding it.

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
