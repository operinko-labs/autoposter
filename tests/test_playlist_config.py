"""The ``playlists:`` section's refusals.

Most of this file is about what a playlist definition CANNOT say. That is not
padding: ``config/schema.py`` sets no ``model_config``, so pydantic's default
applies and an unknown key is silently dropped -- which means leaving a field
off ``PlaylistDefinition`` is not a refusal at all, it is a setting that reads
as applied and never is. Kometa refuses the same set at parse time against an
allowlist ("attribute not compatible with playlists", modules/builder.py:1569);
this is that list, turned inside out, moved to config load, and each entry
carrying its own reason.
"""
import pytest
from pydantic import ValidationError

from autoposter.config.schema import (
    _REFUSED_PLAYLIST_FIELDS,
    Config,
    PlaylistDefinition,
    PlaylistsConfig,
)

# ``list``, not ``list_id``. ``ImdbListParams`` (builders/imdb_lists.py:111-129)
# sets ``extra="forbid"`` and its one required field is ``list: str`` -- "the
# name the operator writes, because ``list:`` is what the IMDb URL calls this
# thing", says the field's own comment. ``MdblistListParams``
# (builders/mdblist.py:86-109) is the same shape. Since
# ``_params_must_satisfy_the_builders_own_model`` below validates ``params``
# through exactly that model, ``list_id`` would make EVERY use of this constant
# raise -- which is not a subtle failure, but it is a silent one if the fixture
# is only ever read and never run.
A_DEFINITION = {
    "title": "Marvel Cinematic Universe",
    "builder": "imdb_list",
    "params": {"list": "ls539646485"},
}


def test_a_minimal_definition_loads_with_the_documented_defaults():
    definition = PlaylistDefinition.model_validate(A_DEFINITION)

    assert definition.libraries is None
    assert definition.summary is None
    assert definition.sync_mode == "sync"
    assert definition.builder_level == "item"
    assert definition.limit is None
    assert definition.schedule is None


@pytest.mark.parametrize("field", sorted(_REFUSED_PLAYLIST_FIELDS))
def test_every_collection_only_field_is_refused_by_name(field):
    """Refused, not ignored -- and the message says which field and why."""
    with pytest.raises(ValidationError) as error:
        PlaylistDefinition.model_validate({**A_DEFINITION, field: "anything"})

    message = str(error.value)
    assert field in message
    assert _REFUSED_PLAYLIST_FIELDS[field] in message


def test_the_refusal_table_names_only_fields_a_collection_definition_has():
    """A refusal for a key no collection definition carries would be a refusal
    nobody could trip, and would read as documentation of a field that does not
    exist."""
    from autoposter.config.schema import CollectionDefinition

    unknown = sorted(set(_REFUSED_PLAYLIST_FIELDS) - set(CollectionDefinition.model_fields))
    assert unknown == [], (
        "these refused keys are not CollectionDefinition fields: %s" % unknown
    )


def test_the_refusal_table_and_the_model_do_not_overlap():
    """A field cannot be both offered and refused."""
    both = sorted(set(_REFUSED_PLAYLIST_FIELDS) & set(PlaylistDefinition.model_fields))
    assert both == [], "offered and refused at the same time: %s" % both


def test_an_unknown_builder_is_refused_with_the_registry_listed():
    with pytest.raises(ValidationError) as error:
        PlaylistDefinition.model_validate({**A_DEFINITION, "builder": "imdb_lsit"})

    assert "unknown collection builder 'imdb_lsit'" in str(error.value)


def test_a_smart_builder_is_refused():
    """Plex evaluates a smart collection's membership itself, so there is no
    ordered list for a playlist to be given."""
    with pytest.raises(ValidationError) as error:
        PlaylistDefinition.model_validate({
            "title": "Ages", "builder": "cs_bucket", "params": {},
        })

    assert "smart builder" in str(error.value)


def test_an_expanding_builder_is_refused():
    """A family builder returns whole definitions; a playlist is one object
    with one ordered membership."""
    with pytest.raises(ValidationError) as error:
        PlaylistDefinition.model_validate({
            "title": "Oscars", "builder": "imdb_award_years", "params": {},
        })

    assert "expands into a FAMILY" in str(error.value)


