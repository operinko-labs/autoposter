import hashlib
import json
from pathlib import Path

import pytest

from autoposter.adopt.__main__ import CONFIG_PATH as ADOPT_CONFIG_PATH
from autoposter.collections.__main__ import CONFIG_PATH as COLLECTIONS_CONFIG_PATH
from autoposter.config.loader import (
    DEFAULT_CONFIG_PATH,
    RENDER_ART_KINDS,
    _shared_render_inputs,
    build_config,
    load_config,
    read_config_document,
    render_version,
    render_version_for,
)
from autoposter.config.schema import ArtworkConfig, Config, Secrets

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

SECRET_NAMES = (
    "DATABASE_URL", "PLEX_TOKEN", "TMDB_TOKEN",
    "TVDB_APIKEY", "FANART_APIKEY", "WEBHOOK_SECRET",
)


def test_example_config_loads():
    cfg = load_config(EXAMPLE)
    assert cfg.assets_root == Path("/assets")
    assert cfg.workers == 5
    assert cfg.providers.order == ["TMDB", "TVDB", "Fanart"]
    assert cfg.artwork.poster.language_order == ["xx", "en", "fi"]
    assert "Muskarit" in cfg.plex.excluded_libraries


def test_poster_text_style_matches_posterizarr():
    style = load_config(EXAMPLE).artwork.poster.text
    assert style.min_point_size == 83
    assert style.max_point_size == 250
    assert style.max_width == 1200
    assert style.max_height == 485
    assert style.text_offset == "+300"
    assert style.all_caps is True
    assert style.add_stroke is False


def test_background_text_is_disabled():
    assert load_config(EXAMPLE).artwork.background.text.add_text is False


def test_title_card_has_two_text_blocks():
    tc = load_config(EXAMPLE).artwork.title_card
    assert tc.text.text_offset == "-400"
    assert tc.episode_text.text_offset == "+100"
    assert tc.season_label == "Season"
    assert tc.episode_label == "Episode"


def test_text_offset_without_sign_is_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        EXAMPLE.read_text(encoding="utf-8").replace('"+300"', '"300"'), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="explicit sign"):
        load_config(bad)


def test_language_order_rejects_bad_codes(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        EXAMPLE.read_text(encoding="utf-8").replace("[xx, en, fi]", "[xx, english]", 1),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="two-letter"):
        load_config(bad)


def test_secrets_come_from_env(monkeypatch):
    for name in SECRET_NAMES:
        monkeypatch.setenv("AUTOPOSTER_" + name, "value-" + name)
    secrets = Secrets.from_env()
    assert secrets.tmdb_token == "value-TMDB_TOKEN"
    assert secrets.database_url == "value-DATABASE_URL"


def test_missing_secret_names_the_variable(monkeypatch):
    for name in SECRET_NAMES:
        monkeypatch.setenv("AUTOPOSTER_" + name, "x")
    monkeypatch.delenv("AUTOPOSTER_TMDB_TOKEN")
    with pytest.raises(RuntimeError, match="AUTOPOSTER_TMDB_TOKEN"):
        Secrets.from_env()


def test_notifications_retry_count_of_zero_is_rejected_at_load(tmp_path):
    """A notifier built from ``retry_count: 0`` would attempt nothing and
    report every send as failed; that has to fail config validation, not
    ship."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        EXAMPLE.read_text(encoding="utf-8").replace(
            "retry_count: 3", "retry_count: 0", 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="retry_count"):
        load_config(bad)


def test_notifications_timeout_of_zero_is_rejected_at_load(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        EXAMPLE.read_text(encoding="utf-8").replace(
            "timeout_seconds: 10 # per-attempt HTTP timeout",
            "timeout_seconds: 0 # per-attempt HTTP timeout",
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="timeout_seconds"):
        load_config(bad)


def test_a_leftover_version_check_section_fails_loudly_at_load(tmp_path):
    """`version_check:` moved out of the schema entirely -- the registry,
    project and repository it held are now derived from AUTOPOSTER_IMAGE_REF
    (see config/image_ref.py). Pydantic ignores unknown keys, so without an
    explicit guard a deployment that forgot to remove this block from its
    YAML would have it silently dropped and believe it still did something."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        EXAMPLE.read_text(encoding="utf-8")
        + "\nversion_check:\n  harbor_url: https://harbor.example\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="version_check"):
        load_config(bad)


