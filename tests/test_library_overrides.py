"""Roadmap row 92 -- the per-library config override matrix.

The storm proof first, and deliberately so: this file's first three tests are
the reason the rest of the row is safe to write, and they are the branch's
first commit. ``render_version`` and ``render_version_for`` hash a NAMED set
of inputs, and a new top-level ``libraries:`` section is not in any of them --
so ``config.version`` and each of the four per-kind versions are byte-unmoved
by every edit this row makes possible, and not one stored fingerprint can be
invalidated by one.

That matters because the alternative shapes are both wrong. A per-library
setting INSIDE the hashed payload moves the one value every stored
fingerprint in every library is compared against, so a per-library edit
strands ~16,000 adopted fingerprints across libraries it never named. One
OUTSIDE the payload silently never invalidates, which is worse, because it is
quiet. Neither is on the table here: the first-cut whitelist is
``operations``, ``badges`` and ``maintenance``, all three of which
``render_version``'s own docstring already names among the sections it
excludes. The artwork half waits for roadmap row 111's ``render_version_for``
to grow a library argument, which is the only shape that confines an artwork
override's invalidation to the library that asked for it.
"""
from pathlib import Path

import pytest
from pydantic import ValidationError

from autoposter.config.live import FROZEN_SECTIONS
from autoposter.config.loader import (
    RENDER_ART_KINDS,
    _shared_render_inputs,
    build_config,
    config_for_library,
    read_config_document,
    render_version,
    render_version_for,
)
from autoposter.config.schema import (
    LIBRARY_OVERRIDE_EXCLUSIONS,
    BadgesConfig,
    BadgesOverride,
    LibraryOverride,
    MaintenanceOverride,
    OperationsConfig,
    OperationsOverride,
    library_override_refusals,
)

SRC = Path(__file__).parent.parent / "src" / "autoposter"
EXAMPLE_CONFIG = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

ROOT_INPUTS = {
    "library_folders",
    "assets_root",
    "manual_assets_root",
    "fonts_root",
    "overlays_root",
}


def test_render_version_hashes_exactly_six_named_inputs(config_factory):
    """Leg 1, for the wholesale value.

    Not "the section happens not to be in there": the payload is a literal
    with six keys, and a top-level section that is not one of them cannot
    reach the hash however it is shaped. Widening this dict is what would
    turn a per-library edit into a full-library re-render, so the key set is
    pinned rather than described.
    """
    config = config_factory()
    source = (SRC / "config" / "loader.py").read_text(encoding="utf-8")
    assert '"artwork": config.artwork.model_dump(mode="json")' in source
    assert '"library_folders": config.library_folders' in source
    assert '"assets_root": str(config.assets_root)' in source
    assert '"manual_assets_root": str(config.manual_assets_root)' in source
    assert '"fonts_root": str(config.fonts_root)' in source
    assert '"overlays_root": str(config.overlays_root)' in source
    # A widened payload -- a 7th key added alongside the six above -- would
    # still satisfy every assert above, since none of them is removed by an
    # addition. The literal pin is what a widening cannot survive.
    assert render_version(config) == "386ea7cf4844f52e"
    # And the value is stable across two calls on one config, so the
    # assertions above are about the thing the rest of this row leans on.
    assert render_version(config) == render_version(config)


def test_no_per_kind_version_reads_anything_outside_artwork_and_the_roots(
    config_factory,
):
    """Leg 1, for row 111's four per-kind values.

    ``_shared_render_inputs`` is a literal member of all four payloads, so it
    is the only place a non-``artwork`` input could enter one. Every key in
    it is either a root, ``library_folders`` or an ``artwork.``-prefixed
    setting -- there is no third category, and a new top-level section cannot
    become one without this failing.
    """
    config = config_factory()
    for key in _shared_render_inputs(config):
        assert key.startswith("artwork.") or key in ROOT_INPUTS, (
            f"{key} is neither an artwork setting nor a root"
        )

    # Pinned literally, not just shape-checked: a widened per-kind payload
    # would still be a 16-char string, so only the exact value catches it.
    versions = {kind: render_version_for(kind, config) for kind in RENDER_ART_KINDS}
    assert versions == {
        "poster": "4ac64b5874ce0ff3",
        "background": "9ae9ab3b95ae68ec",
        "title_card": "31f00cfe0ef31fba",
        "season_poster": "14f86f656d6fa935",
    }


