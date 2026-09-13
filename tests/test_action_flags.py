"""The Action Center's flag registry: what counts as an imperfect asset.

Every predicate is pinned BOTH ways -- it fires on a row shaped to trip it and
is silent on a healthy one -- because a predicate that fires on everything and
a predicate that fires on nothing both pass a one-directional test.

The two derived-not-stored tests are the point of the whole design: editing
the config must change what the queue shows with no row write at all. A stored
verdict would pass every other test in this file and fail those two.
"""
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from autoposter.actions import flags
from autoposter.config.loader import load_config
from autoposter.db.models import MediaItem, Render

from conftest import seed_media_item

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
def config():
    return load_config(EXAMPLE)


async def _seed(session, *, library="Movies", art_kind="poster", **render_fields) -> Render:
    """One media item and its render row, with the render's columns overridable.

    A fresh rating key per call because the column is unique and these tests
    seed several rows into one shared database.
    """
    item = await seed_media_item(
        session, f"rk-{uuid4().hex[:12]}", library=library, kind="movie",
        title="Dune: Part Two",
    )
    fields = {"status": "rendered", "asset_path": "/assets/Movies/Dune/poster.jpg"}
    fields.update(render_fields)
    render = Render(item_id=item.id, art_kind=art_kind, **fields)
    session.add(render)
    await session.commit()
    return render


async def _fires_on(session, config, code: str) -> set[int]:
    """The render ids this flag's SQL selects, evaluated by the database.

    Joined to media_items because some predicates read MediaItem.library --
    the library language override is part of what "first choice" means.
    """
    rows = await session.execute(
        select(Render.id)
        .join(MediaItem, Render.item_id == MediaItem.id)
        .where(flags.predicate_for(code, config))
    )
    return set(rows.scalars().all())


# --- the six that light up on rows that already exist ------------------------


async def test_missing_fires_on_a_no_art_row_and_is_silent_on_a_rendered_one(session, config):
    flagged = await _seed(session, status="no_art")
    healthy = await _seed(session, status="rendered")

    fired = await _fires_on(session, config, "missing")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_skipped_fires_on_a_skipped_row_and_is_silent_on_a_rendered_one(session, config):
    flagged = await _seed(session, status="skipped")
    healthy = await _seed(session, status="rendered")

    fired = await _fires_on(session, config, "skipped")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_truncated_fires_on_a_truncated_row_and_is_silent_on_a_rendered_one(
    session, config
):
    flagged = await _seed(session, status="truncated")
    healthy = await _seed(session, status="rendered")

    fired = await _fires_on(session, config, "truncated")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_render_failed_fires_on_a_failed_row_and_is_silent_on_a_rendered_one(
    session, config
):
    """Roadmap: the unscorable-floor investigation's H1. A per-kind
    containment refusal (render/pipeline.py's `SourceRefused` handler) writes
    `status='failed'` and the reason in `detail`, but nothing read that status
    before this flag -- a partially-refused asset vanished from every count
    and every list. `render_failed` is the fix: default-on, since a
    current-art-missing defect is a true signal rather than noise."""
    flagged = await _seed(session, status="failed", detail="poster refused: decode error")
    healthy = await _seed(session, status="rendered")

    fired = await _fires_on(session, config, "render_failed")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_render_failed_detail_serves_the_stored_refusal_reason(session, config):
    """The row's own `detail` -- the refusal string `render/pipeline.py`
    stamps -- is what the queue's factual sentence must serve. That string is
    class-name-only (a stage label and an exception class name, per the
    review's own check): no URL, no token, safe on a served column."""
    render = await _seed(session, status="failed", detail="poster refused: SourceRefused")

    assert flags.detail_for("render_failed", render) == "poster refused: SourceRefused"


