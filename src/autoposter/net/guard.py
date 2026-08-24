"""Fetching a URL an operator typed, rather than one a provider produced.

Until 6e every URL this service requested came out of a provider client that
had just parsed it from that provider's own JSON, and the pick endpoint
(``api/candidates.py``) kept it that way on purpose: it re-ran the browse
fan-out and refused any ``(provider, url)`` pair the fan-out had not just
returned. The set of fetchable URLs was therefore whatever TMDB, TVDB and
Fanart handed us, and no caller could steer it.

Manual mode gives up that property deliberately -- the whole feature is "use
this image" -- so the property has to be replaced with a real one. Without a
guard, an authenticated operator (or anything that reaches an authenticated
session) can make this process issue a GET to any address the container can
route to and the caller cannot: the cloud metadata service on
``169.254.169.254``, the Plex admin port, a database's HTTP console, another
container on the compose network. That is server-side request forgery, and it
is the security invariant of this phase.

What is checked, in order, for the first request and again for **every**
redirect hop:

1. the scheme is ``http`` or ``https`` -- ``file://`` is the arbitrary-file-
   read spelling of the same hole;
2. the host resolves, and **every** address it resolves to is a public one.
   Every, not the first: a dual-stack or round-robin name answers with a list,
   and connecting to whichever address the OS then picks makes a check of
   ``addresses[0]`` no check at all;
3. redirects are off at the httpx level and followed by hand, at most
   ``MAX_REDIRECTS`` times. ``follow_redirects=True`` would follow a 302 into
   ``169.254.169.254`` inside a single call, with the guard having validated
   only the URL the operator typed.

Then the body guards 6d already had: an error status, a Content-Type outside
the caller's allowlist, an empty body or one past the byte cap is refused
before anything downstream sees it.

**The residual, stated rather than papered over.** The addresses validated
here are the ones ``getaddrinfo`` returned at validation time; httpx then
resolves the name again for itself when it opens the connection. A DNS record
that changes between those two moments -- a rebinding attack -- is therefore
not stopped by this module. Closing it means pinning the connection to a
validated address (a custom transport, or connecting to the IP with the
original ``Host`` header, which breaks TLS verification unless SNI is set by
hand). That is out of scope for this deployment: this is a single-operator
service behind a session, the window is the length of one DNS TTL, and the
guard's job here is to stop the operator-supplied URL, not a determined
attacker who also controls a nameserver. Documented so a later reader knows
it was weighed and not missed.

**Also not stopped: NAT64 and 6to4 literals.** ``ipaddress`` does not classify
``64:ff9b::/96`` (NAT64) or ``2002::/16`` (6to4) as reserved, so a literal
address in either range passes ``_address_refusal`` uncaught. Both are only
exploitable with a NAT64 or 6to4 translator sitting on the network path, which
this deployment's networking does not have -- so, like DNS rebinding above,
this is named rather than range-checked: an untested check against a residual
nothing here can trigger is worse than an honest docstring.

**``100.64.0.0/10`` (RFC 6598, "shared address space" -- CGNAT, and this
deployment's own Tailscale range) is checked, not left as a residual like the
two ranges above.** Unlike NAT64 and 6to4, this needs no translator on the
path: a service listening on a ``100.64.0.0/10`` address is a direct fetch
target the moment ``getaddrinfo`` resolves a name onto it, or an operator
types the literal. ``ipaddress`` classifies neither ``is_private`` nor
``is_global`` as ``True`` for this range -- its own ``is_private`` docstring
calls it out by name as the one range where the two properties are not
opposites -- so ``_address_refusal`` checks membership in the network
explicitly rather than folding it into the property loop above. A blanket
``not is_global`` would also catch it, but ``is_global`` is ``False`` for
every unassigned/reserved range too (and would need auditing against this
deployment's real target set to rule out over-rejection); the explicit
network is the narrower, unambiguous fix for the one range actually in use
here.

**A refusal never names the URL.** The pick endpoint could log a candidate
URL's host, because a provider client had just produced it. A source typed by
an operator is a different object: it can carry ``user:password@`` userinfo, a
signed query parameter, or the name of an internal host that is itself worth
not writing into a log line that gets pasted into a ticket. The exceptions
below carry the reason and the hop, and nothing else -- callers put those in
an HTTP detail, so the same rule keeps the endpoints from being reflectors.
"""
import asyncio
import ipaddress
import logging
import socket
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Hops after the first request. Real image hosts redirect (a CDN edge, a
# canonical-host rewrite, an http->https upgrade), so zero would break
# legitimate sources; a chain that never terminates would otherwise hold a
# worker and a socket for as long as the other end cared to keep answering.
MAX_REDIRECTS = 3

# 303 is in here as well as the 30x pair: a GET that is answered "see other"
# is still a GET somewhere else, which is exactly the thing being validated.
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

_DEFAULT_PORTS = {"http": 80, "https": 443}

# RFC 6598 "shared address space" (CGNAT). ``ipaddress`` reports
# ``is_private=False`` and ``is_global=False`` for the whole range -- neither
# property below catches it -- so it needs its own membership check. See the
# module docstring for why this one residual is checked rather than named.
_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")


class FetchRefused(Exception):
    """This service declined to fetch, or to keep, what it was pointed at.

    Its message carries the reason and (for a redirect) the hop, never the
    URL or the host -- see the module docstring.
    """


class TargetRefused(FetchRefused):
    """The request was never made: the URL failed the guard."""


class BodyRefused(FetchRefused):
    """The request was made and the response is not usable artwork."""


def resolve_host(host: str, port: int) -> list[str]:
    """Every address ``host`` resolves to, as strings.

    ``AF_UNSPEC`` (the default), so both A and AAAA records come back -- a
    guard that asked only for IPv4 would validate an address the connection
    then does not use. Blocking, so callers hop off the event loop; a module
    attribute rather than an inline call so tests can substitute a resolver
    without touching the network.
    """
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _address_refusal(address: str) -> str | None:
    """The reason this address is off limits, or ``None`` if it is fine.

    ``is_private`` alone would cover most of these in CPython's current
    tables, but the categories are listed one by one because they are the
    thing being asserted: a future release moving a range between properties
    must not silently open one of them.
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        # Nothing should reach here -- getaddrinfo returns addresses -- but a
        # value that cannot be classified is refused rather than trusted.
        return "an address that could not be classified"
    # No ipv4_mapped unwrap: on CPython 3.14 every property below already
    # delegates through ``ipv4_mapped``, so ``::ffff:169.254.169.254`` -- the
    # metadata service in its IPv6 spelling -- is flagged as link-local and
    # private without help, and an unwrap here would be a branch no input can
    # reach and no test can falsify. The mapped spellings are in
    # ``tests/test_fetch_guard.py``'s table instead, so a stdlib release that
    # stopped delegating turns into a failing test rather than a silent hole.
    for name, blocked in (
        ("loopback", ip.is_loopback),
        ("link-local", ip.is_link_local),
        ("private", ip.is_private),
        ("reserved", ip.is_reserved),
        ("multicast", ip.is_multicast),
        ("unspecified", ip.is_unspecified),
    ):
        if blocked:
            return f"the host resolves to a {name} address"
    # Not a property, unlike the loop above: membership on an IPv4Network
    # does NOT auto-unwrap an IPv4-mapped IPv6 address the way is_private and
    # friends do, so the unwrap the comment above says is unneeded elsewhere
    # is needed here, or ``::ffff:100.64.0.1`` would pass this check.
    candidate = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) else ip
    if isinstance(candidate, ipaddress.IPv4Address) and candidate in _SHARED_ADDRESS_SPACE:
        return "the host resolves to a shared address space (CGNAT, RFC 6598) address"
    return None


async def validate_target(url: httpx.URL, hop: int) -> None:
    """Refuse ``url`` unless it is a public http(s) target.

    Called for the operator's own URL and again, unchanged, for every
    redirect location: a hop that skipped this is a hop an attacker chooses.
    """
    if url.scheme not in ALLOWED_SCHEMES:
        raise TargetRefused(f"the scheme is not http or https (hop {hop})")
    host = url.host
    if not host:
        raise TargetRefused(f"the URL has no host (hop {hop})")
    port = url.port or _DEFAULT_PORTS[url.scheme]
    try:
        # Off the loop: a resolver that is slow or wedged must not stall the
        # workers, the scheduler and the liveness probe with it.
        addresses = await asyncio.to_thread(resolve_host, host, port)
    except OSError as exc:
        raise TargetRefused(f"the host does not resolve (hop {hop})") from exc
    if not addresses:
        raise TargetRefused(f"the host does not resolve (hop {hop})")
    for address in addresses:
        reason = _address_refusal(address)
        if reason is not None:
            raise TargetRefused(f"{reason} (hop {hop})")


async def store_body(
    response: httpx.Response,
    destination: Path,
    *,
    max_bytes: int,
    content_types: frozenset[str],
) -> None:
    """Keep a streamed response's body, or refuse it.

    Lifted verbatim out of ``api/candidates._download_artwork`` (6d) so the
    manual endpoints get the same three checks rather than a second copy of
    them that drifts: an error status, a Content-Type outside the allowlist,
    and a body that is empty or past the cap.

    The cap is enforced chunk by chunk rather than from ``Content-Length``:
    the header is the other end's claim, and a body that simply never stops
    arriving does not send one at all.
    """
    # Not raise_for_status(): its message embeds the request URL, and this
    # exception's text reaches a log line and an HTTP detail.
    if response.status_code >= 400:
        raise BodyRefused(f"status {response.status_code}")
    media_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type not in content_types:
        raise BodyRefused("content type outside the artwork allowlist")
    size = 0
    with destination.open("wb") as handle:
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise BodyRefused("image exceeded the size cap")
            handle.write(chunk)
    if size == 0:
        raise BodyRefused("empty body")


async def guarded_download(
    http: httpx.AsyncClient,
    url: str,
    destination: Path,
    *,
    max_bytes: int,
    content_types: frozenset[str],
) -> None:
    """Fetch ``url`` to ``destination``, or raise ``FetchRefused``.

    ``follow_redirects=False`` is passed per request rather than configured on
    the client: ``app.state.http`` is shared with the provider clients and the
    collection poster fetcher, and changing its defaults would change their
    behaviour too.

    The caller still decodes what lands here -- a Content-Type is a claim, and
    only a decoder settles it (``_prepare_jpeg``/``_verify_image``).
    """
    try:
        current = httpx.URL(url)
    except httpx.InvalidURL as exc:
        # str(exc) embeds the caller's string.
        raise TargetRefused("the source is not a parsable URL") from exc

    for hop in range(MAX_REDIRECTS + 1):
        await validate_target(current, hop)
        async with http.stream("GET", current, follow_redirects=False) as response:
            if response.status_code in REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise TargetRefused(f"a redirect carrying no location (hop {hop})")
                try:
                    # Against the URL that sent it, so a relative or
                    # protocol-relative Location is validated as the absolute
                    # target it actually resolves to.
                    current = current.join(location)
                except (httpx.InvalidURL, ValueError) as exc:
                    raise TargetRefused(
                        f"a redirect location that does not parse (hop {hop})"
                    ) from exc
                logger.debug("following a redirect at hop %d", hop)
                continue
            await store_body(
                response, destination, max_bytes=max_bytes, content_types=content_types
            )
            return
    raise TargetRefused(f"more than {MAX_REDIRECTS} redirects")