def test_imdb_refresh_defaults_from_the_example_config():
    operations = load_config(EXAMPLE).operations
    assert operations.imdb_refresh_hours == 6
    assert operations.imdb_refresh_enabled is True


def test_artwork_modes_round_trips_from_the_example_config():
    modes = load_config(EXAMPLE).artwork_modes
    assert modes.plex_backup_root == Path("/plexbackup")
    # Every Plex-writing mode is dry run by default, the cleanup.apply posture.
    assert modes.restore_apply is False
    assert modes.reset_apply is False
    assert modes.revert_apply is False
    assert modes.logo_apply is False
    assert modes.logo_revert_apply is False
    assert modes.max_changes == 500
    assert modes.max_change_share == 0.25


def test_artwork_modes_defaults_when_the_section_is_absent():
    """Attached via default_factory, so a config document that omits the
    section still gets the full defaults -- the same guarantee every other
    optional section (operations, badges, cleanup, ...) already carries."""
    document = read_config_document(EXAMPLE)
    document.pop("artwork_modes")
    modes = build_config(document).artwork_modes
    assert modes.plex_backup_root == Path("/plexbackup")
    assert modes.max_changes == 500


def test_config_version_changes_with_content(tmp_path):
    a = load_config(EXAMPLE)
    changed = tmp_path / "changed.yaml"
    changed.write_text(
        EXAMPLE.read_text(encoding="utf-8").replace("min_point_size: 83", "min_point_size: 84"),
        encoding="utf-8",
    )
    assert a.version != load_config(changed).version


# --- config.version covers render-affecting settings ONLY --------------------
#
# It is the first component of every render fingerprint, so anything it covers
# invalidates all ~16,000 stored fingerprints when it changes. The documented
# cutover in deploy/README.md has the operator edit `adopt.apply` twice; when
# this was a hash of the raw file bytes, that edit alone re-rendered the whole
# library through the provider ladder.


def _variant(tmp_path, name, old, new):
    path = tmp_path / name
    text = EXAMPLE.read_text(encoding="utf-8")
    assert old in text, f"{old!r} is no longer in the example config"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return load_config(path)


def test_version_is_stable_across_two_loads_of_identical_content(tmp_path):
    copy = tmp_path / "copy.yaml"
    copy.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    assert load_config(EXAMPLE).version == load_config(copy).version


def test_flipping_adopt_apply_does_not_change_the_version(tmp_path):
    """The cutover procedure's own edit must not strand every adopted row."""
    changed = _variant(
        tmp_path, "adopt.yaml",
        "apply: false # dry run by default: produce the report",
        "apply: true # dry run by default: produce the report",
    )
    assert changed.adopt.apply is True
    assert changed.version == load_config(EXAMPLE).version


def test_adding_a_comment_does_not_change_the_version(tmp_path):
    changed = _variant(
        tmp_path, "commented.yaml", "assets_root: /assets",
        "# a note the operator left for themselves\nassets_root: /assets",
    )
    assert changed.version == load_config(EXAMPLE).version


def test_retuning_the_drift_batch_size_does_not_change_the_version(tmp_path):
    changed = _variant(tmp_path, "drift.yaml", "drift_batch_size: 500", "drift_batch_size: 250")
    assert changed.scheduler.drift_batch_size == 250
    assert changed.version == load_config(EXAMPLE).version


def test_changing_an_artwork_setting_does_change_the_version(tmp_path):
    changed = _variant(tmp_path, "artwork.yaml", "output_quality: 92%", "output_quality: 88%")
    assert changed.artwork.output_quality == "88%"
    assert changed.version != load_config(EXAMPLE).version