async def test_show_fallback_fires_on_the_source_mode_row_two_seven_shipped(session, config):
    """Row 132 shipped `source_mode = 'show_fallback'` and gave it no operator
    surface. This is where already-persisted behaviour finally gets a name."""
    flagged = await _seed(session, art_kind="season_poster", source_mode="show_fallback")
    healthy = await _seed(session, art_kind="season_poster", source_mode="generate")

    fired = await _fires_on(session, config, "show_fallback")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_plex_generated_fires_on_the_source_mode_row_and_is_silent_on_a_rendered_one(
    session, config
):
    """The plex-preview fallback (roadmap row 241) gets the same operator
    surface row 132's show_fallback already has."""
    flagged = await _seed(session, art_kind="title_card", source_mode="plex_generated")
    healthy = await _seed(session, art_kind="title_card", source_mode="generate")

    fired = await _fires_on(session, config, "plex_generated")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_upload_failed_fires_independently_of_the_render_status(session, config):
    """`upload_status` is orthogonal to `status`: a row can be rendered
    perfectly and never have reached Plex."""
    flagged = await _seed(session, status="rendered", upload_status="failed")
    healthy = await _seed(session, status="rendered", upload_status="uploaded")

    fired = await _fires_on(session, config, "upload_failed")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_unknown_provenance_fires_on_an_adopted_row(session, config):
    """An adopted row is art this service found on disk and took over: no
    provider, no source URL, no idea what it is. Honestly unknown quality."""
    flagged = await _seed(session, adopted=True)
    healthy = await _seed(session, adopted=False)

    fired = await _fires_on(session, config, "unknown_provenance")

    assert flagged.id in fired
    assert healthy.id not in fired


# --- the four that need the new bookkeeping ----------------------------------


async def test_language_miss_fires_on_an_en_row_under_an_xx_leading_order(session, config):
    """Roadmap 11a's own acceptance, at the predicate level: the shipped
    example config leads every art kind with `xx`, so English is rank 1."""
    assert config.artwork.poster.language_order[0] == "xx"
    flagged = await _seed(session, selected_language="en", language_rank=1)
    healthy = await _seed(session, selected_language=None, language_rank=0)

    fired = await _fires_on(session, config, "language_miss")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_language_miss_is_silent_on_a_row_the_ladder_never_ranked(session, config):
    """`language_rank IS NULL` is every row written before this taxonomy, plus
    every manual override. Its `selected_language` is NULL, which compares
    unequal to every preference -- without the gate the whole pre-existing
    library would read as a language miss the pipeline never made."""
    unscored = await _seed(session, selected_language=None, language_rank=None)

    fired = await _fires_on(session, config, "language_miss")

    assert unscored.id not in fired


async def test_language_miss_follows_the_library_language_override(session, config):
    """Roadmap row 38: a library can re-point its posters. A queue judging that
    library against the global order would flag every row it re-pointed."""
    config.artwork.library_language_overrides = {"Anime": {"poster": ["en", "xx"]}}
    honoured = await _seed(session, library="Anime", selected_language="en", language_rank=0)
    missed = await _seed(session, library="Movies", selected_language="en", language_rank=1)

    fired = await _fires_on(session, config, "language_miss")

    assert honoured.id not in fired
    assert missed.id in fired


async def test_language_miss_is_silent_on_textless_art_carrying_a_non_first_language_tag(
    session, config
):
    """`ArtCandidate.is_textless` (`providers/base.py`) is authoritative over
    the tag: `providers/tvdb.py` reports `includesText: false` alongside a
    language tag (`eng`) inherited from the entry, so a real TVDB pick can be
    textless and still carry `selected_language = "en"`. The shipped example
    config leads every art kind with `xx`, which is exactly the position a
    textless row occupies -- so this row achieved the operator's first choice
    and must not read as a miss."""
    assert config.artwork.poster.language_order[0] == "xx"
    silent = await _seed(session, selected_language="en", language_rank=0, textless=True)
    fired_id = silent.id

    fired = await _fires_on(session, config, "language_miss")

    assert fired_id not in fired


async def test_language_miss_fires_on_the_same_row_once_it_is_not_textless(session, config):
    """The mirror of the row above: identical tag and rank, but the candidate
    was not textless, so the tag itself is what the row achieved and `en`
    loses to the `xx`-leading order."""
    assert config.artwork.poster.language_order[0] == "xx"
    flagged = await _seed(session, selected_language="en", language_rank=0, textless=False)

    fired = await _fires_on(session, config, "language_miss")

    assert flagged.id in fired


async def test_provider_downgrade_fires_on_a_second_choice_provider(session, config):
    assert config.providers.order[0] == "TMDB"
    flagged = await _seed(session, provider="TVDB", provider_rank=1)
    healthy = await _seed(session, provider="TMDB", provider_rank=0)

    fired = await _fires_on(session, config, "provider_downgrade")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_provider_downgrade_is_silent_on_a_manual_override(session, config):
    """`provider = 'manual'` describes a file an operator placed by hand. No
    ladder position describes it, so `provider_rank` is NULL and the flag must
    not read the operator's own choice as a downgrade."""
    manual = await _seed(session, provider="manual", provider_rank=None)

    fired = await _fires_on(session, config, "provider_downgrade")

    assert manual.id not in fired


