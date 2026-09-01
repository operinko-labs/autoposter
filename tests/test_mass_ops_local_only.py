"""Rows 47 and 48 -- the local-asset and skip controls.

Row 47's negative case is the whole point: with the switch on, NO provider is
ever asked. The recorder proves it by counting fetches, not by inspecting a
return value.
"""
from pathlib import Path

from autoposter.config.loader import load_config
from autoposter.plex.client import ResolvedItem
from autoposter.render import pipeline

EXAMPLE = Path("config/autoposter.example.yaml")


def item(kind="episode", season=1, number=3):
    return ResolvedItem(
        rating_key="e1", library="TV Shows", kind=kind, title="Pilot",
        year=2020, season_number=season, episode_number=number,
        root_folder="Dark", file_path=None, art_url=None, tmdb_id=1,
        tvdb_id=2, imdb_id="tt1", parent_rating_key="s1",
    )


def _config(tmp_path, **artwork):
    config = load_config(EXAMPLE)
    return config.model_copy(update={
        "manual_assets_root": tmp_path,
        "library_folders": True,
        "artwork": config.artwork.model_copy(update=artwork),
    })


# --- row 47: DisableOnlineAssetFetch -----------------------------------------

def test_online_fetch_is_enabled_by_default(tmp_path):
    config = _config(tmp_path)
    for art_kind in ("poster", "season_poster", "background", "title_card"):
        assert pipeline.online_fetch_disabled(config, art_kind) is False


def test_the_global_switch_disables_every_kind(tmp_path):
    config = _config(tmp_path, disable_online_asset_fetch=True)
    for art_kind in ("poster", "season_poster", "background", "title_card"):
        assert pipeline.online_fetch_disabled(config, art_kind) is True


def test_a_per_kind_none_inherits_the_global_switch(tmp_path):
    config = _config(tmp_path, disable_online_asset_fetch=True)
    assert config.artwork.poster.disable_online_asset_fetch is None
    assert pipeline.online_fetch_disabled(config, "poster") is True


def test_a_per_kind_false_overrides_the_global_switch(tmp_path):
    config = _config(tmp_path, disable_online_asset_fetch=True)
    config = config.model_copy(update={
        "artwork": config.artwork.model_copy(update={
            "poster": config.artwork.poster.model_copy(
                update={"disable_online_asset_fetch": False}
            ),
        }),
    })
    assert pipeline.online_fetch_disabled(config, "poster") is False
    assert pipeline.online_fetch_disabled(config, "background") is True


def test_a_per_kind_true_disables_only_that_kind(tmp_path):
    config = _config(tmp_path)
    config = config.model_copy(update={
        "artwork": config.artwork.model_copy(update={
            "background": config.artwork.background.model_copy(
                update={"disable_online_asset_fetch": True}
            ),
        }),
    })
    assert pipeline.online_fetch_disabled(config, "background") is True
    assert pipeline.online_fetch_disabled(config, "poster") is False


# --- row 48: season/episode template assets ----------------------------------

def _place_template(tmp_path, name, data=b"x"):
    folder = tmp_path / "TV Shows" / "Dark"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)


def test_a_template_is_ignored_when_the_key_is_unset(tmp_path):
    _place_template(tmp_path, "EpisodeTemplate.jpg")
    assert pipeline.manual_override_path(_config(tmp_path), item(), "title_card") is None


def test_an_episode_template_is_used_when_the_key_is_on(tmp_path):
    _place_template(tmp_path, "EpisodeTemplate.jpg")
    config = _config(tmp_path, season_episode_templates=True)
    found = pipeline.manual_override_path(config, item(), "title_card")
    assert found == tmp_path / "TV Shows" / "Dark" / "EpisodeTemplate.jpg"


def test_a_season_template_is_used_for_season_posters(tmp_path):
    _place_template(tmp_path, "SeasonTemplate.jpg")
    config = _config(tmp_path, season_episode_templates=True)
    found = pipeline.manual_override_path(config, item(kind="season"), "season_poster")
    assert found == tmp_path / "TV Shows" / "Dark" / "SeasonTemplate.jpg"


def test_the_items_own_override_still_wins_over_a_template(tmp_path):
    _place_template(tmp_path, "EpisodeTemplate.jpg", b"template")
    own = tmp_path / "TV Shows" / "Dark" / "S01E03.jpg"
    own.write_bytes(b"mine")
    config = _config(tmp_path, season_episode_templates=True)
    assert pipeline.manual_override_path(config, item(), "title_card") == own


