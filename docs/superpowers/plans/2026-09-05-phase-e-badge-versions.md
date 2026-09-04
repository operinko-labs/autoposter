# Phase E — badges derived over every `<Media>` version (roadmap row 106) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Derive the `resolution`, `audio_codec` and `video_format` badge values
from **every** `<Media>` version an item carries — an any-of read, exactly as
Kometa's four shipped default files do it — and award each badge by Kometa's own
group/weight table, so an item whose DTS-HD MA 4K remux sits at `media[1]` stops
being badged from whatever Plex happened to put at `media[0]`.

**Architecture:** All of it lands in one module, `src/autoposter/badges/values.py`.
Three of `MediaInfo`'s eleven fields change from "the primary version's scalar"
to "the item's tuple" **in their existing positional slots** —
`video_resolution` → `video_resolutions`, `audio_codec` → `audio_track_titles`,
`file_path` → `file_paths` — and `hdr_flags` becomes the union across every
version. `media_info_from_plex` collapses its two walks into one walk of
`item.media` that fills them. The three accessors (`resolution_image`,
`audio_codec_image`, `video_format_text`) keep their signatures and gain three
transcribed weight tables plus one shared comparator written to
`modules/overlays.py:582-586`'s strict `>`. `badges/compose.py` is not edited:
`badge_fingerprint` keeps its exact body, and only the `values` dict handed to it
moves.

**Tech Stack:** Python 3.12+, pytest, Pillow, Docker Compose. **No new
dependency, no Alembic migration, no config key, no new asset, no frontend
change, no extra Plex request** — every version is already in the payload
`media_info_from_plex`'s existing `item.reload()` fetches.

**Spec:** `.superpowers/sdd/p-phase-e-facts.md` (the controller's binding
rulings **C1**, adjudications A-1…A-8) and `.superpowers/sdd/p-phase-e-recon.md`
(the evidence those rulings argue from). Executors read both; where the recon
and C1 differ, **C1 wins** — see "Deviations from the recon", below.

---

## Read this first — what governs this plan

1. **Kometa never picks a `<Media>`.** This is the finding the whole phase turns
   on, and the roadmap cell's own wording ("surfaces the best matching variant by
   overlay weight") is wrong about it. Every badge-relevant filter in the four
   shipped default files is an **any-of read across every version**, and the
   group/weight table then arbitrates between the *overlays* that matched — not
   between the versions. There is therefore no "best Media" to pick. C1's **A-1**
   ratifies the correct formulation: *whole-versions value derivation + Kometa
   weight award*. The cell is corrected in Task 2.
2. **An oracle EXISTS, and it was re-read this session.** The four weight tables
   were extracted again from the pinned image `kometateam/kometa` digest
   `sha256:c58f6d4af511613f218b6dafbfc84078af4e5a6089790c1fdba58fd7c5dad70a`
   (local id `94982812a95a`) by `docker create` + `docker export` + `tar -x`
   (the layer has no `/app` prefix, so a `docker cp /app/...` route fails against
   this image). Every per-file sha256 below **matches the recon's table byte for
   byte**:

   | file | sha256 |
   |---|---|
   | `defaults/overlays/resolution.yml` | `0ee1533e99bb64d5e649f03669c4ac5ea5e535c7961fb802cb89b3ffa528df3f` |
   | `defaults/overlays/audio_codec.yml` | `3a1f1b0368b56c2e58b47a772ee9a44476a171efd3adca64ba5e1281291fb334` |
   | `defaults/overlays/video_format.yml` | `d70510ea0da57778df94e7b2351655bbbebc5addb302b08e5b2ec41d99228f36` |
   | `defaults/overlays/languages.yml` | `dcd91ab2e17fd41c204e72dc98a250e0cf9f2b72b42e2b15f4c2af9a2446c8f1` |
   | `modules/overlays.py` | `38c7eac79089b93c341106efc017f63adfe808d5e093742b84e204e86a41dcb6` |
   | `modules/plex.py` | `13fa5496e21555091a7e6652b62cca1dbe2c155685d69279721120e785121b86` |

   Every Kometa `file:line` in this plan was read from those exact files. **Do
   not re-derive a weight from memory or from upstream's docs site** — the tables
   in Task 1 Step 9 are the transcription, and Task 1 Steps 7–8 pin them.
3. **This phase ships an output change beyond the multi-version fix, and it is
   deliberate (C1 **A-5**).** Our `AUDIO_CODECS` map read Plex's
   `media.audioCodec` identifier through **ten** entries. Kometa's
   `audio_codec.yml` never reads `media.audioCodec` for this overlay at all — it
   matches **sixteen** regexes against **audio track titles and file paths**
   (`audio_codec.yml:98-100`). C1 rules that the oracle governs: the table mirrors
   Kometa's sixteen **verbatim**, all sixteen become reachable, and the divergence
   is disclosed with an affected-count query rather than left standing. The full
   old→new table is in **Established fact F9** and is repeated in the roadmap
   cell and the PR body.
4. **The storm is structurally bounded, and it is bounded to one fingerprint
   part.** `badges/compose.py:259-268` builds `badge_fingerprint` from five
   parts. `base_fingerprint` cannot move (`config/loader.py:38` names `badges`
   among the sections `render_version` excludes, and the base render never reads
   `MediaInfo`). `art_kind` cannot move. `asset_manifest_sha` cannot move — no
   file under `assets/` is touched, and every stem this phase can now award is
   already vendored (Task 1 Steps 7–8 assert exactly that, over all 39 + 16
   rows). `outcomes` cannot move — all six `OVERLAY_ATTRIBUTES`
   (`overlays/selection.py:133-136`) read `plex_item` or the C2b whole-list
   tuples, and **not one of them reads `media[0]`**. The ratings digest cannot
   move. **`values` is the only mover**, and Task 1 Step 22 pins both halves of
   that: a single-version item byte-identical, a two-version item moved off the
   `media[0]` digest.
5. **`plex/client.py:436-440` is FENCED (C1 **A-3**).** It also reads
   `container.media[0].parts[0].file`, but that path feeds
   `ResolvedItem.file_path → derive_root_folder → asset_path` — **where the
   render file is written on disk**. Widening it would move asset paths for any
   multi-version item whose versions live in different folders, with file moves
   and `asset_cleanup` consequences. It is an asset-path input, not a badge
   input, and this phase does not touch it. Task 2 Step 4 proves it with
   `git diff --stat`.
6. **`audio_languages` stays primary-version-only (C1 **A-4**).** C2b built a
   separate, undeduplicated whole-list pair beside it (`audio_stream_languages` /
   `subtitle_stream_languages`) precisely so the flag badge would not draw
   duplicate flags. That fence was reasoned and it holds. The merged walk in
   Task 1 Step 11 keeps `audio_languages` gated on `index == 0`, and Task 1
   Step 20 re-runs `tests/test_badge_values.py`'s three C2b pins to prove it.

---

## Branch, cut point and merge order

- Worktree: **`D:\Sites\autoposter-phasee`**, branch
  **`feat/phase-e-badge-versions`**, cut from **`origin/main` at `6a84186`**
  (C1 **A-7**).
- **The wait is over: #157 is merged and `main` already carries it.** C2 records
  it and this session re-measured it — `origin/main` = **`6a84186`**, and
  `origin/fix/flag-language-mapping` at `514a2db` is an **ancestor** of it. So
  the rewrite of `media_info_from_plex`'s two stream walks and the 132 lines
  #157 added to `tests/test_badge_media.py` are on `main` already, there is
  nothing to sequence around, and this is a single unstacked PR. Task 1 Step 1
  **records** the cut sha; it no longer gates on a merge.
- **Every `file:line` in this plan holds against `origin/main` at `6a84186`.**
  The `badges/values.py` and `tests/test_badge_media.py` numbers were first read
  from `origin/fix/flag-language-mapping` at `514a2db`, and every file this plan
  cites into — `badges/values.py`, `badges/compose.py`, `tests/test_badge_media.py`,
  `tests/test_badge_values.py`, `tests/test_badge_parity.py`,
  `tests/test_overlay_engine_golden.py`, `tests/test_badge_compose.py`,
  `tests/test_plex_exif.py`, `tests/test_assets_root.py`,
  `tests/test_overlay_entrypoint.py` and `tests/test_badge_pipeline.py` — is
  **byte-identical** between `514a2db` and `6a84186` (verified this session with
  `git hash-object`). Nothing needed re-measuring.
- **#156 does not bind.** This plan does not touch `render/pipeline.py`:
  `media_info_from_plex` and `video_format_text` keep their exact signatures, so
  the two call sites at `render/pipeline.py:1553` and `:1588` are untouched.
  There is no gate to plumb (A-2 is ungated), so there is no reason to widen
  `BadgeInputs` or its construction site either.
- **No stacked-PR merge-order line is needed**, because the branch is cut from
  `main` and `main` already carries #157. There is no base PR to retarget and no
  merge-order line to write into a second PR description.
- **This plan file is committed by Task 1, Step 4.** It is an untracked
  working-tree file until then.

---

## Global Constraints

Every task's requirements implicitly include this section. These are law.

1. **`badge_fingerprint`'s function is untouched.** `src/autoposter/badges/compose.py`
   is not edited at all — not the function, not `badge_values`, not
   `_definitions_digest`, not `BADGE_ICONS`. Only the `values` **input** moves,
   and only for items whose derived values move. Task 2 Step 4 proves it with
   `git diff --stat`.
2. **The two pinned literals are untouched, byte for byte.**
   `"576f88e58b3cf7af26d5058d63a46fa1eebfe89c63d7ce367d529a89ecf5a0bd"` at
   `tests/test_overlay_entrypoint.py:303` and again at `:411`, and
   `PRE_SEAM_ONE_DEFINITION_FINGERPRINT = "973b2cf3010950d3980be8644f92f1ad5243936691ce100f89eb44bdf94f0923"`
   at `:371`. `tests/test_overlay_entrypoint.py` is not edited by any step in
   this plan; it is only ever **run**.
3. **`render_version` is untouched.** `src/autoposter/config/loader.py` is not
   edited. It already excludes `badges` by name (`:38`), and this phase adds no
   config key at all, so no render fingerprint can move by construction.
4. **Fenced, read-only, not edited by any step:** `src/autoposter/render/pipeline.py`,
   `src/autoposter/plex/client.py` (especially `:436-440`),
   `src/autoposter/overlays/` (all of it), `src/autoposter/scheduler/`,
   `src/autoposter/api/`, `src/autoposter/collections/`,
   `src/autoposter/config/`, `src/autoposter/badges/compose.py`,
   `src/autoposter/badges/spec.py`, `src/autoposter/badges/geometry.py`.
   **The only source file this plan edits is `src/autoposter/badges/values.py`.**
   If a step would edit any other source file, stop.
5. **Ungated, disclosed (C1 A-2).** No `BadgesConfig` key, no
   `config/schema.py` edit, no `config/autoposter.example.yaml` edit, no
   `frontend/` edit, no Alembic revision, no file added or changed under
   `assets/`. The change is a correctness fix toward the tool being replaced;
   the guard is that a single-version item's values are unchanged by
   construction, which is stronger than a boolean nobody would ever turn off
   again.
6. **`audio_languages` stays DISTINCT codes off the PRIMARY version (C1 A-4).**
   The merged walk gates it on `index == 0`. The two C2b whole-list fields
   (`audio_stream_languages`, `subtitle_stream_languages`) keep their exact
   contract: every stream, every version, listing order, **no dedupe**, an
   unlabelled stream still contributing one empty-string entry.
7. **`MediaInfo`'s field COUNT and ORDER do not change.** Eleven fields before,
   eleven after, in the same slots. Three change type in place; none is added,
   removed or reordered. Every positional construction site therefore keeps its
   arity, and `tests/test_badge_values.py::test_the_new_fields_are_defaulted_so_positional_construction_still_works`
   stays meaningful.
8. **No fallback paths.** An accessor reads the item-level tuple and nothing
   else. There is no "use the scalar when the tuple is empty" shim: a shim would
   keep `AUDIO_CODECS` alive as a second, drifting source of truth and would let
   tests pass through a path production never takes. `AUDIO_CODECS`,
   `_HDR_SUFFIXES` and `_hdr_suffix` are **deleted** — they are orphaned by this
   change, which is the one class of deletion this repository's house rules
   allow.
9. **Pillow only. No test in this plan invokes `magick`, so no test in this plan
   carries `@pytest.mark.imagemagick`.** Verified this session: none of
   `tests/test_badge_media.py`, `tests/test_badge_values.py`,
   `tests/test_badge_parity.py`, `tests/test_badge_compose.py`,
   `tests/test_overlay_engine_golden.py`, `tests/test_plex_exif.py` or
   `tests/test_assets_root.py` carries `pytestmark` or an `imagemagick`/`deep`
   marker today. `tests/test_badge_parity.py` **does** reach a compositor — but
   it is `badges/compose.py::compose`, which is Pillow, not
   `render/compositor.py`, which is ImageMagick. **It keeps exactly the markers
   it has, which is none.** CI's main run is `-m "not imagemagick and not deep"`
   on a runner with no `magick` binary; adding the marker would silently stop
   running the parity pins.
10. **RED-first.** Every behaviour lands test-first: write the failing test, run
    it, read the failure, then implement. A test never seen failing proves
    nothing.
11. **Container discipline.** One compose project (**`pphe`**), every command
    with **both** `-f` files. The overlay `.superpowers/isolated-db.yml` is
    gitignored and is copied into the worktree **by absolute path** from
    `D:\Sites\autoposter`. **Never `--rm`**: run detached with `--name`, `docker
    wait`, read the teed log file, then `docker rm`. `docker-compose.yml`'s
    `test` service bind-mounts `- .:/app`, so a log teed to
    `/app/.superpowers/<name>.log` appears in the worktree with no `docker cp`.
    Teardown carries the same two `-f` files as every other invocation —
    `docker compose -p pphe -f docker-compose.yml -f .superpowers/isolated-db.yml down`
    — and is **never `down -v`**. There is no exception to the "both `-f`" rule
    anywhere in this plan.
12. **Test selection is by node id**, never by `-k` substring.
13. **The count chain.** `B` is the baseline **PASSED** count of the whole suite,
    measured in Task 1 Step 5. This plan **adds 25 pytest tests, deletes 1
    superseded one, and adds 0 vitest tests — net `+24`**, summed from its own
    code blocks: Task 1 Step 7 writes **5** (the table and comparator pins),
    Task 1 Step 12 writes **19** (5 any-of reads + 9 awards + 3 verbatim quirks
    + 2 storm-guard pins), Task 1 Step 14a writes **1** (the new
    `nb`/`nn`-draw-Norway's-flag pin beside the rewritten one), Task 1 Step 15
    deletes **1** (`test_every_audio_codec_image_name_exists_on_disk`, which
    iterated the retired ten-entry `AUDIO_CODECS` map and is superseded by
    Step 7's `test_every_audio_codec_weight_row_names_a_vendored_png` over all
    sixteen stems), Task 2 writes none. Two existing tests are **rewritten in
    place and therefore add nothing**: Step 14a inverts
    `test_norwegian_bokmal_loses_its_flag_and_that_is_a_decision_not_an_accident`
    (same test, opposite assertion, new name), and every rewritten parametrize
    in Steps 15 and 17 keeps its cardinality. So the delta is exactly +24 and
    nothing else. So: Task 1 Step 10 ends at **`B + 5`**. Task 1 Step 21 ends at
    **`B + 24`**. Task 2 ends at **`B + 24`**. `tests/test_badge_media.py` itself
    goes from **50** collected to **74**. **THE STOP: if a suite run reports anything other than the
    number this chain predicts, STOP — a test elsewhere moved; find it before
    committing, and never adjust the expectation to match the output.** That
    sentence is the only STOP rule for counts in this plan; every step that names
    a count means it. Two steps carry a STOP of a **different class**, and they
    are not derived from this one: Task 1 Step 3's "if the copy fails, STOP" is
    an environment precondition (no isolation overlay, no runnable container),
    and Task 1 Step 1 carries none at all.
14. **Every citation into Kometa names the pinned digest.** A comment or
    docstring citing `resolution.yml:255` says which image it was read from
    (`sha256:c58f6d4a…`, per-file shas in "Read this first" item 2) at least
    once per construct. `tests/test_citation_anchors.py` does **not** guard this
    file — its `SURFACES` tuple is `collections/filters.py`,
    `collections/builders/plex_search.py`, `tests/test_collection_filters.py`
    and `tests/test_builder_plex_search.py`, and its `EXPECTED_REF_COUNT = 30`
    census counts only citations of `tests/oracle/9b/kometa_build_filter.py`.
    Nothing this plan writes can move that count. It is still run in Task 1
    Step 21 as part of the full suite.
15. **Commits** are conventional, staged **by name** (never `git add -A`, `-u`,
    or `.`), and carry **no AI attribution of any kind** — no `Co-Authored-By`,
    no "Generated with", no tool mention. Same for the PR body. If signing
    hangs, retry the same command with `--no-gpg-sign` appended rather than
    skipping the commit.

---

## Established facts

Every repository file below was read from **`origin/main` at `6a84186`**.
(`badges/values.py` and `tests/test_badge_media.py` were first read from
`origin/fix/flag-language-mapping` at `514a2db`, which `main` now contains; both
are byte-identical at the two refs, so every line number holds either way.)
Every Kometa file was read from the pinned image, shas in "Read this first"
item 2.

**F1 — the defect, exactly.** `badges/values.py:162`:
`media = (getattr(item, "media", None) or [None])[0]`. Off that one object come
`video_resolution` (`:242`), `audio_codec` (`:256`) and `audio_channels`
(`:257`); the walk at `:171-198` produces `file_path` (`:172-173`), the DV/HDR/HLG
flags (`:175-182`) and the distinct `audio_languages` (`:183-198`); `:200-201`
adds the HDR10+ flag from that one path. Item-level and therefore already
correct: `duration_ms` (`:258`), `season_number` (`:261`), `episode_number`
(`:262`).

**F2 — the whole-list walk already exists.** `badges/values.py:217-239` is C2b's
`for version in getattr(item, "media", []) or []` walk, filling
`audio_stream_languages`/`subtitle_stream_languages`. Its comment at `:203-216`
is the phase's most load-bearing sentence: *"that loop is scoped to `media` (the
PRIMARY version) and widening it would change `audio_languages`, `hdr_flags` and
`file_path` for every multi-version item"*. Phase E **is** that widening, done
deliberately with the weights as arbiter and with `audio_languages` deliberately
excluded (A-4). **That comment is owed an edit, not a deletion** — Task 1
Step 11 rewrites it.

