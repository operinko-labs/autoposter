"""Which upstream file, if any, is a collection's default poster.

The table this pins is DATA, transcribed from the live listings recorded in
``.superpowers/sdd/p-defimg-probe.md`` -- one sample per key scheme, cited by
that file's own section names. Nothing here reaches the network: the fetch
tests drive ``httpx.MockTransport``.
"""
import httpx

from autoposter.collections.default_images import (
    FAMILIES,
    candidate_urls,
    default_image_url,
    ensure_default_image,
)

BASE = "https://raw.githubusercontent.com/Kometa-Team/Default-Images/master"


def test_the_flat_display_name_families_key_on_the_name_verbatim():
    """p-defimg-probe.md §1 (`franchise/Jurassic Park.jpg`), §5 `genre/`
    (`Action.jpg`) and §5 `studio/` (`101 Studios.jpg`): the exact display
    name is the filename, spaces and punctuation kept literal."""
    assert default_image_url("franchise", "Jurassic Park") == (
        BASE + "/franchise/Jurassic%20Park.jpg"
    )
    assert default_image_url("genre", "Action") == BASE + "/genre/Action.jpg"
    assert default_image_url("studio", "101 Studios") == (
        BASE + "/studio/101%20Studios.jpg"
    )


def test_the_pattern_b_families_read_the_color_rendering_not_the_bare_dir():
    """p-defimg-probe.md §4 Pattern B: `network`, `country` and `streaming`
    keep nothing at their base but an index image -- the full poster is the
    `color/` rendering, which is the path the shipped `chart` kind already
    uses."""
    assert default_image_url("network", "A&E") == BASE + "/network/color/A%26E.jpg"
    assert default_image_url("country", "Australia and New Zealand") == (
        BASE + "/country/color/Australia%20and%20New%20Zealand.jpg"
    )
    assert default_image_url("streaming", "Netflix") == (
        BASE + "/streaming/color/Netflix.jpg"
    )


def test_the_code_keyed_families_key_on_lowercase_codes_and_numbers():
    """p-defimg-probe.md §2/§3 (ISO codes, lowercase), §5 `decade/`
    (decade number), §5 `resolution/` (`4k.jpg`), §5 `year/`, §5 `universe/`
    (short lowercase code)."""
    assert default_image_url("audio_language", "fi") == BASE + "/audio_language/fi.jpg"
    assert default_image_url("subtitle_language", "fil") == (
        BASE + "/subtitle_language/fil.jpg"
    )
    assert default_image_url("decade", "1980") == BASE + "/decade/1980.jpg"
    assert default_image_url("resolution", "4k") == BASE + "/resolution/4k.jpg"
    assert default_image_url("year", "1999") == BASE + "/year/1999.jpg"
    assert default_image_url("universe", "mcu") == BASE + "/universe/mcu.jpg"


def test_an_unknown_family_or_an_empty_key_has_no_url():
    """The `hosted_poster_url` rule, kept: an unrecognised family returns None
    rather than guessing, because a wrong URL 404s quietly."""
    assert default_image_url("playlist", "Arrowverse") is None
    assert default_image_url("franchise", "") is None
    assert candidate_urls("playlist", "Arrowverse") == ()


def test_the_franchise_variant_ladder_tries_the_exact_name_first():
    """Our franchise titles are TMDb's, with the pack's ' Collection' suffix
    already stripped; upstream's are Kometa's own bucket names, and
    p-defimg-probe.md §1 shows three shapes ours can differ by --
    `Mission Impossible.jpg` for TMDb's 'Mission: Impossible',
    `Alien Predator.jpg` for our 'Alien / Predator', and a bare
    `Alien.jpg`/`Batman.jpg` for names ours may decorate. Exact first: a
    variant must never shadow a real hit."""
    urls = candidate_urls("franchise", "Mission: Impossible")
    assert urls[0] == BASE + "/franchise/Mission%3A%20Impossible.jpg"
    assert BASE + "/franchise/Mission%20Impossible.jpg" in urls

    slashed = candidate_urls("franchise", "Alien / Predator")
    assert slashed[0] == BASE + "/franchise/Alien%20%2F%20Predator.jpg"
    assert BASE + "/franchise/Alien%20Predator.jpg" in slashed

    decorated = candidate_urls("franchise", "Batman Collection")
    assert BASE + "/franchise/Batman.jpg" in decorated


def test_the_code_keyed_families_get_no_variant_ladder():
    """A code is a code: 'fi-FI' is not 'fi' by any documented rule upstream
    publishes, and inventing one here would fetch a plausible wrong flag."""
    assert candidate_urls("audio_language", "fi-FI") == (
        BASE + "/audio_language/fi-FI.jpg",
    )
    assert candidate_urls("decade", "1980") == (BASE + "/decade/1980.jpg",)


