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
