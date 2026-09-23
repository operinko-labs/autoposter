"""Perf workstream B2: font and overlay hashes are cached on the file's stat.

``gather_fingerprint_inputs`` hashes the overlay and up to three fonts for
every art kind of every item -- tens of thousands of whole-file reads of the
same handful of files per full pass. The key is (path, st_mtime_ns, st_size),
so these tests move the mtime EXPLICITLY with ``os.utime``: two writes inside
one filesystem timestamp tick would otherwise share an mtime, which is the
cache's one documented blind spot, not what these tests are about.
"""
import hashlib
import os

from autoposter.render import pipeline as pipeline_module
from autoposter.render.pipeline import _asset_sha256

T0 = 1_700_000_000_000_000_000


def _write(path, data: bytes, mtime_ns: int) -> None:
    path.write_bytes(data)
    os.utime(path, ns=(mtime_ns, mtime_ns))


def test_a_second_ask_with_the_same_stat_is_served_from_the_cache(tmp_path, monkeypatch):
    font = tmp_path / "font.ttf"
    _write(font, b"font-v1", T0)
    assert _asset_sha256(font) == hashlib.sha256(b"font-v1").hexdigest()

    def no_read(path):
        raise AssertionError("a cache hit must not read the file")

    monkeypatch.setattr(pipeline_module, "_file_sha256", no_read)
    assert _asset_sha256(font) == hashlib.sha256(b"font-v1").hexdigest()


def test_an_mtime_change_misses(tmp_path):
    font = tmp_path / "font.ttf"
    _write(font, b"font-v1", T0)
    before = _asset_sha256(font)
    _write(font, b"font-v2", T0 + 1_000_000_000)
    assert _asset_sha256(font) == hashlib.sha256(b"font-v2").hexdigest() != before


def test_a_size_change_misses_even_at_the_same_mtime(tmp_path):
    overlay = tmp_path / "overlay.png"
    _write(overlay, b"overlay", T0)
    _asset_sha256(overlay)
    _write(overlay, b"overlay-longer", T0)
    assert _asset_sha256(overlay) == hashlib.sha256(b"overlay-longer").hexdigest()


def test_a_missing_file_is_the_empty_sentinel(tmp_path):
    assert _asset_sha256(tmp_path / "does-not-exist.ttf") == ""
