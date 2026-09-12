"""Roadmap row 92 through the real render entry point.

Two entry-point tests, one per consumer class, because a helper test that
passes while the wired path differs is the recorded failure this project has
paid for twice on one branch: the seam works, the caller never calls it, and
every test is green. So two of the assertions below drive ``process_item``
itself -- the function the queue worker calls -- and the helper-level tests
sit beside them rather than instead of them.

The doubles are the ones ``tests/test_mass_ops_verbs.py`` already uses for
exactly this, imported rather than rebuilt. That matters more than it looks:
``process_item`` calls ``_upsert_media_item`` twice (once in the operations
block, once inside ``render_artifact``) and reads ``parent_rating_key`` and
``file_path`` off the resolved item, so a hand-rolled stand-in raises an
``AttributeError`` that the operations block's own ``except Exception``
SWALLOWS -- and a swallowed crash makes "the library's setting closed the
gate" and "the item exploded" look identical. A real ``ResolvedItem`` removes
that failure mode, and every assertion below is paired with a POSITIVE
assertion on the other library, which a crash cannot fake.

Artwork is disabled in every config here, which is the idiom
``test_mass_ops_verbs.py`` established: this drives the metadata and badge
blocks only, so no provider is resolved and no ImageMagick subprocess is
spawned. That is also why nothing in this file carries the ``imagemagick``
marker -- CI's main step deselects that marker on a runner with no ``magick``,
and marking a non-compositing test makes it FAIL there. A disabled art kind
still produces a ``Render`` row with ``status="skipped"``
(``render/pipeline.py``'s ``render_artifact``), which is why the badge
assertion below counts TWO calls for a movie: ``ART_KINDS_FOR["movie"]`` is
``["poster", "background"]`` and the badge loop runs over both rows.
"""
from pathlib import Path

import httpx
import pytest

from autoposter.config.loader import (
    RENDER_ART_KINDS,
    build_config,
    config_for_library,
    read_config_document,
    render_version_for,
)
from autoposter.db.models import Render
from autoposter.facts.mdblist import NullMDBListClient
from autoposter.facts.models import GatheredFacts
from autoposter.intake.arr import RenderIntent
from autoposter.render.pipeline import apply_badges, apply_metadata, process_item

from conftest import seed_media_item
from test_mass_ops_fields import FakeTMDB, _item
from test_mass_ops_verbs import FakePlexServer, RecordingPlexItem, RecordingServer
from test_overlay_entrypoint import BASE, REF, FakeServer, _FakePlexItem, _Facts, _render

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"


@pytest.fixture
async def media_item_id(session):
    media = await seed_media_item(session, "1", library="Movies", kind="movie", title="Heat")
    return media.id


def _config(libraries: dict | None = None):
    """The example config with artwork off and an optional libraries block.

    Built through ``build_config`` rather than ``config_factory``, because the
    block's own validators are part of what makes the behaviour under test
    legal and setting the attribute afterwards would skip every one of them.
    """
    document = read_config_document(EXAMPLE)
    if libraries is not None:
        document["libraries"] = libraries
    config = build_config(document)
    # Artwork disabled so this drives only the metadata-operations and badge
    # blocks -- no provider, no imagemagick, needed for the render loop below.
    config.artwork.poster.enabled = False
    config.artwork.background.enabled = False
    return config


def _facts():
    """One non-empty fact, so ``apply_metadata``'s write gate can open at all.

    With ``GatheredFacts()`` the gate's ``not facts.is_empty()`` term is false
    and no verb or override is configured either, so nothing would be written
    for EITHER library and the test would pass without testing anything.
    """
    return FakeTMDB(GatheredFacts(audience_rating=7.6))


@pytest.fixture
def offline_http():
    """An httpx client whose transport answers everything with a 404.

    ``process_item`` hands this to ``render_artifact``; with both art kinds
    disabled nothing should reach it, and a MockTransport makes "nothing
    should" into "nothing can" without tripping conftest's
    ``no_outbound_network`` fixture.
    """
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(404))
    )


def _intent():
    return RenderIntent(kind="movie", title="Heat", tmdb_id=949)


