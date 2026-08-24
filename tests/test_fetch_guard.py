"""``autoposter.net.guard`` -- the only place this service fetches an
operator-supplied URL from.

6d could get away without one: the pick endpoint re-ran the provider fan-out
and refused any URL that was not in the answer, so the set of fetchable URLs
was whatever three provider clients had just handed us. 6e takes an arbitrary
string from an authenticated operator, which makes this process a request
proxy for anything it can reach and the caller cannot -- the container network,
the Plex admin port, the cloud metadata service on 169.254.169.254.

Every test below asserts the transport's **request log**, not just the raised
exception. "It raised" would also be true of an implementation that made the
request and complained afterwards, by which time the packet has left the
container -- which is the whole vulnerability. Assertion order is the log
first, the exception second, for the same reason ``test_api_pick.py`` gives.

Nothing here resolves a real name: every URL either carries a literal IP (so
``getaddrinfo`` answers out of the string) or runs against a patched
``resolve_host``.
"""
from pathlib import Path

import httpx
import pytest

from autoposter.net.guard import (
    BodyRefused, FetchRefused, MAX_REDIRECTS, TargetRefused, guarded_download,
)

# A public literal, so no name is ever looked up: 93.184.216.34 is outside
# every range the guard rejects. TEST-NET (192.0.2.0/24) would NOT do here --
# Python's ipaddress marks the documentation ranges private.
PUBLIC = "93.184.216.34"
PUBLIC_URL = f"http://{PUBLIC}/art.png"
# What an SSRF attempt through the manual endpoints looks like.
METADATA_URL = "http://169.254.169.254/latest/meta-data/iam/security-credentials/"

CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
MAX_BYTES = 4 * 1024 * 1024
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class _RecordingTransport:
    """A MockTransport plus the request log the security tests assert on."""

    def __init__(self, response_for):
        self.requests: list[httpx.Request] = []
        self._response_for = response_for

    def client(self) -> httpx.AsyncClient:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self._response_for(request)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    @property
    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]


def _ok(content: bytes = PNG, content_type: str = "image/png"):
    return lambda request: httpx.Response(
        200, content=content, headers={"content-type": content_type}
    )


def _redirect_to(location: str, status: int = 302):
    return lambda request: httpx.Response(status, headers={"location": location})


@pytest.fixture
def transport() -> _RecordingTransport:
    return _RecordingTransport(_ok())


@pytest.fixture
def destination(tmp_path) -> Path:
    return tmp_path / "downloaded"


async def _download(transport: _RecordingTransport, url: str, destination: Path) -> None:
    async with transport.client() as http:
        await guarded_download(
            http, url, destination, max_bytes=MAX_BYTES, content_types=CONTENT_TYPES
        )


# --- the security invariant -------------------------------------------------


async def test_the_metadata_service_is_refused_and_never_requested(transport, destination):
    """The phase's security invariant, in one test.

    A raised exception alone would also be the answer from an implementation
    that fetched ``169.254.169.254`` and inspected the result -- at which point
    the credentials are already in this process. The request log is therefore
    the first assertion, and the one that fails when the address check is
    removed.

    Caught rather than ``pytest.raises`` so that ORDER survives the mutation
    proof: wrapped in ``pytest.raises``, an implementation with no guard fails
    on "DID NOT RAISE" and the request log is never looked at, which is the
    one thing this test exists to look at.
    """
    refused = None
    try:
        await _download(transport, METADATA_URL, destination)
    except FetchRefused as exc:
        refused = exc

    assert transport.urls == []
    assert isinstance(refused, TargetRefused)
    assert not destination.exists()


@pytest.mark.parametrize(
    "host, why",
    [
        ("127.0.0.1", "loopback"),
        ("10.0.0.5", "RFC1918"),
        ("172.16.4.4", "RFC1918"),
        ("192.168.1.1", "RFC1918"),
        ("169.254.169.254", "link-local: the metadata service"),
        ("0.0.0.0", "unspecified"),
        ("224.0.0.1", "multicast"),
        ("240.0.0.1", "reserved"),
        ("[::1]", "IPv6 loopback"),
        ("[fe80::1]", "IPv6 link-local"),
        ("[fc00::1]", "IPv6 unique-local"),
        ("[::ffff:169.254.169.254]", "the IPv4-mapped spelling of the metadata service"),
        ("[::ffff:10.0.0.5]", "the IPv4-mapped spelling of an RFC1918 address"),
        ("[::ffff:224.0.0.1]", "the IPv4-mapped spelling of a multicast address"),
        ("100.64.0.1", "CGNAT: RFC 6598 shared address space, reserved and non-globally-routable"),
        ("100.100.100.100", "CGNAT: well inside 100.64.0.0/10, not just its first address"),
    ],
)
async def test_a_literal_address_off_the_public_internet_is_refused(
    transport, destination, host, why
):
    """A literal IP takes exactly the same path as a name: it is put through
    the resolver and then through the range checks, so there is no spelling of
    an internal address that skips the guard."""
    with pytest.raises(TargetRefused):
        await _download(transport, f"http://{host}/art.png", destination)

    assert transport.urls == [], why