def test_a_blank_libraries_list_is_refused_but_an_absent_one_is_not():
    """Kometa's own rule (modules/builder.py:710-718): omitted means every
    configured library, blank means the operator wrote something that selects
    nothing."""
    assert PlaylistDefinition.model_validate(A_DEFINITION).libraries is None

    with pytest.raises(ValidationError) as error:
        PlaylistDefinition.model_validate({**A_DEFINITION, "libraries": []})

    assert "names no library at all" in str(error.value)


def test_bad_params_are_refused_at_config_load_naming_the_playlist():
    with pytest.raises(ValidationError) as error:
        PlaylistDefinition.model_validate({
            "title": "Marvel", "builder": "imdb_list", "params": {"lst_id": "x"},
        })

    assert "'Marvel' does not configure the 'imdb_list' builder correctly" in str(
        error.value
    )


def test_two_definitions_may_not_share_a_title():
    """A playlist has no library dimension, so ``title`` alone is its identity
    -- the ``managed_playlists`` unique key, and the row the members hash is
    stored on. Two definitions sharing one would overwrite each other on every
    pass and the hash would flap between them forever."""
    with pytest.raises(ValidationError) as error:
        PlaylistsConfig.model_validate({
            # ``MdblistListParams`` sets ``coerce_numbers_to_str=True``, so the
            # bare int is a valid list id -- that is how YAML hands over a
            # numeric one, and quoting it is the operator's business.
            "definitions": [A_DEFINITION, {**A_DEFINITION, "builder": "mdblist_list",
                                           "params": {"list": 46555}}],
        })

    assert "two playlist definitions both build" in str(error.value)


def test_the_section_defaults_are_the_house_posture(config_factory):
    config = config_factory()

    assert config.playlists.enabled is True
    assert config.playlists.apply_to_plex is False
    assert config.playlists.libraries is None
    assert config.playlists.definitions == []
    assert config.playlists.delete_unconfigured is False
    assert config.playlists.max_deletes == 5


def test_a_definition_may_only_scope_itself_to_a_configured_library(config_factory):
    """The half of A7 that config load CAN answer. The config holds library
    NAMES and nothing that says a name is a Music section, so the media-type
    refusal itself belongs to the pass (before any write); what belongs here is
    the typo."""
    config = config_factory()
    document = config.model_dump()
    document["playlists"] = {
        "definitions": [{**A_DEFINITION, "libraries": ["Moves"]}]
    }

    with pytest.raises(ValidationError) as error:
        Config.model_validate(document)

    assert "'Moves'" in str(error.value)
    assert "Movies" in str(error.value)


def test_the_section_scope_is_validated_the_same_way(config_factory):
    config = config_factory()
    document = config.model_dump()
    document["playlists"] = {"libraries": ["Movies"],
                             "definitions": [{**A_DEFINITION, "libraries": ["TV Shows"]}]}

    with pytest.raises(ValidationError) as error:
        Config.model_validate(document)

    assert "'TV Shows'" in str(error.value)


# --- who a playlist is copied to ---------------------------------------------


def test_sync_to_users_defaults_to_nobody():
    """The default this whole phase must have. Kometa's is the same
    (``valid_users == []`` at 498b3af, contrary to older docs claiming
    ``all``), and the alternative -- a definition silently fanning out into
    seventeen accounts -- is the one behaviour a per-user sync must not have."""
    assert PlaylistDefinition.model_validate(A_DEFINITION).sync_to_users is None


def test_a_definition_may_name_users_by_title():
    """By ``title``, because it is the ONLY field present for both kinds of
    user: ``MyPlexAccount.user()``'s own code comments it -- "Home users don't
    have email, username etc." -- and matches Home users on ``title`` alone.
    A config keyed on ``username`` could not name a Home account at all."""
    definition = PlaylistDefinition.model_validate(
        {**A_DEFINITION, "sync_to_users": ["alice", "bob"]}
    )

    assert definition.sync_to_users == ["alice", "bob"]


def test_all_is_refused_unless_the_section_switch_is_on():
    """A9's gate, refused at CONFIG LOAD rather than skipped at pass time: the
    section validator can see both the flag and every definition, so the
    operator learns at the moment of the edit."""
    with pytest.raises(ValidationError) as error:
        PlaylistsConfig.model_validate({
            "definitions": [{**A_DEFINITION, "sync_to_users": "all"}],
        })

    message = str(error.value)
    assert "sync_all_users" in message
    assert "Marvel Cinematic Universe" in message


