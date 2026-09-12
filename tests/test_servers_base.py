import dataclasses

import pytest

from autoposter.servers import base


def test_server_item_ref_is_frozen_and_hashable():
    ref = base.ServerItemRef(server="plex", native_id="123", library="Movies", kind="movie")
    assert hash(ref) == hash(base.ServerItemRef("plex", "123", "Movies", "movie"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.native_id = "9"  # type: ignore[misc]


def test_resolved_item_names_its_server_and_native_id():
    fields = {f.name for f in dataclasses.fields(base.ResolvedItem)}
    assert {"server", "native_id", "parent_native_id"} <= fields
    assert "rating_key" not in fields and "parent_rating_key" not in fields


def test_resolved_item_ref_property():
    item = base.ResolvedItem(
        server="jellyfin", native_id="abc", library="Movies", kind="movie", title="T",
        year=2020, season_number=None, episode_number=None, root_folder="T (2020)",
        file_path="/m/T (2020)/t.mkv", art_url=None, tmdb_id=1, tvdb_id=None, imdb_id=None,
    )
    assert item.ref == base.ServerItemRef("jellyfin", "abc", "Movies", "movie")


def test_path_mismatch_is_an_item_not_found():
    assert issubclass(base.PathMismatch, base.ItemNotFound)
    assert base.ItemNotFound.served_detail is True


def test_unsupported_on_server_sentence():
    exc = base.UnsupportedOnServer("jellyfin", base.CAP_LOCK_ARTWORK)
    assert str(exc) == "Jellyfin does not support lock_artwork"
    assert exc.server == "jellyfin" and exc.capability == "lock_artwork"


def test_the_protocol_lists_every_operation_the_spec_names():
    wanted = {
        "resolve", "fetch_ref", "exists_many", "keys_resolve", "list_items",
        "upload_artwork", "upload_logo", "clear_logo", "has_clearlogo", "fetch_artwork",
        "artwork_provenance", "reset_artwork_to_agent_default", "apply_facts",
        "check_liveness",
    }
    assert wanted <= set(base.MediaServer.__protocol_attrs__)
    assert {"name", "capabilities"} <= set(base.MediaServer.__protocol_attrs__)