async def test_textless_miss_fires_when_the_ladder_took_a_text_bearing_image(session, config):
    flagged = await _seed(session, textless_fallback=True)
    healthy = await _seed(session, textless_fallback=False)

    fired = await _fires_on(session, config, "textless_miss")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_logo_fallback_fires_when_a_poster_wore_text_in_a_logos_place(session, config):
    flagged = await _seed(session, logo_text_fallback=True)
    healthy = await _seed(session, logo_text_fallback=False)

    fired = await _fires_on(session, config, "logo_fallback")

    assert flagged.id in fired
    assert healthy.id not in fired


async def test_unscored_fires_on_a_rendered_row_with_no_quality_stamp(session, config):
    from datetime import datetime, timezone

    flagged = await _seed(session, status="rendered", quality_scored_at=None)
    healthy = await _seed(
        session, status="rendered", quality_scored_at=datetime.now(timezone.utc)
    )
    # A no_art row is already named by `missing`; calling it unscored as well
    # would double-count the same row under two chips.
    not_renderable = await _seed(session, status="no_art", quality_scored_at=None)

    fired = await _fires_on(session, config, "unscored")

    assert flagged.id in fired
    assert healthy.id not in fired
    assert not_renderable.id not in fired


# --- the derived-not-stored property, which is the whole design --------------


async def test_editing_the_language_order_flips_the_flag_with_no_row_write(session, config):
    """The reason no verdict is stored. An operator who re-points
    `language_order` must see the queue change on the next request, without a
    backfill, and the row's own facts must be exactly as the render left them.
    """
    render = await _seed(session, selected_language="en", language_rank=1)

    assert render.id in await _fires_on(session, config, "language_miss")

    config.artwork.poster.language_order = ["en", "fi"]

    assert render.id not in await _fires_on(session, config, "language_miss")

    # Nothing was written. Re-read from the database rather than trusting the
    # identity map: a predicate that "worked" by quietly updating the row
    # would pass the two assertions above and fail here.
    #
    # The id is held before the expiry: reading it afterwards would be a
    # lazy load off an expired instance, which under asyncio is a
    # MissingGreenlet rather than a query.
    render_id = render.id
    session.expire_all()
    reread = (
        await session.execute(select(Render).where(Render.id == render_id))
    ).scalar_one()
    assert reread.selected_language == "en"
    assert reread.language_rank == 1


async def test_editing_the_provider_order_flips_the_flag_with_no_row_write(session, config):
    render = await _seed(session, provider="TVDB", provider_rank=1)

    assert render.id in await _fires_on(session, config, "provider_downgrade")

    config.providers.order = ["TVDB", "TMDB", "Fanart"]

    assert render.id not in await _fires_on(session, config, "provider_downgrade")

    render_id = render.id
    session.expire_all()
    reread = (
        await session.execute(select(Render).where(Render.id == render_id))
    ).scalar_one()
    assert reread.provider == "TVDB"
    assert reread.provider_rank == 1


# --- the default population and the evidence hash ----------------------------


async def test_the_default_population_leaves_adopted_rows_out(session, config):
    """A5: an adopted library may be tens of thousands of rows, and a queue
    that opens showing ten thousand rows is not a queue. The operator opts in
    by picking the chip."""
    adopted = await _seed(session, adopted=True, status="rendered")
    broken = await _seed(session, status="no_art")

    rows = await session.execute(
        select(Render.id)
        .join(MediaItem, Render.item_id == MediaItem.id)
        .where(flags.default_predicate(config))
    )
    default_population = set(rows.scalars().all())

    assert broken.id in default_population
    assert adopted.id not in default_population


async def test_the_evidence_hash_moves_when_a_fact_changes(session, config):
    """11b's "dismissal that sticks until the underlying facts change",
    reduced to its mechanism: a re-render finding Finnish where it found
    English must move the hash, so the dismissal join stops matching and the
    row returns with no sweep and no invalidation job."""
    render = await _seed(session, selected_language="en", language_rank=1)

    async def evidence() -> str:
        return (
            await session.execute(
                select(flags.evidence_expression()).where(Render.id == render.id)
            )
        ).scalar_one()

    before = await evidence()
    assert len(before) == 64

    render.selected_language = "fi"
    await session.commit()

    assert await evidence() != before


