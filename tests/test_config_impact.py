"""``config.impact.count_affected`` -- the offline "what would this cost" walk.

Every fingerprint in the fixture library below is computed by the *real*
pipeline helpers before it is stored, so a no-op config change has to report
zero. That is the only honest starting point: a walk that disagreed with
render_artifact about an unchanged item would report the whole library
affected by every edit, and the number would look plausible.

Note what the fixture set proves and what it cannot. Since roadmap row 111 the
first component of every fingerprint is a PER-ART-KIND version
(config/loader.py's ``render_version_for``), so an edit to one kind's
subsection invalidates that kind's rows and leaves the others alone -- and the
breakdown below is evidence about what the edit touched, which is the exact
inverse of what this file used to say. What ALSO discriminates between rows is
the gates: a disabled art kind and a skipped title are not counted, because
render_artifact would not rebuild them either. And a genuinely shared input --
an asset root, ``use_original_title``, ``output_quality`` -- is a member of
every kind's payload and still reaches every row, which is the edit being
global rather than the walk failing to discriminate.
"""
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import select

from autoposter.config.impact import affected_items, count_affected, count_collection_posters
from autoposter.config.loader import build_config, render_version_for
from autoposter.config.overrides import merge_overrides
from autoposter.db.models import Job, ManagedCollection, MediaItem, Render
from autoposter.plex.client import ResolvedItem
from autoposter.servers.identity import identity_key, identity_key_for
from autoposter.render.pipeline import (
    adopted_fingerprint,
    compute_fingerprint,
    gather_fingerprint_inputs,
)

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

OVERLAYS = {
    "overlay.png": b"the poster overlay",
    "bottom-up-fade.png": b"the season poster overlay",
    "bottom-up-fade-background.png": b"the background overlay",
    "other-overlay.png": b"a different overlay entirely",
}
FONTS = {
    "Comfortaa-Medium.ttf": b"the configured font",
    "Other-Font.ttf": b"a different font",
}


@pytest.fixture
def base_document(tmp_path) -> dict:
    """The example config with real font and overlay files behind it.

    The repository ships only ``.keep`` files under ``assets/``, so every asset
    hash would otherwise be ``_file_sha256``'s missing-file sentinel and an
    overlay change would be invisible -- the test would pass against an
    implementation that never hashed anything.
    """
    fonts, overlays = tmp_path / "fonts", tmp_path / "overlays"
    fonts.mkdir()
    overlays.mkdir()
    for name, body in OVERLAYS.items():
        (overlays / name).write_bytes(body)
    for name, body in FONTS.items():
        (fonts / name).write_bytes(body)
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    document["fonts_root"] = str(fonts)
    document["overlays_root"] = str(overlays)
    return document


@pytest.fixture
def config(base_document):
    return build_config(base_document)


@pytest.fixture
def variant(base_document):
    """A config built from the example plus an overrides document."""

    def make(overrides: dict):
        return build_config(merge_overrides(base_document, overrides))

    return make


def _item(kind: str, title: str, tmdb_id: int, season=None, episode=None) -> ResolvedItem:
    return ResolvedItem(
        server="plex",
        native_id=f"rk{tmdb_id}-{season}-{episode}",
        library="Movies" if kind == "movie" else "TV Shows",
        kind=kind,
        title=title,
        year=1999,
        season_number=season,
        episode_number=episode,
        root_folder=f"{title} (1999)",
        file_path=None,
        art_url=None,
        tmdb_id=tmdb_id,
        tvdb_id=None,
        imdb_id=None,
    )


# (item, art_kind). One movie with two artifacts, a show poster, a season
# poster, a real title card and a TBA one -- enough for the gates to have
# something to exclude and for by_art_kind to have more than one key.
FIXTURE_ROWS = [
    (_item("movie", "A Movie", 550), "poster"),
    (_item("movie", "A Movie", 550), "background"),
    (_item("show", "A Show", 1399), "poster"),
    (_item("season", "A Show", 1399, season=1), "season_poster"),
    (_item("episode", "Pilot", 1399, season=1, episode=1), "title_card"),
    (_item("episode", "TBA", 1399, season=1, episode=2), "title_card"),
]


