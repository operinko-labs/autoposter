from autoposter.intake.arr import RenderIntent


def test_a_legacy_payload_with_rating_key_decodes_into_refs():
    intent = RenderIntent.from_payload({"kind": "movie", "title": "T", "tmdb_id": 1, "rating_key": "42"})
    assert intent.refs == {"plex": "42"}
    assert intent.native_id_on("plex") == "42" and intent.native_id_on("jellyfin") is None


def test_refs_stay_out_of_the_dedupe_key():
    a = RenderIntent(kind="movie", title="T", tmdb_id=1)
    b = RenderIntent(kind="movie", title="T", tmdb_id=1, refs={"plex": "42"})
    assert a.dedupe_key == b.dedupe_key
