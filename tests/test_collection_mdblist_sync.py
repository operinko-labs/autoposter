"""Row 31 -- push a collection's resolved membership to an MDBList list.

MockTransport only: no test here opens a socket (facts C1.4). Two gates, both
of which must be open before anything leaves the process -- the definition
names a list AND the deployment sets collections.mdblist_sync_apply.
"""
import httpx
import pytest

from autoposter.config.schema import CollectionDefinition, CollectionsConfig
from autoposter.collections.mdblist_sync import (
    pushable_ids, push_payload, sync_membership,
)
from autoposter.facts.mdblist import MDBListClient


class FakeItem:
    def __init__(self, rating_key):
        self.ratingKey = rating_key  # noqa: N815


def _index(pairs):
    index = {"imdb": {}, "tmdb": {}, "tvdb": {}, "plex": {}}
    for namespace, value, item in pairs:
        index[namespace][value] = item
    return index


def test_pushable_ids_keeps_only_ids_whose_item_survived():
    kept, dropped = FakeItem("1"), FakeItem("2")
    index = _index([("imdb", "tt1", kept), ("imdb", "tt2", dropped)])
    ids = [("imdb", "tt1"), ("imdb", "tt2")]
    assert pushable_ids(index, ids, [kept]) == [("imdb", "tt1")]


def test_pushable_ids_keeps_the_definitions_own_order():
    a, b = FakeItem("1"), FakeItem("2")
    index = _index([("imdb", "tt2", b), ("imdb", "tt1", a)])
    ids = [("imdb", "tt2"), ("imdb", "tt1")]
    assert pushable_ids(index, ids, [a, b]) == [("imdb", "tt2"), ("imdb", "tt1")]


def test_pushable_ids_drops_the_plex_namespace():
    # A rating key is this server's identity for an item and means nothing to
    # MDBList; pushing it would name a title MDBList cannot resolve.
    item = FakeItem("1")
    index = _index([("plex", "1", item)])
    assert pushable_ids(index, [("plex", "1")], [item]) == []


def test_pushable_ids_names_one_item_once():
    # One Plex item commonly carries both an imdb and a tmdb guid.
    item = FakeItem("1")
    index = _index([("imdb", "tt1", item), ("tmdb", "9", item)])
    ids = [("imdb", "tt1"), ("tmdb", "9")]
    assert pushable_ids(index, ids, [item]) == [("imdb", "tt1")]


def test_push_payload_groups_by_media_type():
    assert push_payload([("imdb", "tt1"), ("tmdb", "9")], is_movie=True) == {
        "movies": [{"imdb": "tt1"}, {"tmdb": "9"}]
    }
    assert push_payload([("tvdb", "77")], is_movie=False) == {
        "shows": [{"tvdb": "77"}]
    }


@pytest.mark.asyncio
async def test_no_list_named_pushes_nothing():
    # The negative case: an untouched definition never reaches the network.
    definition = CollectionDefinition(
        title="Heat", builder="plex_id", params={"ids": ["1"]}
    )
    calls = []

    class Recording:
        async def add_list_items(self, reference, payload):
            calls.append((reference, payload))
            return {}

    actions = await sync_membership(
        definition, [], {}, [], is_movie=True, client=Recording(), apply=True
    )
    assert calls == []
    assert actions == []


@pytest.mark.asyncio
async def test_apply_off_reports_what_it_would_push_and_pushes_nothing():
    item = FakeItem("1")
    definition = CollectionDefinition(
        title="Heat", builder="plex_id", params={"ids": ["1"]},
        sync_to_mdb_list="me/heat",
    )
    calls = []

    class Recording:
        async def add_list_items(self, reference, payload):
            calls.append((reference, payload))
            return {}

    actions = await sync_membership(
        definition, [item], _index([("imdb", "tt1", item)]), [("imdb", "tt1")],
        is_movie=True, client=Recording(), apply=False,
    )
    assert calls == []
    assert actions == [
        "'Heat': would push 1 member(s) to MDBList list 'me/heat' "
        "(collections.mdblist_sync_apply is off)"
    ]