**F3 — the resolution oracle.** `defaults/overlays/resolution.yml:243-251` is the
template's read: a `plex_search: {all: {any: {resolution.regex: <<res>>}, hdr:
<<hdr>>}}` plus `filters: {has_dolby_vision: <<dolby_vision>>, filepath.regex:
[<<regex>>]}`. All three are **item-level**: `has_dolby_vision` is
`modules/plex.py:2832-2838` (`for media in item.media: for part in media.parts:
for stream in part.videoStreams()`), and `filepath` is
`modules/plex.py:2804-2805` (`values = [loc for loc in
self.cached_item_attr(item, "locations") if loc]`, i.e. every file of every
version).

**F4 — what each `alt` actually requires.** `resolution.yml:196-227`'s
conditionals, verbatim: `res` maps `4k→"(?i)2160|4k"`, `1080p→"(?i)1080|2k"`,
`720p→"(?i)720|hd"`, `576p→"(?i)576"`, `480p→"(?i)480|sd"` (`:198-209`);
`dolby_vision` is `true` for `alt: dv` only (`:210-213`); `hdr` is `true` for
`alt: hdr` only (`:214-217`); and `regex` (`:218-227`) is `'(?i)\bhlg\b'` for
`hlg`, `'(?i)\bhdr10(\+|p(lus)?\b)'` for `plus`, `'(?i)\bdv(.hdr10?\b)'` for
`dvhdr`, `'(?i)\bdv.HDR10(\+|P(lus)?\b)'` for `dvhdrplus`. In flag terms — which
is how this repository already reduces those signals — the requirement per alt is
exactly `_HDR_SUFFIXES`' existing required-set, plus an empty set for
`alt: ""`. That equivalence is **why the tree's existing DV/HDR/HLG/HDR10+ stream
and path reads are kept as they are** and only widened to every version.

**F5 — the 39 resolution weight rows.** `resolution.yml:255-371`, in the file's
own order, which is non-increasing in weight:
4k `dvhdrplus 160, dvhdr 158, plus 155, dv 150, hlg 141, hdr 140, "" 130`;
1080p `130, 128, 125, 120, 111, 110, 100`;
720p `99, 98, 95, 90, 81, 80, 70`;
576p `dvhdrplus 69, dvhdr 68, plus 65, dv 60, hdr 50, "" 40` — **no hlg row**;
480p `39, 38, 35, 30, hdr 20, "" 10` — **no hlg row**;
then six resolution-less rows with `key: ""` (`:354-371`):
`dvhdrplus 9, dvhdr 8, plus 7, dv 5, hlg 2, hdr 1`.
**That is 7+7+7+6+6+6 = 39 rows**, not the 41 the recon's T1 sketch said —
corrected here against the file. Five of the six tail rows carry `all: true`
(`:355, :358, :361, :364, :367`); `HDR-Dovetail` (`:369-370`) does not, which is
an upstream inconsistency transcribed as it stands, and which makes no difference
to this derivation because nothing here issues a `plex_search`.

**F6 — the one tie, and why it is unreachable from a real item.** `4K-Dovetail`
(`resolution.yml:273-274`, weight 130) and `1080P-DV-HDR-Plus-Dovetail`
(`:276-277`, also weight 130). `modules/overlays.py:582-586` keeps the member
with the **strictly greater** weight (`if final is None or
overlay_groups[gk][v] > overlay_groups[gk][final]`), so a tie keeps whichever
came first in iteration order — config order. But an item that could reach the
tie has 4k in its resolution set **and** the dv/plus flags, and because the flags
are item-level the moment they hold `4K-DV-HDR-Plus-Dovetail` (160) fires too.
So the tie is pinned twice, at the two levels it exists at: directly on the
comparator (Task 1 Step 7) and as the item-level consequence (Task 1 Step 12).

**F7 — the repo already has this comparator, twice.** `badges/compose.py:410-429`
`_resolve_definitions` arbitrates definition groups with
`current is None or definition.weight > current.weight` — the **same strict `>`**
as `modules/overlays.py:586`. And `badges/values.py:368-384`'s `_VIDEO_FORMATS`
is already Kometa's `video_format.yml` weight order transcribed verbatim
(REMUX 60 → CAM 8) with the comment *"All eight share `group: quality`, so only
the highest-weighted match is drawn"*. The new `_award` is that one rule, written
once, cited to both.

**F8 — the video_format oracle.** `video_format.yml:62-65` is
`filters: {filepath.regex: <<regex_<<key>>>>}` with `plex_all: true` — i.e.
`item.locations`, every path of every version. Weights at `:69-99`: REMUX 60,
BLU-RAY 50, WEB 40, HDTV 30, DVD 20, SDTV 10, TELESYNC 9, CAM 8. Our
`_VIDEO_FORMATS` already holds those eight patterns in that order; this phase
adds the weights to the tuple so it is the same shape as the other two tables,
and runs it over `file_paths` instead of one `file_path`.

**F9 — the audio_codec oracle, and the ten-of-sixteen divergence (C1 A-5).**
`audio_codec.yml:96-100` is `ignore_blank_results: true`, `plex_all: true`,
`filters: [audio_track_title.regex: <<regex_<<key>>>>, filepath.regex:
<<regex_<<key>>>>]` — an OR of two whole-item reads.
`audio_track_title` is `modules/plex.py:2791-2794`:
`for media in item.media: for part in media.parts: values.extend([a.extendedDisplayTitle
for a in part.audioStreams() if a.extendedDisplayTitle])` — every audio stream of
every version, falsy titles dropped. **`media.audioCodec` is never read for this
overlay.** The sixteen regexes are `audio_codec.yml:61-95` and the sixteen
weights `:104-166`.

The shipped-output change, per codec, stated so nobody has to infer it. "Old" is
`AUDIO_CODECS.get(media[0].audioCodec.lower())`; "new" is the weight-award over
every version's audio track titles and file paths:

| Plex `audioCodec` | old stem | new outcome | changed? |
|---|---|---|---|
| `truehd` | `truehd` | `truehd_atmos` (160) when a title or path also says Atmos, else `truehd` (120) | **yes** |
| `eac3` | `plus` | `plus_atmos` (140) when Atmos is present too, else `dolby_atmos` (130) if only "atmos" matches, else `plus` (70) | **yes** |
| `ac3` | `digital` | `dolby_atmos` (130) when a title or path says Atmos, else `digital` (40) | **yes** |
| `dca` | `dts` | `dtsx` (150) / `hra` (80) / `dtses` (60) when the title or path says so, else `dts` (50) | **yes** |
| `dca-ma` | `ma` | `dtsx` (150) for a DTS:X track, else `ma` (110) | **yes** |
| `aac` | `aac` | `aac` (30) — and "Stereo" or "2.0" in a title now also awards it | no (widened) |
| `flac` | `flac` | `flac` (100) | no |
| `pcm` | `pcm` | `pcm` (90) | no |
| `mp3` | `mp3` | `mp3` (20) | no |
| `opus` | `opus` | `opus` (10) — but a title reading "OPUS Stereo" awards `aac` (30), because upstream's aac regex matches "stereo" | no (with that quirk) |
| — | — | **no codec token in any audio track title or any file path → no `audio_codec` badge at all**, where the Plex identifier always produced one | **yes — the one direction that REMOVES a badge** |

Six stems were unreachable before and are reachable now: `truehd_atmos`,
`dtsx`, `plus_atmos`, `dolby_atmos`, `hra`, `dtses`. The removal row is expected
to be rare — Plex's `extendedDisplayTitle` carries the codec for every stream it
knows ("English (EAC3 5.1)", "English (DTS-HD MA 7.1)") — but it is real, it is
pinned by a test (Task 1 Step 12), and it is counted by the query in Task 2
Step 2 before anyone deploys.

**F10 — every stem this phase can award is already vendored.**
`assets/badges/images/resolution/` holds 43 PNGs, a superset of the 39 rows —
including the six resolution-less tail stems (`dv`, `dvhdr`, `dvhdrplus`, `hdr`,
`hlg`, `plus`) and including `576phlg.png`/`480phlg.png`, for which upstream
ships **no overlay row**, so those two files simply become unreachable.
`assets/badges/images/audio_codec/compact/` and `.../standard/` each hold 17
PNGs — all sixteen stems plus an extra `atmos.png` that matches no Kometa key.
`badges/compose.py:56` points the badge at `compact`. **No asset is added,
removed or changed, so `asset_manifest_sha` provably cannot move.**

**F11 — the fingerprint parts, and the one that moves.**
`badges/compose.py:259-268`:
`parts = [base_fingerprint, art_kind, asset_manifest_sha]`, then
`parts += ["%s=%s" % (k, values[k]) for k in sorted(values)]`, then the
definitions, outcomes and rating digests under their guards. `badge_values`
(`compose.py:86-114`) hashes `resolution`, `audio_codec`, `video_format`,
`runtimes`, `critic`, `audience`, `commonsense`, `languages` and (title cards
only) `episode_info`. Of those, the first three are the ones this phase moves.

**F12 — the measured ceiling.** From `p5b-task-3-report.md:105-114` (live probe,
2026-08-22): 1,954 movies, **50** with more than one `<Media>` (two with three:
*Lee* 2024 and *LOTR: Fellowship*), **224** episodes with more than one
`<Media>`. `editionTitle` set on any of the 1,954: **0**. Duplicate primary GUID
groups: **0**. **Ceiling ≤274 re-badges** from the multi-version half — badge
pixels only, no base re-render, one pass, self-settling. The audio_codec half
(F9) is not bounded by that number and is what the Task 2 Step 2 query measures.

**F13 — the impact preview is silent about this, and that is disclosed rather
than papered over (C1 A-8).** `api/routes.py:1588-1604`'s `_render_affecting`
short-circuits on `after.version != before.version or after.skip_tba != ...`.
Since this phase adds **no config key at all**, there are no two configs to
compare and therefore no preview row to add — an impact preview answers "what
does saving this config change?", and the answer here is genuinely "nothing;
the code changed". So A-8 resolves to **disclosure only**, and Task 2 says so in
the roadmap cell and the PR body rather than teaching the render-only walk about
badge fingerprints for a change no config edit can trigger.

**F14 — row 213's rule, and how this phase stays clear of it.** A served string
carries the class name only unless reviewed safe (`plex/client.py:16-27`'s
`served_detail` marker, `queue/worker.py:32-46`'s `_served_reason`). Row 106 does
not reach a served surface and **this plan adds no logging, no warning and no
served field**. The natural diagnostic — "we picked version 2 of 3,
`/data/media/Movies/…`" — would put an operator host path on the Jobs/Failures
column, which is row 213's `PlexPathMismatch` decision replayed. `MediaInfo.file_paths`
stays a badge input and nothing else.

**F15 — every `MediaInfo` construction site in the tree**, read this session.
Source: `badges/values.py:164` and `:241` only. Tests: `tests/test_assets_root.py:89`,
`tests/test_badge_compose.py:22`, `tests/test_badge_media.py` (12 sites),
`tests/test_badge_parity.py:42` and `:81`, `tests/test_badge_values.py:122`,
`:130`, `:331` and `:342`, `tests/test_overlay_engine_golden.py:35` and `:39`,
`tests/test_plex_exif.py:35`. **`MediaInfo.audio_codec` is read by name at
exactly one place outside `values.py`** — `tests/test_badge_media.py:64` —
and `MediaInfo.video_resolution` at exactly two (`test_badge_media.py:63`,
`:81`), and `MediaInfo.file_path` at exactly **two** —
`tests/test_badge_media.py:199` **and `tests/test_badge_pipeline.py:200`**.
Everything else passes them positionally into the constructor and never reads
them back.

That second `file_path` reader is the one site in the tree that touches a
`MediaInfo` field without constructing one, so it is easy to miss and it is
named here, in Task 1's Modify list, in Step 17, in Step 18's and Step 22's
lists and in Task 2 Step 4's expected diff. It is
`assert seen[0].media.file_path.endswith("Dune.2021.REMUX-2160p.mkv")`, where
`seen[0]` is the `BadgeInputs` captured off `render/pipeline.py::compose_badges`
by a monkeypatch, so `.media` is a real `MediaInfo` built by
`media_info_from_plex` and the rename breaks it with an `AttributeError`. It is
an **edit, not an addition**, so it moves no count. `tests/test_badge_pipeline.py`
constructs no `MediaInfo` at all (`grep -c "MediaInfo(" tests/test_badge_pipeline.py`
→ `0`), which is why it is absent from the construction-site census above. Note
also that under the new derivation this file's fixture flips
`audio_codec_image` from `plus` to `None` — its `FakePart` carries no streams
and its path carries no codec token — which is harmless: the file asserts
fingerprint presence and upload counts, never a badge value.

**F16 — the parity and golden pins survive the field-type change with the same
pixels.** `tests/test_badge_parity.py:42` and `tests/test_overlay_engine_golden.py:35`
pass `"eac3"` in slot 2 and rely on the `plus` badge being drawn; `:81`/`:39`
pass `"aac"` and rely on `aac`. Replacing `"eac3"` with
`("English (EAC3 5.1)",)` awards `plus` (70) — the `plus` regex
`(?i)\b(dd[p+])|(dolby[ ._-]digital[ ._-]plus)|(e[ ._-]?ac3)\b` matches "EAC3",
and although upstream's `digital` regex `(?i)\b(dd)|(ac3)|(dolby)(\b|\d)` matches
the same string (its `ac3` alternative is unanchored — transcribed, not
corrected), 70 beats 40. Replacing `"aac"` with `("English (AAC Stereo)",)`
awards `aac` (30). **Both stems are what those files draw today, so
`POSTER_PIXELS_SHA` and `TITLE_CARD_PIXELS_SHA`
(`tests/test_overlay_engine_golden.py:44-45`) do not move and are not touched.**

**F17 — `tests/test_badge_values.py:335`'s docstring is now wrong** and is
corrected by this plan. It says `tests/test_badge_parity.py` and
`tests/test_overlay_engine_golden.py` "are parity-pin files this plan may not
edit -- Global Constraint 8", which was **C2b's** constraint, not this phase's.
Phase E edits them, by two literal substitutions each, and F16 is why that is
safe.

**F18 — `MediaInfo.audio_channels` has no consumer anywhere in the tree.** It is
pre-existing dead weight, not this phase's, so it is **kept** (read off the first
version, as today) and merely documented. Removing it would be an unrelated
change.

---

## File structure

| file | responsibility after this phase |
|---|---|
| `src/autoposter/badges/values.py` | **the only source file edited.** Holds `MediaInfo` (now item-level), `media_info_from_plex` (one walk of `item.media`), the three transcribed weight tables (`RESOLUTION_WEIGHTS`, `AUDIO_CODEC_WEIGHTS`, `_VIDEO_FORMATS`), the alt→flags map `_ALT_FLAGS`, the shared comparator `_award`, the flag-lookup alias map `_FLAG_ALIASES`, and the four accessors that use them. |
| `tests/test_badge_media.py` | **the phase's test file.** The 50 tests it carries on the cut ref, rewritten onto the item-level fields where they construct one (one of them, the Norwegian flag pin, inverted rather than retyped), plus the 25 new ones. |
| `tests/test_badge_values.py` | four `MediaInfo` constructions updated; one docstring corrected (F17). No test added. |
| `tests/test_badge_compose.py`, `tests/test_badge_parity.py`, `tests/test_overlay_engine_golden.py`, `tests/test_plex_exif.py`, `tests/test_assets_root.py` | one or two `MediaInfo` constructions updated each. No test added, no measured sha touched. |
| `tests/test_badge_pipeline.py` | one line: the `MediaInfo.file_path` attribute read at `:200` becomes a `file_paths` read (F15). No construction, no test added. |
| `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` | row 106 (`:208`) filed and closed. |
| `docs/superpowers/plans/2026-09-05-phase-e-badge-versions.md` | this plan, committed by Task 1 Step 4. |

---

## Task 1: The derivation — every `<Media>`, awarded by Kometa's weights

**Files:**
- Modify: `src/autoposter/badges/values.py` — `:28-35` (the two maps), `:93-149`
  (`MediaInfo`), `:152-266` (`media_info_from_plex`), `:326-347`
  (`_HDR_SUFFIXES`/`_hdr_suffix`), `:350-365` (`resolution_image`,
  `audio_codec_image`), `:368-400` (`_VIDEO_FORMATS`, `video_format_text`),
  `:403-412` (`language_slots`'s flag lookup — the Norwegian alias, Step 14a)
- Modify: `tests/test_badge_media.py` (12 construction sites, **4** attribute
  assertions, 1 existing test rewritten in place, +25 tests)
- Modify: `tests/test_badge_values.py:122`, `:130`, `:331`, `:335-341`, `:342`
- Modify: `tests/test_badge_compose.py:22`
- Modify: `tests/test_badge_parity.py:42`, `:81`
- Modify: `tests/test_overlay_engine_golden.py:35`, `:39`
- Modify: `tests/test_plex_exif.py:35`
- Modify: `tests/test_assets_root.py:89`
- Modify: `tests/test_badge_pipeline.py:200` (an attribute READ, not a
  construction — F15)
- Create: `docs/superpowers/plans/2026-09-05-phase-e-badge-versions.md` (this file)

**Interfaces:**
- Consumes: nothing from another task — Task 2 is documentation and
  verification only.
- Produces, all in `autoposter.badges.values`:
  - `MediaInfo(video_resolutions: tuple[str, ...], audio_track_titles: tuple[str, ...],
    audio_channels: int | None, duration_ms: int | None,
    audio_languages: tuple[str, ...], hdr_flags: frozenset[str],
    season_number: int | None, episode_number: int | None,
    file_paths: tuple[str, ...] = (), audio_stream_languages: tuple[str, ...] = (),
    subtitle_stream_languages: tuple[str, ...] = ())` — frozen dataclass,
    eleven fields, same order as before.
  - `RESOLUTION_WEIGHTS: tuple[tuple[str, str, int], ...]` — 39 `(key, alt, weight)` rows.
  - `AUDIO_CODEC_WEIGHTS: tuple[tuple[str, int, re.Pattern[str]], ...]` — 16 `(stem, weight, pattern)` rows.
  - `_VIDEO_FORMATS: tuple[tuple[str, int, re.Pattern[str]], ...]` — 8 `(label, weight, pattern)` rows.
  - `_ALT_FLAGS: dict[str, frozenset[str]]` — 7 entries.
  - `_FLAG_ALIASES: dict[str, str]` — 2 entries (`nb`/`nn` → `no`), Step 14a.
  - `_award(candidates: Iterable[tuple[str, int]]) -> str | None`.
  - `media_info_from_plex(item) -> MediaInfo` — unchanged signature.
  - `resolution_image(info: MediaInfo) -> str | None`,
    `audio_codec_image(info: MediaInfo) -> str | None`,
    `video_format_text(info: MediaInfo) -> str | None` — unchanged signatures.
  - `language_slots(info: MediaInfo, limit: int = 3) -> list[tuple[str, str]]` —
    unchanged signature, one aliasing hop added inside (Step 14a).
  - **Removed:** `AUDIO_CODECS`, `_HDR_SUFFIXES`, `_hdr_suffix`.

---

- [ ] **Step 1: Record the cut ref**

```bash
git -C D:/Sites/autoposter fetch origin --prune
git -C D:/Sites/autoposter rev-parse origin/main
git -C D:/Sites/autoposter show origin/main:src/autoposter/badges/values.py | grep -c "from autoposter.lang import base_language_code"
git -C D:/Sites/autoposter show origin/main:tests/test_badge_media.py | wc -l
```

Expected: the `grep -c` prints `1` and the test file is **362** lines — the
post-#157 shape this plan's line numbers were read against. Record the resolved
`origin/main` sha in the task report; it was **`6a84186`** when this plan was
written and `main` may well have moved since, which is fine — the cut is
whatever `origin/main` resolves to now, and the two checks above are what say
the badge lineage in it is the one this plan describes.

**There is no sequencing gate here.** #157 is merged and `main` carries it, so
there is no stacked PR, no merge-order line and nothing to wait for. If either
check above comes back differently, the badge lineage has moved under the plan:
reconcile with the controller rather than improvising — but that is a
reconciliation, not one of Global Constraint 13's count STOPs.

- [ ] **Step 2: Create the worktree and the branch**

```bash
git -C D:/Sites/autoposter worktree add -b feat/phase-e-badge-versions D:/Sites/autoposter-phasee origin/main
cd D:/Sites/autoposter-phasee && git rev-parse HEAD
```

Record the resolved `HEAD` sha; it must equal the `origin/main` sha from Step 1.
**Do not `git checkout` in `D:\Sites\autoposter`** — it holds other work.

- [ ] **Step 3: Copy the gitignored isolation files into the worktree by absolute path**

Shell state does not survive between tool calls in this harness; only the
working directory does. So the main checkout is spelled as the absolute path
`D:/Sites/autoposter` at every dereference below — the same absolute path with
forward slashes, so it runs verbatim under both the Bash and the PowerShell
tool. There is no `$MAIN` in this plan.

```bash
mkdir -p D:/Sites/autoposter-phasee/.superpowers
cp D:/Sites/autoposter/.superpowers/isolated-db.yml D:/Sites/autoposter-phasee/.superpowers/isolated-db.yml
cp -r D:/Sites/autoposter/.superpowers/sdd D:/Sites/autoposter-phasee/.superpowers/sdd
cat D:/Sites/autoposter-phasee/.superpowers/isolated-db.yml
```

Expected: the small overlay prints (`services: postgres: ports: !reset null` —
what stops this database fighting the main checkout's for port 5432).
**If the copy fails, STOP** — every later container command depends on it. This
is an **environment-precondition STOP, not one of Global Constraint 13's count
STOPs**: nothing has been measured yet and no expectation is being defended;
there is simply no runnable container without this file, so there is nothing to
proceed to.

- [ ] **Step 4: Commit this plan file**

```bash
cd D:/Sites/autoposter-phasee
mkdir -p docs/superpowers/plans
cp D:/Sites/autoposter/docs/superpowers/plans/2026-09-05-phase-e-badge-versions.md docs/superpowers/plans/2026-09-05-phase-e-badge-versions.md
git add docs/superpowers/plans/2026-09-05-phase-e-badge-versions.md
git commit -m "docs(plans): phase E — badges over every Media version"
```

- [ ] **Step 5: Measure the baseline `B` and the badge file's collected count**

```bash
cd D:/Sites/autoposter-phasee
docker compose -p pphe -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pphe-baseline test sh -c \
  'set -o pipefail; pytest -q 2>&1 | tee /app/.superpowers/run-pphe-baseline.log'