async def test_apply_metadata_uses_the_library_value(session, media_item_id):
    """The helper level, stated first, so the entry-point test below is about
    WIRING rather than about the merge."""
    config = _config({"Movies": {"operations": {"write_to_plex": False}}})

    movies_item = RecordingPlexItem(audienceRating=None, locks=[])
    await apply_metadata(
        session, config, media_item_id, _item(library="Movies"),
        RecordingServer(movies_item),
        _facts(), NullMDBListClient(),
    )
    assert movies_item.edits == [], "Movies' write_to_plex: false was ignored"

    shows_item = RecordingPlexItem(audienceRating=None, locks=[])
    await apply_metadata(
        session, config, media_item_id, _item(library="TV Shows"),
        RecordingServer(shows_item),
        _facts(), NullMDBListClient(),
    )
    assert shows_item.edits != [], "TV Shows should inherit write_to_plex: true"


async def test_apply_badges_uses_the_library_value(session, monkeypatch):
    """The badge helper's own gate, which is its first line -- so the
    resolution has to happen ABOVE it, or a library could override everything
    except whether badges happen at all."""
    config = _config({"Movies": {"badges": {"enabled": False}}})
    reached = []
    monkeypatch.setattr(
        "autoposter.render.pipeline.media_info_from_plex",
        lambda plex_item: reached.append("composed") or {},
    )
    # The RED run (this library's gate not yet resolved) reaches past the
    # stubbed `media_info_from_plex` into the rest of the badge stage, which
    # reads real `MediaInfo` attributes (`video_format_text`'s `.file_paths`,
    # `badge_values`' own `.video_resolutions`) the stubbed `{}` does not
    # carry, and then into `compose_badges`, which would open
    # `render.asset_path` with Pillow -- a path this double deliberately does
    # not point at a real file. Stubbed narrowly, in call order, so the
    # double can complete the whole (still-unfixed) badge stage and fail on
    # the ASSERTION below rather than on an unrelated crash; once the gate
    # resolves per library none of these three is reached for "Movies" at
    # all.
    monkeypatch.setattr(
        "autoposter.render.pipeline.video_format_text", lambda media: None,
    )
    monkeypatch.setattr(
        "autoposter.render.pipeline.badge_values", lambda art_kind, inputs: {},
    )
    monkeypatch.setattr(
        "autoposter.render.pipeline.compose_badges",
        lambda *args, **kwargs: b"",
    )

    class _Render:
        art_kind = "poster"
        status = "rendered"
        badge_fingerprint = None
        asset_path = "/tmp/nothing.jpg"
        # `badge_fingerprint()`'s own first positional argument -- not the
        # gate's `.badge_fingerprint` above -- and missing from the brief's
        # literal fixture; the RED run's still-open gate reaches this line,
        # so the double needs it too.
        fingerprint = None

    class _Row:
        """Only what ``apply_badges`` reads before it returns at the gate."""

        id = 1
        rating_key = "1"
        imdb_id = "tt0113277"

        def __init__(self, library):
            self.library = library

    await apply_badges(session, config, _Render(), _Row("Movies"), object(), None, None)
    assert reached == [], "badges.enabled: false for this library was ignored"


async def test_process_item_uses_the_library_value_for_operations(
    session, offline_http,
):
    """THE entry-point test for operations (C4).

    Through ``process_item``, the function the queue worker calls, because a
    seam nothing calls is a seam that does not exist. Three assertions in one:
    the named library's value wins, another library inherits, and the global
    object is unchanged after both. The second is what makes the first
    meaningful -- an item that crashed would also write nothing.
    """
    config = _config({"Movies": {"operations": {"write_to_plex": False}}})

    async with offline_http as http:
        movies_item = RecordingPlexItem(audienceRating=None, locks=[])
        await process_item(
            session, config, http,
            FakePlexServer(_item(library="Movies"), movies_item), [],
            _intent(), tmdb_facts=_facts(), mdblist=NullMDBListClient(),
        )
        assert movies_item.edits == [], (
            "Movies' write_to_plex: false never reached the write"
        )

        shows_item = RecordingPlexItem(audienceRating=None, locks=[])
        await process_item(
            session, config, http,
            FakePlexServer(_item(native_id="2", library="TV Shows"), shows_item), [],
            _intent(), tmdb_facts=_facts(), mdblist=NullMDBListClient(),
        )
        assert shows_item.edits != [], (
            "TV Shows should inherit write_to_plex: true"
        )

    assert config.operations.write_to_plex is True, "the global was mutated"


