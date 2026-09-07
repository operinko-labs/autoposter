"""The *arr Webhook registration, against what probe 2 measured.

Everything asserted here was read off Sonarr 4.0.18.2978 and Radarr 6.4.0.10523
(recon sections 3.1-3.3), not inferred: `Webhook`'s five fields are `url`,
`method`, `username`, `password` and `headers`; `configContract` is
`WebhookSettings`; `method`'s default and both live entries' value is the
integer `1`; and both services ALREADY carry an Autoposter Webhook whose
`headers` holds one entry keyed `X-Autoposter-Token` and whose `url` has path
`/webhook/{service}` with no query string.

The secret is a HEADER and never a query parameter, by the 2026-09-05 amendment
to row 213: the URL this builds is stored in three places outside our control
-- the *arr's database, its UI and its logs -- and it is also what the finish
page shows the operator. The `password` field is the other candidate and is
mechanically wrong: it produces an HTTP Basic `Authorization` header, which no
code path in `src/` reads (`intake/routes.py` reads `X-Autoposter-Token`).

The UPDATE half is facts C2a, the user's 2026-09-07 ruling, and it is the half
that constrains this module most: an entry that already exists is found by the
per-service NAME or by a url whose path is `/webhook/<service>`, it is never
duplicated, and the PUT that refreshes it changes the url and the
`X-Autoposter-Token` header entry AND NOTHING ELSE. Every other field and every
`on*` flag stays as the operator left it -- which is why the fixture below has
an operator's own untickings and a non-empty username in it, and why most of
the assertions are about what did NOT change.
"""

import asyncio
import json
import logging

import httpx
import pytest
import pytest_asyncio

from autoposter.api import setup as setup_api
from autoposter.api import setup_arr

# The autouse environment isolation is the wizard suite's, imported rather than
# copied; the two token helpers with it. The three fixtures this file needs are
# DECLARED below instead -- see the note there.
from tests.test_api_setup import (  # noqa: F401
    _authenticate,
    _headers,
    isolated_state,
)

# No `pytestmark = pytest.mark.asyncio`: `asyncio_mode = "auto"` (pyproject)
# already runs every coroutine here, and half of this file is synchronous --
# `test_api_setup_check.py`'s shape, for the same mixed reason.

PUBLIC_URL = "https://autoposter.example.test"
SECRET = "row-121-webhook-secret-3f8a"
APIKEY = "row-121-arr-apikey-c04d"
SONARR_BASE = "http://sonarr.invalid:8989"
RADARR_BASE = "http://radarr.invalid:7878"
# What a 400 from an *arr echoes back: the fields it was sent, secret included.
ARR_ERROR_BODY = "row-121-arr-echoed-this"

# One entry as `GET /api/v3/notification` really answers it -- with the
# operator's own choices in it. `onUpgrade` and `onSeriesAdd` are UNTICKED and
# `includeHealthWarnings` is ticked, none of which is what a fresh registration
# would send; the tags are theirs; the username is theirs (Radarr's live entry
# has one, doing nothing); and the header carries LAST run's secret.
EXISTING_SONARR_ENTRY = {
    "id": 2,
    "name": "Autoposter - Sonarr",
    "implementation": "Webhook",
    "implementationName": "Webhook",
    "configContract": "WebhookSettings",
    "tags": [7],
    "onDownload": True,
    "onUpgrade": False,
    "onRename": True,
    "onImportComplete": True,
    "onSeriesAdd": False,
    "onGrab": False,
    "includeHealthWarnings": True,
    "fields": [
        {"name": "url", "value": "http://old.invalid/webhook/sonarr"},
        {"name": "method", "value": 1},
        {"name": "username", "value": "row-121-operator-username"},
        {"name": "password", "value": "row-121-operator-password"},
        {
            "name": "headers",
            "value": [{"key": "X-Autoposter-Token", "value": "row-121-last-runs-secret"}],
        },
    ],
}
OTHER_ENTRY = {"id": 1, "name": "Plex Media Server", "implementation": "PlexServer"}


def _fields(body: dict) -> dict:
    return {field["name"]: field["value"] for field in body["fields"]}


def _copy(entry: dict) -> dict:
    return json.loads(json.dumps(entry))


def _arr_transport(existing, *, write_status=200):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=existing)
        if write_status >= 400:
            return httpx.Response(write_status, json={"message": ARR_ERROR_BODY})
        return httpx.Response(write_status, json={"id": 9})

    return httpx.MockTransport(handler), seen


# --- the bodies --------------------------------------------------------------


def test_the_registration_names_are_the_ones_already_on_the_deployment():
    """Probe 2 found `Autoposter - Sonarr` / `Autoposter - Radarr` live. A
    registration named plain `Autoposter` would create a SECOND connection
    beside them, which is a duplicate the operator then has to find."""
    assert setup_arr.NAMES == {
        "radarr": "Autoposter - Radarr",
        "sonarr": "Autoposter - Sonarr",
    }