docker wait pphe-baseline
docker rm pphe-baseline
tail -5 .superpowers/run-pphe-baseline.log
docker compose -p pphe -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pphe-collect test sh -c \
  'set -o pipefail; pytest tests/test_badge_media.py --collect-only -q 2>&1 | tee /app/.superpowers/run-pphe-collect.log'
docker wait pphe-collect
docker rm pphe-collect
tail -3 .superpowers/run-pphe-collect.log
```

Expected: a green summary line, and `50 tests collected` from the second run.
**Read the log files — never trust `docker wait`'s exit code alone.** Record
`B = <passed>` and the skip count in the task report. **If the collected count is
not 50, THE STOP applies** — the base is not the tree this plan was written
against; reconcile before writing code.

- [ ] **Step 6: Capture the two pre-change literals, before any source edit**

These are measurements, not guesses: they must be taken on the **unmodified**
code, because their whole purpose is to prove what did and did not move. Write
the capture script into gitignored scratch (it never enters the branch):

```bash
cd D:/Sites/autoposter-phasee
cat > .superpowers/capture.py <<'PY'
"""Phase E, Task 1 Step 6: the pre-change badge values, captured on the cut ref.

Builds the two fixtures Task 1 Step 12 pins and prints what the SHIPPED
media[0] derivation produces for each. The single-version dict must survive the
change byte-identical; the two-version dict is what the change must move OFF.
"""
from autoposter.badges.compose import BadgeInputs, badge_fingerprint, badge_values
from autoposter.badges.values import media_info_from_plex, video_format_text


class S:
    def __init__(self, kind, **kw):
        self.streamType = kind
        for k, v in kw.items():
            setattr(self, k, v)


class P:
    def __init__(self, streams, file=None):
        self.streams = streams
        self.file = file


class M:
    def __init__(self, parts, **kw):
        self.parts = parts
        for k, v in kw.items():
            setattr(self, k, v)


class I:
    def __init__(self, media, duration=None):
        self.media = media
        self.duration = duration
        self.seasonNumber = None
        self.episodeNumber = None

    def reload(self):
        pass


def report(label, item):
    info = media_info_from_plex(item)
    values = badge_values("poster", BadgeInputs(media=info, video_format=video_format_text(info)))
    print(label, "values =", values)
    print(label, "fingerprint =",
          badge_fingerprint("base-fp", "poster", values, "manifest-sha"))


single = I([M([P([S(1, codec="hevc", colorTrc="bt709"),
                  S(2, languageCode="eng", extendedDisplayTitle="English (EAC3 5.1)")],
                file="/m/Show/S01E01.WEBDL-1080p.mkv")],
             videoResolution="1080", audioCodec="eac3", audioChannels=6)],
            duration=4845912)
report("SINGLE", single)

two = I([M([P([S(2, languageCode="eng", extendedDisplayTitle="English (AAC Stereo)")],
              file="/m/Ben-Hur (1959)/Ben-Hur.1959.WEBDL-1080p.mkv")],
            videoResolution="1080", audioCodec="aac", audioChannels=2),
         M([P([S(2, languageCode="eng", extendedDisplayTitle="English (DTS-HD MA 5.1)")],
              file="/m/Ben-Hur (1959)/Ben-Hur.1959.REMUX-2160p.mkv")],
            videoResolution="4k", audioCodec="dca-ma", audioChannels=6)],
        duration=4845912)
report("TWO", two)
PY
docker compose -p pphe -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pphe-capture test sh -c \
  'set -o pipefail; python /app/.superpowers/capture.py 2>&1 | tee /app/.superpowers/run-pphe-capture.log'
docker wait pphe-capture
docker rm pphe-capture
cat .superpowers/run-pphe-capture.log
```

Expected: four lines. `SINGLE values` must read
`{'resolution': '1080p', 'audio_codec': 'plus', 'video_format': 'WEB', 'runtimes': 'Runtime: 1h 20m', 'languages': 'us:EN'}`
and `TWO values` must read
`{'resolution': '1080p', 'audio_codec': 'aac', 'video_format': 'WEB', 'runtimes': 'Runtime: 1h 20m', 'languages': 'us:EN'}`
— the second is the whole defect in one line: a 4K DTS-HD MA remux sitting at
`media[1]`, badged 1080p/AAC/WEB off `media[0]`. **If either dict differs, THE
STOP applies.**

Record the two 64-character fingerprints in the task report as
`SINGLE_VERSION_FINGERPRINT` and `MEDIA_ZERO_FINGERPRINT`. Step 12's test file
uses them as module-level literals, exactly the way
`tests/test_overlay_entrypoint.py:371`'s `PRE_SEAM_ONE_DEFINITION_FINGERPRINT`
was captured and pinned before its own seam existed.

- [ ] **Step 7: Write the failing tests for the weight tables and the comparator**

Add these five tests to the END of `tests/test_badge_media.py`, and extend the
existing `from autoposter.badges.values import (...)` block at `:11-17` to also
import `AUDIO_CODEC_WEIGHTS`, `RESOLUTION_WEIGHTS`, `_award` and
`video_format_text` (keep the block alphabetical: `AUDIO_CODEC_WEIGHTS`,
`MediaInfo`, `RESOLUTION_WEIGHTS`, `_award`, `audio_codec_image`,
`language_slots`, `media_info_from_plex`, `resolution_image`,
`video_format_text`).

```python
# --- Kometa's weight tables, transcribed and pinned ------------------------
#
# Read from the pinned image `kometateam/kometa`
# sha256:c58f6d4af511613f218b6dafbfc84078af4e5a6089790c1fdba58fd7c5dad70a --
# `defaults/overlays/resolution.yml` (sha256 0ee1533e…df3f) and
# `defaults/overlays/audio_codec.yml` (sha256 3a1f1b03…b334). These two tests
# are the transcription itself: they exist so a mistyped weight is a failing
# test rather than a silently wrong badge on somebody's poster.


def test_the_resolution_weight_table_is_kometas_thirty_nine_rows_verbatim():
    """`resolution.yml:255-371`, in the file's own order. Note the shape of
    what is NOT here: 576p and 480p carry no `hlg` row upstream, which is why
    each of those tiers has six rows and not seven, and the last six rows are
    the `key: ""` tail (`:354-371`) that fires for an item whose resolution is
    none of the five tiers."""
    assert RESOLUTION_WEIGHTS == (
        ("4k", "dvhdrplus", 160), ("4k", "dvhdr", 158), ("4k", "plus", 155),
        ("4k", "dv", 150), ("4k", "hlg", 141), ("4k", "hdr", 140), ("4k", "", 130),
        ("1080p", "dvhdrplus", 130), ("1080p", "dvhdr", 128), ("1080p", "plus", 125),
        ("1080p", "dv", 120), ("1080p", "hlg", 111), ("1080p", "hdr", 110),
        ("1080p", "", 100),
        ("720p", "dvhdrplus", 99), ("720p", "dvhdr", 98), ("720p", "plus", 95),
        ("720p", "dv", 90), ("720p", "hlg", 81), ("720p", "hdr", 80),
        ("720p", "", 70),
        ("576p", "dvhdrplus", 69), ("576p", "dvhdr", 68), ("576p", "plus", 65),
        ("576p", "dv", 60), ("576p", "hdr", 50), ("576p", "", 40),
        ("480p", "dvhdrplus", 39), ("480p", "dvhdr", 38), ("480p", "plus", 35),
        ("480p", "dv", 30), ("480p", "hdr", 20), ("480p", "", 10),
        ("", "dvhdrplus", 9), ("", "dvhdr", 8), ("", "plus", 7), ("", "dv", 5),
        ("", "hlg", 2), ("", "hdr", 1),
    )
    assert len(RESOLUTION_WEIGHTS) == 39
    weights = [weight for _key, _alt, weight in RESOLUTION_WEIGHTS]
    assert weights == sorted(weights, reverse=True), (
        "the file's own order is non-increasing in weight; keep it that way so "
        "config order and weight order are the same thing"
    )


def test_the_audio_codec_weight_table_is_kometas_sixteen_rows_verbatim():
    """`audio_codec.yml:104-166`'s weights against `:61-95`'s regexes, in
    weight order. All sixteen are reachable now, where the retired
    `AUDIO_CODECS` map could only ever answer ten of them."""
    assert [(stem, weight) for stem, weight, _pattern in AUDIO_CODEC_WEIGHTS] == [
        ("truehd_atmos", 160), ("dtsx", 150), ("plus_atmos", 140),
        ("dolby_atmos", 130), ("truehd", 120), ("ma", 110), ("flac", 100),
        ("pcm", 90), ("hra", 80), ("plus", 70), ("dtses", 60), ("dts", 50),
        ("digital", 40), ("aac", 30), ("mp3", 20), ("opus", 10),
    ]


def test_the_award_takes_the_strictly_greater_weight_so_a_tie_keeps_config_order():
    """`modules/overlays.py:582-586` at the pinned digest:

        for v in gv:
            if final is None or overlay_groups[gk][v] > overlay_groups[gk][final]:
                final = v

    STRICTLY greater, so equal weights keep whichever was met first -- config
    order. `badges/compose.py:428` already arbitrates definition groups by the
    same rule; `_award` is that rule written once for the value tables. The one
    tie in the oracle is 4K-Dovetail (130) against 1080P-DV-HDR-Plus-Dovetail
    (130), `resolution.yml:273-277`."""
    assert _award([("first", 130), ("second", 130)]) == "first"
    assert _award([("first", 130), ("second", 131)]) == "second"
    assert _award([("only", 1)]) == "only"
    assert _award([]) is None


def test_every_resolution_weight_row_names_a_vendored_png():
    """A row whose `<key><alt>.png` is missing would render as a silently
    absent badge -- `compose.py` skips a missing image file with a bare
    `continue`. 39 rows, 39 files."""
    from pathlib import Path

    root = Path("assets/badges/images/resolution")
    for key, alt, _weight in RESOLUTION_WEIGHTS:
        stem = key + alt
        assert (root / ("%s.png" % stem)).exists(), "%s.png is not vendored" % stem


def test_every_audio_codec_weight_row_names_a_vendored_png():
    """The six stems the retired `AUDIO_CODECS` map could not reach --
    truehd_atmos, dtsx, plus_atmos, dolby_atmos, hra, dtses -- are vendored
    already, which is what makes folding upstream's whole table in a
    transcription rather than an asset project. `compose.py:56` draws from
    `compact`."""
    from pathlib import Path

    root = Path("assets/badges/images/audio_codec/compact")
    for stem, _weight, _pattern in AUDIO_CODEC_WEIGHTS:
        assert (root / ("%s.png" % stem)).exists(), "%s.png is not vendored" % stem
```

- [ ] **Step 8: Run the five new tests and confirm they fail for the stated reason**

```bash
cd D:/Sites/autoposter-phasee
docker compose -p pphe -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pphe-t1red1 test sh -c \
  'set -o pipefail; pytest tests/test_badge_media.py -q 2>&1 | tee /app/.superpowers/run-pphe-t1red1.log'
docker wait pphe-t1red1
docker rm pphe-t1red1
tail -20 .superpowers/run-pphe-t1red1.log
```

Expected: a **collection error**, not five failures —
`ImportError: cannot import name 'AUDIO_CODEC_WEIGHTS' from
'autoposter.badges.values'`. That is the right RED: the names do not exist yet.
**If the file collects and the existing 50 still pass, the import block was not
extended — go back to Step 7.**

- [ ] **Step 9: Add the three weight tables, the alt map and the comparator**

In `src/autoposter/badges/values.py`, **delete** the `AUDIO_CODECS` dict at
`:30-35` and **delete** `_HDR_SUFFIXES` and `_hdr_suffix` at `:326-347`. Leave
`RESOLUTIONS` (`:28`) and `_HDR10_PLUS` (`:316-323`) exactly as they are. Then
insert the block below immediately after `_HDR10_PLUS`'s definition, and replace
the existing `_VIDEO_FORMATS` tuple (`:375-384`) with the three-column version
at the end of the block. Nothing calls any of this yet — Step 11 and Step 13 do
the wiring.

```python
# Kometa's group arbitration and its four weight tables, transcribed from the
# pinned image `kometateam/kometa`
# sha256:c58f6d4af511613f218b6dafbfc84078af4e5a6089790c1fdba58fd7c5dad70a.
#
# The finding these encode, because it is not what the roadmap cell said:
# Kometa never picks a `<Media>`. Every badge-relevant filter in the four
# shipped default files is an ANY-OF read across EVERY version of the item --
# `resolution.yml:243-251`'s whole-item `plex_search` plus `has_dolby_vision`
# (`modules/plex.py:2832-2838`) and `filepath.regex` (`:2804-2805`, i.e.
# `item.locations`), `audio_codec.yml:98-100`'s `audio_track_title.regex` OR
# `filepath.regex` (`modules/plex.py:2791-2794`), `video_format.yml:64-65`'s
# `filepath.regex`. The weight table then arbitrates between the OVERLAYS that
# matched, not between the versions. So an item with a 4K SDR version and a
# 1080p Dolby Vision version is awarded `4kdv`: the resolution predicate and
# the DV predicate are two independent item-level reads, not one per-version
# conjunction. That is upstream's arithmetic and it is transcribed, not
# corrected (roadmap row 106, adjudication A-1).


def _award(candidates) -> str | None:
    """The highest-weighted candidate, ties going to the first one offered.

    `modules/overlays.py:582-586` at the pinned digest:

        for v in gv:
            if final is None or overlay_groups[gk][v] > overlay_groups[gk][final]:
                final = v

    STRICTLY greater, so equal weights keep the incumbent -- which is config
    order, which is the order the tables below are written in.
    `badges/compose.py:428` already arbitrates definition groups by the same
    `current is None or definition.weight > current.weight`; this is that one
    rule, once more, over the value tables rather than over definitions.
    """
    final: tuple[str, int] | None = None
    for name, weight in candidates:
        if final is None or weight > final[1]:
            final = (name, weight)
    return None if final is None else final[0]


# What each Kometa `alt` requires, as a flag set. `resolution.yml:210-227`
# keys each alt off a different signal -- `dv` off the `has_dolby_vision`
# filter, `hdr` off the search's `hdr: true`, and `hlg`/`plus`/`dvhdr`/
# `dvhdrplus` off `filepath.regex` -- but `media_info_from_plex` already
# reduces every one of those signals to a member of `hdr_flags`, so the
# requirement is stated here as the flag set the alt needs. There is no
# `dvhlg`: upstream ships no such overlay row and this repository vendors no
# such PNG, so Dolby Vision over an HLG base layer resolves to `dv` on weight
# (120 against 111 at 1080p) rather than by a special case.
_ALT_FLAGS: dict[str, frozenset[str]] = {
    "dvhdrplus": frozenset({"dv", "plus"}),
    "dvhdr": frozenset({"dv", "hdr"}),
    "plus": frozenset({"plus"}),
    "dv": frozenset({"dv"}),
    "hlg": frozenset({"hlg"}),
    "hdr": frozenset({"hdr"}),
    "": frozenset(),
}