def test_all_loads_once_the_section_switch_is_on():
    section = PlaylistsConfig.model_validate({
        "sync_all_users": True,
        "definitions": [{**A_DEFINITION, "sync_to_users": "all"}],
    })

    assert section.definitions[0].sync_to_users == "all"


def test_a_named_user_list_needs_no_gate():
    """The gate guards the meaning of one WORD. Naming people explicitly is
    already an explicit act and is not behind it."""
    section = PlaylistsConfig.model_validate({
        "definitions": [{**A_DEFINITION, "sync_to_users": ["alice"]}],
    })

    assert section.sync_all_users is False
    assert section.definitions[0].sync_to_users == ["alice"]


def test_the_list_form_of_all_is_refused_unless_the_section_switch_is_on():
    """``sync_to_users: [all]`` (the YAML list form of the word) means the
    same thing as ``sync_to_users: all`` and must hit the same gate -- not
    take the ``list[str]`` branch and resolve against a user literally
    titled "all"."""
    with pytest.raises(ValidationError) as error:
        PlaylistsConfig.model_validate({
            "definitions": [{**A_DEFINITION, "sync_to_users": ["all"]}],
        })

    message = str(error.value)
    assert "sync_all_users" in message
    assert "Marvel Cinematic Universe" in message


def test_the_list_form_of_all_loads_once_the_section_switch_is_on():
    """Normalised to the same value the bare-word form produces, so nothing
    downstream has to know two spellings mean one thing."""
    section = PlaylistsConfig.model_validate({
        "sync_all_users": True,
        "definitions": [{**A_DEFINITION, "sync_to_users": ["all"]}],
    })

    assert section.definitions[0].sync_to_users == "all"


def test_all_mixed_with_named_users_is_always_refused():
    """Not one meaning or the other, so no gate can allow it -- refused at
    config load the same shape as the other refusals here, regardless of
    ``sync_all_users``."""
    with pytest.raises(ValidationError) as error:
        PlaylistsConfig.model_validate({
            "sync_all_users": True,
            "definitions": [{**A_DEFINITION, "sync_to_users": ["all", "someone"]}],
        })

    message = str(error.value)
    assert "sync_to_users" in message
    assert "all" in message
    assert "mixes" in message
    # The refusal sentence itself, not pydantic's wrapper -- str(error.value)
    # also echoes the raw input dict for debug context, which would make this
    # assertion fail on the fixture's own "someone" regardless of what our
    # validator wrote. The raised message is the one served surface here.
    assert "someone" not in error.value.errors()[0]["msg"]


def test_the_section_defaults_leave_every_user_untouched():
    """The two gates and the two caps, as shipped. ``sync_to_users_apply`` off
    is the whole safety posture of this phase: a pass reports what each user
    would receive and sends nothing."""
    section = PlaylistsConfig.model_validate({})

    assert section.sync_to_users_apply is False
    assert section.sync_all_users is False
    assert section.exclude_users == []
    assert section.max_users == 25
    assert section.max_user_writes == 50


@pytest.mark.parametrize("name", ["max_users", "max_user_writes"])
def test_a_cap_refuses_a_negative_value(name):
    """``ge=0`` on both, matching ``max_deletes``: zero means "opted in and
    writes nothing", which is a coherent state; below zero is not."""
    with pytest.raises(ValidationError):
        PlaylistsConfig.model_validate({name: -1})


def test_the_apply_gate_is_independent_of_apply_to_plex():
    """The combination that has to be expressible: admin playlists live, user
    copies reported. Two switches because they authorise two different things
    -- writing to this account, and writing into other people's."""
    section = PlaylistsConfig.model_validate({
        "apply_to_plex": True, "sync_to_users_apply": False,
    })

    assert section.apply_to_plex is True
    assert section.sync_to_users_apply is False


def test_a_preset_never_carries_sync_to_users():
    """Which is what makes the ``all`` refusal complete: it scans
    ``self.definitions``, and the nine shipped presets are built from a frozen
    table that sets no such field, so there is nothing it could miss."""
    from autoposter.collections.playlist_presets import PLAYLIST_PRESETS

    for preset in PLAYLIST_PRESETS:
        assert preset.definition().sync_to_users is None
