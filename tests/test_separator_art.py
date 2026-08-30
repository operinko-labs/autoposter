"""Generated divider art: caption our exact titles onto upstream's textless
``@base`` layers. The no-magick paths (caching, fetch failure, wiring) run
everywhere; byte determinism is graded under ``@pytest.mark.imagemagick`` in
the compose image, whose ImageMagick is the deployment's own."""
import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

from autoposter.collections import separator_art


def _png_bytes(size=(20, 30), color="navy") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpg_bytes(size=(20, 30), color="olive") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG")
    return buffer.getvalue()


def _config(tmp_path, config_factory):
    return config_factory(assets_root=str(tmp_path))


def test_the_vendored_face_is_the_one_the_verification_used():
    """The face is upstream's, TRANSCRIBED, and these are the exact bytes that
    reproduced upstream's own ``separators/orig/genre.jpg`` to within JPEG
    noise (``.superpowers/sdd/p-div-font.md`` §1/§4). Any other copy of
    Comfortaa -- Google's variable ``Comfortaa[wght].ttf`` instanced down, a
    different release -- throws that verification away, and under OFL's
    Reserved Font Name clause an instanced copy is a Modified Version besides.
    """
    import hashlib

    assert separator_art.FONT.is_file()
    assert (
        hashlib.sha256(separator_art.FONT.read_bytes()).hexdigest()
        == "992f89f3c26be37ccebf784b294d36f40b96ed96ad9a3cc1396f4d389fc69d0c"
    )
    # OFL 1.1 §2 requires the licence to travel with the font; neither Kometa
    # repository ships one beside the .ttf, so we vendor it ourselves.
    assert (separator_art.FONT.parent / "OFL.txt").is_file()


def test_the_text_block_is_upstreams_transcribed_parameters():
    """p-div-font.md §2: box 1900x1000, clamp [100, 203], all caps, #FFFFFF,
    centred at +0. The clamp matters more than it looks -- ``caption:``
    word-wraps, so every real divider label fits at 243 and is CLAMPED to 203;
    a ``max_point_size`` of 200 would render every divider 1.5% small against
    upstream's own art for no reason."""
    style = separator_art.TEXT
    assert (style.max_width, style.max_height) == (1900, 1000)
    assert (style.min_point_size, style.max_point_size) == (100, 203)
    assert style.all_caps is True
    assert style.font_color == "#FFFFFF"
    assert (style.gravity, style.text_offset) == ("center", "+0")


async def test_a_cached_render_is_returned_without_network_or_magick(
    tmp_path, config_factory
):
    """The cache IS the runtime contract: once rendered, a divider's art is a
    file read -- no fetch, no magick, and a magick upgrade cannot re-render
    art that already exists (the determinism caveat's mitigation)."""
    config = _config(tmp_path, config_factory)
    cached = tmp_path / ".generated" / "separators" / "orig" / "content.jpg"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(_jpg_bytes())

    async def handler(request):
        raise AssertionError("a cached render must not fetch")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await separator_art.ensure_separator_art(
            config, http, "orig", "content", "Content Collections"
        )
    assert result == cached


async def test_an_unfetchable_base_layer_yields_none_before_any_magick(
    tmp_path, config_factory
):
    """Fetch first, magick second -- deliberately. An environment where the
    layer cannot be fetched (the golden harness 404s it on purpose) degrades
    to None -> 'no poster source', without ever needing a magick binary."""
    config = _config(tmp_path, config_factory)

    async def handler(request):
        assert "separators/@base/orig.png" in str(request.url)
        return httpx.Response(404, text="not found")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await separator_art.ensure_separator_art(
            config, http, "orig", "content", "Content Collections"
        )
    assert result is None
    assert not (tmp_path / ".generated" / "separators" / "orig" / "content.jpg").exists()


async def test_no_http_client_yields_none(tmp_path, config_factory):
    config = _config(tmp_path, config_factory)
    assert await separator_art.ensure_separator_art(
        config, None, "orig", "content", "Content Collections"
    ) is None


async def test_the_base_layer_is_fetched_once_and_reused(tmp_path, config_factory,
                                                         monkeypatch):
    """One @base fetch per style, however many groups render from it. The
    render itself is stubbed out -- what this grades is the fetch/cache
    seam, which needs no magick."""
    config = _config(tmp_path, config_factory)
    fetched = []

    async def handler(request):
        fetched.append(str(request.url))
        return httpx.Response(200, content=_png_bytes())

    def fake_render(magick, base, target, title):
        target.write_bytes(_jpg_bytes())

    monkeypatch.setattr(separator_art, "_render", fake_render)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        one = await separator_art.ensure_separator_art(
            config, http, "sand", "content", "Content Collections"
        )
        two = await separator_art.ensure_separator_art(
            config, http, "sand", "media", "Media Collections"
        )
    assert one and two and one != two
    assert fetched == [
        "https://raw.githubusercontent.com/Kometa-Team/Default-Images/master"
        "/separators/@base/sand.png"
    ]


async def test_a_render_failure_is_contained_and_leaves_no_half_file(
    tmp_path, config_factory, monkeypatch
):
    config = _config(tmp_path, config_factory)

    async def handler(request):
        return httpx.Response(200, content=_png_bytes())

    def broken_render(magick, base, target, title):
        target.write_bytes(b"half")
        raise RuntimeError("magick failed (1): boom")

    monkeypatch.setattr(separator_art, "_render", broken_render)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await separator_art.ensure_separator_art(
            config, http, "orig", "content", "Content Collections"
        )
    assert result is None
    assert not (tmp_path / ".generated" / "separators" / "orig" / "content.jpg").exists()