def test_the_sonarr_body_is_exactly_the_shape_probe_two_recorded():
    body = setup_arr.build_body("sonarr", PUBLIC_URL, SECRET, None)

    assert body["name"] == "Autoposter - Sonarr"
    assert body["implementation"] == "Webhook"
    assert body["implementationName"] == "Webhook"
    assert body["configContract"] == "WebhookSettings"
    assert body["tags"] == []
    assert "id" not in body
    # intake/arr.py -- SONARR_EVENTS = {"download", "rename", "seriesadd"}.
    # onUpgrade and onImportComplete also arrive as eventType "Download", which
    # is why both live entries carry them.
    assert body["onDownload"] is True
    assert body["onUpgrade"] is True
    assert body["onRename"] is True
    assert body["onImportComplete"] is True
    assert body["onSeriesAdd"] is True
    # Anything Autoposter parses into zero intents is an EventLog row for
    # nothing (intake/routes.py), so it is not ticked.
    assert body["onGrab"] is False
    assert body["onHealthIssue"] is False
    assert body["onSeriesDelete"] is False
    assert body["includeHealthWarnings"] is False


def test_the_sonarr_fields_carry_the_url_the_method_and_the_header():
    fields = _fields(setup_arr.build_body("sonarr", PUBLIC_URL, SECRET, None))

    # Top-level path, not /api/webhook/... -- app.py includes the intake router
    # with no prefix -- and NO query string.
    assert fields["url"] == f"{PUBLIC_URL}/webhook/sonarr"
    assert "?" not in fields["url"]
    assert SECRET not in fields["url"]
    assert fields["method"] == 1
    assert fields["username"] == ""
    assert fields["password"] == ""
    assert fields["headers"] == [{"key": "X-Autoposter-Token", "value": SECRET}]


def test_the_radarr_body_carries_the_radarr_flags_and_none_of_sonarrs():
    """Two tables, not one with nulls: Sonarr 400s on `onMovieAdded` and Radarr
    on `onSeriesAdd`."""
    body = setup_arr.build_body("radarr", PUBLIC_URL, SECRET, None)

    # intake/arr.py -- RADARR_EVENTS = {"download", "rename", "movieadded"}.
    assert body["onMovieAdded"] is True
    assert body["onDownload"] is True
    assert body["onRename"] is True
    assert body["onUpgrade"] is True
    assert "onSeriesAdd" not in body
    assert "onImportComplete" not in body
    assert "onEpisodeFileDelete" not in body
    assert _fields(body)["url"] == f"{PUBLIC_URL}/webhook/radarr"


# --- the update body: facts C2a ----------------------------------------------


def test_an_update_changes_the_url_and_the_token_header_and_nothing_else():
    """The user's C2a ruling, and the whole of it. The operator's own entry is
    returned to them with two values refreshed: a wizard that overwrote their
    `on*` choices with this module's defaults would silently undo whatever they
    tuned since the last run, and the wizard has no way to know they did not
    mean it."""
    body = setup_arr.build_body("sonarr", PUBLIC_URL, SECRET, EXISTING_SONARR_ENTRY)
    fields = _fields(body)

    assert body["id"] == 2
    assert fields["url"] == f"{PUBLIC_URL}/webhook/sonarr"
    assert fields["headers"] == [{"key": "X-Autoposter-Token", "value": SECRET}]
    # Everything the operator set, untouched -- including the two flags a fresh
    # registration WOULD have ticked and the one it would have cleared.
    assert body["onUpgrade"] is False
    assert body["onSeriesAdd"] is False
    assert body["includeHealthWarnings"] is True
    assert body["tags"] == [7]
    assert fields["username"] == "row-121-operator-username"
    assert fields["password"] == "row-121-operator-password"
    assert fields["method"] == 1
    # And structurally: exactly the keys that came in, no more.
    assert set(body) == set(EXISTING_SONARR_ENTRY)
    assert [field["name"] for field in body["fields"]] == [
        field["name"] for field in EXISTING_SONARR_ENTRY["fields"]
    ]


def test_an_update_never_mutates_the_entry_it_was_handed():
    """The fetched listing is read again by the caller (the match, the id), and
    a body built by mutating it in place would make those two disagree."""
    setup_arr.build_body("sonarr", PUBLIC_URL, SECRET, EXISTING_SONARR_ENTRY)

    assert _fields(EXISTING_SONARR_ENTRY)["url"] == "http://old.invalid/webhook/sonarr"
    assert _fields(EXISTING_SONARR_ENTRY)["headers"] == [
        {"key": "X-Autoposter-Token", "value": "row-121-last-runs-secret"}
    ]


def test_an_update_keeps_every_other_header_the_operator_added():
    """`headers` is a keyValueList and the operator's own entries live in it.
    Only ours is replaced."""
    entry = _copy(EXISTING_SONARR_ENTRY)
    for field in entry["fields"]:
        if field["name"] == "headers":
            field["value"] = [
                {"key": "row-121-operator-header", "value": "row-121-operator-value"},
                {"key": "X-Autoposter-Token", "value": "row-121-last-runs-secret"},
            ]

    fields = _fields(setup_arr.build_body("sonarr", PUBLIC_URL, SECRET, entry))

    assert fields["headers"] == [
        {"key": "row-121-operator-header", "value": "row-121-operator-value"},
        {"key": "X-Autoposter-Token", "value": SECRET},
    ]