# `resolution.yml:255-371`, verbatim, in the file's own order -- which is
# non-increasing in weight and is therefore also the tie-break order the
# strict `>` above implies. 39 rows: seven each for 4k, 1080p and 720p, six
# each for 576p and 480p (upstream ships NO `hlg` row for either, so an HLG
# flag at those tiers draws the plain badge and the vendored `576phlg.png`
# and `480phlg.png` are unreachable), and six resolution-less rows at the end
# (`:354-371`) that fire for every item and are what an item whose
# `videoResolution` is none of the five tiers gets. Five of those six carry
# `all: true`; `HDR-Dovetail` (`:369-370`) does not, which is upstream's own
# inconsistency, transcribed rather than corrected -- it makes no difference
# here because this derivation issues no `plex_search`.
RESOLUTION_WEIGHTS: tuple[tuple[str, str, int], ...] = (
    ("4k", "dvhdrplus", 160), ("4k", "dvhdr", 158), ("4k", "plus", 155),
    ("4k", "dv", 150), ("4k", "hlg", 141), ("4k", "hdr", 140), ("4k", "", 130),
    ("1080p", "dvhdrplus", 130), ("1080p", "dvhdr", 128), ("1080p", "plus", 125),
    ("1080p", "dv", 120), ("1080p", "hlg", 111), ("1080p", "hdr", 110),
    ("1080p", "", 100),
    ("720p", "dvhdrplus", 99), ("720p", "dvhdr", 98), ("720p", "plus", 95),
    ("720p", "dv", 90), ("720p", "hlg", 81), ("720p", "hdr", 80),
    ("720p", "", 70),
    ("576p", "dvhdrplus", 69), ("576p", "dvhdr", 68), ("576p", "plus", 65),
    ("576p", "dv", 60), ("576p", "hdr", 50), ("576p", "", 40),
    ("480p", "dvhdrplus", 39), ("480p", "dvhdr", 38), ("480p", "plus", 35),
    ("480p", "dv", 30), ("480p", "hdr", 20), ("480p", "", 10),
    ("", "dvhdrplus", 9), ("", "dvhdr", 8), ("", "plus", 7), ("", "dv", 5),
    ("", "hlg", 2), ("", "hdr", 1),
)


# `audio_codec.yml:61-95`'s sixteen regexes against `:104-166`'s sixteen
# weights, in weight order. These match AUDIO TRACK TITLES and FILE PATHS
# (`:98-100`), never Plex's `media.audioCodec` -- upstream does not read that
# attribute for this overlay at all, which is why the ten-entry
# `AUDIO_CODECS` map this replaces could never reach `truehd_atmos`, `dtsx`,
# `plus_atmos`, `dolby_atmos`, `hra` or `dtses`. Two upstream quirks are
# transcribed rather than corrected, and both are pinned by tests: `aac` also
# matches "stereo" and "2.0", and `digital`'s `ac3` alternative is unanchored,
# so it matches inside "EAC3" too -- harmless, because `plus` (70) outranks
# `digital` (40) and matches the same string.
AUDIO_CODEC_WEIGHTS: tuple[tuple[str, int, re.Pattern[str]], ...] = (
    ("truehd_atmos", 160,
     re.compile(r"(?i)^(?=.*\btrue[ ._-]?hd(\b|\d))(?=.*\batmos(\b|\d))")),
    ("dtsx", 150, re.compile(r"(?i)\b(dts[-_. ]?x7?)\b(?![-_. ]?(26[456]))")),
    ("plus_atmos", 140,
     re.compile(r"(?i)^(?=.*\b((dd[p+])|(dolby[ ._-]digital[ ._-]plus)|(e[ ._-]?ac3)\b))"
                r"(?=.*\batmos(\b|\d))")),
    ("dolby_atmos", 130, re.compile(r"(?i)\batmos(\b|\d)")),
    ("truehd", 120, re.compile(r"(?i)\btrue[ ._-]?hd(\b|\d)")),
    ("ma", 110, re.compile(r"(?i)\bdts[ ._-]?(hd[ ._-])?(ma|xll|hd)(\b|\d)(?![ ._-]hra)")),
    ("flac", 100, re.compile(r"(?i)\bflac(\b|\d)")),
    ("pcm", 90, re.compile(r"(?i)\bl?pcm(\b|\d)")),
    ("hra", 80, re.compile(r"(?i)\bdts[ ._-]?(hd[ ._-])?(hr|hra|hi|ra)(\b|\d)")),
    ("plus", 70,
     re.compile(r"(?i)\b(dd[p+])|(dolby[ ._-]digital[ ._-]plus)|(e[ ._-]?ac3)\b")),
    ("dtses", 60, re.compile(r"(?i)\bdts[ ._-]?es(\b|\d)")),
    ("dts", 50, re.compile(r"(?i)\bdts(\b|\d)")),
    ("digital", 40, re.compile(r"(?i)\b(dd)|(ac3)|(dolby)(\b|\d)")),
    ("aac", 30, re.compile(r"(?i)\b(aac|stereo|2\.0)\b")),
    ("mp3", 20, re.compile(r"(?i)\bmp3(\b|\d)")),
    ("opus", 10, re.compile(r"(?i)\b(?<!-)OPUS(\b|\d)")),
)


# Kometa's `video_format.yml`, transcribed verbatim: each overlay filters on
# `filepath.regex` (`:64-65`, `plex_all: true`, i.e. `item.locations` -- every
# path of every version) and displays its own key as the badge text
# (`text_<<key>>` defaults to `<<overlay_name>>`, no case transform). All
# eight share `group: quality`, so only the highest-weighted match is drawn;
# the weights are `:69-99` and they are carried in the tuple now rather than
# implied by its order, so this table has the same shape as the two above and
# the same `_award` arbitrates it. `bluray` still precedes `dvd`, which no
# longer matters for correctness (the weights decide) but keeps the file
# readable against upstream's own ordering.
_VIDEO_FORMATS: tuple[tuple[str, int, re.Pattern[str]], ...] = (
    ("REMUX", 60, re.compile(r"(?i)\bremux\b")),
    ("BLU-RAY", 50, re.compile(r"(?i)\b(blu[ ._-]?ray|bd|br|hd[ ._-]?dvd)\b")),
    ("WEB", 40, re.compile(r"(?i)web[ ._-]?(dl|rip)")),
    ("HDTV", 30, re.compile(r"(?i)\bhd[ ._-]?tv\b")),
    ("DVD", 20, re.compile(r"(?i)\bdvd\b")),
    ("SDTV", 10, re.compile(r"(?i)\bsd[ ._-]?tv\b")),
    ("TELESYNC", 9, re.compile(r"(?i)\b(TS|HDTS|TELESYNC)\b")),
    ("CAM", 8, re.compile(r"(?i)\b(HQ|HD)?CAM\b")),
)
```

- [ ] **Step 10: Run the file and confirm the five new tests pass**

The three accessors still reference the names just deleted, so this run will
report import/`NameError` failures from the OLD tests, not from the new ones.
Run the five new tests **by node id** (Global Constraint 12) to isolate them:

```bash
cd D:/Sites/autoposter-phasee
docker compose -p pphe -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pphe-t1green1 test sh -c \
  'set -o pipefail; pytest \
   "tests/test_badge_media.py::test_the_resolution_weight_table_is_kometas_thirty_nine_rows_verbatim" \
   "tests/test_badge_media.py::test_the_audio_codec_weight_table_is_kometas_sixteen_rows_verbatim" \
   "tests/test_badge_media.py::test_the_award_takes_the_strictly_greater_weight_so_a_tie_keeps_config_order" \
   "tests/test_badge_media.py::test_every_resolution_weight_row_names_a_vendored_png" \
   "tests/test_badge_media.py::test_every_audio_codec_weight_row_names_a_vendored_png" \
   -q 2>&1 | tee /app/.superpowers/run-pphe-t1green1.log'
docker wait pphe-t1green1
docker rm pphe-t1green1
tail -10 .superpowers/run-pphe-t1green1.log
```

Expected: `5 passed`. Do not commit yet — the module is mid-surgery and the rest
of the file is red by construction. Steps 11–16 close it.

- [ ] **Step 11: Widen `MediaInfo` and rewrite `media_info_from_plex` to one walk**

Replace `MediaInfo`'s first two field declarations (`:97-98`) and its
`file_path` field (`:105-109`) as shown, and replace the whole of
`media_info_from_plex` (`:152-266`). The nine comment blocks C2b left in the
dataclass (`:110-147`) stay exactly as they are — they describe
`audio_stream_languages`/`subtitle_stream_languages`, which this phase does not
change.

```python
@dataclass(frozen=True)
class MediaInfo:
    """The media attributes badges are derived from -- ITEM-LEVEL, not
    primary-version.

    Kometa never picks a `<Media>` (roadmap row 106, adjudication A-1): every
    badge-relevant filter in the four shipped default files is an any-of read
    across every version, and the weight tables arbitrate between the overlays
    that matched. So the three fields a badge is derived from are the item's
    tuples, not one version's scalars -- `video_resolutions`, not
    `video_resolution`; `audio_track_titles` (which is what
    `audio_codec.yml:98-100` actually reads), not `audio_codec`; `file_paths`
    (Kometa's `item.locations`), not `file_path`. `hdr_flags` is the union
    across every version.

    The field COUNT and ORDER are unchanged, deliberately: every construction
    site in the tree passes the first eight or nine fields POSITIONALLY.
    """

    video_resolutions: tuple[str, ...]
    audio_track_titles: tuple[str, ...]
    # Unused by any badge and by anything else in the tree; kept because it
    # predates this phase, read off the FIRST version because that is where it
    # was read from before and nothing consumes it either way.
    audio_channels: int | None
    duration_ms: int | None
    audio_languages: tuple[str, ...]
    hdr_flags: frozenset[str]
    season_number: int | None
    episode_number: int | None
    # Every file of every version -- Kometa's `filepath`, which is
    # `self.cached_item_attr(item, "locations")` (`modules/plex.py:2804-2805`
    # at the pinned digest). Three badge inputs read it: the `video_format`
    # label, the HDR10+ resolution variant, and (as one half of an OR with the
    # track titles) the audio codec. Defaulted because every construction site
    # predating the video_format badge passes the other eight positionally.
    file_paths: tuple[str, ...] = ()
```

```python
def media_info_from_plex(item) -> MediaInfo:
    """Read badge inputs off a Plex item, across EVERY `<Media>` version.

    Calls ``reload()`` when media is absent, because a search result carries no
    stream detail. That is ``reload()``, not ``refresh()`` -- the latter asks
    Plex to re-scan from its metadata agents, which can overwrite the artwork
    this project just uploaded.

    ONE walk of ``item.media``, where sub-phase C2b had two. C2b's comment
    said widening its primary-version loop "would change ``audio_languages``,
    ``hdr_flags`` and ``file_path`` for every multi-version item" -- and that
    is exactly what roadmap row 106 is, done deliberately with Kometa's weight
    tables as the arbiter. Two of those three are widened here.
    ``audio_languages`` is the exception and stays PRIMARY-VERSION-ONLY
    (adjudication A-4): it feeds ``language_slots``' flag badge, which draws
    DISTINCT codes and would draw duplicate flags off a whole-item read, and
    C2b already built the undeduplicated whole-item pair beside it for the
    filter dialect. Hence the ``index == 0`` gate below, which is the only
    place in this function that knows a primary version exists.

    Both reads walk objects ``item.reload()`` already fetched, so the cost
    claim C2b rests on -- zero extra Plex requests, zero extra bytes -- is
    unchanged and this phase inherits it verbatim.
    """
    if not getattr(item, "media", None):
        item.reload()
    versions = getattr(item, "media", None) or []

    resolutions: list[str] = []
    titles: list[str] = []
    paths: list[str] = []
    flags: set[str] = set()
    languages: list[str] = []
    audio_streams: list[str] = []
    subtitle_streams: list[str] = []

    for index, version in enumerate(versions):
        resolution = getattr(version, "videoResolution", None)
        if resolution:
            resolutions.append(resolution)
        for part in getattr(version, "parts", []) or []:
            path = getattr(part, "file", None)
            if path:
                paths.append(path)
            for stream in getattr(part, "streams", []) or []:
                kind = stream.streamType
                if kind == 1:
                    if getattr(stream, "DOVIPresent", None):
                        flags.add("dv")
                    trc = getattr(stream, "colorTrc", None)
                    if trc == "smpte2084":
                        flags.add("hdr")
                    elif trc == "arib-std-b67":
                        flags.add("hlg")
                    continue
                # `base_language_code`, not a two-character slice of
                # `languageCode`. Plex's code is ISO 639-2 and
                # `languages.json` is keyed by ISO 639-1, and truncation is
                # not a conversion between them: `swe` sliced to `sw`, which
                # is a REAL key -- Swahili -- so a Swedish track drew a
                # Tanzanian flag, while `ger`, `cze`, `dut`, `gre`, `ice` and
                # `chi` sliced to nothing the table holds and lost their flag
                # entirely. A code the converter cannot reduce comes back
                # unchanged and simply is not a table key, which is the same
                # no-flag outcome Plex's `und` had before, reached honestly.
                code = base_language_code(getattr(stream, "languageCode", None) or "").lower()
                if kind == 2:
                    # Kometa's `audio_track_title` (`modules/plex.py:2791-2794`
                    # at the pinned digest): every audio stream's
                    # `extendedDisplayTitle`, across every version, in listing
                    # order, falsy titles dropped -- upstream's own
                    # `if a.extendedDisplayTitle` guard.
                    title = getattr(stream, "extendedDisplayTitle", None)
                    if title:
                        titles.append(title)
                    audio_streams.append(code)
                    if index == 0 and code and code not in languages:
                        languages.append(code)
                elif kind == 3:
                    subtitle_streams.append(code)

    # Kometa reads HDR10+ off the path, and off EVERY path: the `plus` alt is
    # a `filepath.regex` (`resolution.yml:222-223`) and `filepath` is
    # `item.locations`.
    if any(_HDR10_PLUS.search(path) for path in paths):
        flags.add("plus")

    return MediaInfo(
        video_resolutions=tuple(resolutions),
        audio_track_titles=tuple(titles),
        # NOT `aspectRatio`, and that is deliberate (roadmap row 100,
        # sub-phase C2b, adjudication A-2). A-2 ruled that `aspect` answers
        # through the same whole-`<Media>`-list walk `resolution` uses, so the
        # read lives in `collections/filter_values.py::_aspect`, shared
        # verbatim by both item views, and `MediaInfo` deliberately carries no
        # aspect field: a second copy of the same value is exactly the drift
        # `overlays/selection.py`'s own R2 note ("one accessor, not two copies
        # that can drift") rules out, and nothing would consume it --
        # `overlays/variables.py`'s grammar has no `<<aspect>>` token in any
        # of its variable classes.
        audio_channels=getattr(versions[0], "audioChannels", None) if versions else None,
        duration_ms=getattr(item, "duration", None),
        audio_languages=tuple(languages),
        hdr_flags=frozenset(flags),
        season_number=getattr(item, "seasonNumber", None),
        episode_number=getattr(item, "episodeNumber", None),
        file_paths=tuple(paths),
        audio_stream_languages=tuple(audio_streams),
        subtitle_stream_languages=tuple(subtitle_streams),
    )
```

- [ ] **Step 12: Write the nineteen failing tests for the reads, the awards and the storm guard**

Add the module-level constants and the fixture helpers below to
`tests/test_badge_media.py` (immediately after the existing `_item_with_path`
helper at `:188-192`), then the nineteen tests at the end of the file. Substitute
the two 64-character digests **recorded in Step 6** for
`SINGLE_VERSION_FINGERPRINT` and `MEDIA_ZERO_FINGERPRINT`; they are measurements
taken on the unmodified code and there is no other legitimate source for them.
Also extend the import block to bring in `BadgeInputs`, `badge_fingerprint` and
`badge_values` — the file already imports all three at `:10`.

```python
# --- Every `<Media>`, awarded by Kometa's weights (roadmap row 106) ---------


def _version(resolution=None, titles=(), path=None, video_kw=None):
    """One `<Media>` with one `<Part>`: an optional video stream carrying
    `video_kw`, one audio stream per string in `titles` (each tagged `eng`),
    and `path` as the part's file. Those three are everything this phase
    reads. `audioCodec` is deliberately NOT set: nothing reads it any more,
    and a fixture that still carried it would suggest otherwise."""
    streams = []
    if video_kw is not None:
        streams.append(FakeStream(1, codec="hevc", **video_kw))
    for title in titles:
        streams.append(FakeStream(2, languageCode="eng", extendedDisplayTitle=title))
    return FakeMedia([FakePart(streams, file=path)], videoResolution=resolution,
                     audioChannels=6)


def _multi(*versions, duration=None):
    return FakeItem(list(versions), duration=duration)


# MEASURED at Task 1 Step 6, on the cut ref, BEFORE any source edit -- the
# only way a "this did not move" pin can mean anything. Do not recompute them
# from the code under test.
SINGLE_VERSION_FINGERPRINT = "<the SINGLE fingerprint recorded in Step 6>"
MEDIA_ZERO_FINGERPRINT = "<the TWO fingerprint recorded in Step 6>"


def test_the_resolution_list_spans_every_media_version():
    item = _multi(_version("1080", (), "/m/X/a.mkv"), _version("4k", (), "/m/X/b.mkv"))
    assert media_info_from_plex(item).video_resolutions == ("1080", "4k")


def test_the_audio_track_titles_span_every_media_version():
    """`modules/plex.py:2791-2794` at the pinned digest:

        for media in item.media:
            for part in media.parts:
                values.extend([a.extendedDisplayTitle
                               for a in part.audioStreams()
                               if a.extendedDisplayTitle])

    Every version, listing order, and a falsy title dropped rather than
    carried as an empty string -- upstream's own guard."""
    item = _multi(
        _version("1080", ("English (AAC Stereo)", ""), "/m/X/a.mkv"),
        _version("4k", ("English (DTS-HD MA 5.1)",), "/m/X/b.mkv"),
    )
    assert media_info_from_plex(item).audio_track_titles == (
        "English (AAC Stereo)", "English (DTS-HD MA 5.1)",
    )


def test_the_file_path_list_spans_every_part_of_every_version():
    """Kometa's `filepath` is `item.locations` (`modules/plex.py:2804-2805`),
    which is every file of every version -- so a version split across two
    parts contributes both, not just the first."""
    two_part = FakeMedia(
        [FakePart([], file="/m/X/a1.mkv"), FakePart([], file="/m/X/a2.mkv")],
        videoResolution="1080", audioChannels=6,
    )
    item = _multi(two_part, _version("4k", (), "/m/X/b.mkv"))
    assert media_info_from_plex(item).file_paths == (
        "/m/X/a1.mkv", "/m/X/a2.mkv", "/m/X/b.mkv",
    )


def test_the_hdr_flags_are_the_union_across_every_version():
    item = _multi(
        _version("1080", (), "/m/X/a.mkv", {"DOVIPresent": True, "colorTrc": "bt709"}),
        _version("4k", (), "/m/X/b.mkv", {"colorTrc": "smpte2084"}),
    )
    assert media_info_from_plex(item).hdr_flags == frozenset({"dv", "hdr"})


def test_the_hdr10_plus_marker_is_read_off_every_path_not_just_the_first():
    item = _multi(
        _version("1080", (), "/m/X/a.1080p.mkv", {"colorTrc": "smpte2084"}),
        _version("4k", (), "/m/X/b.2160p.HDR10+.mkv", {"colorTrc": "smpte2084"}),
    )
    assert "plus" in media_info_from_plex(item).hdr_flags


def test_a_dts_hd_ma_4k_second_version_wins_both_badges_off_an_aac_1080p_first():
    """The row's own example, and the whole point of the phase. Ben-Hur is
    badged AAC/1080p/WEB today because Plex happens to list the web-dl first;
    the DTS-HD MA 4K remux at `media[1]` is what Kometa describes."""
    item = _multi(
        _version("1080", ("English (AAC Stereo)",),
                 "/m/Ben-Hur (1959)/Ben-Hur.1959.WEBDL-1080p.mkv"),
        _version("4k", ("English (DTS-HD MA 5.1)",),
                 "/m/Ben-Hur (1959)/Ben-Hur.1959.REMUX-2160p.mkv"),
    )
    info = media_info_from_plex(item)
    assert resolution_image(info) == "4k"
    assert audio_codec_image(info) == "ma"
    assert video_format_text(info) == "REMUX"


