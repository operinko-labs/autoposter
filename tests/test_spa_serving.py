"""Serving the built SPA without shadowing the API.

The whole risk in mounting a single-page application behind an API is
ordering. A catch-all that answers every unmatched path has to answer `/items`
with the SPA's HTML -- that is what makes a client-side route survive a page
reload -- while still letting `/api/does-not-exist` return its JSON 404 and
`/healthz` return its JSON body. Get that wrong and every API client's error
handling starts parsing HTML, and Kubernetes' probe starts passing against a
page that says nothing about whether the service works.

These tests pin the boundary from both sides.
"""

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from starlette.routing import Mount

from autoposter.api.spa import RESERVED_PREFIXES, mount_spa
from autoposter.app import create_app
from autoposter.config.loader import load_config
from autoposter.config.schema import Secrets

EXAMPLE = Path(__file__).parent.parent / "config" / "autoposter.example.yaml"
INDEX_MARKER = "<!-- test index -->"
SECRET = "do not serve me"


def _secrets() -> Secrets:
    return Secrets(
        database_url="postgresql+asyncpg://unused",
        plex_token="x", tmdb_token="x", tvdb_apikey="x",
        fanart_apikey="x", webhook_secret="x",
        admin_password_hash="",
    )


@pytest.fixture
def dist(tmp_path) -> Path:
    """A stand-in for `npm run build` output.

    Building the real bundle would make this suite need a Node toolchain; the
    server has no opinion about what is inside index.html, only about which
    paths reach it.
    """
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        f"<!doctype html><html><body>{INDEX_MARKER}</body></html>", encoding="utf-8"
    )
    (root / "assets" / "app.js").write_text("export default 1;\n", encoding="utf-8")
    return root


@pytest_asyncio.fixture
async def client_with_spa(session_factory, dist):
    app = create_app(load_config(EXAMPLE), session_factory, _secrets())
    mount_spa(app, dist)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _app_with_spa(session_factory, dist):
    app = create_app(load_config(EXAMPLE), session_factory, _secrets())
    mount_spa(app, dist)
    return app


async def _raw_asgi_get(
    app, raw_path: str, headers: dict[str, str] | None = None
) -> tuple[int, dict[bytes, bytes], bytes]:
    """GET `raw_path` verbatim, with no client-side normalisation.

    httpx canonicalises dot segments as it builds the URL --
    ``httpx.URL("http://test/assets/../../x").raw_path`` is ``b"/x"`` -- so
    anything asserting on traversal has to bypass it or it is only testing
    httpx. This speaks ASGI directly instead: the scope carries the path
    exactly as an attacker's client would put it on the wire.

    ``headers`` is for callers whose target sits behind the session
    dependency (tests/test_api_artwork.py): without a token those would 401
    before the handler ever ran, and a traversal test that stops at the auth
    layer proves nothing about what the handler would have done with the path.
    """
    extra = [
        (name.lower().encode("utf-8"), value.encode("utf-8"))
        for name, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": raw_path,
        "raw_path": raw_path.encode("utf-8"),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test"), *extra],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    status = 500
    # Not `headers`: that is the request-headers parameter above, and rebinding
    # it worked only because `extra` is computed before this point. Moving that
    # comprehension down would have silently dropped the caller's auth header
    # and turned the artwork traversal test into a 401 that proves nothing.
    response_headers: dict[bytes, bytes] = {}
    body = bytearray()

    async def send(message):
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
            response_headers.update(dict(message["headers"]))
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))

    await app(scope, receive, send)
    return status, response_headers, bytes(body)


async def test_root_serves_the_index(client_with_spa):
    response = await client_with_spa.get("/")
    assert response.status_code == 200
    assert INDEX_MARKER in response.text


async def test_a_client_route_returns_the_index(client_with_spa):
    """A deep link the SPA routes internally. The server has no such route,
    and must answer with the shell rather than a 404 -- otherwise reloading
    the page on /failures breaks."""
    response = await client_with_spa.get("/failures")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert INDEX_MARKER in response.text


async def test_built_assets_are_served(client_with_spa):
    response = await client_with_spa.get("/assets/app.js")
    assert response.status_code == 200
    assert "export default 1;" in response.text


async def test_api_404_stays_json_and_is_not_the_spa(client_with_spa):
    """The regression this file exists for."""
    response = await client_with_spa.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert INDEX_MARKER not in response.text


async def test_an_unauthenticated_api_call_still_401s_rather_than_serving_html(
    client_with_spa,
):
    """A real API path behind the session dependency. If the catch-all took
    precedence this would be a 200 of HTML, and the SPA would never learn it
    needs to log in."""
    response = await client_with_spa.get("/api/status")
    assert response.status_code == 401
    assert INDEX_MARKER not in response.text


async def test_healthz_is_not_shadowed(client_with_spa):
    response = await client_with_spa.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_metrics_is_not_shadowed(client_with_spa):
    response = await client_with_spa.get("/metrics")
    assert response.status_code == 200
    assert INDEX_MARKER not in response.text


