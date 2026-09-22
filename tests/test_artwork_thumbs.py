"""api/thumbs.py: the library grid's thumbnails and the cache that holds them.

Pure unit tests against real Pillow images written to tmp_path -- no database,
no ASGI app -- so this file runs on the pull-request lane. The route that uses
it is covered in the deep suite, tests/test_api_artwork.py.
"""
import io
import sys
import threading

import pytest
from PIL import Image, JpegImagePlugin

from autoposter.api import thumbs


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    """A private cache per test: the module-level one lives for the process,
    and a hit left by another test would hide a miss this one means to see."""
    cache = thumbs.ThumbnailCache(thumbs.THUMB_CACHE_BYTES)
    monkeypatch.setattr(thumbs, "_CACHE", cache)
    return cache


def _jpeg(path, size, color=(180, 40, 40)):
    Image.new("RGB", size, color).save(path, "JPEG", quality=92)
    return path


def _decoded(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


WIDTHS = [width.value for width in thumbs.ThumbWidth]


def test_the_accepted_widths_and_the_budget_are_the_specified_constants():
    assert WIDTHS == [320, 640]
    assert thumbs.THUMB_CACHE_BYTES == 64 * 1024 * 1024
    assert thumbs.THUMB_QUALITY == 82


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("size", [(2000, 3000), (3840, 2160)], ids=["poster", "background"])
def test_a_thumbnail_fits_the_width_keeps_the_aspect_ratio_and_is_jpeg(tmp_path, width, size):
    source = _jpeg(tmp_path / "art.jpg", size)

    thumb = _decoded(thumbs.render_thumbnail(source, width))

    assert thumb.format == "JPEG"
    assert thumb.mode == "RGB"
    assert thumb.width == width
    assert thumb.height == pytest.approx(size[1] * width / size[0], abs=1)


def test_a_jpeg_is_decoded_at_reduced_scale_not_full_size(tmp_path, monkeypatch):
    """The whole cost of a thumbnail is the decode, and a 2000x3000 JPEG
    decoded at full size only to be thrown away is most of it. draft() asks
    libjpeg for a DCT-scaled decode (here 1/4, 500x750) before any pixel
    exists. Recorded on the plugin class: Image.thumbnail() calls draft()
    again internally, which is why the first call is the one asserted."""
    source = _jpeg(tmp_path / "poster.jpg", (2000, 3000))
    calls = []
    real_draft = JpegImagePlugin.JpegImageFile.draft

    def recording(self, mode, size):
        result = real_draft(self, mode, size)
        calls.append((mode, size, result))
        return result

    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, "draft", recording)

    thumbs.render_thumbnail(source, 320)

    assert calls, "draft() was never called"
    mode, size, result = calls[0]
    assert (mode, size) == ("RGB", (320, 480))
    assert result is not None, "draft() declined to reduce the decode"


def test_transparency_is_flattened_onto_the_background_not_dropped(tmp_path):
    """JPEG has no alpha. A plain convert("RGB") keeps whatever colour sits
    under alpha 0 -- white here -- so a transparent hand-placed PNG override
    would come out as a white slab. Flattening composites onto the
    background instead."""
    source = tmp_path / "poster.png"
    image = Image.new("RGBA", (800, 1200), (255, 255, 255, 0))
    image.paste((0, 0, 255, 255), (0, 0, 800, 600))
    image.save(source, "PNG")

    thumb = _decoded(thumbs.render_thumbnail(source, 320))

    assert (thumb.format, thumb.mode, thumb.size) == ("JPEG", "RGB", (320, 480))
    red, green, blue = thumb.getpixel((160, 100))
    assert blue > 200 and red < 50 and green < 50
    assert max(thumb.getpixel((160, 380))) < 30


def test_an_image_narrower_than_w_is_not_upscaled(tmp_path):
    source = _jpeg(tmp_path / "small.jpg", (200, 300))

    thumb = _decoded(thumbs.render_thumbnail(source, 640))

    assert thumb.size == (200, 300)
    assert thumb.format == "JPEG"


def test_the_thumbnail_is_encoded_at_the_module_quality(tmp_path):
    """Compared by quantization tables, which is what the quality setting
    actually changes: re-encoding the decoded thumbnail at THUMB_QUALITY
    must reproduce the tables it arrived with."""
    source = _jpeg(tmp_path / "poster.jpg", (2000, 3000))
    thumb = _decoded(thumbs.render_thumbnail(source, 320))

    reference = io.BytesIO()
    thumb.convert("RGB").save(reference, "JPEG", quality=thumbs.THUMB_QUALITY)

    assert thumb.quantization == Image.open(reference).quantization