def test_the_hdr_flags_are_item_level_so_a_4k_sdr_beside_a_1080p_dv_awards_4kdv():
    """The consequence of the any-of formulation, stated rather than
    stumbled into. No single file here is 4K Dolby Vision -- but
    `resolution.yml:243-251`'s resolution predicate and its
    `has_dolby_vision` filter are two INDEPENDENT item-level reads, so
    `4K-DV-Dovetail` (150) fires and outranks both `4K-Dovetail` (130) and
    `1080P-DV-Dovetail` (120). Upstream's arithmetic, transcribed."""
    item = _multi(
        _version("4k", (), "/m/X/X.2160p.mkv", {"colorTrc": "bt709"}),
        _version("1080", (), "/m/X/X.1080p.DV.mkv",
                 {"DOVIPresent": True, "colorTrc": "bt709"}),
    )
    info = media_info_from_plex(item)
    assert info.hdr_flags == frozenset({"dv"})
    assert resolution_image(info) == "4kdv"


def test_the_one_hundred_thirty_tie_is_unreachable_through_the_item_level_any_of():
    """`4K-Dovetail` (`resolution.yml:273-274`) and
    `1080P-DV-HDR-Plus-Dovetail` (`:276-277`) both weigh 130 -- the oracle's
    one tie. It cannot be reached from a real item: the flags that let the
    1080p row fire are item-level, so the moment they hold,
    `4K-DV-HDR-Plus-Dovetail` (160) fires too. The comparator's tie rule is
    pinned directly in
    `test_the_award_takes_the_strictly_greater_weight_so_a_tie_keeps_config_order`;
    this pins the item-level consequence, so a future refactor that reaches
    for an unstable sort has both halves to answer to."""
    item = _multi(
        _version("4k", (), "/m/X/X.2160p.mkv", {"colorTrc": "bt709"}),
        _version("1080", (), "/m/X/X.1080p.DV.HDR10Plus.mkv",
                 {"DOVIPresent": True, "colorTrc": "smpte2084"}),
    )
    assert resolution_image(media_info_from_plex(item)) == "4kdvhdrplus"


def test_video_format_takes_the_highest_weighted_label_across_every_path():
    """`video_format.yml:64-65` filters on `filepath.regex` with
    `plex_all: true` -- `item.locations`, every path. A web-dl beside a remux
    is REMUX (60) over WEB (40)."""
    item = _multi(
        _version("1080", (), "/m/X/X.WEBDL-1080p.mkv"),
        _version("4k", (), "/m/X/X.REMUX-2160p.mkv"),
    )
    assert video_format_text(media_info_from_plex(item)) == "REMUX"


def test_hlg_outranks_hdr_because_the_weight_table_says_so():
    """The retired `_HDR_SUFFIXES` tuple checked `hdr` before `hlg`;
    `resolution.yml:288-292` weighs 1080P-HLG at 111 and 1080P-HDR at 110. The
    weight table governs now. One video stream cannot report two transfer
    curves, so this needs two versions -- which is also the only way the
    disagreement was ever reachable, and why no single-version item moves
    because of it."""
    item = _multi(
        _version("1080", (), "/m/X/X.a.mkv", {"colorTrc": "smpte2084"}),
        _version("1080", (), "/m/X/X.b.mkv", {"colorTrc": "arib-std-b67"}),
    )
    info = media_info_from_plex(item)
    assert info.hdr_flags == frozenset({"hdr", "hlg"})
    assert resolution_image(info) == "1080phlg"


def test_576p_and_480p_carry_no_hlg_row_so_an_hlg_flag_draws_the_plain_badge():
    """Upstream ships seven rows for 4k, 1080p and 720p and only six for 576p
    and 480p -- there is no 576P-HLG or 480P-HLG overlay
    (`resolution.yml:318-353`). This repository vendors `576phlg.png` and
    `480phlg.png` anyway; they are now unreachable, which is upstream's answer
    and not an oversight here."""
    for plex_value, expected in (("576", "576p"), ("480", "480p")):
        item = _multi(_version(plex_value, (), "/m/X/X.mkv",
                               {"colorTrc": "arib-std-b67"}))
        assert resolution_image(media_info_from_plex(item)) == expected


def test_an_unknown_resolution_with_a_dolby_vision_stream_draws_the_tail_badge():
    """`resolution.yml:354-371`'s six `key: ""` rows fire for every item, so
    an item Plex reports as none of the five tiers still gets a badge when it
    carries an alt: `resolution/dv.png`, not nothing. With no flags at all it
    still gets nothing, which is what
    `test_resolution_image_is_suppressed_for_an_unknown_resolution` above
    pins."""
    item = _multi(_version("240", (), "/m/X/X.mkv",
                           {"DOVIPresent": True, "colorTrc": "bt709"}))
    assert resolution_image(media_info_from_plex(item)) == "dv"


def test_an_atmos_track_title_outranks_the_plain_truehd_badge():
    """One of the six stems the retired ten-entry map could never reach.
    `audio_codec.yml:64-65`'s truehd_atmos regex needs both tokens anywhere in
    the string; 160 beats truehd's 120."""
    item = _multi(_version("4k", ("English (TRUEHD 7.1) Atmos",), "/m/X/X.mkv"))
    assert audio_codec_image(media_info_from_plex(item)) == "truehd_atmos"


def test_a_dts_x_title_outranks_dts_hd_ma():
    """`audio_codec.yml:66-67`'s dtsx (150) against `:74-75`'s ma (110). Plex
    reports `dca-ma` for both, which is why the old identifier map could not
    tell them apart at all."""
    item = _multi(_version("4k", ("English (DTS-HD MA 7.1) DTS-X",), "/m/X/X.mkv"))
    assert audio_codec_image(media_info_from_plex(item)) == "dtsx"


def test_a_codec_token_in_the_file_path_counts_when_the_track_title_is_silent():
    """`audio_codec.yml:98-100` is a LIST of two filters --
    `audio_track_title.regex` OR `filepath.regex` -- and either satisfies the
    overlay."""
    item = _multi(_version("1080", ("English",), "/m/X/X.1080p.TrueHD.Atmos.mkv"))
    assert audio_codec_image(media_info_from_plex(item)) == "truehd_atmos"


def test_a_stereo_track_title_draws_the_aac_badge_because_upstream_says_so():
    """`audio_codec.yml:90-91`'s aac regex is `\\b(aac|stereo|2\\.0)\\b`:
    upstream reads "Stereo" and "2.0" as AAC, whatever the actual codec is.
    Transcribed, not corrected -- the same posture `video_format_text`'s
    HD-DVD-is-BLU-RAY case has held since row 14."""
    item = _multi(_version("1080", ("English (Vorbis Stereo)",), "/m/X/X.mkv"))
    assert audio_codec_image(media_info_from_plex(item)) == "aac"


def test_no_codec_token_anywhere_draws_no_audio_codec_badge():
    """The one direction in which this phase REMOVES a badge, pinned so it
    cannot happen quietly. The retired map read Plex's `media.audioCodec` and
    so always answered; Kometa reads track titles and file paths
    (`audio_codec.yml:98-100`) and answers nothing when neither carries a
    codec token. Rare -- Plex's `extendedDisplayTitle` carries the codec for
    every stream it knows -- but real, and counted by the affected-count query
    in the roadmap row before this ships."""
    item = _multi(_version("1080", ("English",), "/m/X/X.1080p.mkv"))
    assert audio_codec_image(media_info_from_plex(item)) is None


def test_a_single_version_item_keeps_its_badge_values_and_fingerprint_to_the_byte():
    """The storm guard's load-bearing half. `badge_values`' `resolution`,
    `audio_codec` and `video_format` keys are `badge_fingerprint` inputs, so
    anything this change moves re-badges and re-uploads once. A ONE-version
    item must move NOTHING: its resolution set is one tier, its flag union is
    one version's flags, its path list is one path, and its track title
    resolves to the same stem the Plex identifier did. Both literals were
    captured on the pre-change code at Task 1 Step 6, not re-derived from the
    function under test -- a re-derivation would pass even if the formula and
    the pin moved together."""
    item = _multi(
        _version("1080", ("English (EAC3 5.1)",), "/m/Show/S01E01.WEBDL-1080p.mkv",
                 {"colorTrc": "bt709"}),
        duration=4845912,
    )
    info = media_info_from_plex(item)
    values = badge_values("poster", BadgeInputs(media=info,
                                                video_format=video_format_text(info)))
    assert values == {
        "resolution": "1080p", "audio_codec": "plus", "video_format": "WEB",
        "runtimes": "Runtime: 1h 20m", "languages": "us:EN",
    }
    assert badge_fingerprint("base-fp", "poster", values, "manifest-sha") == (
        SINGLE_VERSION_FINGERPRINT
    )


def test_the_two_version_item_moves_its_fingerprint_off_the_media_zero_one():
    """The movement, stated honestly and pinned in both directions. The
    left-hand dict is what the shipped code produces for this item today
    (captured at Task 1 Step 6) and is what is in the database; the right-hand
    one is what replaces it. Only the badge layer redraws: `badge_fingerprint`
    is deliberately separate from the base `fingerprint`, so no source art is
    re-fetched and no base is re-composited. Measured ceiling for the whole
    library: 50 movies and 224 episodes carry more than one `<Media>`."""
    item = _multi(
        _version("1080", ("English (AAC Stereo)",),
                 "/m/Ben-Hur (1959)/Ben-Hur.1959.WEBDL-1080p.mkv"),
        _version("4k", ("English (DTS-HD MA 5.1)",),
                 "/m/Ben-Hur (1959)/Ben-Hur.1959.REMUX-2160p.mkv"),
        duration=4845912,
    )
    info = media_info_from_plex(item)
    values = badge_values("poster", BadgeInputs(media=info,
                                                video_format=video_format_text(info)))
    assert values == {
        "resolution": "4k", "audio_codec": "ma", "video_format": "REMUX",
        "runtimes": "Runtime: 1h 20m", "languages": "us:EN",
    }
    assert badge_fingerprint("base-fp", "poster", values, "manifest-sha") != (
        MEDIA_ZERO_FINGERPRINT
    )
    assert badge_fingerprint(
        "base-fp", "poster",
        {"resolution": "1080p", "audio_codec": "aac", "video_format": "WEB",
         "runtimes": "Runtime: 1h 20m", "languages": "us:EN"},
        "manifest-sha",
    ) == MEDIA_ZERO_FINGERPRINT
```

- [ ] **Step 13: Run the nineteen new tests and confirm they fail for the stated reason**

```bash
cd D:/Sites/autoposter-phasee
docker compose -p pphe -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pphe-t1red2 test sh -c \
  'set -o pipefail; pytest tests/test_badge_media.py -q 2>&1 | tee /app/.superpowers/run-pphe-t1red2.log'
docker wait pphe-t1red2
docker rm pphe-t1red2
grep -E "^(FAILED|ERROR)|passed|failed" .superpowers/run-pphe-t1red2.log | tail -30
```

Expected: every test that reaches `resolution_image`, `audio_codec_image` or
`video_format_text` fails with `NameError: name 'AUDIO_CODECS' is not defined`
(or `_hdr_suffix`, or `_VIDEO_FORMATS` unpacking two values into three) — the
accessors still reference what Step 9 deleted, and Step 14 is what wires them.
The five tests from Step 7 still pass. **What must NOT appear is a pass from any
of the nineteen.** Read the log; do not proceed on the summary line alone.

- [ ] **Step 14: Rewrite the three accessors onto the weight tables**

Replace `resolution_image` (`:350-360`), `audio_codec_image` (`:363-365`) and
`video_format_text` (`:387-400`) in `src/autoposter/badges/values.py`.

```python
def resolution_image(info: MediaInfo) -> str | None:
    """Filename stem under ``images/resolution/``, e.g. ``1080pdvhdr``.

    Kometa never picks a `<Media>`: `resolution.yml:243-251` is a whole-item
    `plex_search` for the resolution, plus two whole-item filters
    (`has_dolby_vision`, `filepath.regex`). All three are ANY-OF reads across
    every version, so each of the 39 overlays is evaluated against the ITEM
    and the weight table arbitrates between the ones that matched. The tiers
    below are therefore every version's, the flags are the union across every
    version, and the award is the highest weight under the strict `>`.

    The consequence is real and deliberate: an item with a 4K SDR version and
    a 1080p Dolby Vision version is awarded `4kdv`, because upstream's
    resolution predicate and its DV predicate are two independent item-level
    reads and not one per-version conjunction.

    An unknown resolution no longer suppresses the badge outright -- the six
    resolution-less rows (`resolution.yml:354-371`) still fire on the alt
    alone -- but an unknown resolution with NO flags matches nothing, which is
    the same no-badge outcome as before, reached from the table.
    """
    tiers = {
        RESOLUTIONS[value.lower()]
        for value in info.video_resolutions
        if value and value.lower() in RESOLUTIONS
    }
    return _award(
        (key + alt, weight)
        for key, alt, weight in RESOLUTION_WEIGHTS
        if (not key or key in tiers) and _ALT_FLAGS[alt] <= info.hdr_flags
    )


def audio_codec_image(info: MediaInfo) -> str | None:
    """Filename stem under ``images/audio_codec/compact/``.

    `audio_codec.yml:98-100` filters on `audio_track_title.regex` OR
    `filepath.regex` -- it never reads Plex's `media.audioCodec` for this
    overlay at all -- and both reads span every version
    (`modules/plex.py:2791-2794` and `:2804-2805`). So the haystack is every
    audio track title followed by every file path, and the sixteen regexes are
    weighed against it.

    This is a shipped-output change beyond the multi-version fix and it is
    disclosed on roadmap row 106: six stems the retired ten-entry
    `AUDIO_CODECS` map could not reach are reachable now (`truehd_atmos`,
    `dtsx`, `plus_atmos`, `dolby_atmos`, `hra`, `dtses`), and an item with no
    codec token in any title or path draws no badge where the identifier map
    always drew one.
    """
    haystack = info.audio_track_titles + info.file_paths
    return _award(
        (stem, weight)
        for stem, weight, pattern in AUDIO_CODEC_WEIGHTS
        if any(pattern.search(value) for value in haystack)
    )


def video_format_text(info: MediaInfo) -> str | None:
    """The video_format badge string, e.g. ``"WEB"``, or ``None`` to suppress.

    ``None`` for paths that match nothing is Kometa's
    ``ignore_blank_results: true`` -- no overlay in the group runs, so no
    badge is drawn. An item with no file at all (a show or a season, which
    have no media of their own) likewise gets no badge.

    Every path, not the first one: `video_format.yml:64-65` is a
    `filepath.regex` under `plex_all: true`, and `filepath` is
    `item.locations` (`modules/plex.py:2804-2805`). A web-dl beside a remux is
    REMUX, because the weights arbitrate across the whole item.
    """
    return _award(
        (label, weight)
        for label, weight, pattern in _VIDEO_FORMATS
        if any(pattern.search(path) for path in info.file_paths)
    )
```

- [ ] **Step 14a: The Norwegian flag alias — `nb`/`nn` → `no` at flag lookup**

C2's ADDED step, and the one behaviour in Task 1 that is not about `<Media>`
versions. It is numbered `14a` rather than `15` on purpose: every other step
number in this plan is referenced by another step, and renumbering them to make
room would be a worse change than a lettered insert.

**What is wrong.** Plex tags Norwegian audio `nob` (Bokmål) or `nno` (Nynorsk).
`base_language_code` reduces those to `nb` and `nn`, and
`assets/badges/languages.json` is keyed by ISO 639-1 and carries **`no`** —
`{"country": "no", "text": "NO", "weight": 450}` — but neither `nb` nor `nn`
(verified this session: 79 keys, `no` present, `nb`/`nn` absent). So a Norwegian
track reaches `language_slots`' membership test at `values.py:410` and drops out
with no flag.

**Why an alias at lookup and not two new table keys.** `languages.json` is
listed in `assets/badges/MANIFEST.sha256` (verified: one line). Editing it moves
`asset_manifest_sha`, which is a `badge_fingerprint` input, which re-renders the
badge layer of the **whole library** for two codes. C2's ruling is the alias at
flag lookup, **no manifest move**, and Global Constraint 5's "no file added or
changed under `assets/`" stands unchanged.

**Upstream points both codes at the same flag**, which is what makes this a
transcription rather than an invention. From the pinned image
`kometateam/kometa` `sha256:c58f6d4af511613f218b6dafbfc84078af4e5a6089790c1fdba58fd7c5dad70a`
(`defaults/overlays/languages.yml`, sha256 `dcd91ab2…c8f1`), re-extracted this
session:

```
languages.yml:403:    variables: {key: nb, text: NB, weight: 120, country: no}
languages.yml:407:    variables: {key: nn, text: NN, weight: 110, country: no}
languages.yml:267:    variables: {key: no, text: NO, weight: 450}
```

**One deliberate difference from upstream, stated so it is not mistaken for a
mistranscription.** Upstream ships `nb`/`nn` as their own overlays with their
own text and weight (`NB`/120, `NN`/110). C2's ruling is `nb`/`nn` → **`no` at
flag lookup**, so both resolve to the existing `no` ROW and draw `("no", "NO")`
at weight 450 — upstream's own `norwegian` row (`:267`). The flag drawn is the
same either way; borrowing the existing row keeps `languages.json` the single
source and is what makes the manifest immovable.

**A consequence, named rather than discovered later:** an item carrying **both**
a `nob` and a `nor` track now yields two identical `("no", "NO")` slots, where
before it yielded one. `audio_languages` is distinct in its own codes, not in
their aliases, and no dedupe is added here — that is beyond C2's ruling and
would be a second behaviour in a step that has one. It is possible and it is
harmless (two Norwegian flags where upstream would draw an `NB` and an `NO`),
and if it is ever seen in the wild the fix is a dedupe on the aliased code.

**RED first (Global Constraint 10).** In `tests/test_badge_media.py`, **replace**
`test_norwegian_bokmal_loses_its_flag_and_that_is_a_decision_not_an_accident`
with the first test below — it is the same test inverted, not a new one, which
is why the count chain gains **one** here and not two — and add the second
beside it, in the same place.

**Find it by NAME, not by line.** Every `:nnn` in this step is the cut ref's,
and this file has already grown by Step 7's five tests at the end, Step 12's
helpers inserted after `:188-192` and Step 12's nineteen tests at the end. On
the cut ref the test to replace is `:330-346`; `asset_path` is imported at `:9`,
`language_slots` and `media_info_from_plex` in the block at `:11-17`, and
`_audio_only_item` — the existing variadic helper these tests use — is at
`:243-250`. All four are still there, further down.

```python
def test_norwegian_bokmal_and_nynorsk_draw_norways_flag_through_the_alias():
    """Was `..._loses_its_flag_and_that_is_a_decision_not_an_accident`, and it
    is INVERTED here rather than deleted, because the decision it filed on
    roadmap row 243 has now been taken.

    `nob` reduces to `nb` and `nno` to `nn`; `languages.json` is keyed by ISO
    639-1 and carries `no` but neither of those, so both lost their flag --
    `nob` one it used to get by accident off the retired `[:2]` slice, `nno`
    one it never had. `_FLAG_ALIASES` maps both to `no` BEFORE the table
    membership test, so both now draw Norway's flag. Upstream agrees:
    `languages.yml:403` and `:407` at the pinned digest both carry
    `country: no`. `nor`, the far commoner tag, reduces to `no` directly and
    is asserted here unchanged -- it is the control, and if it ever moves the
    alias has been wired in the wrong place.

    The remedy NOT taken is still not taken: `languages.json` gains no keys,
    so `assets/badges/MANIFEST.sha256` does not move and no
    `badge_fingerprint` moves for any item without a Norwegian track."""
    assert language_slots(media_info_from_plex(_audio_only_item("nob"))) == [("no", "NO")]
    assert language_slots(media_info_from_plex(_audio_only_item("nno"))) == [("no", "NO")]
    assert language_slots(media_info_from_plex(_audio_only_item("nor"))) == [("no", "NO")]