def test_an_update_adds_the_token_header_to_an_entry_that_has_none():
    """The reachable shape: an operator who wired the webhook up without a
    secret at all. The header is ADDED rather than the entry left unauthorised
    -- the intake refuses a delivery without it."""
    entry = _copy(EXISTING_SONARR_ENTRY)
    for field in entry["fields"]:
        if field["name"] == "headers":
            field["value"] = []

    fields = _fields(setup_arr.build_body("sonarr", PUBLIC_URL, SECRET, entry))

    assert fields["headers"] == [{"key": "X-Autoposter-Token", "value": SECRET}]


def test_an_update_adds_a_url_field_to_an_entry_that_has_none():
    entry = _copy(EXISTING_SONARR_ENTRY)
    entry["fields"] = [field for field in entry["fields"] if field["name"] != "url"]

    fields = _fields(setup_arr.build_body("sonarr", PUBLIC_URL, SECRET, entry))

    assert fields["url"] == f"{PUBLIC_URL}/webhook/sonarr"
    assert fields["username"] == "row-121-operator-username"


# --- I1: the half-created entry is finished, not re-preserved disabled ------
#
# `find_existing` matches a leftover disabled entry by NAME, and C2a's ordinary
# rule is to PUT every `on*` flag back exactly as found -- but an entry whose
# ACCEPTED flags are every one `False` cannot be an operator's own choice
# (nobody keeps a webhook that fires on nothing), so it can only be OUR OWN
# half-create. The controller's 2026-09-07 ruling: that one shape is finished
# with the accepted flags instead of re-preserved dead.


def test_an_all_false_entry_is_finished_not_preserved_dead():
    entry = _copy(EXISTING_SONARR_ENTRY)
    for event in setup_arr._EVENTS["sonarr"]:
        entry[event] = False

    body = setup_arr.build_body("sonarr", PUBLIC_URL, SECRET, entry)

    assert body["onDownload"] is True
    assert body["onUpgrade"] is True
    assert body["onRename"] is True
    assert body["onImportComplete"] is True
    assert body["onSeriesAdd"] is True
    assert body["onGrab"] is False
    # And it is still THAT entry: id, tags, everything else preserved.
    assert body["id"] == 2
    assert body["tags"] == [7]


def test_an_all_false_entry_missing_the_header_still_gets_one():
    """Requirement 3: the header-add path and the flag-finish path are
    independent -- an entry that lacks BOTH still gets both fixed in one PUT."""
    entry = _copy(EXISTING_SONARR_ENTRY)
    for event in setup_arr._EVENTS["sonarr"]:
        entry[event] = False
    entry["fields"] = [field for field in entry["fields"] if field["name"] != "headers"]

    body = setup_arr.build_body("sonarr", PUBLIC_URL, SECRET, entry)

    assert _fields(body)["headers"] == [{"key": "X-Autoposter-Token", "value": SECRET}]
    assert body["onDownload"] is True
    assert body["onSeriesAdd"] is True


def test_an_entry_with_any_flag_true_is_still_preserved_exactly():
    """The regression this fix must not cause: `EXISTING_SONARR_ENTRY` has
    `onDownload` etc. ticked, so it is an operator's own entry and every flag,
    ticked or not, survives untouched -- the C2a case, unchanged."""
    body = setup_arr.build_body("sonarr", PUBLIC_URL, SECRET, EXISTING_SONARR_ENTRY)

    assert body["onDownload"] is True
    assert body["onUpgrade"] is False
    assert body["onRename"] is True
    assert body["onImportComplete"] is True
    assert body["onSeriesAdd"] is False
    assert body["onGrab"] is False


# --- finding the existing entry ----------------------------------------------


def test_an_entry_is_found_by_this_wizards_name():
    found = setup_arr.find_existing([OTHER_ENTRY, EXISTING_SONARR_ENTRY], "sonarr")

    assert found is not None and found["id"] == 2


def test_an_entry_is_found_by_its_url_path_whatever_the_operator_named_it():
    """Facts C2a names both tests, and this is the one that stops the
    duplicate: the operator's own hand-made connection may be called anything
    at all, and creating a second one beside it is exactly what the ruling
    forbids."""
    renamed = _copy(EXISTING_SONARR_ENTRY)
    renamed["name"] = "row-121-whatever-they-called-it"

    found = setup_arr.find_existing([renamed], "sonarr")

    assert found is not None and found["id"] == 2


def test_the_url_match_is_the_path_and_not_a_substring():
    """A url whose path merely CONTAINS the string would match somebody else's
    relay. The path is compared whole."""
    elsewhere = _copy(EXISTING_SONARR_ENTRY)
    elsewhere["name"] = "row-121-not-ours"
    for field in elsewhere["fields"]:
        if field["name"] == "url":
            field["value"] = "http://other.invalid/relay/webhook/sonarr/onwards"

    assert setup_arr.find_existing([elsewhere], "sonarr") is None


def test_the_other_services_entry_is_never_matched():
    assert setup_arr.find_existing([EXISTING_SONARR_ENTRY], "radarr") is None


def test_a_connection_of_another_implementation_is_never_matched():
    """Name alone is not the test: a `PlexServer` connection an operator named
    `Autoposter - Sonarr` would be PUT into a Webhook and lose its settings."""
    impostor = {"id": 5, "name": "Autoposter - Sonarr", "implementation": "PlexServer"}

    assert setup_arr.find_existing([impostor], "sonarr") is None


# --- the calls ---------------------------------------------------------------