async def _stored_fingerprint(config, item: ResolvedItem, art_kind: str, base_sha: str) -> str:
    """What render_artifact would have written for this row, verbatim."""
    text_inputs, asset_hashes = await gather_fingerprint_inputs(config, item, art_kind)
    return compute_fingerprint(
        render_version_for(art_kind, config), art_kind,
        f"https://example/{art_kind}", base_sha, text_inputs, asset_hashes,
    )


async def _seed(session, config, rows=FIXTURE_ROWS) -> None:
    by_native_id: dict[str, int] = {}
    for item, art_kind in rows:
        if item.native_id not in by_native_id:
            row = MediaItem(
                identity_key=identity_key_for(item), library=item.library, kind=item.kind,
                title=item.title, year=item.year, tmdb_id=item.tmdb_id,
                season_number=item.season_number, episode_number=item.episode_number,
                root_folder=item.root_folder,
            )
            session.add(row)
            await session.flush()
            by_native_id[item.native_id] = row.id
        base_sha = "a" * 64
        session.add(
            Render(
                item_id=by_native_id[item.native_id], art_kind=art_kind,
                asset_path=f"/assets/{item.title}/{art_kind}.jpg", status="rendered",
                source_url=f"https://example/{art_kind}", base_sha256=base_sha,
                fingerprint=await _stored_fingerprint(config, item, art_kind, base_sha),
            )
        )
    await session.commit()


@pytest_asyncio.fixture
async def seeded(session, config):
    await _seed(session, config)
    return config


# --- the baseline: agreement with the pipeline ---


async def test_an_unchanged_config_affects_nothing(session, seeded):
    """The load-bearing case. Everything else is a delta from this."""
    impact = await count_affected(session, seeded)
    assert impact.affected == 0, (
        "the walk disagrees with render_artifact about rows nothing has changed, "
        "so every count it reports is noise"
    )
    assert impact.by_art_kind == {}


@pytest.mark.parametrize(
    "art_kind,item",
    [
        ("poster", _item("movie", "A Movie", 550)),
        ("background", _item("movie", "A Movie", 550)),
        ("season_poster", _item("season", "A Show", 1399, season=1)),
        ("title_card", _item("episode", "Pilot", 1399, season=1, episode=1)),
    ],
)
async def test_the_fingerprint_inputs_and_the_version_match_the_pipeline_s(
    config, art_kind, item
):
    """The one duplicated computation in this project, pinned to its original.

    ``impact._fingerprint_inputs`` reassembles what
    ``gather_fingerprint_inputs`` assembles, for the sake of a hash cache the
    pipeline must not have. Two implementations of one hash silently stop
    agreeing; this is what makes that loud.

    Roadmap row 111 made the duplication DEEPER: the version argument joined
    the input assembly, so the second assertion below pins element 0 as well.
    It compares this module's own ``_walk_version`` -- the line ``_walk``
    actually executes -- against ``adopted_fingerprint``, which is the
    pipeline's own producer of that element. A preview left on
    ``config.version`` turns it red instead of shipping whole-library counts
    with every other test green.
    """
    from autoposter.config.impact import _fingerprint_inputs, _walk_version

    expected = await gather_fingerprint_inputs(config, item, art_kind)
    assert await _fingerprint_inputs(config, item, art_kind, {}) == expected

    text_inputs, asset_hashes = expected
    assert compute_fingerprint(
        _walk_version(art_kind, config), art_kind, None, "abc", text_inputs, asset_hashes
    ) == adopted_fingerprint(config, art_kind, "abc", text_inputs, asset_hashes)


# --- what an edit reaches ---


async def test_an_artwork_text_change_affects_only_the_kind_it_names(session, seeded, variant):
    """THE ROW, in the preview. The exact inverse of what this pinned before.

    A title-card label edit used to invalidate the whole library, because
    ``render_version`` hashed the entire artwork section and that value was
    the first component of every fingerprint. Since row 111 it moves the title
    card's version and nothing else, so the preview reports the one card the
    operator actually asked about -- and the TBA card beside it stays excluded
    by ``skip_words``, which is why this is 1 and not 2.
    """
    impact = await count_affected(session, variant({"artwork": {"title_card": {"season_label": "Kausi"}}}))
    assert impact.affected == 1
    assert impact.of_total == 5
    assert impact.by_art_kind == {"title_card": 1}


