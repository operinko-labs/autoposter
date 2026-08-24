"""``text_file``: a list the operator maintains as a file on the manual mount.

Two things are being guarded here and they pull in opposite directions.

The path is operator input that names a file this service then reads, so it is
contained exactly the way ``api/manual.py`` and ``collections/posters.py``
contain theirs -- both sides through ``realpath`` before being compared, so a
symlink *inside* the mount pointing at ``/etc/shadow`` is refused on its
target. A lexical check would wave that through, which is why the symlink case
below is the one that matters most in this file.

The contents are operator input that this service must never quietly discard.
A line it cannot parse is a typo in a list of ids that all look alike, and
skipping it would remove exactly one film from a collection with no trace
anywhere. So an unparseable line raises and names its line number.
"""
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from autoposter.collections.builders import BuilderContext, SourceClients
from autoposter.collections.builders.text_file import TextFileRefused


def _ctx(root: Path | None, **params) -> BuilderContext:
    return BuilderContext(
        library="Movies",
        library_type="Movie",
        config=params,
        sources=SourceClients(manual_assets_root=root),
    )


async def _build(root: Path | None, **params):
    from autoposter.collections.builders import REGISTRY

    return await REGISTRY["text_file"].build(_ctx(root, **params))


def _write(root: Path, name: str, text: str) -> Path:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode("utf-8"))
    return target


async def test_every_supported_line_form_in_the_order_it_was_written(tmp_path):
    _write(tmp_path, "list.txt", """
# Curated by hand, newest first.

imdb:tt0111161
tmdb:438631
tvdb:371980
tt0068646
  imdb:tt0071562
tmdb:693134  # Dune: Part Two

""")

    result = await _build(tmp_path, path="list.txt")

    assert result.ids == [
        ("imdb", "tt0111161"),
        ("tmdb", "438631"),
        ("tvdb", "371980"),
        ("imdb", "tt0068646"),
        ("imdb", "tt0071562"),
        ("tmdb", "693134"),
    ]
    assert result.summary is None


async def test_a_bare_id_is_read_as_an_imdb_id(tmp_path):
    """The one inference the format makes, and it is unambiguous: ``tt`` is an
    IMDb prefix and nothing else uses it."""
    _write(tmp_path, "list.txt", "tt0111161\n")

    assert (await _build(tmp_path, path="list.txt")).ids == [("imdb", "tt0111161")]


async def test_a_line_it_cannot_parse_raises_and_names_the_line_number(tmp_path):
    """Never a silent skip: one dropped line is one film missing from a
    collection, with nothing anywhere to say so."""
    _write(tmp_path, "list.txt", "tt0111161\n# a comment\n\n438631\ntt0068646\n")

    with pytest.raises(TextFileRefused, match="line 4"):
        await _build(tmp_path, path="list.txt")


async def test_an_unknown_namespace_prefix_raises(tmp_path):
    """``plex:`` is a rating key, which means nothing in a file meant to be
    portable between servers -- and any other prefix is a typo."""
    _write(tmp_path, "list.txt", "plex:12345\n")

    with pytest.raises(TextFileRefused, match="line 1"):
        await _build(tmp_path, path="list.txt")


async def test_a_malformed_value_after_a_good_prefix_raises(tmp_path):
    _write(tmp_path, "list.txt", "imdb:tt0111161\ntmdb:tt0068646\n")

    with pytest.raises(TextFileRefused, match="line 2"):
        await _build(tmp_path, path="list.txt")


async def test_a_file_with_no_ids_at_all_raises(tmp_path):
    """Empty is the reconciler's "make no changes", so a file that parsed
    clean and yielded nothing must not be reported as a successful build."""
    _write(tmp_path, "list.txt", "# everything is commented out\n\n")

    with pytest.raises(TextFileRefused, match="no ids"):
        await _build(tmp_path, path="list.txt")