async def test_the_cache_is_not_the_operator_override_layout(tmp_path, config_factory,
                                                             monkeypatch):
    """Generated art lives under ``<assets_root>/.generated/separators/`` and
    NOT at ``<library>/<title>/poster.jpg``, so it can never masquerade as a
    hand-placed override and clobbering one is structurally impossible --
    ``local_poster_path`` still wins over it in ``apply_poster``."""
    config = _config(tmp_path, config_factory)

    async def handler(request):
        return httpx.Response(200, content=_png_bytes())

    def fake_render(magick, base, target, title):
        target.write_bytes(_jpg_bytes())

    monkeypatch.setattr(separator_art, "_render", fake_render)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        made = await separator_art.ensure_separator_art(
            config, http, "orig", "content", "Content Collections"
        )

    assert made == tmp_path / ".generated" / "separators" / "orig" / "content.jpg"
    assert not (tmp_path / "Movies").exists()


@pytest.mark.imagemagick("hdri")
async def test_generation_is_deterministic_for_the_same_inputs(
    tmp_path, config_factory
):
    """Same base, same font, same title, same build -> same bytes: the
    property ``apply_poster``'s sha-compare turns into 'an unchanged pass
    uploads nothing'. The base layer is a magick-drawn flat card, seeded into
    the cache so no network is touched; across ImageMagick BUILDS the bytes
    may differ, which the runtime cache absorbs (the test above) and a cache
    loss costs one re-upload that then settles."""
    import hashlib
    import subprocess

    config = _config(tmp_path, config_factory)
    base_dir = tmp_path / ".generated" / "separators" / "@base"
    base_dir.mkdir(parents=True)
    subprocess.run(
        ["magick", "-size", "2000x3000", "xc:navy", str(base_dir / "orig.png")],
        check=True,
    )

    digests = []
    for _ in range(2):
        target = tmp_path / ".generated" / "separators" / "orig" / "content.jpg"
        if target.exists():
            target.unlink()
        result = await separator_art.ensure_separator_art(
            config, None, "orig", "content", "Content Collections"
        )
        assert result == target
        digests.append(hashlib.sha256(target.read_bytes()).hexdigest())
    assert digests[0] == digests[1]


@pytest.mark.imagemagick("hdri")
async def test_the_render_is_the_2000x3000_base_with_the_title_on_it(
    tmp_path, config_factory
):
    """Not merely 'some bytes appeared': the output keeps the base layer's
    2000x3000 canvas (upstream's own separator dimensions, p-div-font.md §3)
    and is not a copy of the blank base -- the caption is actually drawn."""
    import subprocess

    config = _config(tmp_path, config_factory)
    base = tmp_path / ".generated" / "separators" / "@base" / "orig.png"
    base.parent.mkdir(parents=True)
    subprocess.run(
        ["magick", "-size", "2000x3000", "xc:navy", str(base)], check=True
    )

    made = await separator_art.ensure_separator_art(
        config, None, "orig", "operator", "Collections"
    )
    assert made is not None
    with Image.open(made) as image:
        assert image.size == (2000, 3000)
        assert image.convert("L").getextrema()[1] > 200  # white caption pixels

    blank = tmp_path / "blank.jpg"
    subprocess.run(
        ["magick", str(base), "-strip", "-quality", "100", str(blank)], check=True
    )
    assert made.read_bytes() != blank.read_bytes()


@pytest.mark.imagemagick("hdri")
async def test_a_title_that_cannot_be_drawn_legibly_is_refused_not_rendered(
    tmp_path, config_factory, monkeypatch
):
    """Our one deliberate divergence from upstream, pinned so it stays
    deliberate: upstream clamps up to ``min 100`` and logs "Text is too small
    and will be truncated", writing the poster anyway. We refuse instead and
    report no poster source, which is ``fit_point_size``'s stated contract in
    this codebase and the same rule ``render/`` already follows -- illegible
    artwork is worse than none, because it still gets hashed, so a later pass
    would never retry it.

    Unreachable with the shipped constants -- ``separator_title`` only ever
    produces "<Key Name> Collections", every one of which fits at 243 and
    clamps DOWN to 203 -- so the floor is raised here instead of feeding the
    fitter a pathological title. (Which is also the kind thing to do: a
    2000-character caption makes ImageMagick's auto-fit search take minutes.)
    The real fitter still runs; only the range it clamps against moves.
    """
    import subprocess

    config = _config(tmp_path, config_factory)
    base = tmp_path / ".generated" / "separators" / "@base" / "orig.png"
    base.parent.mkdir(parents=True)
    subprocess.run(
        ["magick", "-size", "2000x3000", "xc:navy", str(base)], check=True
    )
    monkeypatch.setattr(
        separator_art, "TEXT",
        separator_art.TEXT.model_copy(
            update={"min_point_size": 500, "max_point_size": 10000}
        ),
    )

    result = await separator_art.ensure_separator_art(
        config, None, "orig", "operator", "Collections"
    )
    assert result is None
    assert not (tmp_path / ".generated" / "separators" / "orig" / "operator.jpg").exists()
    # And no working file is left behind either.
    assert list((tmp_path / ".generated" / "separators" / "orig").glob("*")) == []


def test_the_module_reads_its_cache_root_off_the_configured_assets_root(tmp_path,
                                                                        config_factory):
    config = _config(tmp_path, config_factory)
    assert separator_art._cache_root(config) == Path(tmp_path) / ".generated" / "separators"