async def test_process_item_uses_the_library_value_for_badges(
    session, offline_http, monkeypatch,
):
    """THE entry-point test for badges (C4). Same three assertions, at the
    other gate.

    ``tmdb_facts`` is deliberately NOT passed: the operations block is then
    skipped entirely (``process_item``'s ``tmdb_facts is not None`` term), so
    this test is about the badge gate and nothing else -- and a real
    ``apply_facts`` raising against a double cannot roll the session back
    underneath the badge block and empty ``badged`` for the wrong reason.
    """
    config = _config({"Movies": {"badges": {"enabled": False}}})
    badged: list[str] = []

    async def fake_apply_badges(session_, config_, render, item, *args, **kwargs):
        badged.append(item.library)

    monkeypatch.setattr(
        "autoposter.render.pipeline.apply_badges", fake_apply_badges,
    )

    async with offline_http as http:
        await process_item(
            session, config, http,
            FakePlexServer(_item(library="Movies"), RecordingPlexItem(locks=[])), [],
            _intent(),
        )
        assert badged == [], "Movies' badges.enabled: false never reached the gate"

        await process_item(
            session, config, http,
            FakePlexServer(
                _item(native_id="2", library="TV Shows"), RecordingPlexItem(locks=[]),
            ),
            [], _intent(),
        )

    # TWICE, and that is the correct count rather than an accident: both art
    # kinds are disabled, `render_artifact` still records a Render row per kind
    # with status="skipped", and the badge loop runs over both rows. The point
    # of the assertion is the library name, and that it appears at all.
    assert badged == ["TV Shows", "TV Shows"], (
        "TV Shows should inherit badges.enabled: true"
    )
    assert config.badges.enabled is True, "the global was mutated"


def test_a_library_with_no_block_is_byte_identical():
    """Gate off, in this row's vocabulary: a deployment that never opened the
    matrix must behave exactly as it did before this branch, and the cheapest
    proof of that is object identity -- ``config_for_library`` answers the very
    object it was handed, so not one reader downstream can see a difference."""
    config = _config()
    assert config_for_library(config, "Movies") is config
    assert config_for_library(config, "TV Shows") is config


async def test_a_per_library_badges_override_moves_only_that_librarys_fingerprint(
    session,
):
    """Roadmap row 92 review, Task 2 round 2: the storm proof's other half,
    which that review named as belonging to THIS task rather than T2's --
    "`badges.families` IS overridable and feeds
    `BadgesConfig.all_definitions()`, so the definitions list handed to
    `badge_fingerprint` will differ for the overriding library and for it
    alone. That scoped invalidation is the intended shape."

    Driven through the REAL entry point (``apply_badges``, not a manual
    ``config_for_library`` pre-resolve) with a REAL base image
    (``test_overlay_entrypoint``'s ``BASE``/``_render``), because a stubbed
    badge stage cannot tell "the fingerprint moved" from "the stub always
    answers the same value". Three configs, one item each: a no-``libraries``
    baseline, ``Movies`` (which states the override), and ``TV Shows``
    (which does not) -- Movies' fingerprint must differ from the baseline's,
    and TV Shows' must not.

    The other half of the same proof: ``render_version_for`` and
    ``config.version`` cannot see ``badges`` at all (T1's storm guard), so
    they stay byte-identical between the baseline and the overridden config
    even though a real fingerprint moved -- the invalidation this row causes
    is scoped to badges alone.
    """
    baseline = _config()
    overridden = _config({"Movies": {"badges": {"families": ["direct_play"]}}})

    baseline_item, baseline_render = await _render(session, rating_key="per-lib-baseline")
    await apply_badges(
        session, baseline, baseline_render, baseline_item,
        FakeServer(_FakePlexItem()), REF, _Facts(),
    )

    movies_item, movies_render = await _render(session, rating_key="per-lib-movies")
    await apply_badges(
        session, overridden, movies_render, movies_item,
        FakeServer(_FakePlexItem()), REF, _Facts(),
    )

    shows_item = await seed_media_item(
        session, "per-lib-shows", kind="movie", library="TV Shows", title="X",
    )
    shows_render = Render(
        item_id=shows_item.id, art_kind="poster", base_sha256="abc",
        status="rendered", asset_path=str(BASE),
    )
    session.add(shows_render)
    await session.flush()
    await apply_badges(
        session, overridden, shows_render, shows_item,
        FakeServer(_FakePlexItem()), REF, _Facts(),
    )

    assert movies_render.badge_fingerprint != baseline_render.badge_fingerprint, (
        "Movies' families: [direct_play] override never reached the fingerprint"
    )
    assert shows_render.badge_fingerprint == baseline_render.badge_fingerprint, (
        "TV Shows inherits no override and must stay byte-identical"
    )

    assert overridden.version == baseline.version, "the global was mutated"
    for kind in RENDER_ART_KINDS:
        assert render_version_for(kind, overridden) == render_version_for(kind, baseline), (
            f"a libraries: block moved render_version_for({kind!r}, ...)"
        )