def test_load_config_and_build_config_are_one_construction_path():
    """The overrides layer builds its ``Config`` from a merged dict rather
    than from the file, so validation and the ``version`` derivation must live
    in a piece both callers share -- not be duplicated into the new path,
    where it could drift and silently start versioning merged configs
    differently from file-only ones."""
    from_file = load_config(EXAMPLE)
    from_document = build_config(read_config_document(EXAMPLE))
    assert from_document.model_dump(mode="json") == from_file.model_dump(mode="json")
    assert from_document.version == from_file.version


def test_default_config_path_is_one_object_across_both_clis():
    """``DEFAULT_CONFIG_PATH`` had already drifted into two independently
    re-spelled copies once (row 114) -- both CLIs now import the constant
    itself rather than re-deriving it from ``AUTOPOSTER_CONFIG``, so a future
    divergence is impossible rather than merely unlikely. ``is``, not ``==``:
    two separately-constructed ``Path`` objects with the same string compare
    equal but are not the same spelling-of-a-spelling this row exists to rule
    out.
    """
    assert ADOPT_CONFIG_PATH is DEFAULT_CONFIG_PATH
    assert COLLECTIONS_CONFIG_PATH is DEFAULT_CONFIG_PATH


def test_changing_an_asset_root_does_change_the_version(tmp_path):
    changed = _variant(
        tmp_path, "roots.yaml",
        "overlays_root: /app/assets/overlays",
        "overlays_root: /app/assets/overlays-v2",
    )
    assert changed.version != load_config(EXAMPLE).version


# --- the per-art-kind render version (roadmap row 111) -----------------------
#
# `config.version` above stays the WHOLESALE hash and every assertion above
# still holds: `render_version_for` is a derived function beside it, not a
# replacement. What it buys is confinement -- an operator retuning
# `artwork.season_poster.text.max_point_size` moves one kind's version and
# leaves the other three byte-identical, so the ~16,000-row storm shrinks to
# the season posters the edit actually reaches.
#
# The table below IS the partition. It is exhaustive over `ArtworkConfig` by
# assertion, not by hope: `test_the_partition_table_covers_every_artwork_field`
# fails the moment a field is added to the model without a ruling here, which
# is the failure mode that would otherwise ship a field silently confined to
# nothing.

_ALL_KINDS = frozenset(RENDER_ART_KINDS)


def _moved(mutate) -> set[str]:
    """Which kinds' versions a mutation of the example config moves.

    Two independently loaded configs rather than one deep-copied model: a copy
    that shared a nested object with its source would report "nothing moved"
    for every mutation and the whole file would pass vacuously.
    """
    before = load_config(EXAMPLE)
    after = load_config(EXAMPLE)
    mutate(after)
    return {
        kind for kind in RENDER_ART_KINDS
        if render_version_for(kind, before) != render_version_for(kind, after)
    }