def test_the_aliased_norwegian_flags_name_the_same_vendored_asset_as_nor_does():
    """An alias is only worth anything if the stem it produces has a PNG
    behind it. `compose.py::_draw_languages` skips a missing flag file with a
    bare `continue` -- no error, no fallback, no log line -- so an alias
    pointing at an unvendored country would look exactly like the bug it
    fixes. All three codes must resolve to ONE stem and ONE vendored file, not
    to three that merely happen to render."""
    stems = set()
    for code in ("nob", "nno", "nor"):
        slots = language_slots(media_info_from_plex(_audio_only_item(code)))
        assert len(slots) == 1, code
        stems.add(slots[0][0])
    assert stems == {"no"}, stems
    path = asset_path("badges", "images", "flag", "round", "no.png")
    assert path.exists(), "the aliased Norwegian slot names no vendored flag"
```

```bash
cd D:/Sites/autoposter-phasee
docker compose -p pphe -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pphe-t1red3 test sh -c \
  'set -o pipefail; pytest \
   "tests/test_badge_media.py::test_norwegian_bokmal_and_nynorsk_draw_norways_flag_through_the_alias" \
   "tests/test_badge_media.py::test_the_aliased_norwegian_flags_name_the_same_vendored_asset_as_nor_does" \
   -q 2>&1 | tee /app/.superpowers/run-pphe-t1red3.log'
docker wait pphe-t1red3
docker rm pphe-t1red3
tail -20 .superpowers/run-pphe-t1red3.log
```

**RED, expected: `2 failed`.** The first fails on
`assert [] == [('no', 'NO')]` for `nob`; the second on `assert len(slots) == 1`
with `slots == []`, again for `nob`. **If either passes, the alias already
exists somewhere and this step is not the change it thinks it is — stop and find
it.** `nor` must not be the failing assertion in either test; if it is, the
existing lookup has been broken rather than extended.

**GREEN.** In `src/autoposter/badges/values.py`, add `_FLAG_ALIASES` immediately
above `language_slots` and consult it inside the comprehension at `:410`, before
the `code in table` test. Nothing else in the module changes.

```python
# Plex tags Norwegian audio `nob` (Bokmål) or `nno` (Nynorsk), which
# `base_language_code` reduces to `nb` and `nn` -- and `languages.json` is
# keyed by ISO 639-1 and carries `no` but neither of those, so both codes
# reached the table and drew nothing. Upstream points both at the same
# country: `defaults/overlays/languages.yml:403` is
# `{key: nb, text: NB, weight: 120, country: no}` and `:407` is
# `{key: nn, text: NN, weight: 110, country: no}`, read from the pinned image
# `kometateam/kometa`
# sha256:c58f6d4af511613f218b6dafbfc84078af4e5a6089790c1fdba58fd7c5dad70a
# (`languages.yml` sha256 dcd91ab2...c8f1).
#
# The alias is applied HERE, at lookup, and deliberately NOT by adding `nb`
# and `nn` rows to `assets/badges/languages.json`: that file is listed in
# `assets/badges/MANIFEST.sha256`, so editing it moves `asset_manifest_sha`,
# which is a `badge_fingerprint` input, which re-badges and re-uploads the
# whole library for two codes.
#
# Both resolve to the existing `no` ROW, so they draw `("no", "NO")` at
# weight 450 -- upstream's own `norwegian` row (`languages.yml:267`,
# `{key: no, text: NO, weight: 450}`) -- rather than `NB`/120 and `NN`/110.
# The flag is the same either way and one row stays the single source. An
# item carrying both a `nob` and a `nor` track therefore yields two identical
# slots; that is accepted, not overlooked.
_FLAG_ALIASES: dict[str, str] = {"nb": "no", "nn": "no"}


def language_slots(info: MediaInfo, limit: int = 3) -> list[tuple[str, str]]:
    """``(flag_stem, label)`` pairs, highest Kometa weight first.

    The flag is a country code, not a language code -- English shows the US
    flag -- so it comes from the mapping table rather than the language
    itself. ``_FLAG_ALIASES`` is consulted BEFORE the membership test, so a
    code the table does not carry but upstream points at a country the table
    does carry still draws its flag.
    """
    table = _languages()
    aliased = [_FLAG_ALIASES.get(code, code) for code in info.audio_languages]
    known = [(code, table[code]) for code in aliased if code in table]
    known.sort(key=lambda pair: pair[1]["weight"], reverse=True)
    return [(entry["country"], entry["text"]) for _, entry in known[:limit]]
```

```bash
cd D:/Sites/autoposter-phasee
docker compose -p pphe -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pphe-t1green3 test sh -c \
  'set -o pipefail; pytest \
   "tests/test_badge_media.py::test_norwegian_bokmal_and_nynorsk_draw_norways_flag_through_the_alias" \
   "tests/test_badge_media.py::test_the_aliased_norwegian_flags_name_the_same_vendored_asset_as_nor_does" \
   -q 2>&1 | tee /app/.superpowers/run-pphe-t1green3.log'
docker wait pphe-t1green3
docker rm pphe-t1green3
tail -10 .superpowers/run-pphe-t1green3.log
```

**GREEN, expected: `2 passed`.** The rest of the file is still red by
construction until Step 15 finishes the constructions; Step 16 is where the
whole file goes green at **74**.

- [ ] **Step 15: Update the twelve `MediaInfo` constructions and four attribute reads in `tests/test_badge_media.py`**

These are the file's pre-existing tests, which construct `MediaInfo`
positionally. Slot 1 takes a tuple of Plex resolution values, slot 2 a tuple of
audio track titles, slot 9 a tuple of paths. **Every parametrize keeps its
cardinality**, so the file's count moves by exactly the 25 tests added (Steps 7,
12 and 14a), the 1 deleted below, and nothing else. Step 14a's rewrite of the
Norwegian pin is an inversion in place and adds nothing.

- `:63-64` — `assert info.video_resolution == "1080"` / `assert info.audio_codec == "eac3"` become:
  ```python
      assert info.video_resolutions == ("1080",)
      assert info.audio_track_titles == ("English (EAC3 5.1)",)
      assert audio_codec_image(info) == "plus"
  ```
- `:52-58` — `_item()`'s audio stream gains the title those assertions read, and the
  now-unread `audioCodec` kwarg goes:
  ```python
  def _item():
      video = FakeStream(1, codec="hevc", colorTrc="bt709")
      audio = FakeStream(2, codec="eac3", language="English", languageCode="eng",
                         channels=6, extendedDisplayTitle="English (EAC3 5.1)")
      sub = FakeStream(3, codec="srt", language="Finnish", languageCode="fin")
      media = FakeMedia([FakePart([video, audio, sub])], videoResolution="1080",
                        audioChannels=6)
      return FakeItem([media], duration=4845912, season=1, episode=1)
  ```
- `:81` — `assert info.video_resolution is None` becomes `assert info.video_resolutions == ()`.
- `:101` — `MediaInfo(resolution, None, None, None, (), flags, None, None)` becomes
  `MediaInfo((resolution,), (), None, None, (), flags, None, None)`.
- `:106` — `MediaInfo("240", None, ...)` becomes `MediaInfo(("240",), (), ...)`.
- `:110-117` — the seven-row `test_audio_codec_image_names` parametrize keeps
  seven rows, now over track titles:
  ```python
  @pytest.mark.parametrize(
      "title,expected",
      [("English (EAC3 5.1)", "plus"), ("English (AC3 5.1)", "digital"),
       ("English (TRUEHD 7.1)", "truehd"), ("English (DTS 5.1)", "dts"),
       ("English (AAC Stereo)", "aac"), ("English (FLAC 5.1)", "flac"),
       ("English (OPUS 5.1)", "opus")],
  )
  def test_audio_codec_image_names(title, expected):
      info = MediaInfo((), (title,), None, None, (), frozenset(), None, None)
      assert audio_codec_image(info) == expected
  ```
  (`"English (OPUS 5.1)"` rather than `"…Stereo"` on purpose: upstream's aac
  regex matches "stereo" and would award `aac` at weight 30 over `opus` at 10.
  That quirk has its own test above; this row is about the codec.)
- `:120-122` — the unmapped case becomes a title with no codec token:
  ```python
  def test_audio_codec_image_is_suppressed_for_an_unmapped_codec():
      info = MediaInfo((), ("English (Vorbis 5.1)",), None, None, (), frozenset(),
                       None, None)
      assert audio_codec_image(info) is None
  ```
- `:126`, `:132`, `:137`, `:142` — the four `language_slots` constructions take
  `((), (), ...)` in slots 1–2; nothing else about them changes.
- `:150`, `:155` — `MediaInfo("1080", None, ...)` / `MediaInfo("4k", None, ...)`
  become `MediaInfo(("1080",), (), ...)` / `MediaInfo(("4k",), (), ...)`.
- `:169` — inside `test_every_resolution_image_name_exists_on_disk`, the
  construction becomes
  `MediaInfo((resolution,), (), None, None, (), frozenset(combo), None, None)`.
- `:176-185` — `test_every_audio_codec_image_name_exists_on_disk` iterated the
  retired `AUDIO_CODECS` map, which no longer exists. It is superseded by
  `test_every_audio_codec_weight_row_names_a_vendored_png` from Step 7, which
  asserts the same property over all sixteen stems instead of ten. **Delete it.**
  This is the one deletion in the plan and it is the reason the net is `+24`
  and not `+25` (Global Constraint 13): 50 collected, +5 from Step 7, +19 from
  Step 12, +1 from Step 14a, −1 here, = **74**. If the arithmetic does not land
  on 74 at Step 16 and on `B + 24` at Step 21, THE STOP applies.
- `:190-191` — `_item_with_path`'s `FakeMedia(..., audioCodec="eac3", audioChannels=6)`
  drops the `audioCodec` kwarg; nothing reads it.
- `:199` — `assert info.file_path == "/m/Show/S01E01.WEBDL-1080p.mkv"` becomes
  `assert info.file_paths == ("/m/Show/S01E01.WEBDL-1080p.mkv",)`.

- [ ] **Step 16: Run the badge file and confirm it is green**

```bash
cd D:/Sites/autoposter-phasee
docker compose -p pphe -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pphe-t1green2 test sh -c \
  'set -o pipefail; pytest tests/test_badge_media.py -q 2>&1 | tee /app/.superpowers/run-pphe-t1green2.log'
docker wait pphe-t1green2
docker rm pphe-t1green2
tail -10 .superpowers/run-pphe-t1green2.log
```

Expected: `74 passed` — the 50 collected at Step 5, minus the one deleted at
Step 15, plus the 25 added at Steps 7, 12 and 14a. **If it is not 74, THE STOP
applies.**

- [ ] **Step 17: Update the twelve remaining sites in the seven other test files — eleven `MediaInfo` constructions and one attribute read**

Mechanical, and every one is a slot-1/slot-2/slot-9 type change with no
behavioural consequence — F16 is the argument that the parity and golden pixels
do not move, and Step 18 is the proof.

**Two greps, because the twelfth site is not a construction.** The second one is
the whole reason this step says twelve rather than eleven: `test_badge_pipeline.py`
never builds a `MediaInfo`, it reads one built for it by
`media_info_from_plex` off a monkeypatched `render/pipeline.py::compose_badges`,
so the construction census cannot see it and the rename breaks it with an
`AttributeError` (F15).

```bash
cd D:/Sites/autoposter-phasee
grep -rn "MediaInfo(" tests/ | grep -v tests/test_badge_media.py
grep -rn "\.media\.file_path" tests/
```

Expected from the first: **eleven** lines, in `tests/test_assets_root.py`,
`tests/test_badge_compose.py`, `tests/test_badge_parity.py` (2),
`tests/test_badge_values.py` (4), `tests/test_overlay_engine_golden.py` (2) and
`tests/test_plex_exif.py`. Expected from the second: **one** line,
`tests/test_badge_pipeline.py:200`. Eleven plus one is the twelve this step's
heading names.

**The second grep is deliberately narrow.** A bare `grep -rn "\.file_path"`
also hits `tests/test_pipeline_e2e.py:50`, `tests/test_plex.py:281`, `:694` and
`:720` — those are **`ResolvedItem.file_path`**, the `plex/client.py:436-440`
read that adjudication A-3 FENCES and Global Constraint 4 forbids touching. They
are a different attribute on a different class. **Do not edit them**; if one
appears in Task 2 Step 4's diff, THE STOP applies.

Apply:

- `tests/test_assets_root.py:89`:
  `values.MediaInfo(None, None, None, None, ("xx",), frozenset(), None, None, None)`
  → `values.MediaInfo((), (), None, None, ("xx",), frozenset(), None, None, ())`
- `tests/test_badge_compose.py:22`, `tests/test_badge_parity.py:42`,
  `tests/test_overlay_engine_golden.py:35`, `tests/test_plex_exif.py:35`:
  `MediaInfo("1080", "eac3", 6, 4845912, ("en",), frozenset(), …)`
  → `MediaInfo(("1080",), ("English (EAC3 5.1)",), 6, 4845912, ("en",), frozenset(), …)`
- `tests/test_badge_parity.py:81`, `tests/test_overlay_engine_golden.py:39`:
  `MediaInfo("480", "aac", 2, 1380000, ("en",), frozenset(), 1, 1)`
  → `MediaInfo(("480",), ("English (AAC Stereo)",), 2, 1380000, ("en",), frozenset(), 1, 1)`
- `tests/test_badge_values.py:98-118` — the fifteen-row `video_format_text`
  parametrize takes tuples of paths; **keep all fifteen rows** (the last two
  become `((""), None)`-shaped and `((), None)`, which are genuinely different
  cases: a present-but-empty path versus no path at all):
  ```python
  @pytest.mark.parametrize(
      "paths,expected",
      [
          (("/m/Dune (2021)/Dune.2021.BluRay.REMUX.2160p.mkv",), "REMUX"),
          (("/m/Heat (1995)/Heat.1995.Blu-Ray.1080p.mkv",), "BLU-RAY"),
          (("/m/Heat (1995)/Heat.1995.BD.1080p.mkv",), "BLU-RAY"),
          (("/m/Heat (1995)/Heat.1995.HD-DVD.1080p.mkv",), "BLU-RAY"),
          (("/m/Show/S01E01 - WEBDL-1080p.mkv",), "WEB"),
          (("/m/Show/S01E01.WEBRip.720p.mkv",), "WEB"),
          (("/m/Show/S01E01.HDTV.720p.mkv",), "HDTV"),
          (("/m/Show/S01E01.HD-TV.720p.mkv",), "HDTV"),
          (("/m/Old (1974)/Old.1974.DVD.480p.mkv",), "DVD"),
          (("/m/Show/S01E01.SDTV.mkv",), "SDTV"),
          (("/m/Cam (2018)/Cam.2018.TELESYNC.mkv",), "TELESYNC"),
          (("/m/Cam (2018)/Cam.2018.HDCAM.mkv",), "CAM"),
          (("/m/Show/S01E01.mkv",), None),
          (("",), None),
          ((), None),
      ],
  )
  def test_video_format_text_reproduces_kometas_filepath_regexes(paths, expected):
      """Straight from `video_format.yml`: eight `filepath.regex` filters, the
      overlay key displayed verbatim, and `ignore_blank_results: true` meaning
      paths matching nothing draw no badge at all."""
      info = MediaInfo((), (), None, None, (), frozenset(), None, None, paths)
      assert video_format_text(info) == expected
  ```
- `tests/test_badge_values.py:126-133` — the higher-weight test's construction
  becomes
  `MediaInfo((), (), None, None, (), frozenset(), None, None, ("/m/Dune (2021)/Dune.2021.BluRay.REMUX.2160p.mkv",))`.
- `tests/test_badge_values.py:331` and `:342`:
  `MediaInfo("1080", "eac3", 6, 4845912, …)`
  → `MediaInfo(("1080",), ("English (EAC3 5.1)",), 6, 4845912, …)`.
- `tests/test_badge_values.py:335-341` — the docstring is now wrong (F17).
  Replace its parenthetical:
  ```python
  def test_the_new_fields_are_defaulted_so_positional_construction_still_works():
      """`MediaInfo` is frozen and every construction site predating sub-phase
      C2b passes the first nine fields positionally
      (`tests/test_badge_parity.py`, `tests/test_overlay_engine_golden.py`'s
      ALL_SOULS/EPISODE among them). A non-defaulted tenth or eleventh field
      would be a TypeError in all of them. Roadmap row 106 changed three of
      those nine fields' TYPES in place -- scalars to item-level tuples -- and
      deliberately changed neither the count nor the order, for this reason."""
  ```
- `tests/test_badge_pipeline.py:200` — **the twelfth site, and the only one that
  is a READ rather than a construction** (F15):
  ```python
      assert seen[0].media.file_paths == ("/media/Movies/Dune (2021)/Dune.2021.REMUX-2160p.mkv",)
  ```
  `seen[0]` is the `BadgeInputs` captured off `render/pipeline.py::compose_badges`
  by the test's own monkeypatch, so `.media` is a real `MediaInfo` from
  `media_info_from_plex` and `.file_path` no longer exists on it. The whole
  tuple is asserted rather than `[0].endswith(...)` because the field's new
  contract is "every file of every version" and the fixture has exactly one, so
  the equality is the stronger statement and it fails loudly if the walk ever
  starts double-counting a part. **No test is added or removed here** — it is
  one line changed, and the count chain is unaffected. The line above it
  (`assert [i.video_format for i in seen] == ["REMUX"]`) still passes: the
  fixture's path carries `REMUX`. Note that under the new derivation this
  fixture's `audio_codec_image` flips from `plus` to `None` (its `FakePart`
  carries no streams and its path carries no codec token); nothing in the file
  asserts a badge value, only fingerprint presence and upload counts, so
  nothing else in it moves.

- [ ] **Step 18: Run every suite that constructs or consumes a `MediaInfo`, and the two pinned-literal files**

```bash
cd D:/Sites/autoposter-phasee
docker compose -p pphe -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pphe-t1suites test sh -c \
  'set -o pipefail; pytest tests/test_badge_media.py tests/test_badge_values.py \
   tests/test_badge_compose.py tests/test_badge_parity.py \
   tests/test_overlay_engine_golden.py tests/test_plex_exif.py \
   tests/test_assets_root.py tests/test_badge_pipeline.py \
   tests/test_overlay_entrypoint.py \
   tests/test_overlay_selection.py -q 2>&1 | tee /app/.superpowers/run-pphe-t1suites.log'
