"""How much of the library a config edit would re-render, computed offline.

The editor's whole promise is that an operator can see "this change would
re-render ~N items" *before* committing to it. That answer is a pure read: it
walks the stored ``renders`` rows, recomputes each one's fingerprint under a
candidate ``Config``, and counts the ones that no longer match. Nothing is
written, nothing is enqueued, no provider is contacted and no image is touched
-- the preview endpoint hands this function a session it never flushes.

**The approximation, stated plainly.** Two of the inputs to a real
fingerprint are facts only the render itself knows: whether a clearlogo
replaced the title text, and which logo it was. ``gather_fingerprint_inputs``
takes them as ``draw_text``/``logo_sha`` and defaults them to the adoption
case -- ``draw_text=True, logo_sha=""`` (render/pipeline.py:161-163) -- and
this module uses exactly those defaults, because it has no more information
than the adoption walk does. The consequence is honest and one-directional: a
poster whose real render composited a logo will not match the recomputed
value, so it is reported as affected *whatever the edit was*. That is an
overcount, never an undercount, of the text and version changes the operator
is actually asking about. The UI must therefore say "~N", not "N".

**What the breakdown means, since roadmap row 111.** The first component of
every fingerprint is now a PER-ART-KIND version
(``config/loader.py::render_version_for``), so ``by_art_kind`` is evidence
about the kinds whose stored fingerprints an edit moves -- the exact inverse
of the disclaimer this module and the Settings page both used to carry. It is
NOT, without qualification, evidence about which kinds the edit touched: a
poster whose logo is composited into it is counted for any render-affecting
edit, because -- per the approximation above -- this walk cannot see the
logo it was built with. A genuinely shared input (an asset root,
``library_folders``, ``artwork.use_original_title``, the global
``artwork.disable_online_asset_fetch``, ``artwork.output_quality``) is a
member of every kind's payload and still reaches every row; that is the edit
being global, not the count failing to discriminate.

A third input is now in the same position: with
``artwork.use_original_title`` on, the real render drew an original-language
title this walk cannot know (``renders`` stores no title at all), so such an
item is reported as affected whatever the edit was. Overcount, never
undercount, exactly like the logo case above.

Roadmap row 78 added a fourth input of the same shape and deliberately did NOT
join this list: a season poster draws the show's own title, and the walk
reaches it through a ``MediaItem.parent_id`` self-join rather than declaring
every season_poster row affected forever. A season whose parent row does not
exist yet -- ``parent_id`` null -- still falls into the overcount above, which
is exactly what the OUTER join preserves.
"""
import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from autoposter.config.loader import RENDER_ART_KINDS, render_version_for
from autoposter.config.schema import Config
from autoposter.db.models import ManagedCollection, MediaItem, Render
from autoposter.intake.arr import RenderIntent
from autoposter.plex.client import ResolvedItem
from autoposter.render.pipeline import (
    _should_skip_title,
    art_config_for,
    compute_fingerprint,
    title_text_for,
)

# The art kinds ArtworkConfig actually carries a section for. A renders row
# holding anything else -- a kind removed from ART_KINDS_FOR, a hand-written
# row -- is skipped rather than crashing the preview on getattr. Derived from
# config/loader.py's RENDER_ART_KINDS rather than re-typed: roadmap row 111
# made that list the input to a hash, and two lists that quietly disagreed
# would confine an invalidation to a kind this walk never examines.
_ART_KINDS = frozenset(RENDER_ART_KINDS)


@dataclass(frozen=True)
class Impact:
    """What a candidate config would cost, in rows.

    ``of_total`` is the comparable population, not every row in the table: it
    counts the rows this walk actually examined. Rows excluded from both
    numbers are the ones whose answer would be noise -- a render for an art
    kind the candidate config disables, a title card the candidate config's
    ``skip_words`` skip, and a row with no stored fingerprint at all (which is
    going to be rendered whatever the operator does here, so attributing it to
    this edit would be a lie).
    """

    affected: int
    by_art_kind: dict[str, int]
    of_total: int


@dataclass(frozen=True)
class _Candidate:
    """One examined render row: its item, and whether the edit reaches it."""

    art_kind: str
    affected: bool
    kind: str
    title: str
    tmdb_id: int | None
    tvdb_id: int | None
    imdb_id: str | None
    year: int | None
    season_number: int | None
    episode_number: int | None
    rating_key: str