async def test_a_hit_is_written_to_the_generated_cache_and_reused(
    tmp_path, config_factory, jpeg_bytes
):
    """The separator-art precedent: `<assets_root>/.generated/` -- deliberately
    NOT the operator-override layout, so a fetched default can never masquerade
    as a hand-placed file."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    calls = []

    async def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=jpeg_bytes)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        first = await ensure_default_image(config, http, "genre", "Action")
        second = await ensure_default_image(config, http, "genre", "Action")

    assert first == tmp_path / ".generated" / "collection-posters" / "genre" / "Action.jpg"
    assert first.read_bytes() == jpeg_bytes
    assert second == first
    assert calls == [BASE + "/genre/Action.jpg"]


async def test_a_404_leaves_no_file_returns_none_and_is_not_refetched(
    tmp_path, config_factory
):
    """The fallback that makes 'where available' honest: a family with no
    matching asset renders exactly today's behaviour, and the miss marker stops
    the variant ladder being re-walked on every pass."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    calls = []

    async def handler(request):
        calls.append(str(request.url))
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await ensure_default_image(config, http, "genre", "Nope") is None
        before = len(calls)
        assert await ensure_default_image(config, http, "genre", "Nope") is None

    assert len(calls) == before
    root = tmp_path / ".generated" / "collection-posters" / "genre"
    assert not (root / "Nope.jpg").exists()
    assert (root / "Nope.miss").is_file()


async def test_a_variant_hit_is_cached_under_our_own_key(
    tmp_path, config_factory, jpeg_bytes
):
    """The cache path is derived from OUR key, not from the upstream name that
    answered -- so the ladder is walked once per collection, ever."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)

    async def handler(request):
        # httpx decodes percent-escapes back to literal characters on
        # ``request.url.path`` (verified against the installed httpx: a
        # request built from a %20/%2F-escaped URL string reports a
        # space/slash here, never the escape sequence) -- so the match is
        # against the decoded form, not the encoded one candidate_urls emits.
        if request.url.path.endswith("/franchise/Alien Predator.jpg"):
            return httpx.Response(200, content=jpeg_bytes)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        found = await ensure_default_image(
            config, http, "franchise", "Alien / Predator"
        )

    assert found == (
        tmp_path / ".generated" / "collection-posters" / "franchise"
        / "Alien%20%2F%20Predator.jpg"
    )
    assert found.read_bytes() == jpeg_bytes


async def test_a_key_with_a_separator_cannot_escape_the_cache_directory(
    tmp_path, config_factory, jpeg_bytes
):
    """A collection title is not a value this service chose. The cache stem is
    percent-encoded with nothing safe, so no key can add a path segment."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)

    async def handler(request):
        return httpx.Response(200, content=jpeg_bytes)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        found = await ensure_default_image(config, http, "genre", "../../etc")

    root = tmp_path / ".generated" / "collection-posters" / "genre"
    assert found is not None
    assert found.parent == root


async def test_a_body_that_is_not_an_image_is_refused_and_leaves_no_file(
    tmp_path, config_factory
):
    """A 200 is not proof of an image -- upstream can answer 200 with HTML --
    and an unusable body cached would be uploaded, hashed, and never retried."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)

    async def handler(request):
        return httpx.Response(200, content=b"<html>not an image</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await ensure_default_image(config, http, "genre", "Action") is None

    root = tmp_path / ".generated" / "collection-posters" / "genre"
    assert not (root / "Action.jpg").exists()


async def test_no_http_client_refuses_the_fetch_but_not_the_cache(
    tmp_path, config_factory, jpeg_bytes
):
    """``separator_art._base_layer``'s posture: ``http`` may be None, which
    refuses the FETCH only."""
    config = config_factory(assets_root=str(tmp_path), library_folders=True)
    root = tmp_path / ".generated" / "collection-posters" / "genre"
    root.mkdir(parents=True)
    (root / "Action.jpg").write_bytes(jpeg_bytes)

    assert await ensure_default_image(config, None, "genre", "Nope") is None
    assert await ensure_default_image(config, None, "genre", "Action") == (
        root / "Action.jpg"
    )


def test_every_family_row_names_a_real_directory_shape():
    """The table's own integrity: a directory is either flat (`genre`) or one
    level deep for a Pattern B family (`network/color`), never deeper and never
    a logos/overlays/white/best/standards path -- those are overlay-phase
    material and are out of scope by C1.6."""
    for name, family in FAMILIES.items():
        assert family.directory.count("/") <= 1, name
        for banned in ("logos", "overlays", "white", "best", "standards"):
            assert banned not in family.directory.split("/"), name
