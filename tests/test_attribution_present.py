"""Provider attribution must survive the build, not just exist in a component.

TMDB's terms require their notice verbatim alongside their logo, and
TheTVDB's require attribution carrying a direct link to their site. Both are
acceptance criteria for this phase.

A component test cannot prove either. `frontend/src/pages/Settings.test.tsx`
renders `<Settings />` directly, so it passes whether or not anything routes
to that page -- and this branch shipped exactly that failure once already:
`main.tsx` never imported `App`, so every page including this one was absent
from the bundle while every check stayed green.

So these assertions are made against `frontend/dist`, the artefact the
container serves. If the text is not in there, the user never sees it.

The same reasoning covers the logo. `Settings.tsx` referenced
`/tmdb-logo.png` from `public/`, which Vite copies to the *root* of `dist/`
while `spa.py` mounts only `/assets` -- so the request fell through to the
catch-all and came back as a 200 of `index.html`. A broken image behind a 200
is invisible to monitoring, which is why the check below is "every static
path the bundle references is actually served, and not as HTML" rather than
"the file exists somewhere".
"""

import os
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from autoposter.api.spa import mount_spa

REPO = Path(__file__).resolve().parent.parent
DIST = REPO / "frontend" / "dist"

# Verbatim from TMDB's attribution requirements. Not a paraphrase, not a
# regex with wiggle room: the wording is the requirement.
TMDB_NOTICE = (
    "This product uses TMDB and the TMDB APIs but is not endorsed, "
    "certified, or otherwise approved by TMDB."
)
TVDB_LINK = "https://thetvdb.com"

# Root-absolute references to a static file, in either the HTML or the
# bundled JS. esbuild emits string literals in backticks, so all three
# delimiters have to be accepted.
_STATIC_REF = re.compile(
    r"""["'`](/[A-Za-z0-9_\-./@]+\."""
    r"""(?:png|jpe?g|gif|svg|webp|ico|css|js|mjs|woff2?|ttf|otf|json|txt|webmanifest))["'`]"""
)


def _require_dist() -> Path:
    """The built bundle, or a decision about why it is missing.

    Skipping locally is deliberate: the plan's task 2 promise is that a source
    checkout with no Node toolchain can still run this suite, and turning that
    into a hard failure would make `pytest tests/` unrunnable for anyone who
    has not built the frontend.

    Skipping *in CI* is not acceptable -- a guard that never runs where it
    matters is decoration. CI builds the frontend before this suite (see the
    "Build the frontend" step in .forgejo/workflows/ci.yml), so a missing dist
    there means that step was removed or failed, and this fails rather than
    reporting a green skip.
    """
    if (DIST / "index.html").is_file():
        return DIST
    if os.environ.get("CI", "").lower() in {"1", "true", "yes"}:
        pytest.fail(
            f"{DIST} does not exist, so the attribution required by TMDB's and "
            "TheTVDB's terms went unverified. CI must build the frontend "
            "before running this suite."
        )
    pytest.skip(
        f"{DIST} does not exist; run `npm run build` in frontend/ to check the "
        "shipped bundle. This is a hard failure in CI."
    )


def _bundle_text() -> str:
    dist = _require_dist()
    parts = [(dist / "index.html").read_text(encoding="utf-8")]
    parts.extend(
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted((dist / "assets").glob("*.js"))
    )
    return "\n".join(parts)


def _referenced_static_paths() -> set[str]:
    return {match.group(1) for match in _STATIC_REF.finditer(_bundle_text())}


def test_the_built_bundle_carries_tmdbs_notice_verbatim():
    assert TMDB_NOTICE in _bundle_text(), (
        "TMDB's required notice is not in the built bundle. Either the page "
        "was reworded or nothing routes to it."
    )


def test_the_built_bundle_links_thetvdb_directly():
    assert TVDB_LINK in _bundle_text(), (
        "TheTVDB's terms require attribution with a direct link to their site."
    )


def test_the_bundle_references_static_assets_at_all():
    """Guards the guard below: if the extraction found nothing, every
    servability assertion would pass by having nothing to check."""
    paths = _referenced_static_paths()
    assert paths, "no static asset references found in the bundle"
    assert any(
        path.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"))
        for path in paths
    ), f"the bundle references no image; TMDB's logo is required. Found: {sorted(paths)}"


async def test_every_static_path_the_bundle_references_is_actually_served():
    """The check that would have caught `/tmdb-logo.png`.

    A path the SPA mount does not claim is answered by the catch-all with
    index.html and a 200, so the browser gets HTML where it asked for an
    image and renders a broken one. Asserting on the status code alone would
    miss it entirely; the content type is what separates "served" from
    "swallowed".
    """
    dist = _require_dist()
    paths = sorted(_referenced_static_paths())

    app = FastAPI()
    mount_spa(app, dist)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in paths:
            response = await client.get(path)
            assert response.status_code == 200, f"{path} is not served: {response.status_code}"
            content_type = response.headers["content-type"]
            assert not content_type.startswith("text/html"), (
                f"{path} was answered with the SPA shell rather than the file. "
                "Vite copies public/ to the root of dist/, which spa.py does "
                "not serve -- import the file from src/assets/ instead so it "
                "is emitted under /assets."
            )