async def test_a_poster_overlay_change_affects_the_poster_rows_only(session, seeded, variant):
    """Same rule from the other end. The overlay FILE NAME lives in
    ``artwork.poster``, which is now in the poster's payload alone, so the two
    poster rows in the fixture move and the background, the season poster and
    the title card do not."""
    impact = await count_affected(
        session, variant({"artwork": {"poster": {"overlay_file": "other-overlay.png"}}})
    )
    assert impact.affected == 2
    assert impact.by_art_kind == {"poster": 2}


async def test_a_background_overlay_change_affects_only_the_background_rows(
    session, seeded, variant
):
    """A third direction, and the one that would catch a partition leaking
    into the shared block: the fixture has exactly one background row, so an
    ``artwork.background`` edit that reported anything but 1 would mean either
    the background's payload is too wide or another kind's is."""
    impact = await count_affected(
        session, variant({"artwork": {"background": {"add_border": True}}})
    )
    assert impact.affected == 1
    assert impact.by_art_kind == {"background": 1}


async def test_a_shared_input_still_affects_every_ungated_row(session, seeded, variant):
    """The converse, and it must stay true.

    ``artwork.output_quality`` reaches ``build_base_argv`` on every kind
    (render/pipeline.py:855), so it is a member of all four payloads and the
    whole examined population is genuinely out of date. Without this pin the
    partition could quietly drop a shared input and the preview would under-
    report -- the one direction this module is never allowed to err in.
    """
    impact = await count_affected(session, variant({"artwork": {"output_quality": "88%"}}))
    assert impact.affected == 5
    assert impact.of_total == 5
    assert impact.by_art_kind == {
        "poster": 2, "background": 1, "season_poster": 1, "title_card": 1,
    }


async def test_a_disabled_art_kind_is_counted_neither_way(session, seeded, variant):
    """The gate render_artifact applies before it fingerprints anything.

    The edit is `output_quality` -- a genuinely SHARED input that reaches
    every kind's payload, poster included -- combined with disabling poster.
    That makes "poster" absent from `by_art_kind` prove the `enabled` gate
    rather than mere confinement: since row 111 a `title_card`-only edit
    would exclude poster either way and prove nothing about the gate.
    """
    impact = await count_affected(
        session,
        variant({"artwork": {"poster": {"enabled": False}, "output_quality": "88%"}}),
    )
    assert impact.by_art_kind == {"background": 1, "season_poster": 1, "title_card": 1}
    assert impact.affected == 3
    assert impact.of_total == 3, "a disabled kind must leave the denominator too"


async def test_a_title_card_matching_a_skip_word_is_not_counted(session, seeded, variant):
    """The TBA card is excluded under the example config's skip_words."""
    impact = await count_affected(session, variant({"artwork": {"title_card": {"season_label": "Kausi"}}}))
    assert impact.by_art_kind["title_card"] == 1, "the TBA card was counted as work"


async def test_turning_skip_tba_off_widens_the_denominator_without_affecting_anything(
    session, seeded, variant
):
    """``skip_tba`` is a gate and is deliberately not in ``render_version``.

    So flipping it changes which rows are *examined* while changing no
    fingerprint at all -- which is exactly why the preview's short-circuit has
    to look at it separately from the version.
    """
    impact = await count_affected(session, variant({"skip_tba": False}))
    assert impact.of_total == 6
    assert impact.affected == 0


async def test_a_row_without_a_stored_fingerprint_is_not_counted(session, config, variant):
    await _seed(session, config, rows=FIXTURE_ROWS[:1])
    row = (await session.execute(select(Render))).scalar_one()
    row.fingerprint = None
    await session.commit()

    impact = await count_affected(session, variant({"artwork": {"title_card": {"season_label": "K"}}}))
    assert impact.of_total == 0, (
        "a render with no fingerprint is going to be built whatever the config "
        "says, so counting it attributes work to an edit that did not cause it"
    )


