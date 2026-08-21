# autoposter

Event-driven artwork and metadata automation for a Plex library, replacing
[Posterizarr](https://github.com/fscorrupt/Posterizarr) and
[Kometa](https://kometa.wiki/) with a single service.

Radarr and Sonarr webhooks drive a per-item pipeline: resolve the item in Plex,
fetch textless artwork, composite a poster with ImageMagick, and write it into
the shared asset tree. Work is queued in PostgreSQL and processed by a worker
pool, so a season-pack import becomes one pass per affected item rather than a
full-library scan.

- Design: [`docs/superpowers/specs/2026-08-20-autoposter-design.md`](docs/superpowers/specs/2026-08-20-autoposter-design.md)
- Phase 1 plan: [`docs/superpowers/plans/2026-08-20-phase1-posterizarr-parity.md`](docs/superpowers/plans/2026-08-20-phase1-posterizarr-parity.md)
- Deployment: [`deploy/README.md`](deploy/README.md)

## Status

Phase 1 (Posterizarr parity) is implemented. Rendered output is byte-identical
to the tool it replaces, verified against the production asset tree.

## Web UI

A React SPA (`frontend/`) is built and served by the same container, behind
the app's own routes — no separate server or deployment. It covers the
Dashboard, Failures, Collections (read-only) and Settings views the phase 4a
REST API can already answer; see `docs/superpowers/specs/2026-08-20-autoposter-design.md`
section 6a for what's provisional and what's still deferred to a later phase.

Building it locally requires **Node 26.7.0** — the exact patch, not a floor.
`frontend/package.json`'s `engines.node` is the declaration; the Dockerfile
stage and CI name the same patch, and `tests/test_toolchain_versions.py` fails
if any of them drift apart. `frontend/.npmrc` sets `engine-strict=true`, so any
other Node makes `npm ci` exit 1 instead of printing an `EBADENGINE` warning
and installing anyway:

```bash
cd frontend && npm ci && npm run build
```

On a host with a different Node, run that build in the pinned image instead --
no local toolchain, same result:

```bash
docker run --rm -v "$PWD/frontend:/frontend" -w /frontend node:26.7.0-alpine sh -c "npm ci && npm run build"
```

This emits `frontend/dist/`, which the app serves automatically if present —
`npm run build` first (`tsc --noEmit && vite build`), so a type error fails
the build rather than shipping something nothing typechecked. Without a
`frontend/dist/`, the app still starts and answers `/api`, `/healthz` and
`/metrics` normally; only `/` and the SPA's own routes are unavailable. The
container image builds the frontend itself in a `node:26.7.0-alpine` stage, so no
Node toolchain is needed to run the image.

The UI sits behind a single admin password: set `AUTOPOSTER_ADMIN_PASSWORD_HASH`
to a bcrypt hash (never the plaintext) to allow logins — unset, every login
attempt fails closed rather than skipping auth. See `deploy/README.md`'s
"Web UI authentication" section for how to generate the hash.

## Development

```bash
docker compose up -d postgres
pip install -e ".[dev]"
pytest
```

The image-parity tests require a **Q16-HDRI** ImageMagick build and skip
without one, so a checkout with no ImageMagick still runs `pytest`. They carry
`@pytest.mark.imagemagick`; CI deselects them from the main run and executes
them in a container that has such a build, where the same gate is a hard
failure rather than a skip. See `deploy/README.md`.

## Obtaining a Plex token

```bash
python -m autoposter.plex.auth
```

Runs the Plex PIN flow and prints a token for `AUTOPOSTER_PLEX_TOKEN`.

---

## Attribution

This project retrieves artwork and metadata from third-party APIs whose terms
require attribution.

The web UI (see "Web UI" above) is a user-facing surface displaying metadata
from these APIs — so **in-application attribution is a hard requirement of
this project, not an optional extra**. It now ships on the UI's Settings page
(`frontend/src/pages/Settings.tsx`), which is where TheTVDB's and TMDB's
requirements below are actually satisfied; the readme exemption TheTVDB grants
"command line products or development libraries" no longer applies now that a
UI exists. This file keeps the notices too, both as background on why each one
is required and so they still cover any use of this project without its UI.

### TheTVDB

Metadata and artwork provided by [TheTVDB.com](https://www.thetvdb.com/).
Please consider [supporting TheTVDB](https://www.thetvdb.com/subscribe).

> Unless approved by TheTVDB, attribution with a direct link to TheTVDB.com
> must be displayed to end users viewing metadata from our API. Command line
> products or development libraries may display attribution on your about or
> readme pages.
>
> — [TheTVDB API information](https://www.thetvdb.com/api-information#attribution)

The UI renders this as text with a direct link to `https://thetvdb.com`,
which satisfies the "direct link" requirement, rather than TheTVDB's official
brand image — that asset is not vendored into this repository, and fetching a
third-party brand image from the network unattended isn't something this
project does without asking. **Open item**: vendor the image and swap it in;
see `docs/superpowers/specs/2026-08-20-autoposter-design.md` section 6a.

### TMDB

This product uses TMDB and the TMDB APIs but is not endorsed, certified, or
otherwise approved by TMDB.

[TMDB](https://www.themoviedb.org/) requires this notice to be displayed
prominently, together with the TMDB logo, identified as less prominent than the
application's own branding. The UI renders the notice verbatim alongside the
logo (`frontend/src/assets/tmdb-logo.png`), sized smaller than the
application's own wordmark on the same Settings page.

### Fanart.tv

Artwork provided by [fanart.tv](https://fanart.tv/).

Fanart.tv's attribution obligation ("if you have a publicly available program,
you must inform your users of this website and the images you use") applies to
their **project API key** — the key a developer embeds in a distributed
application. This project embeds no key: each operator supplies their own
personal key via `AUTOPOSTER_FANART_APIKEY`, obtained from their own fanart.tv
account, so that obligation does not attach and every user reaches the site
anyway. The credit above is kept as a courtesy.

Two of their conditions do apply regardless of key type and are treated as
design constraints rather than documentation:

- *"Do not perform more requests than are necessary for each user. This means
  no downloading all of our content. Play nice with our server."* — comfortably
  satisfied: Fanart is queried once per title, so a full sweep of a ~2,200-title
  library is ~2,200 requests, and episode artwork never touches it at all. The
  scheduled passes still cache, skip unchanged items and rate-limit, as a matter
  of good behaviour rather than necessity. See the spec's scheduler section.
- *"You MUST keep the email address in your account information current and
  accurate in case we need to contact you regarding your key."* — an operator
  responsibility, noted in `deploy/README.md`.

If a project key is ever adopted, Fanart also accepts a personal key as
`client_key` alongside it, which shortens their artwork-freshness delay from
about seven days to about two.

### IMDb

Information courtesy of IMDb (https://www.imdb.com). Used with permission.

Ratings come from IMDb's bulk non-commercial datasets, which are licensed for
**personal and non-commercial use only** and must not be republished or
repurposed into another database. If this project is ever distributed
commercially, that licence is a blocker and needs review.

### In the web UI

Attribution appears **in the UI itself**, on the Settings page
(`frontend/src/pages/Settings.tsx`):

- **TheTVDB** — attribution text with a **direct link to TheTVDB.com**. See
  the open item above about the official brand image.
- **TMDB** — the notice above, rendered verbatim, **and the TMDB logo**,
  displayed prominently and less prominently than this application's own
  branding.
- **Fanart.tv** — no in-UI attribution required while each operator supplies
  their own personal key, as above. Revisit if a project key is ever adopted.

This was tracked as an acceptance criterion of the UI phase in the spec, not
as a follow-up — `tests/test_attribution_present.py` asserts against the
built bundle so the requirement can't regress silently.