async def test_the_evidence_hash_ignores_a_column_no_flag_rests_on(session, config):
    """`detail` is free text the pipeline resets on every successful render.
    Folding it into the evidence would resurrect every dismissal on every
    pass -- exactly the bug 11b names."""
    render = await _seed(session, selected_language="en", language_rank=1, detail="unchanged")

    async def evidence() -> str:
        return (
            await session.execute(
                select(flags.evidence_expression()).where(Render.id == render.id)
            )
        ).scalar_one()

    before = await evidence()
    render.detail = None
    await session.commit()

    assert await evidence() == before


async def test_the_evidence_hash_survives_a_backslash_in_a_stored_fact(session, config):
    """`selected_language` is provider-supplied. `cast(text, BYTEA)` resolves
    to Postgres's I/O-conversion cast, which *parses* the text as a bytea
    literal (`byteain`) rather than encoding its bytes -- a lone `\\` is not a
    valid escape and raises `invalid input syntax for type bytea`, taking the
    whole evidence query down. `convert_to` only encodes; it does not parse."""
    render = await _seed(session, selected_language="en\\fi", language_rank=1)

    evidence = (
        await session.execute(
            select(flags.evidence_expression()).where(Render.id == render.id)
        )
    ).scalar_one()

    assert len(evidence) == 64


# --- the registry's agreement with the pipeline ------------------------------


def test_the_registry_reads_the_same_language_order_the_pipeline_renders_with(config):
    """`flags._language_order` restates `pipeline.language_order_for` rather
    than importing it (importing the pipeline pulls plexapi and the badge
    compositor into an API request). Two statements of one rule drift, so the
    agreement is pinned instead of assumed."""
    from autoposter.render.pipeline import language_order_for

    config.artwork.library_language_overrides = {"Anime": {"poster": ["en", "xx"]}}

    for library in ("Movies", "Anime"):
        for art_kind in flags.ART_KINDS:
            assert flags._language_order(config, library, art_kind) == language_order_for(
                config, library, art_kind
            )


def test_the_registry_lists_the_same_art_kinds_the_pipeline_renders():
    """`flags.ART_KINDS` restates the union of `pipeline.ART_KINDS_FOR`'s
    values rather than importing it (importing the pipeline pulls plexapi,
    httpx and the badge compositor into every request that counts a flag).
    Pinned by a test rather than assumed, for the same reason as
    `_language_order`."""
    from autoposter.render.pipeline import ART_KINDS_FOR

    rendered = {art_kind for kinds in ART_KINDS_FOR.values() for art_kind in kinds}
    assert set(flags.ART_KINDS) == rendered


def test_the_action_center_pages_art_kinds_match_the_pipelines():
    """A third statement of the same set, in
    frontend/src/pages/ActionCenter.tsx's own `ART_KINDS` constant -- the
    filter dropdown's options. Hardcoded there rather than fetched, per that
    file's own comment, so the agreement is pinned here rather than assumed,
    exactly as the two tests above pin the backend's two statements.

    Reads the source file as text and regexes out the string literals rather
    than parsing TypeScript, so the pin survives reformatting.
    """
    import re

    from autoposter.render.pipeline import ART_KINDS_FOR

    frontend = (
        Path(__file__).parent.parent / "frontend" / "src" / "pages" / "ActionCenter.tsx"
    ).read_text(encoding="utf-8")
    match = re.search(r'const ART_KINDS = \[([^\]]*)\];', frontend)
    assert match is not None, "ActionCenter.tsx no longer declares a const ART_KINDS = [...]"
    literals = set(re.findall(r'"([^"]*)"', match.group(1)))

    rendered = {art_kind for kinds in ART_KINDS_FOR.values() for art_kind in kinds}
    assert literals == rendered


def test_the_item_detail_pages_upload_cap_matches_the_backend():
    """A third statement of the upload byte cap, in ItemDetail.tsx's own
    `PICK_MAX_BYTES` constant -- checked client-side before an oversized file
    is sent (a 413 with an undrained request body can be lost to a connection
    reset). Not fetched from the server, so the agreement is pinned here
    rather than assumed, exactly as `test_the_action_center_pages_art_kinds_
    match_the_pipelines` pins its own frontend-hardcoded constant.

    Reads the source file as text and evaluates the numeric literal rather
    than parsing TypeScript, so the pin survives reformatting.
    """
    import re

    from autoposter.api.candidates import PICK_MAX_BYTES

    frontend = (
        Path(__file__).parent.parent / "frontend" / "src" / "pages" / "ItemDetail.tsx"
    ).read_text(encoding="utf-8")
    match = re.search(r"const PICK_MAX_BYTES = ([0-9 *]+);", frontend)
    assert match is not None, "ItemDetail.tsx no longer declares a const PICK_MAX_BYTES = ..."
    # Digits and `*` only, straight from our own source file -- eval is safe here.
    literal = eval(match.group(1), {"__builtins__": {}})
    assert literal == PICK_MAX_BYTES