def test_every_fingerprint_call_site_passes_the_config_version():
    """Leg 3, read off the source rather than asserted about a mock.

    ``compute_fingerprint``'s first component is a RENDER version at every
    call site, and since roadmap row 111 there are two spellings of one:
    ``render_version_for(art_kind, config)`` (or its cached
    ``versions[art_kind]``) at the three LIVE sites -- the render path,
    ``adopted_fingerprint`` and the preview walk -- and the legacy
    ``config.version`` at the two dual-read grandfather arms, which accept
    fingerprints written before 111 landed. Leg 1 fixes BOTH values, because
    both are functions of ``artwork`` and the roots alone. A site passing
    anything else would be the quiet way this proof stops holding.
    """
    sources = {
        "render/pipeline.py": (SRC / "render" / "pipeline.py").read_text(encoding="utf-8"),
        "config/impact.py": (SRC / "config" / "impact.py").read_text(encoding="utf-8"),
    }
    seen = 0
    for where, text in sources.items():
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if "compute_fingerprint(" not in line or "def compute_fingerprint" in line:
                continue
            seen += 1
            window = "\n".join(lines[index:index + 4])
            assert (
                "render_version_for(art_kind, config)" in window
                or "versions[art_kind]" in window
                or "config.version" in window
            ), (
                f"{where}:{index + 1} calls compute_fingerprint with a first "
                "component that is neither a per-kind render version nor the "
                "legacy config.version"
            )
    # Five since roadmap row 111: three live sites passing a per-kind
    # version and two dual-read grandfather arms still passing the
    # wholesale one. A sixth passing something else would be the quiet way
    # this proof stopped holding.
    assert seen >= 5, seen


# --------------------------------------------------------------------------
# The schema: the whitelist, the two refusals, and what a block may hold.
# --------------------------------------------------------------------------


def _with_libraries(libraries: dict):
    """A config built from the example document plus a ``libraries:`` block.

    Built through ``build_config`` rather than by setting an attribute,
    because the block's validators are the thing under test and
    ``model_copy``/``setattr`` skip every one of them.
    """
    document = read_config_document(EXAMPLE_CONFIG)
    document["libraries"] = libraries
    return build_config(document)


def test_the_operations_whitelist_is_derived_and_not_hand_maintained():
    """Every ``operations`` field except the six structurally-global ones.

    Derived from ``OperationsConfig`` itself rather than listed, so a field
    added next year is overridable per library by default and a field that
    must NOT be has to be argued for in ``LIBRARY_OVERRIDE_EXCLUSIONS``. The
    alternative -- a hand-kept list -- is how the two drift, and the drift is
    silent: the new field simply never appears in the panel.
    """
    excluded = {
        path.split(".", 1)[1]
        for path in LIBRARY_OVERRIDE_EXCLUSIONS
        if path.startswith("operations.")
    }
    assert excluded == {
        "imdb_refresh_enabled",
        "imdb_refresh_hours",
        "imdb_miss_refresh_minutes",
        "tmdb_backoff_seconds",
        "metadata_backup_enabled",
        "metadata_backup_root",
    }
    assert set(OperationsOverride.model_fields) == (
        set(OperationsConfig.model_fields) - excluded
    )


def test_the_badges_whitelist_excludes_the_list_and_the_safety_bound():
    excluded = {
        path.split(".", 1)[1]
        for path in LIBRARY_OVERRIDE_EXCLUSIONS
        if path.startswith("badges.")
    }
    assert excluded == {"definitions", "definition_image_max_bytes"}
    assert set(BadgesOverride.model_fields) == (
        set(BadgesConfig.model_fields) - excluded
    )


