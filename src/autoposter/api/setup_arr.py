"""Point Radarr's and Sonarr's Webhook connection at this deployment.

The shape is settled by demonstration rather than inference: probe 2 read the
``Webhook`` entry off ``GET /api/v3/notification/schema`` on Sonarr 4.0.18.2978
and Radarr 6.4.0.10523 (identical five-field list, ``configContract``
``WebhookSettings``, ``method`` default ``1``), and read the operator's own live
entries off ``GET /api/v3/notification`` -- which already do exactly what this
module automates: the secret in a ``headers`` entry keyed
``X-Autoposter-Token``, the url with path ``/webhook/{service}`` and no query
string, ``method: 1``. ``/api/v5/connection``, ``/api/v4/notification`` and
``/api/v3/connection`` all 404 on both services: v3 ``notification`` is the
route and the only one.

**Create-or-update, and the update is a PATCH in spirit (facts C2a, the user's
2026-09-07 ruling).** The listing is read first and an existing Autoposter
Webhook is looked for by the per-service NAME or by a url whose path is
``/webhook/<service>`` -- two tests because the operator's own hand-made entry
may be called anything, and creating a second one beside it is the duplicate
the ruling exists to prevent. A found entry is PUT back with exactly two values
changed: ``fields[url]``, and the ``X-Autoposter-Token`` entry inside
``fields[headers]`` (added when it is absent, because the wizard has just
minted a fresh secret and last run's header would now 401). Every other field,
every other header entry and every ``on*`` flag goes back exactly as the
operator left it. A wizard that re-imposed this module's own defaults would
silently undo whatever they tuned since the last run, and it has no way to know
they did not mean it.

Three things are load-bearing in a NEW registration and nothing else is:

1. ``fields[url] = "{public_url}/webhook/{service}"`` -- a TOP-LEVEL path, not
   ``/api/webhook/...`` (``app.py`` includes the intake router with no prefix),
   and never a query string. The 2026-09-05 amendment to row 213 rules a
   token-bearing URL out: this string is stored in the *arr's database, shown in
   its UI, written to its logs, and displayed on the wizard's finish page.
2. ``fields[method] = 1`` -- POST, the schema's own default and both live
   entries' value.
3. ``fields[headers] = [{"key": "X-Autoposter-Token", "value": secret}]`` -- the
   header ``intake/routes.py`` actually reads. ``username``/``password`` produce
   an HTTP Basic ``Authorization`` header, which no code path in ``src/`` reads;
   Radarr's live entry has both set and they are doing nothing.

Two bodies from two tables rather than one with nulls: Sonarr 400s on
``onMovieAdded`` and Radarr on ``onSeriesAdd``. The ticked events are exactly
the ones ``intake/arr.py`` accepts, plus the two that also arrive as
``eventType: "Download"``. Anything else is a delivery Autoposter parses into
zero intents and writes an ``EventLog`` row for.

The outbound bound is ``setup_checks``' idiom held again rather than inherited,
because this module makes its own calls to an operator-supplied address:
``follow_redirects=False``, the address through this module's own scheme /
userinfo / query guard, the listing body read to a cap, the WHOLE call inside
one ``asyncio.wait_for``, and httpx's own request log filtered for the length
of it -- that url carries no credential but it is the operator's address, and
row 213 covers those too.

Nothing here reads the *arr's own response body into anything returned or
logged: a 400 from Sonarr echoes the submitted ``fields``, secret included. A
failure is a status marker or an exception CLASS NAME, and never raises -- a
failed registration must not block the wizard's finish (facts C3). The operator
can paste the secret by hand, which is what they do today, and a registration
that gated the exit would turn a third-party outage into an unfinishable wizard.
"""

import asyncio
import json
from urllib.parse import urlsplit

import httpx

from autoposter.api.setup_checks import no_httpx_request_log

# The whole call, list and write together. `setup_plex`'s value rather than the
# check probes' five seconds: this is two round trips to a service that has just
# been proved reachable, and the second one writes.
ARR_TIMEOUT_SECONDS = 10.0
# The most of a listing this module will hold. The timeout bounds TIME and not
# SIZE, and the address is the operator's: ten seconds of a private network is
# gigabytes into a pod with a memory limit. Far above any real notification
# listing (a handful of small objects) and far below a number the pod notices.
ARR_BODY_LIMIT_BYTES = 1024 * 1024

NOTIFICATION_PATH = "/api/v3/notification"
# The header `intake/routes.py` reads, and the key both live entries already use.
TOKEN_HEADER = "X-Autoposter-Token"