async def test_a_path_in_a_subdirectory_of_the_mount_is_contained(tmp_path):
    """Contained, not merely un-escaped: the check must accept the ordinary
    nested layout the mount actually has."""
    _write(tmp_path, "lists/movies/oscars.txt", "tt0111161\n")

    result = await _build(tmp_path, path="lists/movies/oscars.txt")

    assert result.ids == [("imdb", "tt0111161")]


async def test_a_path_that_climbs_out_of_the_mount_is_refused(tmp_path):
    root = tmp_path / "manual"
    root.mkdir()
    (tmp_path / "outside.txt").write_bytes(b"tt0111161\n")

    with pytest.raises(TextFileRefused, match="manual assets"):
        await _build(root, path="../outside.txt")


async def test_a_symlink_inside_the_mount_pointing_out_of_it_is_refused(tmp_path):
    """The case a lexical containment check passes and this one must not: the
    path has no ``..`` in it and the file is really there, but it is not the
    operator's file -- it is whatever the link points at."""
    root = tmp_path / "manual"
    root.mkdir()
    secret = tmp_path / "elsewhere.txt"
    secret.write_bytes(b"tt0111161\n")
    os.symlink(secret, root / "list.txt")

    with pytest.raises(TextFileRefused, match="manual assets"):
        await _build(root, path="list.txt")


async def test_a_missing_file_raises_naming_only_the_relative_path(tmp_path):
    """The relative path is what the operator wrote and can act on. The
    resolved absolute path is the container's mount layout, and this message
    reaches logs an operator pastes into tickets."""
    with pytest.raises(TextFileRefused) as caught:
        await _build(tmp_path, path="lists/absent.txt")

    assert "lists/absent.txt" in str(caught.value)
    assert str(tmp_path) not in str(caught.value)


async def test_a_file_that_is_not_utf8_text_is_refused(tmp_path):
    """The one read failure that is not an ``OSError``:
    ``UnicodeDecodeError`` is a ``ValueError``, so it escapes the read's
    refusal arm entirely and reaches the engine as an unrelated class carrying
    a message about byte offsets rather than about the operator's file. An
    image or a spreadsheet saved into the mount by mistake is exactly how it
    happens."""
    (tmp_path / "list.bin").write_bytes(b"\xff\xfe\x00t\x00t\x000\x00")

    with pytest.raises(TextFileRefused) as caught:
        await _build(tmp_path, path="list.bin")

    message = str(caught.value)
    assert "list.bin" in message and "UTF-8" in message
    assert str(tmp_path) not in message


async def test_a_directory_is_not_a_list(tmp_path):
    (tmp_path / "lists").mkdir()

    with pytest.raises(TextFileRefused):
        await _build(tmp_path, path="lists")


async def test_an_absolute_path_is_refused_before_anything_is_read(tmp_path):
    """At params validation, so it is a *config load* error rather than a
    mid-pass one: ``path`` is relative to the mount by definition, and joining
    an absolute path onto a root yields the absolute path."""
    with pytest.raises(ValidationError, match="relative"):
        await _build(tmp_path, path="/etc/passwd")


async def test_a_windows_absolute_path_is_refused_too(tmp_path):
    with pytest.raises(ValidationError, match="relative"):
        await _build(tmp_path, path=r"C:\lists\movies.txt")


async def test_an_empty_path_is_refused(tmp_path):
    with pytest.raises(ValidationError):
        await _build(tmp_path, path="   ")


async def test_params_it_does_not_understand_are_refused(tmp_path):
    with pytest.raises(ValidationError):
        await _build(tmp_path, path="list.txt", file="other.txt")


async def test_a_bundle_with_no_manual_mount_raises(tmp_path):
    """The absent-client rule, applied to the one non-client the bundle
    carries: a caller that built no bundle gets a clear refusal rather than a
    traceback out of ``os.path``."""
    with pytest.raises(TextFileRefused, match="manual assets"):
        await _build(None, path="list.txt")