def test_maintenance_offers_only_the_setting_plex_can_scope():
    """Established fact d, as an assertion.

    plexapi puts ``cleanBundles`` and ``optimize`` on ``Library`` only --
    server-wide calls with no per-section form -- and puts ``emptyTrash`` on
    both ``Library`` and ``LibrarySection``. So one of the three can be a
    real per-library setting and two cannot, and the two are refused with
    that reason rather than accepted and quietly ignored.
    """
    assert set(MaintenanceOverride.model_fields) == {"empty_trash"}
    assert LIBRARY_OVERRIDE_EXCLUSIONS["maintenance.clean_bundles"]
    assert LIBRARY_OVERRIDE_EXCLUSIONS["maintenance.optimize"]


def test_a_library_block_holds_only_the_three_whitelisted_sections():
    """No ``artwork``, and no fourth section by accident."""
    assert set(LibraryOverride.model_fields) == {
        "operations", "badges", "maintenance",
    }
    assert "artwork" not in LibraryOverride.model_fields


def test_no_exclusion_names_a_section_outside_the_whitelist():
    """The map cannot grow a key nothing reads."""
    for path in LIBRARY_OVERRIDE_EXCLUSIONS:
        section, _, name = path.partition(".")
        assert section in {"operations", "badges", "maintenance"}, path
        assert name, path


def test_the_frozen_operations_paths_are_all_excluded_per_library():
    """Roadmap row 92 review, Important 2.

    ``LIBRARY_OVERRIDE_EXCLUSIONS``' four ``operations.*`` entries are
    derived from ``config/live.py``'s ``FROZEN_SECTIONS`` rather than a
    second hand-copied literal, so this is a characterization pin rather
    than a live check: it protects the derivation itself, in case a future
    edit reverts ``LIBRARY_OVERRIDE_EXCLUSIONS`` back to a literal that
    quietly stops matching a FIFTH ``operations.*`` entry added to
    ``FROZEN_SECTIONS`` (a new startup-captured cadence, a new process-wide
    client) -- which would otherwise validate clean per library, merge
    clean, and then be ignored at runtime by the object built once at
    startup.
    """
    frozen_operations = {p for p in FROZEN_SECTIONS if p.startswith("operations.")}
    assert frozen_operations, "FROZEN_SECTIONS carries no operations.* path to check"
    assert frozen_operations <= set(LIBRARY_OVERRIDE_EXCLUSIONS), (
        frozen_operations - set(LIBRARY_OVERRIDE_EXCLUSIONS)
    )


@pytest.mark.parametrize("section", ["artwork", "render", "scheduler", "made_up_section"])
def test_a_non_whitelisted_section_is_refused_at_load(section):
    """Roadmap row 92 review, Important 1.

    ``LIBRARY_OVERRIDE_EXCLUSIONS`` is keyed ``section.name`` and only ever
    matches a LEAF of an already-whitelisted section, so it could never
    refuse a whole SECTION -- ``artwork`` and ``scheduler`` are real
    ``Config`` fields this service does not let vary per library, and
    ``render``/``made_up_section`` are not real fields at all, but all four
    used to be dropped in silence by pydantic's default ``extra="ignore"``.
    Refused now, and never naming the library the operator typed (Global
    Constraint 13).
    """
    with pytest.raises(ValidationError) as caught:
        _with_libraries({"Movies": {section: {"anything": True}}})
    message = str(caught.value)
    assert section in message
    assert "Movies" not in message


def test_a_library_block_validates_and_the_rest_of_the_config_is_untouched():
    config = _with_libraries({
        "Movies": {"operations": {"write_to_plex": False}},
    })
    assert config.libraries["Movies"].operations.write_to_plex is False
    assert config.libraries["Movies"].badges is None
    # The GLOBAL section is not mutated by a library naming it.
    assert config.operations.write_to_plex is True