@pytest.mark.asyncio
async def test_apply_on_pushes_and_reports_the_count():
    item = FakeItem("1")
    definition = CollectionDefinition(
        title="Heat", builder="plex_id", params={"ids": ["1"]},
        sync_to_mdb_list="me/heat",
    )
    calls = []

    class Recording:
        async def add_list_items(self, reference, payload):
            calls.append((reference, payload))
            return {}

    actions = await sync_membership(
        definition, [item], _index([("imdb", "tt1", item)]), [("imdb", "tt1")],
        is_movie=True, client=Recording(), apply=True,
    )
    assert calls == [("me/heat", {"movies": [{"imdb": "tt1"}]})]
    assert actions == ["'Heat': pushed 1 member(s) to MDBList list 'me/heat'"]


@pytest.mark.asyncio
async def test_a_failed_push_reports_the_class_name_and_never_the_message():
    # facts C1.4: an httpx error's str() carries the request URL, and the URL
    # carries apikey= as a query parameter. The served action string gets the
    # exception CLASS NAME and a fixed sentence; the detail goes to the log.
    item = FakeItem("1")
    definition = CollectionDefinition(
        title="Heat", builder="plex_id", params={"ids": ["1"]},
        sync_to_mdb_list="me/heat",
    )

    class Failing:
        async def add_list_items(self, reference, payload):
            raise httpx.HTTPStatusError(
                "500 Server Error for url "
                "https://api.mdblist.com/lists/me/heat/items/add?apikey=SECRET",
                request=None, response=None,
            )

    actions = await sync_membership(
        definition, [item], _index([("imdb", "tt1", item)]), [("imdb", "tt1")],
        is_movie=True, client=Failing(), apply=True,
    )
    assert actions == [
        "'Heat': the push to MDBList list 'me/heat' failed (HTTPStatusError); "
        "the collection itself was applied as usual"
    ]
    assert "SECRET" not in actions[0]
    assert "apikey" not in actions[0]


@pytest.mark.asyncio
async def test_no_client_configured_reports_rather_than_crashing():
    item = FakeItem("1")
    definition = CollectionDefinition(
        title="Heat", builder="plex_id", params={"ids": ["1"]},
        sync_to_mdb_list="me/heat",
    )
    actions = await sync_membership(
        definition, [item], _index([("imdb", "tt1", item)]), [("imdb", "tt1")],
        is_movie=True, client=None, apply=True,
    )
    assert actions == [
        "'Heat': MDBList is not configured, so nothing was pushed to "
        "list 'me/heat'"
    ]


@pytest.mark.asyncio
async def test_add_list_items_posts_the_payload_to_the_add_endpoint():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"added": {"movies": 1}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = MDBListClient("KEY", http)
        body = await client.add_list_items("me/heat", {"movies": [{"imdb": "tt1"}]})

    assert seen["method"] == "POST"
    assert seen["url"].startswith("https://api.mdblist.com/lists/me/heat/items/add")
    assert "apikey=KEY" in seen["url"]
    assert '"imdb": "tt1"' in seen["body"] or '"imdb":"tt1"' in seen["body"]
    assert body == {"added": {"movies": 1}}


@pytest.mark.asyncio
async def test_add_list_items_raises_on_mdblists_200_with_an_error_body():
    # MDBList answers a spent budget with 200 and an error body, so nothing in
    # the HTTP layer notices -- the same trap content_rating/list_items handle.
    def handler(request):
        return httpx.Response(200, json={"error": "API Limit Reached!"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = MDBListClient("KEY", http)
        with pytest.raises(Exception):
            await client.add_list_items("me/heat", {"movies": []})


def test_the_apply_switch_defaults_off():
    assert CollectionsConfig().mdblist_sync_apply is False


def test_a_definition_names_no_list_by_default():
    assert CollectionDefinition(
        title="Heat", builder="plex_id", params={"ids": ["1"]}
    ).sync_to_mdb_list is None