def test_thumbnail_bytes_serves_a_repeat_from_the_cache_and_misses_on_any_key_change(
    tmp_path, monkeypatch
):
    """Keyed on (path, size, mtime_ns, width): a rewritten file stats
    differently, so it can never be answered from its predecessor's entry."""
    source = _jpeg(tmp_path / "poster.jpg", (1000, 1500))
    calls = []
    real = thumbs.render_thumbnail

    def counting(path, width):
        calls.append((path, width))
        return real(path, width)

    monkeypatch.setattr(thumbs, "render_thumbnail", counting)

    first = thumbs.thumbnail_bytes(source, 100, 1, 320)
    again = thumbs.thumbnail_bytes(source, 100, 1, 320)
    assert again == first
    assert len(calls) == 1

    thumbs.thumbnail_bytes(source, 100, 2, 320)  # same path, new mtime
    thumbs.thumbnail_bytes(source, 101, 1, 320)  # same path, new size
    thumbs.thumbnail_bytes(source, 100, 1, 640)  # the other width
    assert len(calls) == 4


def test_bytes_pillow_cannot_decode_raise_undecodable_artwork(tmp_path):
    source = tmp_path / "poster.jpg"
    source.write_bytes(b"\xff\xd8\xff\xe0 not really a jpeg")

    with pytest.raises(thumbs.UndecodableArtwork):
        thumbs.thumbnail_bytes(source, 1, 1, 320)


@pytest.mark.parametrize("exc_type", [SyntaxError, ValueError, EOFError])
def test_other_pillow_decode_errors_also_raise_undecodable_artwork(
    tmp_path, monkeypatch, exc_type
):
    """Pillow does not confine every bad-file complaint to OSError: a damaged
    chunk header can surface as SyntaxError, and some codecs raise ValueError
    or EOFError. Any of those from render_thumbnail must still map to
    UndecodableArtwork rather than escape as a generic 500 (api/artwork.py)."""
    source = _jpeg(tmp_path / "poster.jpg", (100, 150))

    def raising(path, width):
        raise exc_type("bad artwork")

    monkeypatch.setattr(thumbs, "render_thumbnail", raising)

    with pytest.raises(thumbs.UndecodableArtwork):
        thumbs.thumbnail_bytes(source, 1, 1, 320)


def test_a_file_gone_since_its_stat_stays_file_not_found(tmp_path):
    """A 404 for the route, not a 500: the render was deleted between the
    stat and the open, which is an ordinary miss."""
    with pytest.raises(FileNotFoundError):
        thumbs.thumbnail_bytes(tmp_path / "gone.jpg", 1, 1, 320)


def _key(name: str) -> thumbs.ThumbKey:
    return (f"/assets/{name}.jpg", 1, 1, 320)


def test_the_cache_evicts_the_least_recently_used_entry_to_stay_within_budget():
    cache = thumbs.ThumbnailCache(budget_bytes=100)
    cache.put(_key("a"), b"a" * 40)
    cache.put(_key("b"), b"b" * 40)
    assert cache.get(_key("a")) == b"a" * 40  # a is now the most recently used

    cache.put(_key("c"), b"c" * 40)

    assert cache.get(_key("b")) is None
    assert cache.get(_key("a")) == b"a" * 40
    assert cache.get(_key("c")) == b"c" * 40
    assert cache.total_bytes == 80
    assert len(cache) == 2


def test_an_entry_larger_than_the_whole_budget_is_not_kept():
    """Storing it would evict everything else and then itself."""
    cache = thumbs.ThumbnailCache(budget_bytes=100)
    cache.put(_key("a"), b"a" * 40)

    cache.put(_key("huge"), b"h" * 101)

    assert cache.get(_key("huge")) is None
    assert cache.get(_key("a")) == b"a" * 40
    assert cache.total_bytes == 40


def test_putting_a_key_again_replaces_it_without_double_counting():
    cache = thumbs.ThumbnailCache(budget_bytes=100)
    cache.put(_key("a"), b"a" * 40)

    cache.put(_key("a"), b"A" * 30)

    assert cache.get(_key("a")) == b"A" * 30
    assert cache.total_bytes == 30
    assert len(cache) == 1


def test_concurrent_puts_and_gets_keep_the_byte_count_exact():
    """The endpoint's worker threads share one cache. Without the lock, the
    OrderedDict and the running byte total drift apart under interleaving.

    A narrowed switch interval forces the interpreter to hand off between
    threads far more often than the 5 ms default, so an unlocked
    implementation actually interleaves mid-mutation here instead of just
    getting lucky. Each worker's exceptions are collected rather than left
    for pytest's thread-exception hook, which only warns -- an unsynchronized
    move_to_end() racing a concurrent evict raises KeyError, and that must
    fail this test, not decorate it with a warning."""
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        cache = thumbs.ThumbnailCache(budget_bytes=1000)
        errors: list[Exception] = []

        def worker(n: int) -> None:
            try:
                for i in range(500):
                    cache.put((f"/{n}/{i}", 1, 1, 320), b"x" * 10)
                    cache.get((f"/{n}/{i - 1}", 1, 1, 320))
            except Exception as exc:  # pragma: no cover - only without the lock
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors
        assert cache.total_bytes == 10 * len(cache)
        assert cache.total_bytes <= 1000
    finally:
        sys.setswitchinterval(old_interval)
