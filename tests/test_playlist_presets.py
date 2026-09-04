"""``playlists.presets``: the nine Kometa timeline playlists, as data.

Kometa's only default playlist file (``defaults/playlist.yml``) defines nine
playlists and every one of them sits on ``imdb_list`` or ``mdblist_list`` --
both shipped here in roadmap row 95, so the whole table costs zero new
provider work and needs no Trakt. What earns its keep is not the fetching but
the *posture*: a preset is data an operator switches on by key, switches off by
removing the key, and overrides by writing a definition of the same title --
and that last case is REPORTED rather than resolved silently in either
direction (adjudication A6).
"""
import pytest
from pydantic import ValidationError

from autoposter.collections.builders import REGISTRY
from autoposter.collections.playlist_presets import (
    BY_KEY,
    BY_TITLE,
    PLAYLIST_PRESETS,
    playlist_definitions,
    preset_conflicts,
    preset_definitions,
    presets_needing_mdblist,
)
from autoposter.config.schema import PlaylistsConfig

# The nine, by key, in table order. Written out here rather than derived from
# the table so a row silently disappearing is a failure rather than a shorter
# loop that still passes.
NINE = [
    "arrowverse_timeline",
    "mcu_timeline",
    "dc_animated_timeline",
    "pokemon_timeline",
    "star_trek_timeline",
    "star_wars_timeline",
    "clone_wars_timeline",
    "x_men_timeline",
    "dragon_ball_timeline",
]


def _section(**overrides) -> PlaylistsConfig:
    return PlaylistsConfig.model_validate(overrides)


def _config(**overrides):
    """A stand-in whose only attribute the expansion reads is ``playlists``.

    ``preset_definitions`` and its siblings are pure functions of
    ``config.playlists`` -- the same shim shape ``_titles_must_not_collide``
    uses to run ``default_definitions`` during validation of the section it is
    validating (``config/schema.py``'s ``SimpleNamespace(collections=self)``).
    """
    from types import SimpleNamespace

    return SimpleNamespace(playlists=_section(**overrides))


def test_the_table_holds_exactly_the_nine_kometa_timeline_playlists():
    assert [preset.key for preset in PLAYLIST_PRESETS] == NINE
    assert set(BY_KEY) == set(NINE)
    assert len(BY_TITLE) == 9


@pytest.mark.parametrize("key", NINE)
def test_every_preset_key_builds_a_valid_definition(key):
    """Through the real model, so a params typo is a failure here rather than
    a definition that loads clean and builds nothing.

    ``PlaylistDefinition._params_must_satisfy_the_builders_own_model`` runs the
    params through the builder's own ``extra="forbid"`` model, so ``list_id``
    instead of ``list`` -- the single likeliest transcription error in this
    whole table -- raises right here.
    """
    definition = BY_KEY[key].definition()

    assert definition.title == BY_KEY[key].title
    assert definition.builder in ("imdb_list", "mdblist_list")
    assert definition.libraries is None
    assert definition.sync_mode == "sync"
    assert definition.builder_level == "item"


def test_the_two_builders_the_presets_use_are_registered():
    assert {preset.builder for preset in PLAYLIST_PRESETS} <= set(REGISTRY)


def test_no_preset_field_the_api_serves_carries_a_url():
    """The served-surface rule, mechanically. The upstream citation lives in
    the module's comment table, never in a field the listing projects."""
    for preset in PLAYLIST_PRESETS:
        served = "%s %s %s %s" % (
            preset.key, preset.title, preset.builder, preset.params
        )
        assert "http" not in served
        assert "/" not in preset.key


def test_an_empty_presets_list_expands_to_nothing():
    assert preset_definitions(_config()) == []
    assert playlist_definitions(_config()) == []


def test_a_switched_on_key_expands_to_its_definition():
    definitions = preset_definitions(_config(presets=["star_wars_timeline"]))

    assert [d.title for d in definitions] == ["Star Wars (Timeline Order)"]
    assert definitions[0].builder == "imdb_list"
    assert definitions[0].params == {"list": "ls501373412"}