def test_templates_never_apply_to_posters_or_backgrounds(tmp_path):
    _place_template(tmp_path, "SeasonTemplate.jpg")
    _place_template(tmp_path, "EpisodeTemplate.jpg")
    config = _config(tmp_path, season_episode_templates=True)
    assert pipeline.template_override_path(config, item(kind="show"), "poster") is None
    assert pipeline.template_override_path(config, item(kind="show"), "background") is None


class RecordingProvider:
    name = "Recording"

    def __init__(self):
        self.requests = []

    async def fetch(self, request):
        self.requests.append(request)
        return []


async def test_no_provider_is_asked_when_online_fetch_is_disabled(
    session, tmp_path, monkeypatch
):
    """Row 47's negative case: not 'the ladder returned nothing', but 'the
    ladder was never walked'."""
    import httpx

    config = _config(tmp_path, disable_online_asset_fetch=True)
    config = config.model_copy(update={"assets_root": tmp_path / "out"})
    provider = RecordingProvider()
    async with httpx.AsyncClient() as http:
        render = await pipeline.render_artifact(
            session, config, http, item(), "title_card", [provider]
        )
    assert provider.requests == []
    assert render.status == "skipped"
    assert "artwork.disable_online_asset_fetch" in render.detail


async def test_the_ladder_is_walked_when_the_switch_is_unset(
    session, tmp_path
):
    """The default pin, same scenario: one provider request is made."""
    import httpx

    config = _config(tmp_path)
    config = config.model_copy(update={"assets_root": tmp_path / "out"})
    provider = RecordingProvider()
    async with httpx.AsyncClient() as http:
        render = await pipeline.render_artifact(
            session, config, http, item(), "title_card", [provider]
        )
    assert len(provider.requests) == 1
    assert render.status == "no_art"


# --- the crux: a local logo pick must survive disable_online_asset_fetch ----
#
# The field's own name and docstring say "no provider request", not "no local
# lookup" -- and find_logo_override makes zero network calls, it only stats
# files under manual_assets_root. Every asset in this scenario is local (a
# manual poster override, a manual logo pick), so no provider is ever a
# candidate to be asked. The logo must still be composited.

def _movie():
    return ResolvedItem(
        rating_key="m1", library="Movies", kind="movie", title="Dune: Part Two",
        year=2024, season_number=None, episode_number=None, root_folder="Dune (2024)",
        file_path=None, art_url=None, tmdb_id=1, tvdb_id=None, imdb_id="tt1",
        parent_rating_key=None,
    )


def _stub_out_imagemagick(monkeypatch):
    from autoposter.render.textfit import FitResult

    calls: list[list[str]] = []
    logo_calls: list = []
    monkeypatch.setattr(pipeline.compositor, "run", lambda argv: calls.append(argv))
    monkeypatch.setattr(
        pipeline, "fit_point_size",
        lambda *a, **k: FitResult(point_size=120, truncated=False),
    )
    original_build_logo_argv = pipeline.compositor.build_logo_argv

    def spy_build_logo_argv(*args, **kwargs):
        logo_calls.append((args, kwargs))
        return original_build_logo_argv(*args, **kwargs)

    monkeypatch.setattr(pipeline.compositor, "build_logo_argv", spy_build_logo_argv)
    return calls, logo_calls


async def test_a_manual_logo_still_composites_when_online_fetch_is_disabled(
    session, tmp_path, monkeypatch
):
    """The crux: a manual poster override + a manual logo pick +
    disable_online_asset_fetch on for 'poster'. No provider is ever a
    candidate here, so the logo must not be silently dropped."""
    import httpx

    from autoposter.render import naming

    movie = _movie()
    config = _config(tmp_path, disable_online_asset_fetch=True)
    config = config.model_copy(update={"assets_root": tmp_path / "out"})

    manual = config.model_copy(update={"assets_root": config.manual_assets_root})
    poster_override = naming.asset_path(manual, movie.library, movie.root_folder, "poster")
    poster_override.parent.mkdir(parents=True, exist_ok=True)
    poster_override.write_bytes(b"manual poster")

    logo_override = pipeline.logo_override_path(config, movie, ".png")
    logo_override.parent.mkdir(parents=True, exist_ok=True)
    logo_override.write_bytes(b"manual logo")

    _calls, logo_calls = _stub_out_imagemagick(monkeypatch)
    provider = RecordingProvider()

    async with httpx.AsyncClient() as http:
        render = await pipeline.render_artifact(
            session, config, http, movie, "poster", [provider]
        )

    assert provider.requests == []
    assert render.status == "rendered"
    assert len(logo_calls) == 1
