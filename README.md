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

## Development

```bash
docker compose up -d postgres
pip install -e ".[dev]"
pytest
```

The image-parity tests require a **Q16-HDRI** ImageMagick build and skip
without one; the runtime image provides it. See `deploy/README.md`.

## Obtaining a Plex token

```bash
python -m autoposter.plex.auth
```

Runs the Plex PIN flow and prints a token for `AUTOPOSTER_PLEX_TOKEN`.

---

## Attribution

This project retrieves artwork and metadata from third-party APIs whose terms
require attribution.

A web UI is part of the design (spec section 6), and that is a user-facing
surface displaying metadata from these APIs — so **in-application attribution
is a hard requirement of this project, not an optional extra**. The notice
below satisfies the current phase, where no UI has shipped yet and TheTVDB
explicitly permits readme attribution for command line products and libraries.
It stops being sufficient the moment the UI does ship. The requirement is
recorded in the spec alongside the UI's own definition so it is designed in
rather than bolted on.

### TheTVDB

Metadata and artwork provided by [TheTVDB.com](https://www.thetvdb.com/).
Please consider [supporting TheTVDB](https://www.thetvdb.com/subscribe).

> Unless approved by TheTVDB, attribution with a direct link to TheTVDB.com
> must be displayed to end users viewing metadata from our API. Command line
> products or development libraries may display attribution on your about or
> readme pages.
>
> — [TheTVDB API information](https://www.thetvdb.com/api-information#attribution)

### TMDB

This product uses TMDB and the TMDB APIs but is not endorsed, certified, or
otherwise approved by TMDB.

[TMDB](https://www.themoviedb.org/) requires this notice to be displayed
prominently, together with the TMDB logo, identified as less prominent than the
application's own branding. The logo is not shipped here because there is no
user-facing surface yet; see the note below.

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
  no downloading all of our content. Play nice with our server."* — the
  pipeline is event-driven and fetches per item; the scheduled passes in later
  phases must not bulk-crawl Fanart. See the spec's scheduler section.
- *"You MUST keep the email address in your account information current and
  accurate in case we need to contact you regarding your key."* — an operator
  responsibility, noted in `deploy/README.md`.

If a project key is ever adopted, Fanart also accepts a personal key as
`client_key` alongside it, which shortens their artwork-freshness delay from
about seven days to about two.

### Required in the web UI

When the UI ships, attribution must appear **in the UI itself**, on any view
showing artwork or metadata from these providers — not only in this file:

- **TheTVDB** — attribution with a **direct link to TheTVDB.com**, shown to end
  users viewing that metadata.
- **TMDB** — the notice above **and the TMDB logo**, displayed prominently, and
  less prominently than this application's own branding. The logo asset needs
  to be added to `assets/` as part of that work.
- **Fanart.tv** — no in-UI attribution required while each operator supplies
  their own personal key, as above. Revisit if a project key is ever adopted.

This is tracked as a requirement of the UI in the spec, not as a follow-up.