def _file_sha256(path: Path) -> str:
    """SHA-256 of a file's bytes, or ``""`` if it does not exist.

    Deliberately the same sentinel as ``render.pipeline._file_sha256``: a
    fingerprint computed here has to agree digit for digit with one the
    pipeline computes, and a missing font must therefore contribute the same
    empty string on both sides.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return ""


async def _cached_sha(cache: dict[str, str], path: Path) -> str:
    """``_file_sha256`` memoised across the whole walk.

    A 16,000-item library has at most a handful of distinct overlay and font
    files between all of its rows, and re-reading each of them once per row is
    the difference between a preview that answers in a moment and one that
    reads tens of thousands of files off a possibly-NFS mount. Offloaded on a
    miss for the same reason every other read of those roots is.
    """
    key = str(path)
    if key not in cache:
        cache[key] = await asyncio.to_thread(_file_sha256, path)
    return cache[key]


async def _fingerprint_inputs(
    config: Config, item: ResolvedItem, art_kind: str, cache: dict[str, str]
) -> tuple[list[str], list[str]]:
    """``gather_fingerprint_inputs`` at its adoption defaults, with a hash cache.

    Assembled here rather than called through, for the cache above: the
    pipeline's version reads every file every time, which is exactly right for
    a single render and exactly wrong for a walk over the whole library.

    Two implementations of one hash are the divergence risk this project's
    pipeline docstring warns about, so ``title_text_for`` is the real one and
    ``tests/test_config_impact.py`` pins the two against each other on real
    fixtures -- if the pipeline's assembly changes and this one does not, that
    test goes red rather than the preview quietly reporting nonsense.
    """
    settings = art_config_for(config, art_kind)
    primary_text, secondary_text = title_text_for(art_kind, item, config)
    text_inputs = [t for t in (primary_text, secondary_text) if t]

    overlay_hash = (
        await _cached_sha(cache, Path(config.overlays_root) / settings.overlay_file)
        if settings.add_overlay
        else ""
    )
    font_hashes = []
    if settings.text is not None and primary_text:
        font_hashes.append(
            await _cached_sha(cache, Path(config.fonts_root) / settings.text.font)
        )
    if art_kind == "title_card" and settings.episode_text is not None and secondary_text:
        font_hashes.append(
            await _cached_sha(cache, Path(config.fonts_root) / settings.episode_text.font)
        )
    # Row 78's block. The mirror of `gather_fingerprint_inputs`' own season
    # branch, minus `draw_text` -- this module runs at the adoption defaults,
    # where draw_text is True (see the module docstring).
    if art_kind == "season_poster" and settings.show_title is not None and secondary_text:
        font_hashes.append(
            await _cached_sha(cache, Path(config.fonts_root) / settings.show_title.font)
        )
    # The trailing "" is logo_sha: see this module's docstring, and
    # render/pipeline.py:161-163 for where that default comes from.
    return text_inputs, [overlay_hash, *font_hashes, ""]


def _show_title_for_row(kind: str, parent_title: str | None) -> str | None:
    """``ResolvedItem.show_title`` for one walked row.

    ``parent_title`` is the immediate parent's title from the outer self-join
    below -- the SHOW for a season, but the SEASON for an episode
    (``plex/client.py`` populates ``parent_id`` with the show's id for a
    season and the season's id for an episode). Reaching the true grandparent
    for an episode would need a second self-join; not worth it today, since
    ``show_title_for`` reads this field only for ``season_poster`` rows and
    those are always seasons -- so an episode gets ``None`` here rather than
    its own season's title mislabelled as the show's.
    """
    return parent_title if kind == "season" else None


def _walk_version(art_kind: str, config: Config) -> str:
    """Element 0 of every fingerprint this walk recomputes.

    One line, and it is a NAMED one because of what it duplicates. The
    pipeline's own producer is ``render_artifact``/``adopted_fingerprint``
    (render/pipeline.py), and this module has always carried a second
    implementation of the *other* two-thirds of a fingerprint for the sake of
    a hash cache the pipeline must not have. Roadmap row 111 pulled the
    version argument into that same hazard: a preview left on
    ``config.version`` while the pipeline moved to ``render_version_for``
    would report whole-library counts for every edit and every other test in
    this suite would stay green, because this file's fixtures build their
    stored fingerprints the same way this walk recomputes them.

    So it is named, and
    ``tests/test_config_impact.py::test_the_fingerprint_inputs_and_the_version_match_the_pipeline_s``
    pins it against ``adopted_fingerprint`` -- implementation against
    implementation, rather than an expression re-typed into a test.
    """
    return render_version_for(art_kind, config)


async def _walk(session: AsyncSession, config: Config) -> list[_Candidate]:
    """Every render row this config has an opinion about, and its verdict.

    Read-only by construction: explicit columns rather than ORM entities, so
    nothing enters the session's identity map and there is nothing for a later
    autoflush to write back.
    """
    # Roadmap row 78: a season poster now draws the SHOW's title, and this walk
    # builds its ResolvedItem from the join below rather than from Plex. The
    # parent link exists (`db/models.py`'s MediaItem.parent_id) and
    # `render/pipeline.py` populates it for every season it processes, so one
    # OUTER join makes the preview exact for this kind. The alternative -- a
    # third entry in this module's docstring's list of honest overcounts --
    # would report every season_poster row as affected whatever the edit was,
    # permanently. An orphan row (parent_id null, which the pipeline leaves
    # only when the show has not been processed yet) still lands in that
    # overcount, and that is the outer join's whole purpose: it degrades to
    # today's behaviour instead of dropping the row.
    parent = aliased(MediaItem)
    rows = (
        await session.execute(
            select(
                Render.art_kind,
                Render.source_url,
                Render.base_sha256,
                Render.fingerprint,
                MediaItem.library,
                MediaItem.kind,
                MediaItem.title,
                MediaItem.year,
                MediaItem.root_folder,
                MediaItem.season_number,
                MediaItem.episode_number,
                MediaItem.tmdb_id,
                MediaItem.tvdb_id,
                MediaItem.imdb_id,
                MediaItem.rating_key,
                parent.title.label("parent_title"),
            )
            .join(MediaItem, Render.item_id == MediaItem.id)
            .outerjoin(parent, MediaItem.parent_id == parent.id)
            .where(Render.fingerprint.is_not(None))
            .order_by(Render.id)
        )
    ).all()

    cache: dict[str, str] = {}
    # Element 0 of every fingerprint this walk recomputes, once per kind
    # rather than once per row: _walk_version dumps the whole artwork config
    # (loader.py's render_version_for), and a 16,000-row library has only
    # four distinct values for it.
    versions = {art_kind: _walk_version(art_kind, config) for art_kind in _ART_KINDS}
    candidates: list[_Candidate] = []
    for row in rows:
        art_kind = row.art_kind
        if art_kind not in _ART_KINDS:
            continue
        settings = art_config_for(config, art_kind)
        # The same two short-circuits render_artifact takes before it
        # fingerprints anything (render/pipeline.py:314-324). Without them a
        # disabled art kind or a TBA title card counts as work the run would
        # never actually do.
        if not settings.enabled:
            continue
        item = ResolvedItem(
            rating_key="",
            library=row.library,
            kind=row.kind,
            title=row.title,
            year=row.year,
            season_number=row.season_number,
            episode_number=row.episode_number,
            root_folder=row.root_folder or "",
            file_path=None,
            art_url=None,
            tmdb_id=row.tmdb_id,
            tvdb_id=row.tvdb_id,
            imdb_id=row.imdb_id,
            show_title=_show_title_for_row(row.kind, row.parent_title),
        )
        if _should_skip_title(config, item, art_kind):
            continue

        text_inputs, asset_hashes = await _fingerprint_inputs(config, item, art_kind, cache)
        # source_url and base_sha256 come off the stored row rather than being
        # guessed: they are facts about the image already on disk, and an
        # adopted row's null source_url reproduces adopted_fingerprint's
        # dropped-URL comparison exactly.
        candidate = compute_fingerprint(
            versions[art_kind], art_kind, row.source_url, row.base_sha256,
            text_inputs, asset_hashes,
        )
        # Roadmap row 111's dual-read grandfather, mirrored here because
        # render_artifact holds it: a row still carrying the pre-111 wholesale
        # element 0 is accepted and rewritten by the next pass rather than
        # re-rendered, so counting it as affected would report ~16,000 items
        # for a change that re-renders nothing. It only bites while
        # config.version itself has not moved -- which is exactly the case
        # _render_affecting lets through on `skip_tba` alone.
        legacy = compute_fingerprint(
            config.version, art_kind, row.source_url, row.base_sha256,
            text_inputs, asset_hashes,
        )
        candidates.append(
            _Candidate(
                art_kind=art_kind,
                affected=row.fingerprint not in (candidate, legacy),
                kind=row.kind,
                title=row.title,
                tmdb_id=row.tmdb_id,
                tvdb_id=row.tvdb_id,
                imdb_id=row.imdb_id,
                year=row.year,
                season_number=row.season_number,
                episode_number=row.episode_number,
                rating_key=row.rating_key,
            )
        )
    return candidates


async def count_affected(session: AsyncSession, new_config: Config) -> Impact:
    """How many stored renders ``new_config`` would invalidate.

    See the module docstring for the one approximation this makes and which
    way it errs. Purely a read: the session is never flushed and no row is
    loaded into it.
    """
    candidates = await _walk(session, new_config)
    by_art_kind: dict[str, int] = {}
    affected = 0
    for candidate in candidates:
        if not candidate.affected:
            continue
        affected += 1
        by_art_kind[candidate.art_kind] = by_art_kind.get(candidate.art_kind, 0) + 1
    return Impact(affected=affected, by_art_kind=by_art_kind, of_total=len(candidates))


async def affected_items(session: AsyncSession, new_config: Config) -> list[RenderIntent]:
    """The distinct items behind ``count_affected``'s number, as render intents.

    Distinct, because one item carries several renders (a movie has a poster
    and a background) and ``process_item`` rebuilds every art kind for the
    intent it is given -- enqueueing per render row would queue the same work
    twice and have the queue's dedupe silently absorb it.
    """
    seen: set[str] = set()
    intents: list[RenderIntent] = []
    for candidate in await _walk(session, new_config):
        if not candidate.affected:
            continue
        intent = RenderIntent(
            kind=candidate.kind,
            title=candidate.title,
            tmdb_id=candidate.tmdb_id,
            tvdb_id=candidate.tvdb_id,
            imdb_id=candidate.imdb_id,
            year=candidate.year,
            season_number=candidate.season_number,
            episode_number=candidate.episode_number,
            rating_key=candidate.rating_key,
        )
        if intent.dedupe_key in seen:
            continue
        seen.add(intent.dedupe_key)
        intents.append(intent)
    return intents


async def count_collection_posters(
    session: AsyncSession, before: Config, after: Config
) -> int:
    """How many managed collection posters this edit would re-composite.

    The honesty row the preview owed and did not have (roadmap row 105).
    ``count_affected`` above walks the ``renders`` table and ``_ART_KINDS``
    holds the four ITEM kinds, so a ``collections.poster_title`` edit previewed
    as "no re-renders" while up to every managed collection's poster was about
    to be re-composited and re-uploaded on the next pass. Not wrong -- out of
    scope -- but the page's promise is "see the cost before committing", and
    this is the first setting with a real cost that walk cannot see.

    Zero unless the block actually changed AND at least one side of the edit
    has it switched on: with the gate off the composite is skipped entirely, so
    retuning the boxes moves no bytes and reporting a cost would teach an
    operator to ignore the number.

    An OVER-estimate, in the same direction and for the same reason
    ``count_affected`` is one: a managed collection whose poster comes from the
    operator's own file under ``assets_root``, or a divider whose art is
    already captioned, passes through untouched -- and this count cannot know
    which those are without reading the filesystem. Render it with a "~".

    One ``SELECT COUNT`` and no walk: unlike the render preview there is
    nothing per-row to recompute, because a collection poster carries no
    fingerprint. Read-only, like everything else in this module.
    """
    if after.collections.poster_title == before.collections.poster_title:
        return 0
    if not (
        before.collections.poster_title.enabled or after.collections.poster_title.enabled
    ):
        return 0
    total = await session.execute(select(func.count()).select_from(ManagedCollection))
    return int(total.scalar_one())