async def test_a_missing_dist_directory_is_not_an_error(session_factory):
    """A developer checkout with no `npm run build`, and the Python test suite
    itself, must not need a Node toolchain to start the app."""
    app = create_app(load_config(EXAMPLE), session_factory, _secrets())
    mount_spa(app, Path("does/not/exist"))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/healthz")).status_code == 200
        # No SPA to serve, so the catch-all must not exist at all rather than
        # answering with a broken page.
        assert (await c.get("/failures")).status_code == 404


@pytest.mark.parametrize(
    "raw_path",
    [
        "/assets/../../outside.txt",
        "/assets/../outside.txt",
        "/assets/subdir/../../../outside.txt",
    ],
)
async def test_traversal_through_the_assets_mount_is_refused(
    session_factory, dist, raw_path
):
    """Path traversal into the directory dist sits in.

    The secret here is a fixture file; in the container dist sits beside the
    application's own files and its config mount.

    Driven straight at the ASGI app rather than through AsyncClient: httpx
    resolves dot segments while building the URL, so
    `client.get("/assets/../../outside.txt")` puts `/outside.txt` on the wire
    and the server never sees a `..` to reject. A test written that way passes
    against an implementation that would have served the file, which is what
    the previous version of this test did. A hostile client does not normalise,
    so neither does this one.
    """
    outside = dist.parent / "outside.txt"
    outside.write_text(SECRET, encoding="utf-8")

    status, _headers, body = await _raw_asgi_get(_app_with_spa(session_factory, dist), raw_path)

    assert status == 404
    assert SECRET not in body.decode("utf-8", "replace")


@pytest.mark.parametrize(
    "raw_path",
    ["/../outside.txt", "/failures/../../outside.txt", "/./../outside.txt"],
)
async def test_traversal_through_the_catch_all_serves_the_shell_not_the_file(
    session_factory, dist, raw_path
):
    """The other half of the same surface.

    The catch-all is handed whatever the /assets mount did not claim, so a
    `..` arrives there too. It must keep treating the path as an opaque
    client-side route and answer with index.html -- never resolve it against
    the filesystem.
    """
    outside = dist.parent / "outside.txt"
    outside.write_text(SECRET, encoding="utf-8")

    status, headers, body = await _raw_asgi_get(_app_with_spa(session_factory, dist), raw_path)

    assert status == 200
    assert headers[b"content-type"].startswith(b"text/html")
    text = body.decode("utf-8", "replace")
    assert INDEX_MARKER in text
    assert SECRET not in text


CATCH_ALL_PATH = "/{full_path:path}"


def _registered_routes(routes, prefix: str = ""):
    """Every (route, full path) the app serves, across both FastAPI layouts.

    FastAPI used to flatten `include_router` straight into `app.routes`, so a
    walk over `.path` saw everything. The version this project pins does not:
    it leaves an opaque `_IncludedRouter` there and resolves the real routes
    from it, so the same walk sees two routers plus `/metrics` and none of the
    API's own paths at all.

    That difference is exactly the trap this file keeps catching in other
    forms. A walk written for the flat layout passes on the nested one having
    inspected nothing -- it was written against a host interpreter, went green,
    and only the anchor assertions in the caller showed that the container
    running the pinned FastAPI was checking three routes out of seventeen.

    So handle both, and let the caller prove the walk found something. A route
    with a `.path` is a real route, including a `Mount`, which owns its whole
    subtree and is therefore not recursed into. One without is an inclusion,
    and its routes are read back off it with the include-time prefix applied.
    """
    for route in routes:
        path = getattr(route, "path", None)
        if path is not None:
            yield route, prefix + path
            continue
        included = getattr(route, "original_router", None)
        if included is None:
            continue
        context = getattr(route, "include_context", None)
        nested = getattr(context, "prefix", "") or ""
        yield from _registered_routes(included.routes, prefix + nested)


