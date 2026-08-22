# Phase 5b: Cutover Verification Sweep — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every unknown that could bite at cutover is answered with evidence,
recorded in the roadmap's gap table, and any trivial exercised gap an answer
exposes is closed in the same PR. This is the last gate before retiring
Posterizarr and Kometa.

**Architecture:** This phase is mostly investigation, not construction. Each
task answers a cluster of the roadmap's verification rows
(`docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`, Part 1, rows
1–16) with file:line evidence or live output, writes the answer into the
roadmap's Notes column, and fixes only what is small, exercised, and certain.
An answer that reveals a non-trivial gap becomes a scoped follow-up row —
**never a rushed patch**.

## Global Constraints

- `.superpowers/sdd/p4c-verified-facts.md` binds throughout.
- **Answers carry evidence, not conclusions.** File:line for code answers;
  verbatim output for behavioural ones; a pixel comparison for row 4. "It
  looks fine" is not an answer.
- **The roadmap doc is the ledger of record**: each answered row's Notes cell
  gets `answered 5b: <one line>` (+ a pointer to the report for detail). A
  revealed gap gets a new row with a difficulty estimate instead.
- Fix-in-place criteria (ALL must hold): the gap is exercised by the user's
  config, the fix is < ~50 lines, and it cannot regress byte-parity (any fix
  touching `render/` must re-run the golden tests in the container).
- Every fix follows the phase discipline: failing test → red → green →
  mutation proof where it guards something.
- Container-only testing; one pytest session at a time; never
  `docker compose down -v`; no backgrounding; stage by name; `--no-gpg-sign`;
  no AI attribution.

## Task 1: Code-answer sweep (rows 1, 2, 3, 5, 8, 13, 15)

Pure reading plus at most trivial tests. For each row: the question, the
answer with file:line, and — where the answer is "no, autoposter does not do
this" — whether the user's config actually exercises it (re-check the
inventory's claim rather than trusting it).

- Row 1: does `collections/buckets.py` create Plex-native smart collections or
  reconciled dumb ones?
- Row 2: is `collection_order: custom` genuinely applied by `reconcile.py`?
- Row 3: `plex/writer.py` lock semantics vs Kometa's lock/unlock behaviour.
- Row 5: do created collections get `collection_mode` / sort-title prefixes?
- Row 8: are title-card text lines (`AddEPTitleText`/`AddEPText` equivalents)
  independently toggleable or unconditional?
- Row 13: skip-words: regex-capable? Is a matching existing card deleted?
- Row 15: `render/naming.py` vs characters Plex allows but filesystems do not
  (write the cheap test: a title with `: / ? *` renders to a legal path).

## Task 2: Collection poster parity (row 4 — the one that matters most)

The single *exercised* rendering path whose parity is unconfirmed: Posterizarr
composites styled title text onto collection posters
(`CollectionTitlePosterPart`; fonts are configured in the user's config), and
whether autoposter's collection-poster path (phase 3g) does the same or only
uploads hosted images as-is, and whether an `AddCollectionTitle` equivalent is
honoured.

Method, in the project's harvest-the-oracle style: read the 3g path
(`collections/posters.py`) first; if it composites, pixel-compare its output
for one real collection against the Posterizarr-styled oracle (harvest one
from the live Plex if none is in `tests/fixtures/`); if it does not composite,
that is the gap — measure its size honestly (it may be a genuine 5b fix if the
compositor from phase 1 is reusable on a 1000×1500 canvas with the configured
font, or a follow-up row if not). Do not guess which; find out.

## Task 3: Multiple versions (row 14 — may reveal a real gap)

Posterizarr covers all versions of a movie/show (theatrical vs director's
cut). Does autoposter's per-item model? Read how `media_items` maps to Plex
rating keys when one title has multiple versions/editions; check what the
webhook intake does with a second version's import event; state what happens
today with evidence (a live-Plex probe against a known multi-version item if
the user's library has one — the adoption walk's data may already answer it).
If a gap: scope it as a follow-up row with a difficulty estimate. **Explicitly
not a rushed patch.**

## Task 4: Throughput and the cheap remainder (rows 16; 6, 7, 9–12 if cheap)

- Row 16: measure a forced full-library pass with the merged full-pass
  trigger against the fingerprint-gated pipeline in the compose stack — the
  question is whether a full re-render is tolerable without Posterizarr's
  text-size cache. Report the number; no fix expected.
- Rows 6, 7, 9–12 (parity-only unknowns): answer any that fall out of reading
  already done in Tasks 1–3; defer the rest to 7a explicitly in the roadmap
  rather than silently.

## Task 5: Record, fix-round, PR

Roadmap Notes column updated for every row touched; trivial exercised fixes
committed with their tests; follow-up rows added for anything scoped out.
PR `feat/phase5b-verification` → `main` via `tea`, description summarising
each row's verdict in one line. The PR is the cutover-readiness document.