# field name -> (a mutation away from the example's value, the kinds it must move)
_PARTITION = {
    # Each kind's own `artwork.<kind>` subsection, confined to that kind.
    #
    # ONE exception, deliberate, since roadmap row 78: the key
    # `artwork.title_card.season_name_overrides` also decides what a SEASON
    # POSTER draws (render/pipeline.py's title_text_for), so
    # `render_version_for` projects it into that kind's payload and an edit to
    # it moves TWO kinds. This table cannot express that -- its mutations are
    # keyed by ArtworkConfig field name and that key lives one level down, on
    # TitleCardConfig -- so the pin lives at
    # tests/test_season_show_title.py::test_a_season_name_override_moves_the_season_posters_version_too.
    # The `title_card` entry below mutates `season_label`, which IS confined,
    # so this table's own answers stay correct.
    "poster": (lambda c: setattr(c.artwork.poster, "border_width", 31), {"poster"}),
    "season_poster": (lambda c: setattr(c.artwork.season_poster, "add_border", True), {"season_poster"}),
    "background": (lambda c: setattr(c.artwork.background, "overlay_file", "other-overlay.png"), {"background"}),
    "title_card": (lambda c: setattr(c.artwork.title_card, "season_label", "Kausi"), {"title_card"}),
    # The five logo fields. There is no `logo` art kind and no renders row for
    # one; a logo is an ingredient of POSTERS only, gated at
    # render/pipeline.py:1202's `if art_kind == "poster" and use_logo`.
    "use_logo": (lambda c: setattr(c.artwork, "use_logo", False), {"poster"}),
    "logo_language_order": (lambda c: setattr(c.artwork, "logo_language_order", ["fi"]), {"poster"}),
    "logo_text_fallback": (lambda c: setattr(c.artwork, "logo_text_fallback", True), {"poster"}),
    "use_clearart": (lambda c: setattr(c.artwork, "use_clearart", True), {"poster"}),
    "logo_flat_color": (lambda c: setattr(c.artwork, "logo_flat_color", "#ffffff"), {"poster"}),
    # Names a template file for a season poster or a title card and nothing
    # else (render/pipeline.py:242, :258).
    "season_episode_templates": (
        lambda c: setattr(c.artwork, "season_episode_templates", True),
        {"season_poster", "title_card"},
    ),
    # Genuinely global: read on every kind's path.
    "use_original_title": (lambda c: setattr(c.artwork, "use_original_title", True), _ALL_KINDS),
    "disable_online_asset_fetch": (
        lambda c: setattr(c.artwork, "disable_online_asset_fetch", True), _ALL_KINDS,
    ),
    # `build_base_argv` (render/pipeline.py:855) runs for every kind and
    # `build_text_argv` (:905) for every kind that draws text, so this is the
    # one shared field a text-drawing kind reads -- and it reaches all four.
    # The recon's field table omits it; it is here because the compositor's
    # own readers put it there.
    "output_quality": (lambda c: setattr(c.artwork, "output_quality", "88%"), _ALL_KINDS),
}

# The one field the table cannot rule on with a single mutation, because WHICH
# kinds it moves depends on what the operator wrote inside it. It has its own
# test below.
_PROJECTED_FIELDS = {"library_language_overrides"}

# The mutation half of `_PROJECTED_FIELDS`, kept separately because "which
# kinds move" isn't a single answer for this field -- see
# `test_a_library_language_override_moves_only_the_kind_it_names`. Reused by
# the superset test below (M3) so that test's SUPERSET claim isn't checked
# for `_PARTITION` alone.
_PROJECTED_MUTATIONS = {
    "library_language_overrides": lambda c: setattr(
        c.artwork, "library_language_overrides", {"Movies": {"title_card": ["fi"]}}
    ),
}

# Global render inputs that do not live under `artwork` at all. They decide
# where every kind reads its inputs and writes its output (render/naming.py:105),
# and scheduler/jobs.py:792,818 already treats a root repoint as a
# library-wide event -- confining them would be a real bug.
_GLOBAL_INPUTS = {
    "assets_root": lambda c: setattr(c, "assets_root", Path("/assets-v2")),
    "manual_assets_root": lambda c: setattr(c, "manual_assets_root", Path("/manual-v2")),
    "fonts_root": lambda c: setattr(c, "fonts_root", Path("/fonts-v2")),
    "overlays_root": lambda c: setattr(c, "overlays_root", Path("/overlays-v2")),
    "library_folders": lambda c: setattr(c, "library_folders", False),
}


def test_the_partition_table_covers_every_artwork_field():
    """Exhaustiveness, by assertion rather than by review.

    A field added to `ArtworkConfig` with no ruling here would otherwise be
    silently confined to nothing: it would enter no kind's payload, an
    operator's edit to it would move no version, and the library would keep
    serving art the config no longer describes. This test is the alarm.
    """
    assert set(_PARTITION) | _PROJECTED_FIELDS == set(ArtworkConfig.model_fields)