async def test_a_render_that_used_a_logo_reads_as_affected(session, config):
    """The documented overcount, pinned rather than left as a claim.

    A poster whose real render composited a clearlogo stored a fingerprint
    with ``draw_text=False`` and a logo hash. The walk cannot know either, so
    it recomputes at the adoption defaults and reports a mismatch even though
    nothing changed. Overcount, never undercount -- and the reason the UI says
    "~".
    """
    item, art_kind = FIXTURE_ROWS[0]
    row = MediaItem(
        identity_key=identity_key_for(item), library=item.library, kind=item.kind,
        title=item.title, year=item.year, tmdb_id=item.tmdb_id, root_folder=item.root_folder,
    )
    session.add(row)
    await session.flush()
    text_inputs, asset_hashes = await gather_fingerprint_inputs(
        config, item, art_kind, draw_text=False, logo_sha="deadbeef"
    )
    session.add(
        Render(
            item_id=row.id, art_kind=art_kind, asset_path="/assets/x.jpg", status="rendered",
            source_url="https://example/poster", base_sha256="a" * 64,
            fingerprint=compute_fingerprint(
                render_version_for(art_kind, config), art_kind,
                "https://example/poster", "a" * 64, text_inputs, asset_hashes,
            ),
        )
    )
    await session.commit()

    assert (await count_affected(session, config)).affected == 1


async def test_a_logo_poster_is_counted_by_an_edit_that_never_touches_poster(
    session, config, variant
):
    """The overcount is not confined by the per-art-kind version.

    `_walk_version` only moves the kinds an edit's own payload reaches
    (`test_an_artwork_text_change_affects_only_the_kind_it_names` above), so a
    `title_card`-only edit leaves a PLAIN poster row's fingerprint untouched.
    A logo-composited poster is different: its stored fingerprint was built
    with `draw_text=False` and a real `logo_sha` this walk can never
    reproduce (the module docstring's approximation), so it mismatches under
    every render-affecting edit -- including this one, which never reaches
    `artwork.poster` at all. That is exactly the gap the "which kinds an edit
    touched" phrasing cannot honestly cover on its own (roadmap row 111
    follow-up), which is why the Settings/impact copy hedges rather than
    promising confinement.
    """
    movie, poster_kind = FIXTURE_ROWS[0]
    poster_row = MediaItem(
        identity_key=identity_key_for(movie), library=movie.library, kind=movie.kind,
        title=movie.title, year=movie.year, tmdb_id=movie.tmdb_id, root_folder=movie.root_folder,
    )
    session.add(poster_row)
    await session.flush()
    text_inputs, asset_hashes = await gather_fingerprint_inputs(
        config, movie, poster_kind, draw_text=False, logo_sha="deadbeef"
    )
    session.add(
        Render(
            item_id=poster_row.id, art_kind=poster_kind, asset_path="/assets/poster.jpg",
            status="rendered", source_url="https://example/poster", base_sha256="a" * 64,
            fingerprint=compute_fingerprint(
                render_version_for(poster_kind, config), poster_kind,
                "https://example/poster", "a" * 64, text_inputs, asset_hashes,
            ),
        )
    )

    episode, title_card_kind = FIXTURE_ROWS[4]
    episode_row = MediaItem(
        identity_key=identity_key_for(episode), library=episode.library, kind=episode.kind,
        title=episode.title, year=episode.year, tmdb_id=episode.tmdb_id,
        season_number=episode.season_number, episode_number=episode.episode_number,
        root_folder=episode.root_folder,
    )
    session.add(episode_row)
    await session.flush()
    session.add(
        Render(
            item_id=episode_row.id, art_kind=title_card_kind, asset_path="/assets/title_card.jpg",
            status="rendered", source_url="https://example/title_card", base_sha256="b" * 64,
            fingerprint=await _stored_fingerprint(config, episode, title_card_kind, "b" * 64),
        )
    )
    await session.commit()

    impact = await count_affected(
        session, variant({"artwork": {"title_card": {"season_label": "Kausi"}}})
    )
    assert impact.by_art_kind["poster"] == 1, (
        "the logo row must be counted by ANY render-affecting edit, not only "
        "one that touches artwork.poster"
    )
    assert impact.by_art_kind["title_card"] == 1
    assert impact.affected == 2


# --- side-effect freedom ---