docker wait pphe-t1suites
docker rm pphe-t1suites
tail -10 .superpowers/run-pphe-t1suites.log
```

Expected: `0 failed`. `tests/test_badge_pipeline.py` is in the list because
Step 17 edited it; it is the one file here whose edit is an attribute read
rather than a construction, and it is also the file whose fixture silently
changes `audio_codec` from `plus` to `None` (F15) — a green run is what says
that change reaches no assertion.

**Five things this run proves and must be read for, not inferred:**

1. `tests/test_overlay_engine_golden.py` passes **without `POSTER_PIXELS_SHA` or
   `TITLE_CARD_PIXELS_SHA` (`:44-45`) having been touched** (F16).
2. `tests/test_overlay_entrypoint.py` passes **without either pinned literal
   having been touched** — `576f88e5…a0bd` at `:303` and `:411`, and
   `PRE_SEAM_ONE_DEFINITION_FINGERPRINT = 973b2cf3…0923` at `:371` (Global
   Constraint 2).
3. `tests/test_badge_values.py`'s three C2b stream-language pins pass, which is
   A-4 holding (Global Constraint 6).
4. **`tests/test_badge_media.py`'s own three pre-existing 64-character
   `badge_fingerprint` literals pass unmoved** — `:306`
   (`0b2c79c1…dbdb`, the `eng` byte-identity pin), `:323` (`2ea31fff…5611`, the
   Swedish digest) and `:327` (`2dfb76df…f0cd`, the Swahili digest it moved off).
   This plan edits that file heavily, including Step 14a's rewrite two tests
   below them, so they are named here rather than left to luck. They **cannot**
   move by construction: `_audio_only_item` (`:243-250`) builds a `FakeMedia`
   with no `videoResolution`, a `FakePart` with no `file`, and audio streams
   with no `extendedDisplayTitle`, so under the new code `video_resolutions`,
   `audio_track_titles` and `file_paths` are all `()` and `badge_values` still
   yields `{"languages": …}` and nothing else. If one of them moves, the walk in
   Step 11 is contributing something it should not.
5. Step 14a's alias moved **only** Norwegian: `:306`'s `eng` digest and `:323`'s
   `swe` digest are exactly the codes that prove it, and they are in item 4.

```bash
git -C D:/Sites/autoposter-phasee diff --stat -- tests/test_overlay_entrypoint.py
```

Expected: **empty output**. If that file shows a diff, THE STOP applies.

- [ ] **Step 19: Run ruff**

```bash
cd D:/Sites/autoposter-phasee
docker compose -p pphe -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pphe-t1lint test sh -c \
  'set -o pipefail; ruff check src tests 2>&1 | tee /app/.superpowers/run-pphe-t1lint.log'
docker wait pphe-t1lint
docker rm pphe-t1lint
cat .superpowers/run-pphe-t1lint.log
```

Expected: `All checks passed!`. The three deletions from Step 9 (`AUDIO_CODECS`,
`_HDR_SUFFIXES`, `_hdr_suffix`) are what this catches if any reference survives.

- [ ] **Step 20: Confirm the fence held**

```bash
cd D:/Sites/autoposter-phasee
git diff --stat origin/main..HEAD -- src/
git diff --stat -- src/
```

Expected from both, taken together: **`src/autoposter/badges/values.py` and
nothing else**. Specifically no `src/autoposter/badges/compose.py`, no
`src/autoposter/config/loader.py`, no `src/autoposter/render/pipeline.py`, no
`src/autoposter/plex/client.py`, nothing under `src/autoposter/overlays/`,
`src/autoposter/scheduler/`, `src/autoposter/api/` or
`src/autoposter/collections/`. If any other source file appears, THE STOP
applies (Global Constraint 4).

- [ ] **Step 21: Run the full suite**

```bash
cd D:/Sites/autoposter-phasee
docker compose -p pphe -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pphe-t1full test sh -c \
  'set -o pipefail; pytest -q 2>&1 | tee /app/.superpowers/run-pphe-t1full.log'
docker wait pphe-t1full
docker rm pphe-t1full
tail -5 .superpowers/run-pphe-t1full.log
```

Expected: `B + 24` passed, `0 failed`, the skip count unchanged from Step 5. THE
STOP applies to any other number.

- [ ] **Step 22: Commit the code**

```bash
cd D:/Sites/autoposter-phasee
git add src/autoposter/badges/values.py tests/test_badge_media.py \
        tests/test_badge_values.py tests/test_badge_compose.py \
        tests/test_badge_parity.py tests/test_overlay_engine_golden.py \
        tests/test_plex_exif.py tests/test_assets_root.py \
        tests/test_badge_pipeline.py
git commit -m "fix(badges): derive every badge value over every Media version

Kometa never picks a <Media>. Every badge-relevant filter in the four shipped
default files is an any-of read across every version, and the group/weight
table arbitrates between the overlays that matched -- so resolution, audio
codec and video format are now derived item-wide and awarded by Kometa's own
weights under the same strict > that modules/overlays.py:582-586 and
badges/compose.py:428 already use.

MediaInfo keeps its eleven fields in their eleven slots; three change type in
place (video_resolutions, audio_track_titles, file_paths) and hdr_flags becomes
the union across versions. audio_languages stays primary-version-only, which is
sub-phase C2b's fence and is deliberate.

The audio codec table now mirrors upstream's sixteen regexes against audio
track titles and file paths rather than mapping ten Plex codec identifiers, so
truehd_atmos, dtsx, plus_atmos, dolby_atmos, hra and dtses become reachable and
an item with no codec token in any title or path draws no badge. That is a
shipped-output change and it is disclosed on roadmap row 106 with the query
that counts it.

language_slots also aliases nb/nn to no at flag lookup, so Plex's nob and nno
tags draw Norway's flag as languages.yml:403 and :407 say they should. The alias
is at lookup and not in languages.json, because that file is in
MANIFEST.sha256 and editing it would move asset_manifest_sha and re-badge the
whole library for two codes.

badge_fingerprint is untouched; only the values dict handed to it moves, and a
single-version item's is byte-identical -- pinned against a digest captured on
the pre-change code."
```

---

## Task 2: The wrap — file and close row 106, disclose what ships

**Files:**
- Modify: `docs/superpowers/specs/2026-08-22-full-parity-roadmap.md:208` (row 106,
  in place) and `:341` (row 243, one appended sentence — the Norwegian remedy
  Task 1 Step 14a took)

**Interfaces:**
- Consumes: Task 1's shipped behaviour and its measured `B + 24`.
- Produces: nothing importable. This task's deliverable is the disclosure and the
  proof that the fences held.

---

- [ ] **Step 1: Rewrite row 106's cell in place, and close row 243's open remedy**

Row 106 is an existing row, so it is **edited in place** — the convention rows
104 and 107 already follow — not appended as a new row. Open
`docs/superpowers/specs/2026-08-22-full-parity-roadmap.md` and confirm the row
is still at `:208`:

```bash
cd D:/Sites/autoposter-phasee
grep -n "^| 106 " docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
```

Replace the whole row with the following. The columns are
`| # | Gap | What it is | Difficulty | Config impact | Depends on |`.

```markdown
| 106 | Multi-version badge selection & edition splits ((a) CLOSED — phase E; (b) DEFERRED) | Row 14 follow-up (5b): badges described Media[0], which Plex orders arbitrarily (Ben-Hur: AAC/198min badged while a DTS-HD MA remux sits at media[1]). **The cell's own framing was wrong and is corrected here: Kometa does NOT "surface the best matching variant by overlay weight" — it never picks a `<Media>` at all.** Every badge-relevant filter in the four shipped default files is an ANY-OF read across EVERY version, and `modules/overlays.py:582-586`'s group/weight table then arbitrates between the OVERLAYS that matched: `resolution.yml:243-251` is a whole-item `plex_search` plus `has_dolby_vision` (`modules/plex.py:2832-2838`, every stream of every part of every media) and `filepath.regex` (`:2804-2805`, `item.locations`); `audio_codec.yml:98-100` is `audio_track_title.regex` (`:2791-2794`) OR `filepath.regex`; `video_format.yml:64-65` is `filepath.regex`; `languages.yml:196-198` is a whole-item `plex_search`. **AN ORACLE EXISTS and (a) is proven against it, not styled after it** — the four weight tables, read from the pinned image `kometateam/kometa` sha256:c58f6d4af511613f218b6dafbfc84078af4e5a6089790c1fdba58fd7c5dad70a (per-file sha256: `resolution.yml` 0ee1533e…df3f, `audio_codec.yml` 3a1f1b03…b334, `video_format.yml` d70510ea…8f36, `languages.yml` dcd91ab2…c8f1, `modules/overlays.py` 38c7eac7…1dcb, `modules/plex.py` 13fa5496…6b86). **(a) delivered:** `badges/values.py`'s `MediaInfo` now carries item-level tuples in the same eleven slots (`video_resolutions`, `audio_track_titles`, `file_paths`; `hdr_flags` unioned across versions), `media_info_from_plex` walks `item.media` once, and `resolution_image`/`audio_codec_image`/`video_format_text` award by `RESOLUTION_WEIGHTS` (39 rows, `resolution.yml:255-371`), `AUDIO_CODEC_WEIGHTS` (16 rows, `audio_codec.yml:61-95`+`:104-166`) and `_VIDEO_FORMATS` (8 rows, `video_format.yml:69-99`) through one `_award` comparator written to `overlays.py:586`'s STRICT `>` — the same rule `badges/compose.py:428` already used. Ben-Hur now badges DTS-HD MA/4K/REMUX. **The consequence of the any-of formulation, stated rather than hidden:** a 4K SDR version beside a 1080p Dolby Vision version is awarded `4kdv`, because upstream's resolution and DV predicates are two independent item-level reads. The oracle's one tie (4K-Dovetail 130 vs 1080P-DV-HDR-Plus-Dovetail 130, `resolution.yml:273-277`) is pinned twice — on the comparator, and as the item-level finding that it is unreachable, since the flags that fire the 1080p row fire 4K-DV-HDR-Plus (160) too. **The shipped-output change beyond the multi-version fix, disclosed rather than slipped in:** `AUDIO_CODECS` mapped Plex's `media.audioCodec` through ten entries; upstream never reads that attribute for this overlay and matches sixteen regexes against track titles and paths, so the table now mirrors Kometa's verbatim. `truehd_atmos`, `dtsx`, `plus_atmos`, `dolby_atmos`, `hra` and `dtses` become reachable (all sixteen PNGs were already vendored in `audio_codec/compact` and `standard`, so `asset_manifest_sha` cannot move); `truehd`→`truehd_atmos`, `eac3`→`plus_atmos`/`dolby_atmos`, `ac3`→`dolby_atmos`, `dca`→`dtsx`/`hra`/`dtses` and `dca-ma`→`dtsx` all change when a title or path says so; `aac`/`flac`/`pcm`/`mp3`/`opus` are unchanged; upstream's aac regex also reads "stereo" and "2.0" as AAC and its `digital` `ac3` alternative is unanchored, both transcribed not corrected; and **the one direction that REMOVES a badge — no codec token in any audio track title or file path draws nothing, where the identifier map always drew something** — is pinned by test and counted before deploy. Also corrected on the weights' authority: `hlg` now outranks `hdr` (111 vs 110), where the retired `_HDR_SUFFIXES` checked `hdr` first; and 576p/480p carry no upstream `hlg` row, so an HLG flag at those tiers draws the plain badge and the vendored `576phlg.png`/`480phlg.png` are unreachable. **One flag fix rides along — row 243's deferred remedy, now taken:** `language_slots` (`badges/values.py:403-412`) aliases `nb`/`nn` → `no` at flag lookup, so Plex's `nob` and `nno` tags draw Norway's flag, which is where `languages.yml:403` (`{key: nb, text: NB, weight: 120, country: no}`) and `:407` (`{key: nn, …, country: no}`) point them upstream. The alias is at LOOKUP and deliberately not two new keys in `assets/badges/languages.json`: that file is listed in `assets/badges/MANIFEST.sha256`, so editing it would move `asset_manifest_sha` — a `badge_fingerprint` input — and re-badge the whole library for two codes. Only items with a Norwegian audio track move. **Ungated, and the storm is structurally bounded** (not by a config gate — `badges` is outside `render_version` by `config/loader.py:38`, so no gate could have cost anything, and a boolean defaulting to "keep producing the wrong badge" is the outcome the storm discipline exists to avoid): `values` is the ONLY `badge_fingerprint` part that can move — `base_fingerprint` cannot (render never reads `MediaInfo`), `asset_manifest_sha` cannot (no asset touched), `outcomes` cannot (all six `OVERLAY_ATTRIBUTES` read `plex_item` or C2b's whole-list tuples, none reads media[0]), the ratings digest cannot. A single-version item is byte-identical, pinned against a digest captured on the pre-change code. **Ceiling for the multi-version half: ≤274 re-badges** (50 movies + 224 episodes carry >1 `<Media>`, of 1,954 movies; `editionTitle` set on 0 of them; duplicate primary GUID groups 0 — `p5b-task-3-report.md:105-114`, live probe 2026-08-22) — badge pixels only, no base re-render, one pass, self-settling. **Count the audio-codec half before deploying** with the affected-count query in `docs/superpowers/plans/2026-09-05-phase-e-badge-versions.md` (Task 2 Step 2): it re-derives old and new values for every leaf item off Plex, read-only, and prints how many move per family. **The impact preview reports nothing about this and that is honest, not a gap** (`api/routes.py:1588-1604`'s `_render_affecting` compares two configs; this change adds no config key, so there is nothing for a preview to compare) — no additive preview row was taken. **`plex/client.py:436-440`'s `media[0].parts[0].file` is FENCED and untouched:** it feeds `ResolvedItem.file_path → derive_root_folder → asset_path`, i.e. where the render file is written, and widening it would move asset paths for any multi-version item whose versions live in different folders. `audio_languages` also stays PRIMARY-VERSION-ONLY: it feeds the flag badge, which draws distinct codes and would draw duplicate flags off a whole-item read, and C2b already built the undeduplicated whole-item pair beside it. **(b) edition splits stay DEFERRED, with two corrections to this cell's own claims.** (1) The `arr/sync.py:341-352` citation is DEAD — the identity re-key rewrote that region; the current code is `_stale_rows_by_key` (`arr/sync.py:321-392`) and `enqueue_unknown_items` (`:395-470`), and the behaviour is worse and more precise than "safety net re-enqueues forever": for a second edition-split item B with no row, `_stale_rows_by_key` finds exactly ONE matching row (A's — same kind, same library, non-empty guid intersection, `:357-367`, `:390-391`), so `:450-468` enqueues A's stale intent carrying A's own live rating_key. The resolver resolves A. **B is never enqueued, and each sweep burns one `batch_size` slot re-processing A** — it re-enqueues the WRONG item forever, and B is invisible. (2) The downstream hazard is already guarded: `scheduler/merge.py:552-553` refuses a pair where both keys live — "two real Plex items carrying one identity, i.e. a duplicate in the library. An operator's decision, not this job's" — reporting them (`:583`, surfaced at `:1133-1135`) rather than destroying one. That refusal is the closest thing to edition-split awareness the tree has and it is correct. Live exercise remains 0 splits and 0 duplicate GUID groups, so fixing (b) means inventing a rating-key-keyed identity for a second same-GUID item (a migration, an intake change, a `root_folder`/`asset_path` disambiguator) against zero validation surface | (a) M — one module, 39+16+8 transcribed weight rows, one comparator, one walk, one two-entry flag alias, 25 tests added and 1 retired; no migration, no config key, no asset, no UI, no new Plex request. (b) not sized — deferred | (a) none: no config key exists, so the impact preview has nothing to report and no `render_version` can move. Operator-visible cost is ≤274 badge re-uploads for the multi-version half plus whatever the audio-codec query counts, once, badge layer only. (b) — | 14 |
```

Then close row 243's open remedy in the same file, in the same commit. Row 243
(the `[:2]`-slice flag fix, already CLOSED) ends by filing the Norwegian
remedies as the operator's call. Task 1 Step 14a took one of them, so the cell
is now stale in exactly one sentence:

```bash
cd D:/Sites/autoposter-phasee
grep -n "^| 243 " docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
```

Append this sentence to that row's "What it is" column, immediately before its
closing ` | ` (do not restructure the rest of the cell, and do not touch its
Difficulty, Config impact or Depends-on columns):

```
 **Remedy taken (phase E, row 106's branch):** `language_slots` now aliases `nb`/`nn` → `no` at flag lookup (`badges/values.py:403-412`), so `nob` and `nno` draw Norway's flag — which is where `languages.yml:403`/`:407` point them upstream (`country: no` on both). The alias is at lookup, NOT in `languages.json`, so `MANIFEST.sha256` and `asset_manifest_sha` do not move and only items with a Norwegian audio track re-badge. The OTHER remedy — real `nb`/`nn` rows in `languages.json`, with upstream's own `NB`/120 and `NN`/110 text and weights — stays untaken, and stays the operator's call, because it moves the manifest.
```

That is one file, already in this task's Modify list; no fence changes and no
test moves.

- [ ] **Step 2: Write the affected-count query into gitignored scratch and record what it says**

This is the number the roadmap cell and the PR body promise. It runs on the
branch — it imports the new tables — and **before** the branch is deployed, which
is exactly when the operator wants it. It is read-only: it opens Plex, reloads
each leaf item, and prints counts.

