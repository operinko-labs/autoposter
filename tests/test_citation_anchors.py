"""Roadmap row 216: the resolve-to-anchor guard for by-line citations into
``tests/oracle/9b/kometa_build_filter.py``.

Why it exists: search-tails-1's Task 2 inserted a ``CHOICES`` block into the
vendored transcription and every by-line citation below the insertion point
silently moved. Nothing failed -- a citation is a comment, so a stale one
reads as authoritative while pointing at the wrong construct. 41 citations
were corrected across THREE successive human enumeration rounds, each of
which missed sites the next one found; only the fourth, mechanical pass came
back empty. This file is that mechanical pass, standing.

Scope -- src/ and tests/ only, and the exclusions are deliberate:

- The roadmap (docs/superpowers/specs/2026-08-22-full-parity-roadmap.md)
  carries 8 more refs (3 filename-anchored on rows 169/170/172 plus 5 bare
  continuations), but row 216's own line holds meta-citations ABOUT
  citations (`filters.py:1220`'s `:282`) whose antecedent a mechanical
  tracker resolves wrong. Excluded; the exclusion is documented in the row's
  close as an accepted residual.
- The frozen plan documents under docs/superpowers/plans/ keep their
  as-authored numbers by design (a historical brief rewritten is a record
  falsified), so a guard walking them would red on them by design.
- .superpowers/ is gitignored scratch, not a citing surface the repo owns.

The two anchor rules -- and an anchorless citation FAILS rather than skips,
so a new citation can never pass vacuously:

1. identifier anchor: any FILTER_ATTRIBUTES row name (``filters.BY_NAME``)
   appearing word-bounded in the citing construct;
2. quoted-fragment anchor: any backtick-delimited literal in the construct,
   ``{...}`` placeholders stripped, fragments of 8+ characters kept -- the
   two Kometa raise-site citations (oracle :962/:964) carry their anchors
   this way ("attribute is blank" / "must be a dictionary").

A range citation (``:184-188``) resolves if ANY anchor appears anywhere in
the range -- the citing construct legitimately also names attributes whose
own citations point outside the range.

The citing construct is the enclosing segment: files are split at lines
opening a ``FilterAttribute(`` entry, a ``def``, or any column-0 statement,
so one table entry's note, one test's docstring-plus-body, or one function's
comment block each read as one construct.

Bare continuations (``(:355)``, ``at :361``, ``/:964``) resolve against the
last file named in a ``<file>:<line>`` reference -- WHATEVER file that is:
several constructs cite Kometa's upstream ``plex.py`` and ``builder.py``
(files not in this repo) and then continue bare, and a tracker that only
remembered the oracle's name would mis-resolve those nine into the oracle
and fail spuriously. The antecedent resets at every blank line.

The census is the drift alarm the human rounds lacked: a new citation, a
deleted one, or a shape these regexes stop parsing all move the count.
"""
import re
from pathlib import Path

from autoposter.collections.filters import BY_NAME

ROOT = Path(__file__).resolve().parent.parent
ORACLE = ROOT / "tests" / "oracle" / "9b" / "kometa_build_filter.py"
SURFACES = (
    ROOT / "src" / "autoposter" / "collections" / "filters.py",
    ROOT / "src" / "autoposter" / "collections" / "builders" / "plex_search.py",
    ROOT / "tests" / "test_collection_filters.py",
    ROOT / "tests" / "test_builder_plex_search.py",
)
# 17 filename-anchored + 9 bare continuations. The whole inventory is 34
# counting the roadmap's 8, which sit outside this guard's scope (see the
# module docstring).
EXPECTED_REF_COUNT = 26

_FILE_REF = re.compile(r"\b([\w./-]+\.(?:py|md|ts|tsx|yml|yaml)):(\d+)(?:-(\d+))?")
_BARE_REF = re.compile(r"(?<![\w:]):(\d+)(?:-(\d+))?")
_BACKTICKED = re.compile(r"``([^`]+)``|`([^`]+)`")
_PLACEHOLDER = re.compile(r"\{[^}]*\}")
_SEGMENT_OPEN = re.compile(r"^\s*FilterAttribute\(|^\s*(?:async )?def |^\S")


def _oracle_refs():
    """Every citation of the oracle across SURFACES, with its construct:
    ``(surface, line_no, cited_lo, cited_hi, construct_text)``."""
    refs = []
    for surface in SURFACES:
        lines = surface.read_text(encoding="utf-8").splitlines()
        segment_ids, current = [], 0
        for line in lines:
            if _SEGMENT_OPEN.match(line):
                current += 1
            segment_ids.append(current)
        antecedent = None
        for i, line in enumerate(lines, 1):
            if not line.strip():
                antecedent = None
                continue
            spans, here = [], []
            for m in _FILE_REF.finditer(line):
                antecedent = m.group(1).rsplit("/", 1)[-1]
                spans.append(m.span())
                if antecedent == ORACLE.name:
                    here.append((int(m.group(2)), int(m.group(3) or m.group(2))))
            for m in _BARE_REF.finditer(line):
                if any(a <= m.start() < b for a, b in spans):
                    continue  # the :N half of a _FILE_REF match on this line
                if antecedent == ORACLE.name:
                    here.append((int(m.group(1)), int(m.group(2) or m.group(1))))
            if not here:
                continue
            construct = "\n".join(
                text for text, seg in zip(lines, segment_ids)
                if seg == segment_ids[i - 1]
            )
            for lo, hi in here:
                refs.append((surface, i, lo, hi, construct))
    return refs


def _anchors(construct: str) -> set[str]:
    names = {
        name for name in BY_NAME
        if re.search(r"\b%s\b" % re.escape(name), construct)
    }
    fragments = set()
    for m in _BACKTICKED.finditer(construct):
        for fragment in _PLACEHOLDER.split(m.group(1) or m.group(2)):
            fragment = fragment.strip()
            if len(fragment) >= 8:
                fragments.add(fragment)
    return names | fragments


def test_every_oracle_citation_resolves_to_its_anchor():
    oracle_lines = ORACLE.read_text(encoding="utf-8").splitlines()
    for surface, line_no, lo, hi, construct in _oracle_refs():
        site = f"{surface.name}:{line_no} -> {ORACLE.name}:{lo}" + (
            f"-{hi}" if hi != lo else ""
        )
        anchors = _anchors(construct)
        assert anchors, (
            f"{site} cites the oracle with no anchor to check against -- "
            "give the citing text an attribute name or a backticked quote "
            "of the cited line, rather than weakening this guard"
        )
        cited = "\n".join(oracle_lines[lo - 1:hi])
        assert any(anchor in cited for anchor in anchors), (
            f"{site} does not resolve: none of the citing construct's "
            f"anchors {sorted(anchors)!r} appears on the cited line(s):\n"
            f"{cited}"
        )


def test_the_citation_census_is_exact():
    """The count is what notices a NEW citation entering the surface
    unguarded, or the parse silently going blind -- the failure mode each
    human enumeration round hit. Same idiom as the table's own column-total
    checksums in test_collection_filters.py."""
    refs = [
        (surface.name, line_no, lo, hi)
        for surface, line_no, lo, hi, _ in _oracle_refs()
    ]
    assert len(refs) == EXPECTED_REF_COUNT, (
        "the oracle-citation census moved (expected %d, found %d) -- update "
        "EXPECTED_REF_COUNT only after confirming every added or removed "
        "site is deliberate: %r" % (EXPECTED_REF_COUNT, len(refs), refs)
    )
