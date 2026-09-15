"""Roadmap row 219 -- the four orphan Posterizarr-parity keys, removed.

``ProvidersConfig.favourite``, ``ProvidersConfig.tmdb_vote_sorting``,
``ArtKindConfig.min_width`` and ``ArtKindConfig.min_height`` were day-one
scaffold (``c62daaa``) accepted for Posterizarr-config compatibility. Nothing
in ``src/`` ever read any of them. The row asked wire-or-remove and the ruling
(2026-09-08) is REMOVE all four.

Three proofs, and the third is the expensive one. The row's own premise --
that removing a field rejects an operator config still carrying it -- is
FALSE: nothing in ``config/schema.py`` sets ``model_config``, so pydantic's
default applies and an unknown key is silently dropped
(``schema.py``'s own note above ``_REFUSED_PLAYLIST_FIELDS`` says so). What
removal DOES cost is the render fingerprint: ``config/loader.py:110-117``'s
``render_version`` hashes ``config.artwork.model_dump(mode="json")``
WHOLESALE, and ``render_version_for`` (``loader.py:241-244``) hashes each
kind's whole ``ArtKindConfig`` dump -- and ``TitleCardConfig``
(``schema.py:523``) and ``SeasonPosterConfig`` (``schema.py:737``) both
subclass it, so all four art kinds carry the two removed fields. Removing them
moves all four per-kind fingerprints and the wholesale version, and the next
pass re-renders the whole library: every one of the ~16,000 moved fingerprints re-composites
and re-uploads. The ~3.5 h figure was measured on a pass with zero composites, so the real
pass is bounded by ``magick`` and ~3.5 h is a floor, not an estimate. The user accepted that
cost explicitly.

So the third test is the HONEST INVERSE of the usual pin: instead of proving a
fingerprint did not move, it proves it DID, against the exact literals the
tree carried before the removal. Nothing in this file invokes ``magick``, so
nothing here carries ``@pytest.mark.imagemagick`` (#149).
"""
from pathlib import Path

import pytest

from autoposter.config.loader import (
    RENDER_ART_KINDS, build_config, load_config, read_config_document,
    render_version, render_version_for,
)
from autoposter.config.overrides import MIGRATED_SETTINGS
from autoposter.config.schema import ArtKindConfig, ProvidersConfig

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"

# The four per-kind render versions and the wholesale one that the shipped
# example config produced at 997836e, BEFORE this row removed
# ArtKindConfig.min_width/min_height. Not re-measured for this file: they are
# read off the absolute pins the removal turns red --
# tests/test_config.py::EXAMPLE_PER_KIND_VERSIONS and
# tests/test_library_overrides.py's two pinned dicts -- which is what makes
# them a genuine before-value rather than a value this file chose.
PRE_ROW_219_PER_KIND = {
    "poster": "4ac64b5874ce0ff3",
    "background": "9ae9ab3b95ae68ec",
    "title_card": "31f00cfe0ef31fba",
    "season_poster": "14f86f656d6fa935",
}
PRE_ROW_219_WHOLESALE = "386ea7cf4844f52e"

# The four keys, as (model, field name) and as the dotted paths an operator
# would have written them at.
REMOVED_FIELDS = [
    (ArtKindConfig, "min_width"),
    (ArtKindConfig, "min_height"),
    (ProvidersConfig, "favourite"),
    (ProvidersConfig, "tmdb_vote_sorting"),
]


@pytest.mark.parametrize("model, field", REMOVED_FIELDS)
def test_the_four_orphan_keys_are_no_longer_schema_fields(model, field):
    """Asserted against ``model_fields`` rather than ``hasattr`` on an
    instance: a pydantic model answers ``hasattr`` for anything the class body
    happens to define, while ``model_fields`` is exactly the set the config
    document can address and the set ``config/descriptions.py`` walks onto the
    Settings page."""
    assert field not in model.model_fields, (
        f"{model.__name__}.{field} is still a schema field -- roadmap row 219 "
        "removed all four orphan Posterizarr-parity keys"
    )