def test_an_unset_leaf_is_unset_rather_than_none():
    """The whole inheritance mechanism in one assertion.

    Every override field is ``T | None`` with a ``None`` default, so "absent"
    and "explicitly null" are the same VALUE and can only be told apart by
    pydantic's own ``model_fields_set``. ``config_for_library`` dumps with
    ``exclude_unset=True`` for exactly that reason, and this is what makes
    that dump mean "the leaves this library actually stated".
    """
    config = _with_libraries({
        "Movies": {"operations": {"write_to_plex": False}},
    })
    operations = config.libraries["Movies"].operations
    assert operations.model_fields_set == {"write_to_plex"}
    assert operations.model_dump(exclude_unset=True) == {"write_to_plex": False}
    # An explicit null IS a statement -- "this library uses no user rating
    # source" -- and survives the dump, unlike an absent key.
    explicit = _with_libraries({
        "Movies": {"operations": {"user_rating_source": None}},
    })
    assert explicit.libraries["Movies"].operations.model_dump(exclude_unset=True) == {
        "user_rating_source": None,
    }


def test_an_unknown_library_name_is_refused_without_naming_it():
    """Refused rather than ignored: this document holds library NAMES and
    nothing that says whether a name is real, so a name absent from
    ``collections.libraries`` cannot be told apart from a typo, and refusing
    is the honest answer -- the same limit
    ``_playlist_libraries_must_be_configured`` records for itself.

    The message carries a COUNT and never the name the operator typed
    (Global Constraint 13); the document that failed is in the pod log, which
    is where a config-load refusal is read.
    """
    with pytest.raises(ValidationError) as caught:
        _with_libraries({"Nope": {"badges": {"enabled": False}}})
    message = str(caught.value)
    assert "collections.libraries" in message
    assert "Nope" not in message


def test_a_structurally_global_key_is_refused_with_its_reason():
    """Not "unknown setting": these are real settings, and telling an operator
    to look for a typo would send them hunting for one that is not there."""
    with pytest.raises(ValidationError) as caught:
        _with_libraries({
            "Movies": {"operations": {"tmdb_backoff_seconds": 30}},
        })
    message = str(caught.value)
    assert "operations.tmdb_backoff_seconds" in message
    assert "Movies" not in message


def test_a_server_wide_maintenance_key_is_refused_with_its_reason():
    with pytest.raises(ValidationError) as caught:
        _with_libraries({
            "Movies": {"maintenance": {"clean_bundles": True}},
        })
    assert "maintenance.clean_bundles" in str(caught.value)


def test_the_refusal_walk_reports_every_offending_path():
    """The shared walk both callers use, on its own. Paths are dotted from
    the document root so the config editor can put each one on its row."""
    document = {
        "libraries": {
            "Movies": {
                "operations": {"enabled": False, "metadata_backup_root": "/x"},
                "badges": {"definitions": []},
            },
            "TV Shows": {"maintenance": {"optimize": True}},
        }
    }
    paths = [path for path, _ in library_override_refusals(document)]
    assert paths == [
        "libraries.Movies.badges.definitions",
        "libraries.Movies.operations.metadata_backup_root",
        "libraries.TV Shows.maintenance.optimize",
    ]
    assert library_override_refusals({}) == []
    assert library_override_refusals({"libraries": {}}) == []


def test_a_cross_field_rule_inside_a_library_block_still_fires():
    """Established fact o. ``operations.field_verbs``' vocabulary and
    ``badges.families``' closed set are NOT re-stated on the partial models;
    ``Config`` builds every library's merged sections at load instead, so the
    real models' own validators run and nothing can drift out of step with
    them.
    """
    with pytest.raises(ValidationError) as verbs:
        _with_libraries({
            "Movies": {"operations": {"field_verbs": {"nosuchfield": "lock"}}},
        })
    assert "field_verbs" in str(verbs.value)

    with pytest.raises(ValidationError) as families:
        _with_libraries({
            "Movies": {"badges": {"families": ["nosuchfamily"]}},
        })
    assert "family" in str(families.value)