# The names already on the deployment probe 2 read (recon section 3.2). A
# registration called plain "Autoposter" would create a SECOND connection beside
# each of them.
NAMES = {
    "radarr": "Autoposter - Radarr",
    "sonarr": "Autoposter - Sonarr",
}

# Per service, because the two schemas reject each other's flags. `True` is
# ticked; every listed `False` is deliberate -- an untouched flag would take the
# schema's default, which is not always False. Applied to a NEW registration
# only: an existing one keeps the operator's own (facts C2a).
_EVENTS = {
    "sonarr": {
        "onDownload": True,
        "onUpgrade": True,
        "onRename": True,
        "onImportComplete": True,
        "onSeriesAdd": True,
        "onGrab": False,
        "onSeriesDelete": False,
        "onEpisodeFileDelete": False,
        "onEpisodeFileDeleteForUpgrade": False,
        "onHealthIssue": False,
        "onHealthRestored": False,
        "onApplicationUpdate": False,
        "onManualInteractionRequired": False,
    },
    "radarr": {
        "onDownload": True,
        "onUpgrade": True,
        "onRename": True,
        "onMovieAdded": True,
        "onGrab": False,
        "onMovieDelete": False,
        "onMovieFileDelete": False,
        "onMovieFileDeleteForUpgrade": False,
        "onHealthIssue": False,
        "onHealthRestored": False,
        "onApplicationUpdate": False,
        "onManualInteractionRequired": False,
    },
}


class UnsupportedAddress(Exception):
    """The base address is not a plain http(s) origin this module will call.

    Its own exception rather than ``api.setup``'s ``HTTPException`` guard: this
    module is imported BY that one, and a function whose whole contract is "it
    never raises" cannot hand back a framework error anyway. It reaches the
    caller as the class name every other failure reaches it as.
    """


def webhook_path(service: str) -> str:
    """The intake's own path for one service. One spelling, used to build the
    url AND to recognise an entry that already points at us."""
    return f"/webhook/{service}"


def _guarded_base_url(base_url: str) -> str:
    """``api/setup._require_http_url``'s rule, held here as well.

    Deliberately duplicated rather than imported: importing it would be an
    import cycle, it raises an ``HTTPException`` this module must not, and a
    guard that only runs in the caller is a guard that a second caller silently
    does without. Schemes are case-insensitive (RFC 3986); userinfo is refused
    rather than stripped; a query or fragment is refused because this module
    appends a fixed path to what it returns.
    """
    cleaned = base_url.strip()
    scheme, separator, rest = cleaned.partition("://")
    if scheme.lower() not in {"http", "https"} or not separator or not rest:
        raise UnsupportedAddress()
    if "?" in rest or "#" in rest:
        raise UnsupportedAddress()
    authority = rest.partition("/")[0]
    if "@" in authority or authority == "":
        raise UnsupportedAddress()
    return cleaned.rstrip("/")


def _field_value(entry: dict, name: str):
    for field in entry.get("fields") or []:
        if field.get("name") == name:
            return field.get("value")
    return None


def find_existing(entries: list, service: str) -> dict | None:
    """The Autoposter Webhook already on this *arr, or None (facts C2a).

    Two tests, either of which is enough, and both gated on the implementation
    actually being ``Webhook``:

    * the per-service NAME -- what this wizard and the operator's own live
      entries both use;
    * a url whose PATH is ``/webhook/<service>`` -- because an entry the
      operator made by hand may be called anything at all, and a registration
      that missed it would create a duplicate beside it, which is exactly what
      the ruling forbids.

    The path is compared whole and never as a substring: a relay whose url
    merely contains ours is somebody else's connection. And the implementation
    gate is not optional -- a ``PlexServer`` connection an operator happened to
    name ``Autoposter - Sonarr`` would otherwise be PUT into a Webhook and lose
    its settings.
    """
    wanted_name = NAMES[service]
    wanted_path = webhook_path(service)
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("implementation") != "Webhook":
            continue
        if entry.get("name") == wanted_name:
            return entry
        url = _field_value(entry, "url")
        if isinstance(url, str) and urlsplit(url).path.rstrip("/") == wanted_path:
            return entry
    return None


def _refreshed_headers(existing_headers, secret: str) -> list:
    """The operator's header list with OUR entry's value replaced.

    Every other entry survives in place -- ``headers`` is a keyValueList and an
    operator can legitimately have put their own proxy's header in it -- and
    ours is appended when there is none, which is the reachable shape of a
    webhook wired up without a secret at all.
    """
    headers = [dict(header) for header in (existing_headers or []) if isinstance(header, dict)]
    for header in headers:
        if header.get("key") == TOKEN_HEADER:
            header["value"] = secret
            return headers
    headers.append({"key": TOKEN_HEADER, "value": secret})
    return headers