async def test_a_name_resolving_to_a_private_address_is_refused(
    transport, destination, monkeypatch
):
    """The check that cannot be done on the string. ``intranet.example.com``
    looks like any other host until it is resolved, and an attacker who
    controls a DNS record controls what a name-only allowlist sees."""
    monkeypatch.setattr("autoposter.net.guard.resolve_host", lambda h, p: ["10.11.12.13"])

    with pytest.raises(TargetRefused):
        await _download(transport, "http://intranet.example.com/art.png", destination)

    assert transport.urls == []


async def test_every_resolved_address_must_pass_not_just_the_first(
    transport, destination, monkeypatch
):
    """A dual-stack name answers with a list. Checking ``addresses[0]`` and
    connecting to whichever address the OS then picks is not a guard at all --
    the public answer is the decoy and the private one is the target."""
    monkeypatch.setattr(
        "autoposter.net.guard.resolve_host", lambda h, p: [PUBLIC, "169.254.169.254"]
    )

    with pytest.raises(TargetRefused):
        await _download(transport, "http://dual.example.com/art.png", destination)

    assert transport.urls == []


async def test_a_host_that_does_not_resolve_is_refused_not_raised(
    transport, destination, monkeypatch
):
    """``socket.gaierror`` escaping here would be a 500 on an authenticated
    operator request instead of a refusal naming the reason."""
    def explode(host, port):
        raise OSError("Name or service not known")

    monkeypatch.setattr("autoposter.net.guard.resolve_host", explode)

    with pytest.raises(TargetRefused):
        await _download(transport, "http://nowhere.example.com/art.png", destination)

    assert transport.urls == []


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://93.184.216.34/art.png",
        "gopher://93.184.216.34:70/_art",
        "//93.184.216.34/art.png",
        "/etc/passwd",
    ],
)
async def test_a_scheme_outside_http_and_https_is_refused(transport, destination, url):
    """``file://`` is the arbitrary-file-read spelling of the same hole, and
    a schemeless string must not be quietly promoted to one."""
    with pytest.raises(TargetRefused):
        await _download(transport, url, destination)

    assert transport.urls == []


async def test_a_malformed_url_is_refused_not_raised(transport, destination):
    """``httpx.InvalidURL`` (an unparsable IPv6 host) must not escape as a
    500 -- and its own message embeds the caller's string."""
    with pytest.raises(TargetRefused):
        await _download(transport, "http://[::g]/art.png", destination)

    assert transport.urls == []


# --- redirects --------------------------------------------------------------


async def test_a_redirect_to_the_metadata_service_is_refused_at_the_second_hop(
    transport, destination
):
    """The reason redirects are followed by hand. A public URL that answers
    302 to ``169.254.169.254`` defeats any check made only on the URL the
    operator typed: with ``follow_redirects=True`` httpx follows it inside the
    same call and the guard never sees the second target. The request log is
    what proves the second request was never made -- asserted first, and
    caught rather than ``pytest.raises``, for the reason the test above
    gives."""
    def respond(request):
        # The second hop ANSWERS, so dropping the manual handling produces a
        # successful download rather than an httpx redirect-loop error -- the
        # failure then lands on the request-log assertion, which is the one
        # that has to be read.
        if request.url.host == PUBLIC:
            return httpx.Response(302, headers={"location": METADATA_URL})
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    transport._response_for = respond
    refused = None

    try:
        await _download(transport, PUBLIC_URL, destination)
    except FetchRefused as exc:
        refused = exc

    assert transport.urls == [PUBLIC_URL]
    assert isinstance(refused, TargetRefused)
    assert not destination.exists()


async def test_a_relative_redirect_is_resolved_against_the_url_that_sent_it(
    transport, destination
):
    """A ``Location: /elsewhere.png`` is legal and common. Resolved against
    the current URL, so the second hop is validated as the absolute target it
    actually is rather than skipped for not looking like a URL."""
    def respond(request):
        if request.url.path == "/art.png":
            return httpx.Response(302, headers={"location": "/elsewhere.png"})
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    transport._response_for = respond

    await _download(transport, PUBLIC_URL, destination)

    assert transport.urls == [PUBLIC_URL, f"http://{PUBLIC}/elsewhere.png"]
    assert destination.read_bytes() == PNG


async def test_a_relative_redirect_onto_a_refused_host_is_still_refused(
    transport, destination
):
    """Relative resolution is not a bypass: ``//169.254.169.254/x`` is a
    protocol-relative Location that keeps the scheme and changes the host."""
    transport._response_for = _redirect_to("//169.254.169.254/latest/meta-data/")

    with pytest.raises(TargetRefused):
        await _download(transport, PUBLIC_URL, destination)

    assert transport.urls == [PUBLIC_URL]