def test_the_libraries_section_moves_no_render_version(config_factory):
    """The storm argument's other half -- the one that needs the field.

    ``render_version``'s payload is a six-key literal naming ``artwork`` and
    five path/flag scalars (pinned above), so a ``libraries:`` block is
    outside it BY CONSTRUCTION. Setting a representative leaf of each
    whitelisted section (9 of the 27 available leaves) across the 2
    configured libraries must leave the wholesale value and all four
    per-kind values digit-for-digit identical -- not exhaustive over every
    leaf; the six-key literal above is what makes that enough.
    """
    before = config_factory()
    before_versions = {
        kind: render_version_for(kind, before) for kind in RENDER_ART_KINDS
    }

    after = _with_libraries({
        "Movies": {
            "operations": {"enabled": False, "write_to_plex": False,
                           "ignore_labels": ["skip"], "user_rating_source": "tmdb"},
            "badges": {"enabled": False, "upload_to_plex": True,
                       "lock_artwork": False, "families": []},
            "maintenance": {"empty_trash": True},
        },
        "TV Shows": {"badges": {"adopt_from_plex": False}},
    })

    assert render_version(after) == render_version(before)
    assert after.version == before.version
    for kind in RENDER_ART_KINDS:
        assert render_version_for(kind, after) == before_versions[kind], kind


# --------------------------------------------------------------------------
# The seam: config_for_library, and the merge rule leaf by leaf.
# --------------------------------------------------------------------------


def test_a_library_with_no_block_gets_the_config_object_itself(config_factory):
    """Identity, not equality. The ordinary deployment -- every one whose
    operator never opened the matrix -- must allocate nothing at all, which
    is the posture ``without_migrated_sections`` already takes for the same
    reason."""
    config = config_factory()
    assert config_for_library(config, "Movies") is config
    assert config_for_library(config, "Anything At All") is config


def test_a_library_whose_block_states_nothing_also_gets_the_object_itself():
    """A block holding only sections that state no leaf is not a change."""
    config = _with_libraries({"Movies": {"operations": {}}})
    assert config_for_library(config, "Movies") is config


def test_a_stated_leaf_wins_for_that_library_only():
    config = _with_libraries({
        "Movies": {"operations": {"write_to_plex": False}},
    })
    movies = config_for_library(config, "Movies")
    shows = config_for_library(config, "TV Shows")

    assert movies.operations.write_to_plex is False
    assert shows.operations.write_to_plex is True
    assert config.operations.write_to_plex is True, "the global was mutated"


def test_an_absent_leaf_inherits_the_global():
    """Kometa's rule, restated: present wins, absent inherits, leaf by leaf.
    Stating ONE leaf must not blank the other nineteen."""
    config = _with_libraries({
        "Movies": {"operations": {"write_to_plex": False}},
    })
    movies = config_for_library(config, "Movies")

    assert movies.operations.enabled is config.operations.enabled
    assert movies.operations.field_verbs == config.operations.field_verbs
    assert movies.operations.parental_labels_enabled == (
        config.operations.parental_labels_enabled
    )
    assert movies.operations.tmdb_backoff_seconds == (
        config.operations.tmdb_backoff_seconds
    )


def test_a_list_leaf_replaces_and_does_not_union():
    """The one place this codebase deliberately differs from Kometa, pinned.

    Kometa UNIONs ``ignore_ids``/``ignore_imdb_ids`` with the global. Here a
    list is a complete statement of intent, so a library's list REPLACES --
    and an empty list means "none", not "the global ones". One merge rule in
    this codebase beats byte parity with Kometa on two keys.
    """
    config = _with_libraries({
        "Movies": {"operations": {"ignore_labels": ["only_this"]}},
    })
    config.operations.ignore_labels.append("global_label")
    movies = config_for_library(config, "Movies")
    assert movies.operations.ignore_labels == ["only_this"]

    emptied = _with_libraries({
        "Movies": {"operations": {"ignore_labels": []}},
    })
    assert config_for_library(emptied, "Movies").operations.ignore_labels == []


