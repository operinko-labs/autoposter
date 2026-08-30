# assets/fonts

The typeface used to caption generated collection-divider art
(`src/autoposter/collections/separator_art.py`). Three separate things are in
play here and they carry three different licences — conflating them is the
mistake this file exists to prevent.

## 1. The font file — SIL Open Font License

    Comfortaa-Medium.ttf
    sha256 992f89f3c26be37ccebf784b294d36f40b96ed96ad9a3cc1396f4d389fc69d0c
    108 728 bytes, fetched 2026-08-30 from
    https://raw.githubusercontent.com/Kometa-Team/Defaults-Image-Creation/a9e02e9d001f9516a48f2706646b66170c80c32e/create_defaults/fonts/Comfortaa-Medium.ttf

    OFL.txt
    sha256 bc85bae0b512b799bbfb2b916e4d0a34cfd963d09778cd783e248b479e67760a
    fetched 2026-08-30 from
    https://raw.githubusercontent.com/google/fonts/main/ofl/comfortaa/OFL.txt

Copyright line, verbatim from that `OFL.txt`:

> Copyright 2011 The Comfortaa Project Authors
> (https://github.com/alexeiva/comfortaa), with Reserved Font Name "Comfortaa".

Designer: Johan Aakerlund (`google/fonts` `ofl/comfortaa/METADATA.pb`,
`license: "OFL"`). The licence is Comfortaa's own and is **independent of both
Kometa repositories** — which is what makes vendoring the file clean, on the
same grounds `assets/badges/PROVENANCE.md` states for its own fonts.

Two things follow from OFL and are the reason for the exact bytes above:

- **`OFL.txt` is vendored here** because OFL 1.1 §2 requires the licence to
  travel with the font, and *neither* Kometa repository ships one beside the
  `.ttf` (`create_defaults/fonts/` is 17 `.ttf` files and nothing else).
- **This is not Google Fonts' variable `Comfortaa[wght].ttf`.** Instancing that
  file down to weight 500 would produce a Modified Version under OFL's Reserved
  Font Name clause, and it would also throw away the verification below, which
  was performed against these exact bytes. `tests/test_separator_art.py` pins
  the sha256 so a well-meaning substitution fails loudly.

## 2. The rendering parameters — MIT, © 2025 Kometa Team

Face, casing, colour, caption box, point-size clamp and composition are
**transcribed, not chosen by us**, from
`Kometa-Team/Defaults-Image-Creation` at
`a9e02e9d001f9516a48f2706646b66170c80c32e` — `create_defaults/create_default_posters.ps1`
(`Function CreateSeparators`, line 5531) and `create_defaults/create_poster.ps1`
(line 1453). That repository is MIT licensed, `Copyright (c) 2025 Kometa Team`,
read from its own `LICENSE` file.

`.superpowers/sdd/p-div-font.md` records the transcription and the render that
verified it: upstream's own shipped `separators/orig/genre.jpg`, reproduced from
upstream's own `@base/orig.png` with this font at point size 203, RMSE 0.000158
normalised — JPEG quantisation noise.

## 3. The images — deliberately unlicensed

`Kometa-Team/Default-Images`, the source of both the finished separator JPEGs we
fetch for three groups and the textless `@base/<style>.png` layers we caption for
the rest, carries **no LICENSE file** (the API returns `"license": null`,
confirmed 2026-08-30). That is deliberate. Per the maintainer on Discord,
2026-08-29:

> nearly all the default images are based on other work, so I'm not sure it's
> reasonable or valid to apply a license to derivative works

Generating does **not** buy a cleaner posture than fetching. We do not
redistribute upstream's captioned separators — but our output is upstream's
textless layer with our caption composited onto it, fetched at runtime. Only the
caption layer is ours. The result is a derivative of an image set the maintainer
states is itself derivative and deliberately unlicensed. Same posture as
`posters.py`'s hosted defaults: private single-operator deployment, fetched at
runtime, nothing from that repository committed here. If this repository is ever
published, revisit this alongside `assets/badges/PROVENANCE.md`.