```bash
cd D:/Sites/autoposter-phasee
cat > .superpowers/affected.py <<'PY'
"""Roadmap row 106: how many items the whole-versions badge derivation moves.

Read-only. Run on the feat/phase-e-badge-versions branch, BEFORE deploying it.
Re-derives each leaf item's badge values BOTH ways -- the shipped media[0] way
and the new item-level way -- and reports how many items move, per family.

The invocation is the shell block below this heredoc in the plan, under Global
Constraint 11's discipline. One `reload()` per item, so a ~16k-item library
takes a while. Nothing is written to Plex, to the database, or to disk beyond
the teed log.
"""
import os

from plexapi.server import PlexServer

from autoposter.badges.values import (
    RESOLUTIONS,
    _HDR10_PLUS,
    _VIDEO_FORMATS,
    audio_codec_image,
    media_info_from_plex,
    resolution_image,
    video_format_text,
)

# The SHIPPED tables, copied here rather than imported: this file's whole job
# is to compare against code that no longer exists on this branch.
_OLD_CODECS = {
    "eac3": "plus", "ac3": "digital", "truehd": "truehd", "dca": "dts",
    "dca-ma": "ma", "aac": "aac", "flac": "flac", "mp3": "mp3",
    "opus": "opus", "pcm": "pcm",
}
_OLD_SUFFIXES = (
    (frozenset({"dv", "plus"}), "dvhdrplus"),
    (frozenset({"dv", "hdr"}), "dvhdr"),
    (frozenset({"plus"}), "plus"),
    (frozenset({"dv"}), "dv"),
    (frozenset({"hdr"}), "hdr"),
    (frozenset({"hlg"}), "hlg"),
)


def old_values(item):
    """What the shipped code derived: media[0], its first part, that one path."""
    versions = getattr(item, "media", None) or []
    if not versions:
        return (None, None, None)
    media = versions[0]
    flags = set()
    path = None
    for part in getattr(media, "parts", []) or []:
        if path is None:
            path = getattr(part, "file", None)
        for stream in getattr(part, "streams", []) or []:
            if stream.streamType == 1:
                if getattr(stream, "DOVIPresent", None):
                    flags.add("dv")
                trc = getattr(stream, "colorTrc", None)
                if trc == "smpte2084":
                    flags.add("hdr")
                elif trc == "arib-std-b67":
                    flags.add("hlg")
    if path and _HDR10_PLUS.search(path):
        flags.add("plus")
    base = RESOLUTIONS.get((getattr(media, "videoResolution", None) or "").lower())
    suffix = ""
    for required, name in _OLD_SUFFIXES:
        if required <= flags:
            suffix = name
            break
    resolution = None if base is None else base + suffix
    codec = _OLD_CODECS.get((getattr(media, "audioCodec", None) or "").lower())
    fmt = None
    if path:
        for label, _weight, pattern in _VIDEO_FORMATS:
            if pattern.search(path):
                fmt = label
                break
    return (resolution, codec, fmt)


plex = PlexServer(os.environ["PLEX_URL"], os.environ["PLEX_TOKEN"])
totals = dict(items=0, multi_version=0, resolution=0, audio_codec=0,
              video_format=0, codec_lost=0, any_family=0)
for section in plex.library.sections():
    if section.type not in ("movie", "show"):
        continue
    leaves = section.all() if section.type == "movie" else section.searchEpisodes()
    for item in leaves:
        item.reload()
        totals["items"] += 1
        if len(getattr(item, "media", None) or []) > 1:
            totals["multi_version"] += 1
        before = old_values(item)
        info = media_info_from_plex(item)
        after = (resolution_image(info), audio_codec_image(info),
                 video_format_text(info))
        moved = False
        for name, was, now in zip(("resolution", "audio_codec", "video_format"),
                                  before, after):
            if was != now:
                totals[name] += 1
                moved = True
        if before[1] is not None and after[1] is None:
            totals["codec_lost"] += 1
        if moved:
            totals["any_family"] += 1
for key in ("items", "multi_version", "resolution", "audio_codec",
            "video_format", "codec_lost", "any_family"):
    print("%-14s %d" % (key, totals[key]))
PY
```

**Then run it, under Global Constraint 11 — detached with `--name`, `docker
wait`, read the teed log, `docker rm`.** Writing the script and not running it
is how a promised number turns into an invented one.

**This one runs against the operator's own Plex, so it runs only when the
operator chooses.** Every other command in this plan touches nothing but the
worktree and its throwaway container; this one needs a real `PLEX_URL` and a
real `PLEX_TOKEN` for the production library, and the number it prints is a
statement about that library and no other. Ask before running it, and substitute
the operator's own values for the two placeholders below — they are placeholders,
not defaults.

```bash
cd D:/Sites/autoposter-phasee
docker compose -p pphe -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pphe-affected \
  -e PLEX_URL="<the operator's Plex URL>" -e PLEX_TOKEN="<the operator's token>" \
  test sh -c \
  'set -o pipefail; python /app/.superpowers/affected.py 2>&1 | tee /app/.superpowers/run-pphe-affected.log'
docker wait pphe-affected
docker rm pphe-affected
cat .superpowers/run-pphe-affected.log
```

`docker wait` can sit for a long time here — a ~16k-item library is one
`reload()` per item — and that is the expected shape, not a hang. **Read the
log; never trust `docker wait`'s exit code alone**, and note that a `0` from a
run that died on `KeyError: 'PLEX_URL'` still prints nothing useful, so the
seven counter lines are the only evidence a run succeeded.

Expected on success: seven lines, `items`, `multi_version`, `resolution`,
`audio_codec`, `video_format`, `codec_lost`, `any_family`, each a label and an
integer. **No number in this plan predicts them and none may be written into
the report without this log behind it.**

Record the seven numbers in the task report and in the PR body. `any_family` is
the true re-badge count; `multi_version` should land near 274 (F12's measured
50 movies + 224 episodes); `codec_lost` is the count of items that lose their
audio_codec badge entirely, and **if it is large rather than a handful, say so in
the PR and let the controller decide before merging** — that is the one outcome
this phase's disclosure exists to surface.

**If Plex is not reachable from the execution environment, do not fake a
number**: report in the PR body that the query is written and unrun, with the
command, and leave the roadmap cell's pointer to it as it stands. The measured
ceiling (F12) is still a real, cited bound on the multi-version half.

- [ ] **Step 3: Re-run the badge suites after the documentation edit**

```bash
cd D:/Sites/autoposter-phasee
docker compose -p pphe -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pphe-t2docs test sh -c \
  'set -o pipefail; pytest tests/test_badge_media.py tests/test_citation_anchors.py \
   -q 2>&1 | tee /app/.superpowers/run-pphe-t2docs.log'
docker wait pphe-t2docs
docker rm pphe-t2docs
tail -5 .superpowers/run-pphe-t2docs.log
```

Expected: `0 failed`, 74 from the badge file plus the citation guard's two. The
citation guard's `EXPECTED_REF_COUNT = 30` must be unchanged — its `SURFACES`
tuple does not include `badges/values.py` or `tests/test_badge_media.py`, and the
roadmap is outside its scope by its own docstring, so nothing this phase wrote
can move that census (Global Constraint 14).

- [ ] **Step 4: Prove the fences with a diff, not with a claim**

```bash
cd D:/Sites/autoposter-phasee
git diff --stat origin/main..HEAD
```

Expected, and nothing else — **eleven files**: `src/autoposter/badges/values.py`,
`tests/test_badge_media.py`, `tests/test_badge_values.py`,
`tests/test_badge_compose.py`, `tests/test_badge_parity.py`,
`tests/test_overlay_engine_golden.py`, `tests/test_plex_exif.py`,
`tests/test_assets_root.py`, **`tests/test_badge_pipeline.py`** (Step 17's
twelfth site, the `MediaInfo.file_path` read at `:200` — one line, F15),
`docs/superpowers/specs/2026-08-22-full-parity-roadmap.md`,
`docs/superpowers/plans/2026-09-05-phase-e-badge-versions.md`.

(One source file, eight test files, the roadmap and this plan — which Task 1
Step 4 committed before any code was written.) **`tests/test_badge_pipeline.py`
belongs in this list** — an executor
who fixed `:200` correctly and then found the file missing from the fence would
have to STOP on a correct fix, which is exactly what naming it here prevents.

**Not present, and each one is a law:** `src/autoposter/badges/compose.py`
(Global Constraint 1), `tests/test_overlay_entrypoint.py` (Constraint 2),
`src/autoposter/config/loader.py` (Constraint 3), `src/autoposter/render/pipeline.py`,
`src/autoposter/plex/client.py`, anything under `src/autoposter/overlays/`,
`src/autoposter/scheduler/`, `src/autoposter/api/`, `src/autoposter/collections/`
or `src/autoposter/config/` (Constraint 4), anything under `assets/` or
`frontend/` and any `alembic/versions/` file (Constraint 5). If any of those
appears, THE STOP applies.

- [ ] **Step 5: Run the full suite once more**

```bash
cd D:/Sites/autoposter-phasee
docker compose -p pphe -f docker-compose.yml -f .superpowers/isolated-db.yml \
  run -d --name pphe-t2full test sh -c \
  'set -o pipefail; pytest -q 2>&1 | tee /app/.superpowers/run-pphe-t2full.log'
docker wait pphe-t2full
docker rm pphe-t2full
tail -5 .superpowers/run-pphe-t2full.log
```

Expected: `B + 24` passed, `0 failed`, the skip count unchanged from Task 1
Step 5.

- [ ] **Step 6: Commit the documentation**

```bash
cd D:/Sites/autoposter-phasee
git add docs/superpowers/specs/2026-08-22-full-parity-roadmap.md
git commit -m "docs(badges): file and close row 106(a), defer (b) with its citations corrected

Records that Kometa never picks a <Media> -- the cell's 'best matching variant'
framing was wrong -- names the four weight tables and the image digest they were
read from, discloses the audio_codec ten-of-sixteen change including the one
case that removes a badge, states the <=274 multi-version ceiling and points at
the affected-count query, says why no impact-preview row was taken, and replaces
(b)'s dead arr/sync.py:341-352 citation with _stale_rows_by_key's real behaviour
and the merge job's both-keys-live refusal.

Also records on row 243 that its deferred Norwegian remedy is taken -- nb/nn are
aliased to no at flag lookup, not added to languages.json -- and that the
manifest-moving alternative stays untaken."
```

- [ ] **Step 7: Confirm the branch is clean and the isolation files never entered it**

```bash
cd D:/Sites/autoposter-phasee
git status --porcelain
git log --oneline origin/main..HEAD
```

Expected: the only untracked entry is `.superpowers/`, nothing staged or
modified, and exactly three commits ahead of `origin/main` (the plan, the code,
the documentation).

- [ ] **Step 8: Tear down the compose project**

```bash
cd D:/Sites/autoposter-phasee
docker compose -p pphe -f docker-compose.yml -f .superpowers/isolated-db.yml down
```

Both `-f` files, like every other invocation in this plan — teardown is not an
exception to Global Constraint 11 — and `down`, **never `down -v`**.

- [ ] **Step 9: Open the PR**

Title: `Badges: derive every value over every Media version, awarded by Kometa's weights`

The body states, in this order — the roadmap cell written in Task 2 Step 1 is
the source for all of it, so it is a condensation, not a second draft:

1. What was wrong: badges described `media[0]`, which Plex orders arbitrarily.
2. What Kometa actually does, with the four `file:line` cites and the image
   digest — **it never picks a `<Media>`**, and the cell's own framing was
   corrected.
3. The audio_codec ten-of-sixteen change, named as a shipped-output change,
   with the changed codecs and **the one case that removes a badge**.
4. The storm guard: `values` is the only fingerprint part that moves, a
   single-version item is byte-identical (pinned against a captured digest), the
   ≤274 multi-version ceiling, and **the seven numbers from Task 2 Step 2's
   query** (or, if Plex was unreachable, the command and the fact that it is
   unrun).
5. Why there is no config gate and no impact-preview row.
6. That `plex/client.py:436-440` and `audio_languages` are fenced and untouched.
7. The Norwegian flag alias that rides along (`nb`/`nn` → `no` at flag lookup),
   why it is at lookup and not in `languages.json`, that only items with a
   Norwegian audio track re-badge for it, and that row 243's other remedy stays
   the operator's call.
8. That (b) stays deferred, with the two citation corrections.

**No AI attribution of any kind** (Global Constraint 15).

---

## Self-Review

**1. Spec coverage.** Every one of C1's eight adjudications maps to something
executable. **A-1** (whole-versions + weight award, not a better media[0]) —
Task 1 Steps 9, 11 and 14, pinned by Step 12's `..._spans_every_media_version`,
`..._4k_sdr_beside_a_1080p_dv_awards_4kdv` and the Ben-Hur test; the cell's wrong
framing corrected in Task 2 Step 1. **A-2** (ungated, disclosed; ≤274; `values`
the only mover; the two pinned literals untouched) — Global Constraints 2, 3, 5,
Task 1 Step 12's two storm-guard tests, Task 1 Step 18's empty-diff check on
`test_overlay_entrypoint.py`, Task 2 Steps 1, 2 and 4. **A-3**
(`plex/client.py:436-440` fenced) — Global Constraint 4, proved by Task 2 Step 4.
**A-4** (`audio_languages` left as C2b left it) — Global Constraint 6, the
`index == 0` gate in Task 1 Step 11, re-run in Task 1 Step 18. **A-5**
(audio_codec mirrors Kometa verbatim; the divergence is a shipped-output change,
disclosed with the affected-count query) — `AUDIO_CODEC_WEIGHTS` in Task 1 Step 9,
F9's per-codec table, five tests in Task 1 Step 12 including the badge-removal
pin, the query in Task 2 Step 2, the disclosure in Task 2 Step 1. **A-6** ((b)
deferred, dead citation corrected to `_stale_rows_by_key`, merge refusal noted) —
Task 2 Step 1. **A-7** (cut from main, which now carries #157;
`render/pipeline.py` untouched) — the branch section, Task 1 Step 1's recorded
cut ref, Global Constraint 4. **A-8** (impact
preview row if cheap, else disclosed) — F13 and Task 2 Step 1: no config key
exists, so there is nothing for a preview to compare and disclosure is the only
honest answer.

**C2's ADDED item** (the Norwegian alias, `nb`/`nn` → `no` at flag lookup, no
manifest move, "one step + pin") — **Task 1 Step 14a**, which is the step, the
alias map, the `language_slots` rewrite, its own RED and its own GREEN. The
"pin" is two tests, and C2's "one" is honoured in the sense that matters: the
existing Norwegian pin at `tests/test_badge_media.py:330-346` is **inverted in
place** rather than left contradicting the new behaviour, and exactly **one**
test is added beside it (the same-vendored-asset check). That +1 is why the
whole count chain reads +24/74 rather than +23/73, and Global Constraint 13,
Steps 15, 16, 21 and Task 2 Steps 3 and 5 all say so consistently.

Requirements from the prompt that are not adjudications: the tie pin (Task 1
Step 7's comparator test and Step 12's item-level unreachability test), the
byte-identical single-version pin (Step 12), the affected-count query written
out in full **and actually run under Global Constraint 11's `--name`/`docker
wait`/read-the-log discipline** (Task 2 Step 2), the example-config note (there
is none to make — no config key is added, stated in Global Constraint 5).

**2. Placeholder scan.** No TBD, no "handle edge cases", no "similar to Task N",
no step that says what to do without showing how. Two values are deliberately
**measured rather than written**: `B` (the baseline PASSED count, Task 1 Step 5)
and the two 64-character fingerprints (Task 1 Step 6). Both come with the exact
command that produces them, the exact expected surrounding output, and a STOP if
that output differs — the same pattern
`tests/test_overlay_entrypoint.py:365-371`'s own captured constant and
`tests/test_overlay_engine_golden.py:44-45`'s measured shas already use. A plan
cannot precompute a sha256 of code it has not run, and inventing one would be
worse than measuring it.

**3. Type consistency.** `MediaInfo`'s eleven fields are declared once (Task 1
Step 11) and constructed with exactly those names in `media_info_from_plex`, at
the twelve sites Task 1 Step 15 lists and positionally at the **eleven**
construction sites Task 1 Step 17 lists; the twelfth site Step 17 names is
`tests/test_badge_pipeline.py:200`, which **reads** `.media.file_path` off a
`MediaInfo` it did not build and is the one type change in the tree that no
`MediaInfo(` census can see.
`video_resolutions`/`audio_track_titles`/`file_paths` are `tuple[str, ...]`
everywhere they appear, `hdr_flags` stays `frozenset[str]`, and
`audio_languages` stays `tuple[str, ...]` — Step 14a's `_FLAG_ALIASES` is
`dict[str, str]` and is applied to its elements at lookup, never stored, so no
field's type moves for it. `_award(candidates)
-> str | None` is defined once and called three times, always over
`(str, int)` pairs. `RESOLUTION_WEIGHTS` is `(str, str, int)` and is unpacked as
`key, alt, weight` in both the accessor and the two tests that read it.
`AUDIO_CODEC_WEIGHTS` and `_VIDEO_FORMATS` are both `(str, int, re.Pattern[str])`
and are unpacked as `stem/label, weight, pattern` at every site — including
`.superpowers/affected.py`'s `for label, _weight, pattern in _VIDEO_FORMATS`,
which is the one place outside the module that unpacks a table and would have
been a two-versus-three-value bug if `_VIDEO_FORMATS` had kept its old shape.
`media_info_from_plex`, `resolution_image`, `audio_codec_image`,
`video_format_text` and `language_slots` keep their exact signatures, which is
what lets `render/pipeline.py:1553` and `:1588` stay untouched.

**4. The count chain, re-summed from the plan's own code blocks.** Step 7 writes
5 `def test_`; Step 12 writes 19; Step 14a writes 2 but one of them **replaces**
`test_norwegian_bokmal_loses_its_flag_and_that_is_a_decision_not_an_accident`,
so it contributes 1; Step 15 deletes 1. 5 + 19 + 1 − 1 = **+24**, and
50 + 24 = **74**. Every place that names a number agrees: Global Constraint 13
(+24, `B + 24`, 50 → 74), Step 10 (`B + 5`, before Step 14a exists), Step 14a
(2 passed, by node id), Step 15's arithmetic line, Step 16 (74), Step 21
(`B + 24`), Task 2's Interfaces (`B + 24`), Task 2 Step 3 (74) and Task 2 Step 5
(`B + 24`). No rewritten parametrize changes cardinality: 10, 7, 10 and 15 rows
before and after.

**Deviations from the recon, and why.**

- The recon's T1 sketch says the resolution table has **41** `(key, alt)` rows.
  It has **39** (F5), read from `resolution.yml:255-371` this session with the
  sha256 matching the recon's own table. The plan uses 39 and pins the count.
- The recon recommends restricting `AUDIO_CODEC_WEIGHTS` "with a named comment
  to the ten codecs `AUDIO_CODECS` can reach" and naming the divergence as
  still-open (its A-5 recommendation). **C1 overrode that**: the table mirrors
  Kometa's sixteen verbatim and the divergence is closed, disclosed with the
  affected-count query. The plan follows C1.
- The recon proposes three tasks (tables → widening → wrap). **C1 rules two**
  (derivation → wrap). The plan follows C1; the table work is Task 1 Steps 7–10
  with its own RED/GREEN cycle inside the derivation task, which is where the
  reviewer gate for it belongs — a reviewer cannot meaningfully approve the
  tables while rejecting the walk that fills them.
- The recon's A-3 asks whether `MediaInfo.file_path` becomes the winning
  version's path while `video_format_text` walks every path independently.
  **Neither.** `file_path` becomes `file_paths` and there is no "winning
  version" anywhere in the design — that concept belongs to the "pick a Media"
  formulation A-1 rejected. Kometa's `filepath` is `item.locations`, one
  item-level list, and all three consumers (video_format, HDR10+, the audio
  codec's OR branch) read that same list. C1's own A-3 is about the fenced
  `plex/client.py` read and is honoured separately (Global Constraint 4).
- The recon suggests logging the pick at DEBUG with the path. **Not taken**: the
  design has no "pick" to log, and F14 explains why a host path near a served
  surface is a row-213 decision this phase should not make as a side effect.