def test_a_mapping_leaf_merges_key_by_key():
    """The consequence of reusing ``overrides._merge``, stated rather than
    discovered. A nested mapping merges; that is what the stored overrides
    document already does to these same three settings, so the two layers
    agree instead of disagreeing quietly."""
    config = _with_libraries({
        "Movies": {"operations": {"genre_mapper": {"Sci-Fi": "Science Fiction"}}},
    })
    config.operations.genre_mapper["Anime"] = "Animation"
    movies = config_for_library(config, "Movies")
    assert movies.operations.genre_mapper == {
        "Anime": "Animation",
        "Sci-Fi": "Science Fiction",
    }


def test_every_non_whitelisted_section_is_carried_through_by_identity():
    """``model_copy(update=...)`` on the whitelisted sections only, so
    ``artwork``, ``version`` and the other sixteen sections are the SAME
    objects. That identity is the storm proof at runtime: the effective
    config for a library cannot have a different ``artwork`` and so cannot
    have a different version, whatever a caller does with it."""
    config = _with_libraries({
        "Movies": {"badges": {"enabled": False}},
    })
    movies = config_for_library(config, "Movies")

    assert movies.artwork is config.artwork
    assert movies.plex is config.plex
    assert movies.collections is config.collections
    assert movies.operations is config.operations
    assert movies.version == config.version
    assert render_version(movies) == render_version(config)
    for kind in RENDER_ART_KINDS:
        assert render_version_for(kind, movies) == render_version_for(kind, config)


def test_only_the_sections_the_block_names_are_rebuilt():
    config = _with_libraries({
        "Movies": {"maintenance": {"empty_trash": True}},
    })
    movies = config_for_library(config, "Movies")
    assert movies.maintenance.empty_trash is True
    assert movies.maintenance is not config.maintenance
    assert movies.badges is config.badges


def test_the_seam_is_idempotent():
    """Applied twice it answers the same values, because merging the same
    stated leaves onto an already-merged base changes nothing. That is what
    makes it safe for ``process_item`` to resolve once for its gates and for
    ``apply_metadata``/``apply_badges`` to resolve again at their own reads."""
    config = _with_libraries({
        "Movies": {"operations": {"write_to_plex": False},
                   "badges": {"enabled": False}},
    })
    once = config_for_library(config, "Movies")
    twice = config_for_library(once, "Movies")
    assert twice.operations.write_to_plex is False
    assert twice.badges.enabled is False
    assert twice.artwork is config.artwork


def test_a_second_resolve_for_a_different_library_does_not_compose():
    """Roadmap row 92 review, Minor 1.

    ``test_the_seam_is_idempotent`` covers same-library re-entry only; this
    covers the composition that must NOT happen -- resolving a second,
    DIFFERENT library's overrides on top of an already-resolved config.
    Without ``config_for_library`` clearing ``libraries`` on its result,
    this would merge TV Shows' stated leaf over Movies' merged
    ``operations`` instead of the plain global one.
    """
    config = _with_libraries({
        "Movies": {"operations": {"write_to_plex": False}},
        "TV Shows": {"operations": {"enabled": False}},
    })
    movies = config_for_library(config, "Movies")
    composed = config_for_library(movies, "TV Shows")

    assert composed is movies
    assert composed.operations.write_to_plex is False
    assert composed.operations.enabled is True


def test_the_seam_does_no_io_and_takes_no_session():
    """Pure over the validated model, so it is safe to call per item on the
    render path with no transaction in hand."""
    import inspect

    signature = inspect.signature(config_for_library)
    assert list(signature.parameters) == ["config", "library"]
    assert not inspect.iscoroutinefunction(config_for_library)