def test_a_document_still_carrying_all_four_keys_loads_and_drops_them():
    """Proved rather than asserted in prose.

    The row's premise was that removing these fields would reject any operator
    config still carrying them -- "a breaking parse error". It does not.
    Nothing in ``config/schema.py`` sets ``model_config``, so an unknown key is
    dropped silently and a deployment whose mounted YAML still names all four
    boots byte-identically to one that does not. That posture is deliberate
    (``config/overrides.py``'s ``unknown_key_paths`` docstring: switching the
    schema to ``extra="forbid"`` would make a stray key fail a boot, "a much
    worse failure than the one being fixed").

    The removed keys are also absent from the round-tripped dump, which is the
    half a load-succeeds assertion cannot prove: a config that still HELD them
    would load just as cleanly.
    """
    document = read_config_document(EXAMPLE)
    document.setdefault("providers", {})["favourite"] = "TMDB"
    document["providers"]["tmdb_vote_sorting"] = "vote_average"
    for art_kind in RENDER_ART_KINDS:
        document.setdefault("artwork", {}).setdefault(art_kind, {})["min_width"] = 1000
        document["artwork"][art_kind]["min_height"] = 1500

    config = build_config(document)

    assert "favourite" not in config.providers.model_dump(mode="json")
    assert "tmdb_vote_sorting" not in config.providers.model_dump(mode="json")
    artwork_json = config.artwork.model_dump(mode="json")
    for art_kind in RENDER_ART_KINDS:
        assert "min_width" not in artwork_json[art_kind], art_kind
        assert "min_height" not in artwork_json[art_kind], art_kind


def test_every_position_the_four_keys_lived_at_is_a_stale_stored_path():
    """The half this file's second test proved harmless for the FILE and that
    turned out not to be harmless for the STORE, met on a v0.4.0 deployment.

    A mounted file still naming these keys seeds them into the stored
    document, where they are not silently dropped: every whole-document write
    walks ``config/overrides.py``'s ``unknown_key_paths`` first and answers
    422 ``unknown setting``, and the editor cannot take out a key it never
    renders -- so the Settings page, the library map and the server cards were
    all blocked at once. ``MIGRATED_SETTINGS`` is what the read seam drops, so
    it has to name every position the schema once accepted these four at, and
    ``TitleCardConfig`` and ``SeasonPosterConfig`` subclassing
    ``ArtKindConfig`` is what makes that all four art kinds rather than two.
    """
    expected = {"providers.favourite", "providers.tmdb_vote_sorting"}
    for art_kind in RENDER_ART_KINDS:
        expected |= {f"artwork.{art_kind}.min_width", f"artwork.{art_kind}.min_height"}

    assert set(MIGRATED_SETTINGS) == expected


def test_removing_the_two_min_keys_moved_every_render_fingerprint():
    """The storm, pinned as a MOVE rather than hidden.

    Every other absolute pin in this suite says "this value did not change".
    This one says the opposite on purpose, because the opposite is what
    shipped: ``min_width``/``min_height`` lived on ``ArtKindConfig``, which all
    four render art kinds are or subclass, and both ``render_version`` and
    ``render_version_for`` dump that model wholesale. So every fingerprint
    moved, every stored render is stale, and the next pass re-renders the whole
    library. The user accepted that in the ruling; this test is what stops a
    later reader from believing it was free, and what would go red if someone
    "restored" the fields to make the older pins green again.

    The wholesale value moves too. Since roadmap row 111 it is no longer a
    component of any render fingerprint -- it is the config editor's "version A
    to B" line and ``api/routes._render_affecting``'s superset short-circuit --
    so it is pinned here for the disclosure, not for the re-render.
    """
    config = load_config(EXAMPLE)

    for art_kind in RENDER_ART_KINDS:
        assert render_version_for(art_kind, config) != PRE_ROW_219_PER_KIND[art_kind], (
            f"{art_kind}'s render version is unchanged -- either the two "
            "min_* fields are back on ArtKindConfig, or render_version_for "
            "stopped dumping the art kind's model wholesale"
        )

    assert render_version(config) != PRE_ROW_219_WHOLESALE