@pytest.mark.parametrize("api_docs_enabled", [False, True])
def test_every_registered_top_level_segment_is_reserved(dist, api_docs_enabled):
    """`RESERVED_PREFIXES` is hand-maintained; this ties it to the real routes.

    Every test above names a path. That is fine for the paths that existed
    when they were written and useless for the next one: phase 4c adds
    image-serving and item endpoints, and a new top-level prefix would work
    perfectly for its own paths while every *miss* under it -- a bad id, a
    trailing slash, a typo -- came back as a 200 of index.html instead of a
    404. The API client then parses HTML looking for an error, which is the
    single failure this module exists to prevent, reintroduced by a file
    nobody thought to edit.

    So walk what the app actually registered rather than restating it. A route
    is safe from the catch-all only if its first segment is reserved, because
    the catch-all matches on that segment alone.

    Both `api_docs_enabled` settings are exercised: FastAPI registers `/docs`,
    `/redoc` and `/openapi.json` outside any router and only when they are
    switched on, so a run with the example config's default alone would never
    see them.
    """
    config = load_config(EXAMPLE).model_copy(update={"api_docs_enabled": api_docs_enabled})
    # No session factory: nothing here sends a request, and building one would
    # make an introspection test wait on PostgreSQL.
    app = create_app(config, None, _secrets())
    mount_spa(app, dist)

    registered = list(_registered_routes(app.routes))
    paths = [path for _route, path in registered]
    # Anchors, before believing anything the walk reports. Both of these are
    # about the walk, not about the app: if it stops understanding how routes
    # are registered it must go red rather than quietly finding nothing.
    assert CATCH_ALL_PATH in paths, (
        f"no {CATCH_ALL_PATH!r} route, so mount_spa did not install the catch-all "
        "and this test is checking an app that has no SPA fallback at all"
    )
    assert "/api/status" in paths, (
        "the walk did not reach the API router's routes, so it would report no "
        f"offenders whatever spa.py said. Found: {sorted(paths)}"
    )

    offenders: dict[str, list[str]] = {}
    for route, path in registered:
        if path == CATCH_ALL_PATH:
            continue
        if isinstance(route, Mount):
            # A Mount owns its whole subtree -- `/assets/missing.js` is
            # answered by StaticFiles' own 404, never by the catch-all -- so
            # it cannot leak the shell the way an unreserved APIRoute can.
            continue
        first = path.lstrip("/").split("/", 1)[0]
        if first and first not in RESERVED_PREFIXES:
            offenders.setdefault(first, []).append(path)

    assert not offenders, (
        f"these top-level prefixes are registered on the app but missing from "
        f"RESERVED_PREFIXES in src/autoposter/api/spa.py: {offenders}. Their own "
        "paths work, but any unmatched path under them falls through to the SPA "
        "catch-all and comes back as a 200 of index.html instead of a 404"
    )


def test_the_dockerfile_ships_the_built_spa():
    """The image is where all of the above has to hold, and it is the one
    place `spa_dist()`'s fallback cannot work: the package is pip-installed
    into site-packages, which has no frontend/ beside it. So the Node stage's
    output has to be copied in and AUTOPOSTER_SPA_DIST pointed at it, exactly
    as AUTOPOSTER_ASSETS_ROOT is for the bundled assets.

    Drop either line and the service still starts, /healthz still passes and
    the API still answers -- while `/` returns 404 and the UI simply is not
    there. The image job in .forgejo/workflows/ci.yml catches that by asking
    the built image for the page; this catches it without a Docker daemon.
    """
    dockerfile = (Path(__file__).parent.parent / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY --from=frontend /frontend/dist ./frontend/dist" in dockerfile, (
        "the image does not copy the built SPA in, so there is nothing to serve"
    )
    assert "AUTOPOSTER_SPA_DIST=/app/frontend/dist" in dockerfile, (
        "the image does not point AUTOPOSTER_SPA_DIST at the copied bundle, so "
        "spa_dist() falls back to a path relative to site-packages and finds nothing"
    )
    # `npm ci` installs exactly what package-lock.json pins and fails on a
    # mismatch; `npm install` would quietly resolve something newer and
    # rewrite the lockfile instead. A swap from one to the other would leave
    # the two assertions above green while silently reintroducing the kind of
    # version skew (ruff, Pillow, fastapi) that has broken this repo three
    # times, so it has to be pinned here explicitly. Checked against the
    # actual command rather than a bare substring: the comment above `RUN npm
    # ci` in the Dockerfile itself says the words "npm install", which a bare
    # `"npm install" not in dockerfile` would trip over.
    assert "npm ci" in dockerfile, "the frontend stage must install with npm ci, not npm install"
    assert "RUN npm install" not in dockerfile, (
        "npm install would ignore package-lock.json and rewrite it, defeating "
        "the point of a committed lockfile"
    )


# --- cache headers (perf spec A2) ---


async def test_built_assets_are_cached_as_immutable(client_with_spa):
    """Vite names every file under /assets by its content hash, so the bytes
    behind one URL never change: a browser may keep it for a year without
    even revalidating."""
    response = await client_with_spa.get("/assets/app.js")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


async def test_a_missing_asset_is_not_cached_as_immutable(client_with_spa):
    """A 404 held for a year would outlive the deploy that fixes it."""
    response = await client_with_spa.get("/assets/not-built.js")

    assert response.status_code == 404
    assert "immutable" not in response.headers.get("cache-control", "")


@pytest.mark.parametrize("path", ["/", "/failures"])
async def test_the_shell_is_revalidated_on_every_load(client_with_spa, path):
    """index.html names the hashed files of the build that produced it. A
    cached shell from the previous deploy would ask the new pod for files it
    no longer has -- a blank page until the cache expired."""
    response = await client_with_spa.get(path)

    assert response.status_code == 200
    assert INDEX_MARKER in response.text
    assert response.headers["cache-control"] == "no-cache"