async def _render_snapshot(session) -> list[tuple]:
    return (
        await session.execute(
            select(
                Render.id, Render.fingerprint, Render.status, Render.source_url,
                Render.base_sha256, Render.asset_path, Render.badge_fingerprint,
                Render.upload_status, Render.detail,
            ).order_by(Render.id)
        )
    ).all()


async def test_a_count_writes_nothing(session, seeded, variant):
    """Roadmap risk 2: the preview must be free to run on every keystroke."""
    before = await _render_snapshot(session)

    await count_affected(session, variant({"artwork": {"title_card": {"season_label": "Kausi"}}}))
    await session.commit()

    assert await _render_snapshot(session) == before, "the walk mutated renders"
    assert (await session.execute(select(Job))).scalars().all() == [], (
        "the walk enqueued work; it is a read, and the apply endpoint owns queueing"
    )


# --- the items behind the number ---


async def test_the_affected_items_are_the_distinct_items_not_the_rows(session, seeded, variant):
    """A movie with a poster and a background is one process_item job.

    The property needs an edit that reaches more than one row of one item, so
    it takes a SHARED input (``artwork.output_quality``, read by
    ``build_base_argv`` on every kind) rather than the title-card edit it used
    to take -- which since row 111 reaches exactly one row.
    """
    intents = await affected_items(session, variant({"artwork": {"output_quality": "88%"}}))
    keys = sorted(intent.dedupe_key for intent in intents)
    assert keys == sorted({intent.dedupe_key for intent in intents}), "duplicate intents"
    assert len(keys) == 4, keys


async def test_the_affected_items_honour_the_same_gates(session, seeded, variant):
    """The disabled-kind gate holds even under a genuinely shared edit.

    `output_quality` reaches every kind's payload (the converse pin above),
    so if the `enabled` gate in `_walk` were ever dropped, the show's poster
    -- disabled here -- would move like everything else and "show" would
    appear in `kinds` too. It does not: the gate is checked before a row's
    version is even read.
    """
    intents = await affected_items(
        session,
        variant({"artwork": {"poster": {"enabled": False}, "output_quality": "88%"}}),
    )
    kinds = {intent.kind for intent in intents}
    assert kinds == {"movie", "season", "episode"}, kinds
    assert "show" not in kinds, "the show's only artifact is a disabled poster"


# --- collection posters (roadmap row 105, adjudication A-7) -------------------
#
# The honesty row this preview owed and did not have. ``count_affected`` walks
# ``renders``, and there is no renders row for a collection -- so a
# ``collections.poster_title`` edit previewed as "no re-renders" while every
# managed collection's poster was about to be re-composited and re-uploaded.


async def _seed_collections(session, count: int) -> None:
    for index in range(count):
        session.add(
            ManagedCollection(
                library="Movies", title=f"Collection {index}", kind="smart",
                definition_hash="d" * 64,
            )
        )
    await session.flush()


async def test_a_poster_title_edit_counts_every_managed_collection(
    session, config, variant
):
    await _seed_collections(session, 3)
    after = variant({"collections": {"poster_title": {"enabled": True}}})
    assert await count_collection_posters(session, config, after) == 3


async def test_turning_knobs_while_the_gate_is_off_counts_nothing(
    session, config, variant
):
    """Off means the composite is skipped entirely, so no byte moves however
    the boxes are retuned. Reporting a cost here would teach an operator to
    ignore the number."""
    await _seed_collections(session, 3)
    after = variant({"collections": {"poster_title": {"collection_line_text": "SET"}}})
    assert await count_collection_posters(session, config, after) == 0


async def test_an_unrelated_edit_counts_no_collection_posters(session, config, variant):
    await _seed_collections(session, 3)
    after = variant({"workers": 9})
    assert await count_collection_posters(session, config, after) == 0


