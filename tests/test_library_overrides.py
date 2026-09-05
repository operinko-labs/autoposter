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

from autoposter.config.loader import (
    RENDER_ART_KINDS,
    _shared_render_inputs,
    render_version,
    render_version_for,
)

SRC = Path(__file__).parent.parent / "src" / "autoposter"

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