def test_every_flag_declares_a_label_a_description_and_a_detail(config):
    """The page renders all three; a flag that shipped with an empty label is
    a chip an operator cannot identify."""
    assert set(flags.FLAGS) == {
        "missing", "skipped", "truncated", "render_failed", "show_fallback",
        "plex_generated", "upload_failed", "unknown_provenance", "language_miss",
        "provider_downgrade", "textless_miss", "logo_fallback", "unscored",
        "near_miss",
    }
    for code, flag in flags.FLAGS.items():
        assert flag.code == code
        assert flag.label
        assert flag.description
        assert callable(flag.detail)
    assert flags.FLAGS["unknown_provenance"].default_on is False
    assert flags.FLAGS["unscored"].default_on is False
    # A current-art-missing defect is a true signal, not noise -- the flood
    # argument that keeps `skipped`/`unknown_provenance` off by default does
    # not apply to a row a rerender can actually fix.
    assert flags.FLAGS["render_failed"].default_on is True
    # A deliberately disabled art kind writes `status = 'skipped'` to every one
    # of its rows -- the same flood argument that keeps `unknown_provenance`
    # off by default.
    assert flags.FLAGS["skipped"].default_on is False
    # Roadmap row 235. The population depends entirely on the configured box
    # and floor and is unmeasured until an operator has seen the count, so it
    # ships behind the chip rather than in the default queue.
    assert flags.FLAGS["near_miss"].default_on is False


# --- the near-miss fit, roadmap row 235 --------------------------------------


async def test_near_miss_fires_at_the_floor_and_is_silent_one_point_above(session, config):
    """The whole judgement in one test. `fit_point_size`
    (`render/textfit.py:154-172`) clamps a raw fit BELOW the floor to the
    floor and reports `truncated=True`, and a truncated render returns at
    `render/pipeline.py:1471` before the write-back -- so a stored
    `text_point_size` equal to the floor means the raw fit landed exactly
    there, which is "fitted only by dropping to the minimum".

    Both directions, because a predicate that fires on everything and one
    that fires on nothing both pass a one-directional test.
    """
    at_the_floor = await _seed(session, art_kind="poster", text_point_size=83)
    one_above = await _seed(session, art_kind="poster", text_point_size=84)

    fired = await _fires_on(session, config, "near_miss")

    assert at_the_floor.id in fired
    assert one_above.id not in fired


async def test_near_miss_is_silent_on_a_row_that_drew_no_text(session, config):
    """NULL is a logo poster, a verbatim source, a suppressed title, or a
    row rendered before the column was captured. None of those is a near
    miss, and `<=` against NULL is NULL rather than TRUE -- the explicit
    `IS NOT NULL` gate says so rather than relying on it."""
    no_text = await _seed(session, art_kind="poster", text_point_size=None)

    assert no_text.id not in await _fires_on(session, config, "near_miss")


async def test_near_miss_uses_each_art_kinds_own_floor(session, config):
    """Two kinds, two floors. The example config puts the poster floor at 83
    and the background floor at 95, so a background row at 95 is a near miss
    and a poster row at 95 is twelve points clear of its own floor. A
    predicate that used one floor for every kind passes the test above and
    fails this one."""
    background_at_its_floor = await _seed(session, art_kind="background", text_point_size=95)
    background_one_above = await _seed(session, art_kind="background", text_point_size=96)
    poster_well_clear = await _seed(session, art_kind="poster", text_point_size=95)

    fired = await _fires_on(session, config, "near_miss")

    assert background_at_its_floor.id in fired
    assert background_one_above.id not in fired
    assert poster_well_clear.id not in fired