async def test_the_impact_walk_reads_the_shows_title_through_the_parent_join(
    session, config,
):
    """Roadmap row 78's impact obligation, and the reason it is a JOIN rather
    than a third declared overcount.

    This module's docstring already names two approximations -- the logo case
    and ``use_original_title`` -- each "honest and one-directional … an
    overcount, never an undercount". A third would say that every season_poster
    row reports as affected whatever the edit is, permanently, for a whole art
    kind. ``MediaItem.parent_id`` exists and ``render/pipeline.py`` populates
    it, so the show's title is one outer join away and the preview stays exact.

    Seeded with the fingerprint the REAL pipeline would have written for an
    item carrying the show title, so a walk that fed ``None`` where the render
    fed "A Show" reports this unchanged row as affected and this test goes red.
    """
    config.artwork.season_poster.show_title.add_text = True
    show = MediaItem(
        identity_key=identity_key(
            "show", tmdb_id=1399, tvdb_id=None, imdb_id=None,
            season_number=None, episode_number=None, file_path=None,
        ),
        library="TV Shows", kind="show", title="A Show",
        year=1999, tmdb_id=1399, root_folder="A Show (1999)",
    )
    session.add(show)
    await session.flush()
    season_item = ResolvedItem(
        server="plex", native_id="rk-season", library="TV Shows", kind="season",
        title="Season 1",
        year=1999, season_number=1, episode_number=None,
        root_folder="A Show (1999)", file_path=None, art_url=None,
        tmdb_id=1399, tvdb_id=None, imdb_id=None,
        parent_native_id="rk-show", show_title="A Show",
    )
    season = MediaItem(
        identity_key=identity_key(
            "season", tmdb_id=1399, tvdb_id=None, imdb_id=None,
            season_number=1, episode_number=None, file_path=None,
        ),
        library="TV Shows", kind="season", title="Season 1",
        year=1999, tmdb_id=1399, season_number=1, root_folder="A Show (1999)",
        parent_id=show.id,
    )
    session.add(season)
    await session.flush()
    base_sha = "b" * 64
    session.add(
        Render(
            item_id=season.id, art_kind="season_poster",
            asset_path="/assets/A Show/season_poster.jpg", status="rendered",
            source_url="https://example/season_poster", base_sha256=base_sha,
            fingerprint=await _stored_fingerprint(
                config, season_item, "season_poster", base_sha
            ),
        )
    )
    await session.commit()

    impact = await count_affected(session, config)

    assert impact.affected == 0, (
        "the walk fed a different show title than the render did -- the parent "
        "join is missing or wrong"
    )
    assert impact.of_total == 1


def test_show_title_for_row_is_the_parent_title_only_for_a_season():
    """``MediaItem.parent_id`` means a different thing per kind: the SHOW for
    a season, but the SEASON for an episode (``plex/client.py``). Harmless
    today -- ``show_title_for`` reads this field only for ``season_poster``
    rows, which are always seasons -- but the walk's whole contract is to say
    digit for digit what the pipeline says, so an episode row must not get a
    season's title mislabelled as the show's."""
    from autoposter.config.impact import _show_title_for_row

    assert _show_title_for_row("season", "A Show") == "A Show"
    assert _show_title_for_row("episode", "Season 1") is None
    assert _show_title_for_row("movie", "A Show") is None
    assert _show_title_for_row("season", None) is None


@pytest.mark.parametrize(
    "art_kind,item",
    [
        (
            "season_poster",
            ResolvedItem(
                server="plex", native_id="rk-season", library="TV Shows", kind="season",
                title="Season 1", year=1999, season_number=1, episode_number=None,
                root_folder="A Show (1999)", file_path=None, art_url=None,
                tmdb_id=1399, tvdb_id=None, imdb_id=None,
                parent_native_id="rk-show", show_title="A Show",
            ),
        ),
    ],
)
async def test_the_impact_inputs_match_the_pipelines_with_the_show_title_on(
    config, art_kind, item,
):
    """The duplicated-computation pin, extended to the gate this row adds.

    ``test_the_fingerprint_inputs_and_the_version_match_the_pipeline_s`` above
    runs at the
    shipped defaults, where the show-title block is off and both
    implementations trivially agree. This runs the same comparison with the
    block ON, which is the configuration in which the two can diverge: the
    font gate is a THIRD place the two assemblies have to say the same thing.
    """
    from autoposter.config.impact import _fingerprint_inputs

    config.artwork.season_poster.show_title.add_text = True
    expected = await gather_fingerprint_inputs(config, item, art_kind)
    assert await _fingerprint_inputs(config, item, art_kind, {}) == expected
    assert "A Show" in expected[0], "the gate really was on for this comparison"