def build_body(service: str, public_url: str, secret: str, existing: dict | None) -> dict:
    """The object a POST needs, or the object a PUT needs.

    With ``existing`` absent this is the whole registration, from the two
    tables above. With one present it is THAT ENTRY, deep-copied, with the url
    and the token header refreshed and nothing else touched (facts C2a) -- the
    copy matters because the caller reads the fetched listing again for the id
    it PUTs to.
    """
    if existing is None:
        return {
            "name": NAMES[service],
            "implementation": "Webhook",
            "implementationName": "Webhook",
            "configContract": "WebhookSettings",
            "tags": [],
            **_EVENTS[service],
            "includeHealthWarnings": False,
            "fields": [
                {"name": "url", "value": f"{public_url}{webhook_path(service)}"},
                {"name": "method", "value": 1},
                {"name": "username", "value": ""},
                {"name": "password", "value": ""},
                {"name": "headers", "value": [{"key": TOKEN_HEADER, "value": secret}]},
            ],
        }

    body = json.loads(json.dumps(existing))
    fields = body.get("fields")
    if not isinstance(fields, list):
        fields = []
        body["fields"] = fields

    seen = set()
    for field in fields:
        if field.get("name") == "url":
            field["value"] = f"{public_url}{webhook_path(service)}"
            seen.add("url")
        elif field.get("name") == "headers":
            field["value"] = _refreshed_headers(field.get("value"), secret)
            seen.add("headers")
    if "url" not in seen:
        fields.append({"name": "url", "value": f"{public_url}{webhook_path(service)}"})
    if "headers" not in seen:
        fields.append({"name": "headers", "value": [{"key": TOKEN_HEADER, "value": secret}]})
    return body


async def _capped_json(response: httpx.Response):
    """The head of a streamed listing, with the rest abandoned rather than read.

    A body over the cap is truncated, fails to parse, and is reported as the
    ``JSONDecodeError`` class name -- which is the honest answer and a bounded
    one.
    """
    head = bytearray()
    async for chunk in response.aiter_bytes():
        head += chunk
        if len(head) >= ARR_BODY_LIMIT_BYTES:
            break
    return json.loads(bytes(head[:ARR_BODY_LIMIT_BYTES]))


async def register(
    service: str,
    base_url: str,
    api_key: str,
    public_url: str,
    secret: str,
    transport: httpx.BaseTransport | None = None,
) -> tuple[str | None, str | None]:
    """Create or update, idempotently. Never raises.

    ``("created" | "updated", None)`` on success, and on failure
    ``(None, "refused" | "HTTPStatus<n>" | "<ClassName>")`` -- a marker or a
    class name, never the *arr's own text.

    ``transport`` is the test seam and nothing else; production passes ``None``.
    """
    headers = {"X-Api-Key": api_key, "accept": "application/json"}

    async def attempt() -> tuple[str | None, str | None]:
        origin = _guarded_base_url(base_url)
        with no_httpx_request_log():
            async with httpx.AsyncClient(
                transport=transport,
                timeout=ARR_TIMEOUT_SECONDS,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "GET", f"{origin}{NOTIFICATION_PATH}", headers=headers
                ) as listing:
                    if listing.status_code in (401, 403):
                        return None, "refused"
                    if not listing.is_success:
                        return None, f"HTTPStatus{listing.status_code}"
                    entries = await _capped_json(listing)

                existing = find_existing(entries if isinstance(entries, list) else [], service)
                body = build_body(service, public_url, secret, existing)

                if existing is None:
                    written = await client.post(
                        f"{origin}{NOTIFICATION_PATH}", headers=headers, json=body
                    )
                    action = "created"
                else:
                    written = await client.put(
                        f"{origin}{NOTIFICATION_PATH}/{existing['id']}",
                        headers=headers,
                        json=body,
                    )
                    action = "updated"

        if written.status_code in (401, 403):
            return None, "refused"
        if not written.is_success:
            # The status and nothing else: the body echoes the fields that were
            # sent, and one of them is the secret.
            return None, f"HTTPStatus{written.status_code}"
        return action, None

    try:
        return await asyncio.wait_for(attempt(), timeout=ARR_TIMEOUT_SECONDS)
    except Exception as exc:
        # The class name. httpx embeds the request URL in its own messages, and
        # that url is the operator's address.
        return None, type(exc).__name__
