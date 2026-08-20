# assets/

This directory is a placeholder. `docker build` copies it (`COPY assets ./assets`
in the Dockerfile), so the directory structure below must exist in the repo even
though the real files are not checked in — they are binary assets licensed to
the operator, not something this project can generate or fabricate.

Before building an image that will actually render posters, populate these
files from the operator's existing Posterizarr installation (they live
alongside its config, typically under `fonts/` and `overlays/` there):

## assets/fonts/

- `Comfortaa-Medium.ttf` — the title/text font used for poster and title-card
  text overlays (`font:` in `config/autoposter.example.yaml`).
- `Colus-Regular.ttf` — secondary font used by the existing Posterizarr overlay
  styles.

## assets/overlays/

- `overlay.png` — the standard poster overlay (`overlay_file` for posters).
- `bottom-up-fade.png` — the bottom-up gradient overlay used on some poster
  styles.
- `bottom-up-fade-background.png` — the bottom-up gradient overlay used on
  background/fanart art.

## Why these aren't committed

Committing substitute fonts or overlays would silently produce artwork that
does not match the operator's existing library — wrong typeface, wrong
gradient, wrong branding — with no error to signal it. That failure mode is
worse than a build that refuses to run until the real assets are supplied.

The `.keep` files in `fonts/` and `overlays/` exist only so git tracks the
empty directories; delete them once the real files are in place (they are
not referenced by the application).