def test_presets_come_first_and_operator_definitions_are_appended():
    """``service.library_definitions``' order, for its reason: the operator's
    are APPENDED, so an empty ``presets:`` leaves exactly what was configured.
    """
    config = _config(
        presets=["mcu_timeline"],
        definitions=[{
            "title": "Household Picks",
            "builder": "imdb_list",
            "params": {"list": "ls055350410"},
        }],
    )

    assert [d.title for d in playlist_definitions(config)] == [
        "Marvel Cinematic Universe (Timeline Order)",
        "Household Picks",
    ]


def test_an_operator_definition_of_the_same_title_shadows_the_preset():
    """The override half of A6: the operator's definition wins, and the preset
    expands to nothing rather than to a second writer on one title."""
    config = _config(
        presets=["mcu_timeline"],
        definitions=[{
            "title": "Marvel Cinematic Universe (Timeline Order)",
            "builder": "mdblist_list",
            "params": {"list": "someone/mcu"},
        }],
    )

    assert preset_definitions(config) == []
    built = playlist_definitions(config)
    assert [d.title for d in built] == [
        "Marvel Cinematic Universe (Timeline Order)"
    ]
    assert built[0].builder == "mdblist_list"


def test_the_shadowed_preset_is_reported_by_preset_conflicts():
    """The report half of A6. Nothing is silently overwritten in either
    direction: the operator's definition is built, and the fact that it
    displaced a preset is a fact the surfaces can show."""
    config = _config(
        presets=["mcu_timeline", "star_trek_timeline"],
        definitions=[{
            "title": "Marvel Cinematic Universe (Timeline Order)",
            "builder": "mdblist_list",
            "params": {"list": "someone/mcu"},
        }],
    )

    assert preset_conflicts(config) == [
        ("mcu_timeline", "Marvel Cinematic Universe (Timeline Order)")
    ]


def test_only_unshadowed_mdblist_presets_are_reported_as_needing_the_key():
    """The KEY-naming input, and the exclusion its docstring argues for.

    Three switched on: ``mcu_timeline`` sits on ``imdb_list`` and needs no
    MDBList key at all; ``pokemon_timeline`` and ``dragon_ball_timeline`` are
    the two of the nine that do. The operator has written their own definition
    over the Pokémon title, so that preset is not built for a DIFFERENT reason
    -- ``preset_conflicts`` already says which -- and naming it here would send
    an operator to the MDBList settings over a playlist they took charge of
    themselves. Only ``dragon_ball_timeline`` survives, and the assertion is an
    equality rather than an ``in`` so that a regression which reports the
    shadowed key fails here rather than passing with one row too many.
    """
    config = _config(
        presets=["mcu_timeline", "pokemon_timeline", "dragon_ball_timeline"],
        definitions=[{
            "title": "Pokémon (Timeline Order)",
            "builder": "mdblist_list",
            "params": {"list": "someone/pokemon"},
        }],
    )

    assert presets_needing_mdblist(config) == ["dragon_ball_timeline"]
    assert presets_needing_mdblist(_config()) == []


def test_an_unknown_preset_key_is_refused_with_the_keys_listed():
    """The ONLY thing that makes a bad key an error. The expansion is a scan
    of the table rather than a lookup of this list precisely so it cannot
    raise during validation -- an unknown key expands to nothing there, and
    without this refusal a mis-typed key would be a switch the operator
    believed they had flipped. ``CollectionsConfig._presets_must_be_known_and_
    ready`` says the same thing one section along."""
    with pytest.raises(ValidationError) as error:
        _section(presets=["marvel"])

    message = str(error.value)
    assert "unknown playlist preset 'marvel'" in message
    assert "mcu_timeline" in message


def test_a_repeated_preset_key_is_refused():
    with pytest.raises(ValidationError) as error:
        _section(presets=["mcu_timeline", "mcu_timeline"])

    assert "listed twice" in str(error.value)