async def test_an_absent_connection_is_created():
    """The residual from the diagnosis (section 7): a service with no existing
    Autoposter entry is created in TWO writes now, not one -- see the block
    below for why."""
    transport, seen = _arr_transport([])

    action, failure = await setup_arr.register(
        "sonarr", SONARR_BASE, APIKEY, PUBLIC_URL, SECRET, transport=transport
    )

    assert (action, failure) == ("created", None)
    assert [request.method for request in seen] == ["GET", "POST", "PUT"]
    assert seen[1].url.path == "/api/v3/notification"
    assert json.loads(seen[1].content)["name"] == "Autoposter - Sonarr"


# --- the create-path residual: a fresh *arr still tests on CREATE -----------
#
# `ProviderControllerBase.CreateProvider` runs its save-time connection test
# whenever the definition's derived `Enable` is true, and that is NOT gated on
# `forceSave` the way `UpdateProvider`'s is (diagnosis doc section 4). `Enable`
# is true the instant any `on*` flag is ticked, so a fresh registration -- no
# existing Autoposter entry to find -- would still 400 for exactly the reason
# row 187 fixed for updates. The fix: create disabled (every flag `False`,
# `Enable` false, no test at all), then enable with a second write through the
# UPDATE path, where `forceSave` already skips the test.


async def test_a_created_connection_is_two_writes_post_then_put():
    transport, seen = _arr_transport([])

    action, failure = await setup_arr.register(
        "sonarr", SONARR_BASE, APIKEY, PUBLIC_URL, SECRET, transport=transport
    )

    assert (action, failure) == ("created", None)
    assert [request.method for request in seen] == ["GET", "POST", "PUT"]
    # `{"id": 9}` is what the fake transport's write handler answers.
    assert seen[2].url.path == "/api/v3/notification/9"


async def test_the_create_post_carries_every_flag_off_and_forcesave():
    transport, seen = _arr_transport([])

    await setup_arr.register("sonarr", SONARR_BASE, APIKEY, PUBLIC_URL, SECRET, transport=transport)

    posted = json.loads(seen[1].content)
    assert all(posted[event] is False for event in setup_arr._EVENTS["sonarr"])
    assert seen[1].method == "POST"
    assert seen[1].url.params["forceSave"] == "true"


async def test_the_enabling_put_carries_the_accepted_flags_the_url_the_header_and_the_new_id():
    transport, seen = _arr_transport([])

    await setup_arr.register("sonarr", SONARR_BASE, APIKEY, PUBLIC_URL, SECRET, transport=transport)

    enabling = json.loads(seen[2].content)
    assert enabling["onDownload"] is True
    assert enabling["onUpgrade"] is True
    assert enabling["onRename"] is True
    assert enabling["onImportComplete"] is True
    assert enabling["onSeriesAdd"] is True
    assert enabling["onGrab"] is False
    assert enabling["id"] == 9
    assert _fields(enabling)["url"] == f"{PUBLIC_URL}/webhook/sonarr"
    assert _fields(enabling)["headers"] == [{"key": "X-Autoposter-Token", "value": SECRET}]
    assert seen[2].method == "PUT"
    assert seen[2].url.params["forceSave"] == "true"