async def test_a_redirect_chain_longer_than_the_cap_is_refused(transport, destination):
    """Bounded, so a chain that never terminates cannot hold a worker and a
    socket open indefinitely. The cap counts hops after the first request."""
    transport._response_for = _redirect_to("/next.png")

    with pytest.raises(TargetRefused):
        await _download(transport, PUBLIC_URL, destination)

    assert len(transport.urls) == MAX_REDIRECTS + 1
    assert not destination.exists()


async def test_a_chain_within_the_cap_still_downloads(transport, destination):
    """The cap is a ceiling, not a ban: three hops is fine."""
    hops = {"/a.png": "/b.png", "/b.png": "/c.png", "/c.png": "/d.png"}

    def respond(request):
        location = hops.get(request.url.path)
        if location is not None:
            return httpx.Response(302, headers={"location": location})
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    transport._response_for = respond

    await _download(transport, f"http://{PUBLIC}/a.png", destination)

    assert len(transport.urls) == MAX_REDIRECTS + 1
    assert destination.read_bytes() == PNG


async def test_a_redirect_with_no_location_is_refused(transport, destination):
    transport._response_for = lambda request: httpx.Response(302)

    with pytest.raises(TargetRefused):
        await _download(transport, PUBLIC_URL, destination)

    assert transport.urls == [PUBLIC_URL]


async def test_a_redirect_to_a_scheme_outside_the_allowlist_is_refused(
    transport, destination
):
    transport._response_for = _redirect_to("file:///etc/passwd")

    with pytest.raises(TargetRefused):
        await _download(transport, PUBLIC_URL, destination)

    assert transport.urls == [PUBLIC_URL]


# --- what a refusal is allowed to say ---------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://user:hunter2@169.254.169.254/latest/meta-data/",
        "http://intranet.corp.example/secret.png?token=hunter2",
        "file:///etc/passwd",
    ],
)
async def test_a_refusal_names_the_reason_and_never_the_url(
    transport, destination, url, monkeypatch
):
    """6d could log a provider URL's host because a provider client had just
    produced it. This URL is an operator's own string: it can carry userinfo
    credentials, a signed query parameter, or the name of an internal host
    that is itself worth not writing down. The reason is the whole message.
    """
    monkeypatch.setattr("autoposter.net.guard.resolve_host", lambda h, p: ["10.0.0.9"])

    with pytest.raises(FetchRefused) as caught:
        await _download(transport, url, destination)

    message = str(caught.value)
    assert "hunter2" not in message
    assert "169.254" not in message
    assert "intranet" not in message
    assert "passwd" not in message
    assert message.strip()


async def test_a_redirect_refusal_names_the_hop_it_happened_on(transport, destination):
    """Which hop refused is the difference between "the operator typed a bad
    URL" and "a public host redirected us somewhere internal", and it is the
    only part of a redirect chain that can be written down safely."""
    transport._response_for = _redirect_to(METADATA_URL)

    with pytest.raises(TargetRefused) as caught:
        await _download(transport, PUBLIC_URL, destination)

    assert "hop 1" in str(caught.value)
    assert "169.254" not in str(caught.value)


# --- the 6d body guards, which the guard now owns ---------------------------


async def test_a_public_target_is_downloaded(transport, destination):
    """The whole point of the guard is that legitimate fetches still work."""
    await _download(transport, PUBLIC_URL, destination)

    assert transport.urls == [PUBLIC_URL]
    assert destination.read_bytes() == PNG


@pytest.mark.parametrize(
    "response_for, why",
    [
        (lambda request: httpx.Response(404), "an upstream 404"),
        (_ok(b"<!doctype html>", "text/html"), "a content type outside the allowlist"),
        (_ok(b"", "image/png"), "an empty body"),
        (_ok(PNG, "image/png; charset=binary"), None),
    ],
)
async def test_the_body_guards_survive_the_move_into_the_guard(
    transport, destination, response_for, why
):
    """6d's cap, content-type and non-empty checks moved here so the manual
    endpoints get them without a second copy. The last row is the one that
    must still SUCCEED: a parameterised Content-Type is legal."""
    transport._response_for = response_for

    if why is None:
        await _download(transport, PUBLIC_URL, destination)
        assert destination.read_bytes() == PNG
        return

    with pytest.raises(BodyRefused):
        await _download(transport, PUBLIC_URL, destination)


async def test_a_body_beyond_the_cap_is_refused_mid_stream(transport, destination):
    """Mid-stream, not after: a body that never stops arriving must not fill
    the container's tmpdir before anyone objects."""
    transport._response_for = _ok(b"\x00" * (MAX_BYTES + 1), "image/png")

    with pytest.raises(BodyRefused):
        await _download(transport, PUBLIC_URL, destination)


async def test_a_body_refusal_is_a_fetch_refusal_too(transport, destination):
    """One base class, so a caller that does not care which half objected
    writes one except clause."""
    assert issubclass(BodyRefused, FetchRefused)
    assert issubclass(TargetRefused, FetchRefused)