@pytest.mark.parametrize("field", sorted(_PARTITION))
def test_an_artwork_field_moves_exactly_the_kinds_the_partition_says(field):
    """Exactly -- both directions in one assertion.

    An over-wide payload (every field in every kind) passes a "did it move"
    test and delivers nothing; an under-wide one (a field in no kind) passes a
    "did the others hold still" test and strands the library.
    """
    mutate, expected = _PARTITION[field]
    assert _moved(mutate) == set(expected)


def test_a_library_language_override_moves_only_the_kind_it_names():
    """`artwork.library_language_overrides` is already keyed by art kind
    (render/pipeline.py:160, validator config/schema.py:689-695), so it is
    PROJECTED into each kind's payload rather than included wholesale. An
    override naming `title_card` for one library must not move a poster."""
    def mutate(config: Config) -> None:
        config.artwork.library_language_overrides = {"Movies": {"title_card": ["fi"]}}

    assert _moved(mutate) == {"title_card"}


@pytest.mark.parametrize("name", sorted(_GLOBAL_INPUTS))
def test_a_global_render_input_moves_every_kind(name):
    """The shared block is a literal member of all four payloads, so these
    edits keep costing the whole library -- and that is correct, not a
    failure of the partition."""
    assert _moved(_GLOBAL_INPUTS[name]) == _ALL_KINDS