async def test_a_failed_enabling_write_is_the_fixed_refusal_and_no_third_write():
    """The half-created disabled entry left behind is harmless -- it is found
    by NAME (`find_existing`) and turned on the next time this wizard runs.
    What must not happen is the *arr's own response body leaking, or a third
    write being attempted."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[])
        if request.method == "POST":
            return httpx.Response(200, json={"id": 9})
        return httpx.Response(400, json={"message": ARR_ERROR_BODY})

    action, failure = await setup_arr.register(
        "sonarr", SONARR_BASE, APIKEY, PUBLIC_URL, SECRET, transport=httpx.MockTransport(handler)
    )

    assert (action, failure) == (None, "HTTPStatus400")
    assert ARR_ERROR_BODY not in str(failure)
    assert [request.method for request in seen] == ["GET", "POST", "PUT"]


async def test_a_half_created_entry_is_finished_on_the_next_run():
    """The two-run reproduction of I1: run one's enabling PUT fails and leaves
    a disabled entry behind; run two's GET finds it by NAME and must send ONE
    PUT carrying the accepted flags -- not the dead ones it was left with --
    and report `("updated", None)`."""
    store: list[dict] = []
    # The first PUT ever seen (run one's enabling write) fails; any PUT after
    # that (run two's update write) succeeds and is applied to the stored
    # entry, the way a real *arr would persist it.
    put_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=store)
        if request.method == "POST":
            created = json.loads(request.content)
            created["id"] = 9
            store.append(created)
            return httpx.Response(200, json={"id": 9})
        put_count["n"] += 1
        if put_count["n"] == 1:
            return httpx.Response(400, json={"message": ARR_ERROR_BODY})
        written = json.loads(request.content)
        for index, stored in enumerate(store):
            if stored["id"] == written["id"]:
                store[index] = written
        return httpx.Response(200, json={"id": written["id"]})

    transport = httpx.MockTransport(handler)

    # Run one: GET (nothing) -> POST ok -> PUT fails -> the fixed refusal.
    action1, failure1 = await setup_arr.register(
        "sonarr", SONARR_BASE, APIKEY, PUBLIC_URL, SECRET, transport=transport
    )

    assert (action1, failure1) == (None, "HTTPStatus400")
    assert len(store) == 1
    assert all(store[0][event] is False for event in setup_arr._EVENTS["sonarr"])

    # Run two: GET finds the all-false leftover -- ONE PUT, the accepted
    # flags, the header, the url, forceSave=true, and "updated".
    seen2: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        seen2.append(request)
        return handler(request)

    action2, failure2 = await setup_arr.register(
        "sonarr",
        SONARR_BASE,
        APIKEY,
        PUBLIC_URL,
        SECRET,
        transport=httpx.MockTransport(recording_handler),
    )

    assert (action2, failure2) == ("updated", None)
    assert [request.method for request in seen2] == ["GET", "PUT"]
    updated = json.loads(seen2[1].content)
    assert updated["onDownload"] is True
    assert updated["onUpgrade"] is True
    assert updated["onRename"] is True
    assert updated["onImportComplete"] is True
    assert updated["onSeriesAdd"] is True
    assert updated["onGrab"] is False
    assert _fields(updated)["url"] == f"{PUBLIC_URL}/webhook/sonarr"
    assert _fields(updated)["headers"] == [{"key": "X-Autoposter-Token", "value": SECRET}]
    assert seen2[1].url.params["forceSave"] == "true"


async def test_an_existing_connection_is_updated_in_place():
    """Create-or-update, idempotent: a second run of the wizard must not leave
    two connections -- and the PUT it sends is the operator's own entry with
    two values refreshed."""
    transport, seen = _arr_transport([OTHER_ENTRY, EXISTING_SONARR_ENTRY])

    action, failure = await setup_arr.register(
        "sonarr", SONARR_BASE, APIKEY, PUBLIC_URL, SECRET, transport=transport
    )

    assert (action, failure) == ("updated", None)
    assert [request.method for request in seen] == ["GET", "PUT"]
    assert seen[1].url.path == "/api/v3/notification/2"
    sent = json.loads(seen[1].content)
    assert sent["onUpgrade"] is False
    assert sent["tags"] == [7]
    assert _fields(sent)["url"] == f"{PUBLIC_URL}/webhook/sonarr"
    assert _fields(sent)["headers"] == [{"key": "X-Autoposter-Token", "value": SECRET}]


async def test_the_create_write_carries_forcesave_so_the_arrs_connection_test_is_skipped():
    """The live-found fact: on saving a Webhook the *arr POSTs a test event to
    `fields[url]` and refuses the save on anything but a 200 back -- and that
    test can never pass during setup, because the secret it would be signed
    with is only staged here until finish. `forceSave=true` is the *arr's own
    switch that skips it, and it belongs on the API call, never inside the url
    this module registers."""
    transport, seen = _arr_transport([])

    await setup_arr.register("sonarr", SONARR_BASE, APIKEY, PUBLIC_URL, SECRET, transport=transport)

    assert seen[1].method == "POST"
    assert seen[1].url.params["forceSave"] == "true"
    # On the *arr API call, never inside the url this module registers.
    assert _fields(json.loads(seen[1].content))["url"] == f"{PUBLIC_URL}/webhook/sonarr"


async def test_the_update_write_carries_forcesave_too():
    transport, seen = _arr_transport([OTHER_ENTRY, EXISTING_SONARR_ENTRY])

    await setup_arr.register("sonarr", SONARR_BASE, APIKEY, PUBLIC_URL, SECRET, transport=transport)

    assert seen[1].method == "PUT"
    assert seen[1].url.params["forceSave"] == "true"


async def test_a_connection_of_another_implementation_is_never_overwritten():
    transport, seen = _arr_transport(
        [{"id": 5, "name": "Autoposter - Sonarr", "implementation": "PlexServer"}]
    )

    action, _failure = await setup_arr.register(
        "sonarr", SONARR_BASE, APIKEY, PUBLIC_URL, SECRET, transport=transport
    )

    assert action == "created"
    assert seen[1].method == "POST"


async def test_the_api_key_rides_the_x_api_key_header_on_every_call():
    transport, seen = _arr_transport([])

    await setup_arr.register("radarr", RADARR_BASE, APIKEY, PUBLIC_URL, SECRET, transport=transport)

    assert all(request.headers["X-Api-Key"] == APIKEY for request in seen)
    assert all(APIKEY not in str(request.url) for request in seen)


async def test_a_401_is_the_credential_refusal_and_never_the_arrs_body():
    transport, _seen = _arr_transport([], write_status=401)

    action, failure = await setup_arr.register(
        "sonarr", SONARR_BASE, APIKEY, PUBLIC_URL, SECRET, transport=transport
    )

    assert action is None
    assert failure == "refused"


async def test_a_listing_that_refuses_the_key_never_reaches_a_write():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": ARR_ERROR_BODY})

    action, failure = await setup_arr.register(
        "sonarr",
        SONARR_BASE,
        APIKEY,
        PUBLIC_URL,
        SECRET,
        transport=httpx.MockTransport(handler),
    )

    assert (action, failure) == (None, "refused")


async def test_a_400_never_echoes_the_arrs_response_body():
    """A 400 from Sonarr echoes the submitted `fields` -- which contains the
    secret. Reported as a status marker and nothing else."""
    transport, _seen = _arr_transport([], write_status=400)

    action, failure = await setup_arr.register(
        "sonarr", SONARR_BASE, APIKEY, PUBLIC_URL, SECRET, transport=transport
    )

    assert action is None
    assert failure == "HTTPStatus400"
    assert ARR_ERROR_BODY not in str(failure)
    assert SECRET not in str(failure)


async def test_a_redirect_is_not_followed():
    """`follow_redirects=False`, the check module's rule: a redirect off the
    address the operator gave is not a place this API key should go."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://elsewhere.invalid/"})

    action, failure = await setup_arr.register(
        "sonarr",
        SONARR_BASE,
        APIKEY,
        PUBLIC_URL,
        SECRET,
        transport=httpx.MockTransport(handler),
    )

    assert (action, failure) == (None, "HTTPStatus302")


