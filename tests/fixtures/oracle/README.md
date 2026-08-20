# Badge parity oracle (Phase 2b)

Harvested from the production deployment, not synthetic.

- `All_Souls_base_no_overlay.jpg` — our Phase 1 base, 2000x3000 JPEG, byte-identical
  to what the tool being replaced writes to `/assets`.
- `All_Souls_plex_overlaid.jpg` — what Kometa uploaded to Plex for the same item:
  **WebP, 1000x1500**, EXIF tag `0x04BC = "overlay"`. Eight badges: resolution,
  audio codec, languages, IMDb critic, TMDB audience, Common Sense, video format,
  runtime.
- `Dune_Part_Two_plex_overlaid.jpg` — a second overlaid example (also WebP 1000x1500).

The `.jpg` extensions are how they were downloaded; the bytes are WebP. Phase 2b's
parity test composites badges onto the base and compares against the overlaid file.

## Episode oracle (added for Phase 2b)

- `8OO10C_S01E01_base_no_overlay.jpg` — our Phase 1 title-card base, **3840x2160 JPEG**.
- `8OO10C_S01E01_plex_overlaid.jpg` — Kometa's output for the same episode: **WebP, 1920x1080**,
  EXIF `0x04BC = "overlay"`.

Both were pulled from the live Plex server, where they coexist as two `upload://` entries on the
same episode ("8 Out of 10 Cats" S01E01) — Plex keeps the pre-overlay upload alongside the
overlaid one, which is what makes a matched pair recoverable at all.

This pair is what fixes the episode-card geometry: diffing the downscaled base against the
overlaid output confirmed `episode_info` sits at `vertical_offset: 150` (y0 = 825 = 1080-105-150),
not the naively-read 30, and confirmed the `ratings` backdrop is 190x190.

It also captures badge **suppression**: only one of the two rating badges is drawn, because the
episode has no IMDb critic rating. Anything reproducing this must skip absent values, not render
an empty badge.