def test_render_version_reconstructs_from_shared_inputs_and_wholesale_artwork():
    """I1's drift guard.

    `_shared_render_inputs` hand-duplicates `render_version`'s non-artwork
    inputs (the four roots, `library_folders`) as a literal list of five
    names. Nothing before this test pinned the two together: a root added to
    `render_version`'s own `relevant` dict but missed in
    `_shared_render_inputs` would leave every existing test green (the
    wholesale hash still moves, `ArtworkConfig.model_fields` is unchanged, and
    `_GLOBAL_INPUTS` above never names the new root) while no kind's version
    moved for that edit -- silent under-invalidation.

    This reconstructs `render_version`'s exact payload from
    `_shared_render_inputs` plus the wholesale `artwork` dump, using the
    identical json.dumps/hash recipe `render_version` uses, and checks it
    against the real thing. `_shared_render_inputs`'s three `artwork.*`
    projected keys are dropped from the reconstruction first -- they are
    already inside the wholesale `artwork` dump, so keeping them would double
    them up rather than reconstruct anything. If a root is ever added to
    `render_version` without a matching addition to `_shared_render_inputs`,
    the reconstructed payload is missing that root's key and this fails.
    """
    def reconstruct(config: Config) -> str:
        shared = _shared_render_inputs(config)
        payload_from_shared = {
            key: value for key, value in shared.items() if not key.startswith("artwork.")
        }
        payload_from_shared["artwork"] = config.artwork.model_dump(mode="json")
        payload = json.dumps(payload_from_shared, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    example = load_config(EXAMPLE)
    assert reconstruct(example) == render_version(example)

    every_root_and_library_folders_changed = load_config(EXAMPLE)
    for mutate in _GLOBAL_INPUTS.values():
        mutate(every_root_and_library_folders_changed)
    assert reconstruct(every_root_and_library_folders_changed) == render_version(
        every_root_and_library_folders_changed
    )


def test_render_version_for_is_stable_across_two_loads_of_identical_content(tmp_path):
    """C7's no-behaviour-change guard. If any kind's value were derived from
    anything but the validated model -- a dict iteration order, a `str(Path)`
    that differed by platform, an `id()` -- every fingerprint in the library
    would move on a restart that changed nothing."""
    copy = tmp_path / "copy.yaml"
    copy.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    a, b = load_config(EXAMPLE), load_config(copy)
    for kind in RENDER_ART_KINDS:
        assert render_version_for(kind, a) == render_version_for(kind, b), kind


# The four values the shipped example config hashes to, captured from the tree
# in the test container. Linux path spelling: `_shared_render_inputs` dumps the
# roots through `str(Path)`, which is what the pod and CI compute.
EXAMPLE_PER_KIND_VERSIONS = {
    "poster": "4ac64b5874ce0ff3",
    # Moved TWICE, on purpose, by roadmap row 78: artwork.season_poster gained
    # its show_title block, and render_version_for then gained a second,
    # unconditional projection of artwork.title_card.season_name_overrides
    # into this kind's payload (row 43's gap, co-delivered). The other three
    # did not move -- that is the partition doing its job, and
    # tests/test_season_show_title.py pins them against the values measured
    # before the key existed.
    "season_poster": "3ac29ec40facbe8c",
    "background": "9ae9ab3b95ae68ec",
    "title_card": "31f00cfe0ef31fba",
}


def test_the_example_config_s_four_per_kind_versions_are_pinned_literally():
    """The one ABSOLUTE pin in a file of relative ones.

    Every other test here says "this kind moved, that one did not", which is
    blind to a change that moves all four the same way -- a payload key
    renamed, a field dumped in a new shape, a root spelled differently. Any
    of those re-renders a whole library on deploy while every relative pin
    stays green. When one of these moves on purpose (an `artwork` field added
    is the ordinary case, and it moves production the same way), the commit
    that moves it re-captures it and says why.
    """
    config = load_config(EXAMPLE)
    assert {
        kind: render_version_for(kind, config) for kind in RENDER_ART_KINDS
    } == EXAMPLE_PER_KIND_VERSIONS


def test_a_non_render_edit_moves_no_kind_s_version():
    """The same posture `config.version` has held since it stopped being a
    hash of the file's bytes: a cadence tweak cannot change a pixel."""
    assert _moved(lambda c: setattr(c.scheduler, "drift_batch_size", 250)) == set()


def test_two_kinds_with_identical_settings_still_get_distinct_versions():
    """The art kind's own name is in the payload, deliberately.

    `art_kind` is already fingerprint element 1, so this is belt-and-braces
    rather than load-bearing -- but it means the four values are readable as
    four in a log, and a future kind whose defaults happen to match another's
    cannot silently share a version.
    """
    config = load_config(EXAMPLE)
    config.artwork.background = config.artwork.poster.model_copy(deep=True)
    assert render_version_for("poster", config) != render_version_for("background", config)


def test_render_version_for_refuses_an_unknown_art_kind():
    """Named by the kind, so a caller that grew a fifth artifact type without
    a partition ruling is told which one -- rather than getting a payload
    silently missing its subsection through a getattr default."""
    with pytest.raises(ValueError) as excinfo:
        render_version_for("logo", load_config(EXAMPLE))
    assert "'logo'" in str(excinfo.value)


def test_the_wholesale_version_still_moves_for_every_partitioned_edit():
    """C1, pinned rather than assumed.

    `config.version` stays the wholesale hash and stays a strict SUPERSET of
    every per-kind payload -- which is what lets `_render_affecting` keep its
    cheap short-circuit in Task 2 (`if after.version == before.version:
    return False`). If an edit could move a kind's version without moving the
    wholesale one, that short-circuit would swallow a real re-render.

    M3: folded in are the projected field (`_PROJECTED_MUTATIONS`,
    `library_language_overrides`) and the five `_GLOBAL_INPUTS` roots -- both
    hold the same SUPERSET claim, and Task 2's short-circuit would swallow a
    real re-render just as badly if either stopped holding.
    """
    mutations: dict = dict(_PARTITION)
    mutations.update((field, (mutate, None)) for field, mutate in _PROJECTED_MUTATIONS.items())
    mutations.update((name, (mutate, None)) for name, mutate in _GLOBAL_INPUTS.items())
    for field, (mutate, _expected) in mutations.items():
        before = load_config(EXAMPLE)
        after = load_config(EXAMPLE)
        mutate(after)
        after.version = render_version(after)
        assert after.version != before.version, field