async def test_a_transport_error_is_the_class_name():
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to " + SONARR_BASE)

    action, failure = await setup_arr.register(
        "sonarr",
        SONARR_BASE,
        APIKEY,
        PUBLIC_URL,
        SECRET,
        transport=httpx.MockTransport(explode),
    )

    assert action is None
    assert failure == "ConnectError"


@pytest.mark.parametrize(
    "address",
    [
        # A query string would compose `/api/v3/notification` onto a url that
        # already ended in one.
        "http://sonarr.invalid:8989/?x=1",
        "file:///etc/passwd",
        "http://row-121-user:row-121-pass@sonarr.invalid:8989",
        "sonarr.invalid:8989",
        "",
    ],
)
async def test_an_address_the_guard_refuses_makes_no_request_at_all(address):
    """`api/setup._require_http_url`'s rule, held again HERE rather than
    inherited: this function takes a base address as an argument and must not
    depend on a caller having guarded it first."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    action, failure = await setup_arr.register(
        "sonarr",
        address,
        APIKEY,
        PUBLIC_URL,
        SECRET,
        transport=httpx.MockTransport(handler),
    )

    assert (action, failure) == (None, "UnsupportedAddress")
    assert seen == []


async def test_a_listing_larger_than_the_cap_is_a_class_name_and_not_a_pod():
    """The timeout bounds TIME and not SIZE. `setup_plex`'s argument exactly: a
    listing read unbounded off an operator-supplied address is gigabytes into a
    pod with a memory limit. A truncated head fails to parse and is reported the
    way every other failure is."""
    oversized = json.dumps([{"id": 1, "name": "x" * setup_arr.ARR_BODY_LIMIT_BYTES}])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=oversized.encode())

    action, failure = await setup_arr.register(
        "sonarr",
        SONARR_BASE,
        APIKEY,
        PUBLIC_URL,
        SECRET,
        transport=httpx.MockTransport(handler),
    )

    assert action is None
    assert failure == "JSONDecodeError"


async def test_the_whole_call_is_bounded_by_one_total_timeout(monkeypatch):
    """`asyncio.wait_for` over the WHOLE call, `setup_checks.run_check`'s idiom
    and for its reason: httpx's own `timeout=` is per operation, so a service
    that emits one byte inside every window gets a fresh one for each and holds
    this handler until the size cap is reached."""
    monkeypatch.setattr(setup_arr, "ARR_TIMEOUT_SECONDS", 0.1)
    offered = 0
    chunks = 200

    async def drip():
        nonlocal offered
        for _ in range(chunks):
            offered += 1
            yield b"x"
            await asyncio.sleep(0.02)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=drip())

    action, failure = await setup_arr.register(
        "sonarr",
        SONARR_BASE,
        APIKEY,
        PUBLIC_URL,
        SECRET,
        transport=httpx.MockTransport(handler),
    )

    assert (action, failure) == (None, "TimeoutError")
    # The bound and not the fixture: the drip was cut short.
    assert offered < chunks


async def test_no_call_logs_the_address_the_key_or_the_secret(caplog):
    """`caplog.set_level(logging.DEBUG)` deliberately defeats `boot.main`'s
    process-wide clamp: httpx logs one INFO line per request with the FULL url,
    and row 213 is this module's own property rather than another file's
    setting."""
    caplog.set_level(logging.DEBUG)
    transport, _seen = _arr_transport([OTHER_ENTRY, EXISTING_SONARR_ENTRY])

    await setup_arr.register("sonarr", SONARR_BASE, APIKEY, PUBLIC_URL, SECRET, transport=transport)

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert APIKEY not in text
    assert SECRET not in text
    assert SONARR_BASE not in text


# --- the endpoint ------------------------------------------------------------
#
# The fixtures below are declared rather than imported, following
# `test_api_setup_plex.py`: a fixture imported into this namespace and then
# named as a test parameter is an F811 redefinition, and this repository imports
# CONSTANTS across test modules and not fixtures. `isolated_state` is the
# exception it already makes -- it is autouse, so it is never named as a
# parameter.


@pytest.fixture
def setup_app():
    return setup_api.build_setup_app()


@pytest_asyncio.fixture
async def setup_client(setup_app):
    transport = httpx.ASGITransport(app=setup_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def setup_state(setup_app):
    """The in-memory `SetupState` behind `setup_client`. The address a check
    staged and the secret the provider step minted are never served back, so
    the tests that arrange them have to reach the object."""
    return setup_app.state.setup


async def _staged(client, state, token, *, address=True, url=True, key=True, secret=True):
    """Put the wizard in the state the registration needs.

    The deployment URL goes through its OWN route, because that one has a
    public entry point. The other three do not: an address is staged by a
    SUCCESSFUL check against a live service, and the two credentials by the
    provider step's own mint and save -- arranging either through its route
    would need a network this suite does not have.
    """
    if url:
        response = await client.post(
            "/api/setup/public-url", json={"url": PUBLIC_URL}, headers=_headers(token)
        )
        assert response.status_code == 200, response.text
    if address:
        state.base_urls["sonarr"] = SONARR_BASE
    if key:
        state.staged["AUTOPOSTER_SONARR_APIKEY"] = APIKEY
    if secret:
        state.staged["AUTOPOSTER_WEBHOOK_SECRET"] = SECRET


def _answers(action, failure):
    async def registered(*_args, **_kwargs):
        return action, failure

    return registered


async def test_the_route_reports_created(setup_client, setup_state, monkeypatch):
    monkeypatch.setattr(setup_arr, "register", _answers("created", None))
    token = await _authenticate(setup_client)
    await _staged(setup_client, setup_state, token)

    response = await setup_client.post(
        "/api/setup/arr/webhook", json={"service": "sonarr"}, headers=_headers(token)
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "ok": True,
        "action": "created",
        "detail": "Sonarr accepted the webhook registration (created). No test was sent; "
        "Sonarr will exercise the hook on its first real event.",
    }


async def test_the_route_reports_updated_as_its_own_word(setup_client, setup_state, monkeypatch):
    """Facts C2a asks for "updated" against "created" ON THE FINISH PAGE, so the
    word has to survive the route rather than be flattened into a boolean."""
    monkeypatch.setattr(setup_arr, "register", _answers("updated", None))
    token = await _authenticate(setup_client)
    await _staged(setup_client, setup_state, token)

    response = await setup_client.post(
        "/api/setup/arr/webhook", json={"service": "sonarr"}, headers=_headers(token)
    )

    assert response.json() == {
        "ok": True,
        "action": "updated",
        "detail": "Sonarr accepted the webhook registration (updated). No test was sent; "
        "Sonarr will exercise the hook on its first real event.",
    }


async def test_the_route_registers_with_the_checked_address_and_the_staged_secret(
    setup_client, setup_state, monkeypatch
):
    """The precondition wiring, asserted rather than assumed: the address comes
    from `base_urls` -- which only a SUCCESSFUL check writes -- the API key and
    the secret from the staged credentials, and the callback base from the URL
    step."""
    seen = {}

    async def capture(service, base_url, api_key, public_url, secret):
        seen.update(
            service=service,
            base_url=base_url,
            api_key=api_key,
            public_url=public_url,
            secret=secret,
        )
        return "created", None

    monkeypatch.setattr(setup_arr, "register", capture)
    token = await _authenticate(setup_client)
    await _staged(setup_client, setup_state, token)

    await setup_client.post(
        "/api/setup/arr/webhook", json={"service": "sonarr"}, headers=_headers(token)
    )

    assert seen == {
        "service": "sonarr",
        "base_url": SONARR_BASE,
        "api_key": APIKEY,
        "public_url": PUBLIC_URL,
        "secret": SECRET,
    }


async def test_a_concurrent_second_call_is_refused_without_reaching_the_arr(
    setup_client, setup_state, monkeypatch
):
    """Review I1: the register button has no `disabled`, so a double press can
    overlap two calls, and an overlapping pair both lists an empty
    registration and both creates -- the duplicate facts C2a exists to
    prevent. The in-flight guard refuses the second outright, and the fake
    transport proves only one create sequence ever reached the *arr.
    """
    entered = asyncio.Event()
    release = asyncio.Event()
    transport, seen = _arr_transport([])
    real_register = setup_arr.register

    async def gated(service, base_url, api_key, public_url, secret, transport=transport):
        entered.set()
        await release.wait()
        return await real_register(
            service, base_url, api_key, public_url, secret, transport=transport
        )

    monkeypatch.setattr(setup_arr, "register", gated)
    token = await _authenticate(setup_client)
    await _staged(setup_client, setup_state, token)

    first_task = asyncio.create_task(
        setup_client.post(
            "/api/setup/arr/webhook", json={"service": "sonarr"}, headers=_headers(token)
        )
    )
    await entered.wait()

    second = await setup_client.post(
        "/api/setup/arr/webhook", json={"service": "sonarr"}, headers=_headers(token)
    )
    release.set()
    first = await first_task

    assert first.json() == {
        "ok": True,
        "action": "created",
        "detail": "Sonarr accepted the webhook registration (created). No test was sent; "
        "Sonarr will exercise the hook on its first real event.",
    }
    assert second.json() == {
        "ok": False,
        "action": None,
        "detail": "Sonarr's webhook registration is already in progress.",
    }
    # The whole point: the second call never listed, created, or enabled.
    assert [request.method for request in seen] == ["GET", "POST", "PUT"]


async def test_the_in_flight_flag_is_freed_after_the_call_so_a_later_one_still_works(
    setup_client, setup_state, monkeypatch
):
    monkeypatch.setattr(setup_arr, "register", _answers("created", None))
    token = await _authenticate(setup_client)
    await _staged(setup_client, setup_state, token)

    first = await setup_client.post(
        "/api/setup/arr/webhook", json={"service": "sonarr"}, headers=_headers(token)
    )
    second = await setup_client.post(
        "/api/setup/arr/webhook", json={"service": "sonarr"}, headers=_headers(token)
    )

    assert first.json()["ok"] is True
    assert second.json()["ok"] is True
    assert "already in progress" not in second.text


async def test_the_route_reports_a_refusal_as_the_checks_own_sentence(
    setup_client, setup_state, monkeypatch
):
    monkeypatch.setattr(setup_arr, "register", _answers(None, "refused"))
    token = await _authenticate(setup_client)
    await _staged(setup_client, setup_state, token)

    response = await setup_client.post(
        "/api/setup/arr/webhook", json={"service": "sonarr"}, headers=_headers(token)
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": False,
        "action": None,
        "detail": "Sonarr refused the credential.",
    }


async def test_the_route_reports_a_failure_as_the_class_name(
    setup_client, setup_state, monkeypatch
):
    monkeypatch.setattr(setup_arr, "register", _answers(None, "ConnectError"))
    token = await _authenticate(setup_client)
    await _staged(setup_client, setup_state, token)

    response = await setup_client.post(
        "/api/setup/arr/webhook", json={"service": "sonarr"}, headers=_headers(token)
    )

    assert response.json()["ok"] is False
    assert response.json()["detail"] == (
        "Sonarr would not accept the webhook registration (ConnectError)."
    )


async def test_an_unknown_service_is_one_fixed_sentence(setup_client):
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/arr/webhook",
        json={"service": "row-121-not-a-service"},
        headers=_headers(token),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.NOT_A_SERVICE_THIS_WIZARD_REGISTERS
    assert "row-121-not-a-service" not in response.text


async def test_plex_is_not_a_service_this_route_registers(setup_client):
    """A system the CHECK endpoint knows is not therefore one this registers:
    `NAMES` is the allowlist here, and it has two entries."""
    token = await _authenticate(setup_client)

    response = await setup_client.post(
        "/api/setup/arr/webhook", json={"service": "plex"}, headers=_headers(token)
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.NOT_A_SERVICE_THIS_WIZARD_REGISTERS


async def test_registering_without_a_checked_address_says_which_step_is_missing(
    setup_client, setup_state
):
    """The address is staged by a SUCCESSFUL check and by nothing else, so
    "check it first" is the honest instruction -- and it is a step name, never a
    value."""
    token = await _authenticate(setup_client)
    await _staged(setup_client, setup_state, token, address=False)

    response = await setup_client.post(
        "/api/setup/arr/webhook", json={"service": "sonarr"}, headers=_headers(token)
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.NO_CHECKED_ADDRESS


async def test_registering_without_the_deployment_url_says_which_step_is_missing(
    setup_client, setup_state
):
    token = await _authenticate(setup_client)
    await _staged(setup_client, setup_state, token, url=False)

    response = await setup_client.post(
        "/api/setup/arr/webhook", json={"service": "sonarr"}, headers=_headers(token)
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.NO_DEPLOYMENT_URL


async def test_registering_without_the_key_or_the_secret_names_the_provider_step(
    setup_client, setup_state
):
    token = await _authenticate(setup_client)
    await _staged(setup_client, setup_state, token, key=False)

    response = await setup_client.post(
        "/api/setup/arr/webhook", json={"service": "sonarr"}, headers=_headers(token)
    )

    assert response.status_code == 400
    assert response.json()["detail"] == setup_api.STEP_PROVIDERS


async def test_no_response_from_this_route_carries_the_secret(
    setup_client, setup_state, monkeypatch
):
    monkeypatch.setattr(setup_arr, "register", _answers(None, "HTTPStatus400"))
    token = await _authenticate(setup_client)
    await _staged(setup_client, setup_state, token)

    response = await setup_client.post(
        "/api/setup/arr/webhook", json={"service": "sonarr"}, headers=_headers(token)
    )

    assert SECRET not in response.text
    assert APIKEY not in response.text
    assert SONARR_BASE not in response.text


async def test_the_route_logs_the_step_and_never_the_address_or_the_secret(
    setup_client, setup_state, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(setup_arr, "register", _answers("created", None))
    token = await _authenticate(setup_client)
    await _staged(setup_client, setup_state, token)

    await setup_client.post(
        "/api/setup/arr/webhook", json={"service": "sonarr"}, headers=_headers(token)
    )

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert SECRET not in text
    assert APIKEY not in text
    assert SONARR_BASE not in text
    assert PUBLIC_URL not in text


async def test_a_failed_registration_never_blocks_finish(setup_client, setup_state, monkeypatch):
    """Facts C3, and the property the whole design turns on: a third-party
    outage must not produce an unfinishable wizard. The operator can paste the
    secret by hand, which is what they do today."""
    monkeypatch.setattr(setup_arr, "register", _answers(None, "ConnectError"))
    token = await _authenticate(setup_client)
    await _staged(setup_client, setup_state, token)
    await setup_client.post(
        "/api/setup/arr/webhook", json={"service": "sonarr"}, headers=_headers(token)
    )

    # The finish gate is unchanged: it reads credentials and the document, and
    # has never had an opinion about registrations.
    unmet = setup_api._unmet_step(setup_state.staged, config_ready=True)

    assert unmet == setup_api.STEP_DATABASE  # the database, not the registration