async def test_raising_the_floor_brings_a_row_into_the_queue_with_no_row_write(
    session, config
):
    """The derived-not-stored property, for this flag -- the same shape as
    `test_editing_the_language_order_flips_the_flag_with_no_row_write`
    (`:313`) and `test_editing_the_provider_order_...` (`:342`). An operator
    who raises `min_point_size` must see the queue change on the next
    request, with no backfill and the row's own facts exactly as the render
    left them. `<=` rather than `==` is what makes the raise retroactive.
    """
    render = await _seed(session, art_kind="poster", text_point_size=90)

    assert render.id not in await _fires_on(session, config, "near_miss")

    config.artwork.poster.text.min_point_size = 90

    assert render.id in await _fires_on(session, config, "near_miss")

    # Nothing was written. Re-read from the database rather than trusting the
    # identity map: a predicate that "worked" by quietly updating the row
    # would pass both assertions above and fail here. The id is held before
    # the expiry -- reading it afterwards is a lazy load off an expired
    # instance, which under asyncio is a MissingGreenlet rather than a query.
    render_id = render.id
    session.expire_all()
    reread = (
        await session.execute(select(Render).where(Render.id == render_id))
    ).scalar_one()
    assert reread.text_point_size == 90


async def test_near_miss_is_off_by_default_and_names_one_integer(session, config):
    """Off by default because the population is unmeasured: a library of long
    titles under a tight `max_width` could put thousands of rows on the
    floor, which is the flood argument that already keeps `skipped` and
    `unknown_provenance` out of the default queue (`flags.py`'s own
    `skipped` and `unknown_provenance` registry rows). Non-instant because
    the column only exists on rows the Action Center phase's write-back has
    filled -- what `unscored` is for.

    The detail sentence names the fitted size and nothing else -- one
    integer, fixed words, never `render.detail`, never a path (roadmap row
    213). Never the floor either: `Flag.detail` stays
    `Callable[[Render], str]`, unwidened, so this callable has no config to
    read a floor from.
    """
    assert flags.FLAGS["near_miss"].default_on is False
    assert flags.FLAGS["near_miss"].instant is False

    render = await _seed(session, art_kind="poster", text_point_size=83)

    rows = await session.execute(
        select(Render.id)
        .join(MediaItem, Render.item_id == MediaItem.id)
        .where(flags.default_predicate(config))
    )
    assert render.id not in set(rows.scalars().all())

    assert render.id in await _fires_on(session, config, "near_miss")
    assert (
        flags.detail_for("near_miss", render)
        == "fitted at 83 pt, at or below this kind's floor"
    )


async def test_the_evidence_hash_moves_when_the_fitted_point_size_changes(session, config):
    """The reason the one-time dismissal reset is worth paying for: a
    dismissal made while a title sat on the floor must not survive the
    re-render that fitted it at 180 -- so `text_point_size` is part of what a
    dismissal is a dismissal OF. Removing it again would be a visible change,
    which is the point of pinning it here."""
    render = await _seed(session, art_kind="poster", text_point_size=83)

    async def evidence() -> str:
        return (
            await session.execute(
                select(flags.evidence_expression()).where(Render.id == render.id)
            )
        ).scalar_one()

    before = await evidence()
    assert len(before) == 64

    render.text_point_size = 180
    await session.commit()

    assert await evidence() != before


def test_the_near_miss_flag_moves_no_render_version(config):
    """A storm guard: `render_version` (`config/loader.py:71`) hashes
    `config.artwork` WHOLESALE and `render_version_for` (`:188`) hashes
    `artwork.<art_kind>`; both are allow-lists, so a threshold field placed
    under either would move every stored fingerprint and re-render the whole
    library through the provider ladder to add a chip.

    Two assertions, because the hazard has two shapes. First: this flag reads
    the operator's EXISTING `min_point_size` rather than a key of its own --
    a future refactor that introduced `artwork.<kind>.near_miss_margin` would
    fail here. Second: evaluating the predicate and the detail leaves every
    fingerprint digit-for-digit identical -- a predicate that mutated the
    config it was handed (normalising a floor, say) would move them.
    """
    # `Render`, `select` and `MediaItem` are already module-level imports in
    # this file (`:15`, `:19`); re-importing `Render` here would be an F811.
    from autoposter.config.loader import RENDER_ART_KINDS, render_version, render_version_for

    for art_kind in flags.ART_KINDS:
        assert flags._min_point_size(config, art_kind) == (
            getattr(config.artwork, art_kind).text.min_point_size
        )

    before = render_version(config)
    before_per_kind = {kind: render_version_for(kind, config) for kind in RENDER_ART_KINDS}

    flags.predicate_for("near_miss", config)
    flags._near_miss_detail(Render(art_kind="poster", text_point_size=83))

    assert render_version(config) == before
    assert {kind: render_version_for(kind, config) for kind in RENDER_ART_KINDS} == (
        before_per_kind
    )
